"""
mariadb_reporter — pytest plugin for publishing test results to MariaDB
=======================================================================

This plugin hooks into the pytest lifecycle and writes one row per test result
into a MariaDB database.  The results can then be visualised with the bundled
Grafana dashboards (see ``grafana/`` at the repository root).

Architecture overview
---------------------

Plugin registration (``pytest_configure``)
    Two plugin objects are registered on startup:

    ``mariadb-failure-tagger``  (:class:`MariaDBFailureTagger`)
        Loads ``failure_mapper/failure_map.yaml`` at startup and provides
        :meth:`MariaDBFailureTagger.tag_failure` to classify ``fail`` / ``error``
        results with a human-readable *failure tag*.

    ``mariadb-reporter``  (:class:`MariaDBReporter`)
        Connects to MariaDB (lazily, on ``pytest_sessionstart``), buffers one
        result dict per test during the run, and flushes everything to the
        database in ``pytest_sessionfinish``.

Database schema
---------------

The schema is managed by ``schema/db.sql`` (full reset) or by passing
``--mariadb-init-schema`` which runs idempotent ``CREATE TABLE IF NOT EXISTS``
and ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` statements directly from the
plugin.

Five tables are used:

``hosts``
    One row per unique host name observed across all runs.

``tests``
    One row per unique test identity (keyed by a SHA-1 *test_uid* derived from
    ``canonical_nodeid``, ``test_name``, ``test_suite``, and ``test_class``).

``test_runs``
    One row per pytest session (UUID ``run_id``).

``test_results``
    One row per (run, host, test) triple — the main fact table.

``test_result_markers``
    Normalised pytest markers attached to each result row.

Timezone handling
-----------------

All ``DATETIME(6)`` columns are stored as **naive IST** (UTC+05:30) to match
the timezone of the Grafana data source.  The module-level constant :data:`IST`
and helpers :func:`_istnow_naive` / :func:`_epoch_to_ist_naive` centralise this
conversion.

CLI options (``--mariadb-*``)
------------------------------

All options are in the ``mariadb-reporting`` group added by
:func:`pytest_addoption`.  See the ``README.md`` in this directory for a full
option reference table.

Dependencies
------------

``PyMySQL``
    Runtime dependency; the plugin disables itself with a warning when not
    installed.

``PyYAML``
    Required only for failure tagging; missing PyYAML disables the tagger but
    not the reporter.
"""

import datetime as dt
import hashlib
import os
import re
import socket
from urllib.parse import urlsplit
import uuid
import warnings

import pytest


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Fixed-offset timezone for IST (UTC+05:30).
#: All timestamps stored in the database use this timezone stripped of tzinfo
#: so that MariaDB ``DATETIME(6)`` columns receive a naive local time.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _istnow_naive():
    """Return the current wall-clock time as a naive :class:`datetime.datetime` in IST.

    MariaDB ``DATETIME(6)`` columns do not carry timezone information.  By
    always converting to IST before stripping ``tzinfo`` every timestamp stored
    in the database is consistently in the same local timezone, which makes
    Grafana time-range queries behave correctly without a timezone offset.

    Returns
    -------
    datetime.datetime
        Current time in IST with ``tzinfo=None``.
    """
    return dt.datetime.now(IST).replace(tzinfo=None)


def _epoch_to_ist_naive(value):
    """Convert a POSIX epoch float to a naive IST :class:`datetime.datetime`.

    pytest report objects expose ``start`` and ``stop`` as float seconds since
    the Unix epoch.  This helper converts them to the same naive-IST format used
    by :func:`_istnow_naive`.

    Parameters
    ----------
    value : float or None
        POSIX timestamp.  ``None`` is passed through as ``None``.

    Returns
    -------
    datetime.datetime or None
        Naive IST datetime, or ``None`` when *value* is ``None``.
    """
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).astimezone(IST).replace(
        tzinfo=None
    )


# ---------------------------------------------------------------------------
# Node-ID helpers
# ---------------------------------------------------------------------------

def _canonical_nodeid(nodeid):
    """Strip the parametrize bracket suffix from a pytest node ID.

    For a node ID such as ``tests/test_foo.py::test_bar[param0-salt://host]``
    this returns ``tests/test_foo.py::test_bar``, which is the stable identity
    used to correlate results across multiple parametrised runs.

    Parameters
    ----------
    nodeid : str
        Full pytest node ID (may or may not contain ``[…]``).

    Returns
    -------
    str
        Node ID with everything from the first ``[`` onwards removed.
    """
    return nodeid.split("[", 1)[0]


def _test_uid(canonical_nodeid, test_name, test_suite, test_class):
    """Compute a stable SHA-1 fingerprint for a test identity tuple.

    The fingerprint is stored in ``tests.test_uid`` and used as a unique key so
    that the same logical test always maps to the same ``tests`` row regardless
    of the order in which runs are inserted.

    The payload is a ``|``-delimited string of the four fields; ``None`` values
    are coerced to empty string so the hash is deterministic.

    Parameters
    ----------
    canonical_nodeid : str or None
        Node ID without parametrize bracket (see :func:`_canonical_nodeid`).
    test_name : str or None
        Human-readable test name (see :func:`_normalized_test_name`).
    test_suite : str or None
        Relative path to the test module (``item.location[0]``).
    test_class : str or None
        ``__name__`` of the test class, or ``None`` for module-level tests.

    Returns
    -------
    str
        40-character lowercase hex SHA-1 digest.
    """
    payload = "|".join(
        [
            str(canonical_nodeid or ""),
            str(test_name or ""),
            str(test_suite or ""),
            str(test_class or ""),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Backend / host extraction helpers
# ---------------------------------------------------------------------------

def _is_backend_selector(value):
    """Return ``True`` when *value* looks like a testinfra backend URI.

    Recognised schemes: ``salt``, ``ssh``, ``paramiko``, ``docker``,
    ``podman``, ``local``, ``ansible``, ``chroot``.

    Parameters
    ----------
    value : str or None
        String to inspect.

    Returns
    -------
    bool
    """
    if value is None:
        return False
    text = str(value).strip()
    return bool(
        re.match(
            r"^(?:salt|ssh|paramiko|docker|podman|local|ansible|chroot)://",
            text,
        )
    )


def _contains_backend_selector(text):
    """Return ``True`` when *text* contains a testinfra backend URI anywhere.

    Unlike :func:`_is_backend_selector` this does a substring search, making it
    useful for inspecting parametrized test names that embed the backend URI in
    the middle of the string.

    Parameters
    ----------
    text : str or None

    Returns
    -------
    bool
    """
    value = str(text or "")
    return bool(
        re.search(r"(?:salt|ssh|paramiko|docker|podman|local|ansible|chroot)://", value)
    )


def _has_dash_after_phonepe(text):
    """Return ``True`` when there is a ``-`` character after the last ``.phonepe`` token.

    This guards against stripping the parameter segment from hostnames that look
    like ``test_name-host.phonepe.tld-param`` vs plain
    ``test_name-host.phonepe.tld`` (no trailing parameter).

    Parameters
    ----------
    text : str or None

    Returns
    -------
    bool
        ``True`` if no ``.phonepe`` is found (safe default), or if a ``-``
        exists after the last ``.phonepe`` substring.
    """
    value = str(text or "")
    idx = value.rfind(".phonepe")
    if idx == -1:
        return True
    return "-" in value[idx + len(".phonepe") :]


def _display_name_from_raw(raw_name):
    """Derive a concise display name from a raw pytest item name.

    The raw name produced by testinfra parametrization often looks like
    ``test_foo-salt://host.example.com-param``.  This function normalises
    several cases:

    - **Bracketed params** (``test_foo[salt://host-param]``) →
      ``test_foo-param``
    - **Host-only** (``test_foo-salt://host``) → ``test_foo``
    - **Host + param** (``test_foo[salt://host-param]``) →
      ``test_foo-param``
    - **Plain name** (``test_foo``) → ``test_foo``

    Parameters
    ----------
    raw_name : str or None

    Returns
    -------
    str
        Cleaned display name.
    """
    text = str(raw_name or "").strip()
    if not text:
        return text

    had_brackets = False
    if "[" in text and text.endswith("]"):
        had_brackets = True
        base, bracket = text[:-1].split("[", 1)
        text = "%s-%s" % (base, bracket)

    if "-" not in text:
        return text

    base_name = text.split("-", 1)[0]

    # Non-parameterized host suffixes often end in *.phonepe.<domain> with no
    # trailing "-<param>" segment; keep only the base test name in that case.
    if ".phonepe" in text and not _has_dash_after_phonepe(text):
        return base_name

    # Host-only pattern (non-parameterized): test_name-salt://host... -> keep only base name.
    if _contains_backend_selector(text) and not had_brackets:
        return base_name

    # Requested behavior: keep first segment and last segment for host+param names.
    if _contains_backend_selector(text):
        last_segment = text.rsplit("-", 1)[-1]
        if not last_segment or "://" in last_segment or last_segment.endswith(">"):
            return base_name
        return "%s-%s" % (base_name, last_segment)

    return text


def _normalized_test_name(item, nodeid):
    """Return a human-readable test name for a pytest item or node ID.

    Prefers ``item.name`` when available (which already carries the parametrize
    bracket), otherwise falls back to the last ``::``-delimited segment of
    *nodeid*.  Either way the raw value is cleaned with
    :func:`_display_name_from_raw`.

    Parameters
    ----------
    item : _pytest.python.Function or None
        The pytest item object.  May be ``None`` when called from
        ``pytest_runtest_logreport`` after ``makereport``.
    nodeid : str
        Full pytest node ID used as fallback.

    Returns
    -------
    str
    """
    item_name = getattr(item, "name", None) if item is not None else None
    if item_name:
        return _display_name_from_raw(item_name)

    node_suffix = str(nodeid).rsplit("::", 1)[-1]
    return _display_name_from_raw(node_suffix)


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def _normalize_marker_value(value):
    """Coerce a marker argument to a stripped string or ``None``.

    Parameters
    ----------
    value : object
        Raw marker argument value.

    Returns
    -------
    str or None
        Stripped string, or ``None`` if *value* was ``None`` / empty.
    """
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _extract_item_markers(item):
    """Collect all pytest markers on *item* as a list of dicts.

    The ``parametrize`` marker is excluded because its value is already encoded
    in the node ID.  Duplicate ``(name, value)`` pairs are deduplicated.
    Marker values longer than 512 characters are truncated.

    Parameters
    ----------
    item : _pytest.python.Function
        The pytest item whose markers are to be extracted.

    Returns
    -------
    list[dict]
        Each dict has keys ``"name"`` (str) and ``"value"`` (str or None).

    Examples
    --------
    Given a test decorated with ``@pytest.mark.component("auth")``, this
    returns ``[{"name": "component", "value": "auth"}]``.
    """
    markers = []
    seen = set()
    excluded_marker_names = {"parametrize"}

    for marker in item.iter_markers():
        marker_name = str(getattr(marker, "name", "") or "").strip()
        if not marker_name:
            continue
        if marker_name in excluded_marker_names:
            continue

        raw_values = []
        for arg in getattr(marker, "args", ()):
            normalized = _normalize_marker_value(arg)
            if normalized is not None:
                raw_values.append(normalized)

        kwargs = getattr(marker, "kwargs", {}) or {}
        for key in sorted(kwargs):
            normalized = _normalize_marker_value(kwargs[key])
            if normalized is not None:
                raw_values.append("%s=%s" % (key, normalized))

        marker_values = raw_values or [None]
        for marker_value in marker_values:
            marker_value = marker_value[:512] if marker_value is not None else None
            marker_key = (marker_name, marker_value)
            if marker_key in seen:
                continue
            seen.add(marker_key)
            markers.append({"name": marker_name, "value": marker_value})

    return markers


# ---------------------------------------------------------------------------
# Backend target helpers
# ---------------------------------------------------------------------------

def _is_concrete_host_value(value):
    """Return ``True`` when *value* represents a single concrete hostname.

    Rejects glob patterns (``*``, ``?``), host lists (contains ``,`` or
    spaces), and bracket expressions used by Salt targeting.

    Parameters
    ----------
    value : str or None

    Returns
    -------
    bool
    """
    if not value:
        return False

    text = str(value).strip()
    if not text:
        return False

    # Reject globs and host lists; these are selectors, not a concrete host target.
    return not any(ch in text for ch in ("*", "?", "[", "]", ",", " "))


def _normalize_backend_target(value):
    """Extract the bare hostname from a testinfra backend URI or plain hostname.

    Examples
    --------
    - ``"salt://host.example.com"`` → ``"host.example.com"``
    - ``"ssh://user@host.example.com"`` → ``"host.example.com"``
    - ``"host.example.com"`` → ``"host.example.com"``
    - ``"salt://*"`` → ``None``  (glob, not a concrete host)
    - ``None`` → ``None``

    Parameters
    ----------
    value : str or None
        Raw backend URI or hostname string.

    Returns
    -------
    str or None
        Bare hostname, or ``None`` when the value cannot be resolved to a
        single concrete host.
    """
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    parsed = urlsplit(text)
    if parsed.scheme:
        scheme = parsed.scheme.lower()
        if scheme == "salt":
            host_value = parsed.netloc or parsed.path.lstrip("/")
        else:
            host_value = parsed.hostname or parsed.netloc or parsed.path.lstrip("/")
    else:
        host_value = text

    host_value = str(host_value).strip() if host_value else None
    if not _is_concrete_host_value(host_value):
        return None

    return host_value


def _extract_backend_host_from_nodeid(nodeid):
    """Parse the backend hostname out of a testinfra parametrized node ID.

    Testinfra embeds the backend target as the last bracket parameter, e.g.::

        test_x[param0-salt://adv-rmq101.example.com]

    This function searches for any known-scheme URI in *nodeid*, iterates
    matches in reverse order, and returns the first one that
    :func:`_normalize_backend_target` accepts as a concrete host.

    Parameters
    ----------
    nodeid : str or None

    Returns
    -------
    str or None
        Bare hostname extracted from the node ID, or ``None``.
    """
    if not nodeid:
        return None

    # Typical testinfra node IDs include backend target as the last parameterized id,
    # for example: test_x[param0-salt://adv-rmq101.example.com].
    # Restrict schemes to known backends to avoid false positives like
    # "common_systemd_services0-salt://...".
    matches = re.findall(
        r"((?:salt|ssh|paramiko|docker|podman|local|ansible|chroot)://[^\]\s]+)",
        str(nodeid),
    )
    if not matches:
        return None

    for candidate in reversed(matches):
        normalized = _normalize_backend_target(candidate)
        if normalized:
            return normalized

    return None


# ---------------------------------------------------------------------------
# Failure map path helper
# ---------------------------------------------------------------------------

def _default_failure_map_path():
    """Return the default path to ``failure_mapper/failure_map.yaml``.

    The path is resolved relative to the directory that contains this plugin
    package, so it works regardless of the current working directory.

    Returns
    -------
    str
        Absolute path to ``failure_mapper/failure_map.yaml`` inside this
        plugin directory.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(repo_root, "failure_mapper", "failure_map.yaml")


# ---------------------------------------------------------------------------
# pytest option registration
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    """Register ``--mariadb-*`` CLI options in the ``mariadb-reporting`` group.

    Options
    -------
    ``--mariadb-report``
        Master switch.  When absent, the reporter is a no-op.
    ``--mariadb-host``
        MariaDB server hostname (default: ``localhost``).
    ``--mariadb-port``
        MariaDB server port (default: ``3306``).
    ``--mariadb-user``
        Database username (default: ``testinfra_user``).
    ``--mariadb-password``
        Database password (default: ``password``).
    ``--mariadb-database``
        Database / schema name (default: ``testinfra_reports``).
    ``--mariadb-suite-version``
        Optional string (e.g. git SHA) stored in ``test_runs.suite_version``.
    ``--mariadb-init-schema``
        When set, run idempotent ``CREATE TABLE IF NOT EXISTS`` DDL before the
        session starts.  Convenient for CI where the schema may not exist yet.
    ``--mariadb-failure-map``
        Path to the YAML failure-map file used by :class:`MariaDBFailureTagger`.
        Defaults to :func:`_default_failure_map_path`.
    """
    group = parser.getgroup("mariadb-reporting")
    group.addoption(
        "--mariadb-report",
        action="store_true",
        default=False,
        help="Enable writing pytest results to MariaDB.",
    )
    group.addoption("--mariadb-host", action="store", default="localhost")
    group.addoption("--mariadb-port", action="store", type=int, default=3306)
    group.addoption("--mariadb-user", action="store", default="testinfra_user")
    group.addoption("--mariadb-password", action="store", default="password")
    group.addoption("--mariadb-database", action="store", default="testinfra_reports")
    group.addoption(
        "--mariadb-suite-version",
        action="store",
        default=None,
        help="Optional suite version or git SHA.",
    )
    group.addoption(
        "--mariadb-init-schema",
        action="store_true",
        default=False,
        help="Create/update required MariaDB schema before sending reports.",
    )
    group.addoption(
        "--mariadb-failure-map",
        action="store",
        default=_default_failure_map_path(),
        help="Path to YAML map used for tagging failed/error tests.",
    )


# ---------------------------------------------------------------------------
# MariaDBFailureTagger
# ---------------------------------------------------------------------------

class MariaDBFailureTagger:
    """Classify ``fail`` / ``error`` test results with a human-readable tag.

    On construction this class loads the YAML failure map referenced by the
    ``--mariadb-failure-map`` CLI option.  Each entry in the map describes a
    set of text patterns and the *defect name* to assign when any pattern
    matches the combined failure output of a test.

    The tagger is registered as a separate plugin (``"mariadb-failure-tagger"``)
    so that it can be reused or replaced independently of
    :class:`MariaDBReporter`.

    YAML schema (``failure_map.yaml``)
    -----------------------------------

    .. code-block:: yaml

        error_maps:
          - target_logs:
              - "Connection refused"
            match_type: exact      # "exact" (default) or "regex"
            defect:
              name: "network_error"

          - target_logs:
              - "AssertionError.*expected.*but got"
            match_type: regex
            defect:
              name: "assertion_mismatch"

    Parameters
    ----------
    config : _pytest.config.Config
        The pytest configuration object, used to read ``--mariadb-failure-map``.

    Attributes
    ----------
    enabled : bool
        ``True`` when at least one valid map entry was loaded.
    """

    def __init__(self, config):
        self.config = config
        self._disabled_reason = None
        self._error_maps = []
        self._map_path = config.getoption("--mariadb-failure-map")
        self._load_error_maps()

    @property
    def enabled(self):
        """``True`` when the failure tagger has at least one loaded map entry."""
        return bool(self._error_maps)

    def _disable(self, reason):
        """Emit a warning and mark the tagger as disabled.

        Subsequent calls are silent so the warning only appears once.

        Parameters
        ----------
        reason : str
            Human-readable explanation shown in the warning message.
        """
        if not self._disabled_reason:
            warnings.warn("MariaDB failure tagger disabled: %s" % reason)
        self._disabled_reason = reason
        self._error_maps = []

    def _normalize_match_type(self, value):
        """Normalise a ``match_type`` field value to lowercase.

        Parameters
        ----------
        value : str or None

        Returns
        -------
        str
            ``"exact"`` when *value* is ``None`` or empty, otherwise the
            stripped lowercase version of *value*.
        """
        if not value:
            return "exact"
        return str(value).strip().lower()

    def _load_error_maps(self):
        """Parse and compile the failure map YAML file.

        Reads the file at ``self._map_path``, validates each entry, compiles
        regex patterns where ``match_type == "regex"``, and populates
        ``self._error_maps``.  Any entry that is missing required fields or has
        invalid regex emits a :func:`warnings.warn` and is skipped rather than
        aborting the entire load.

        The tagger is disabled (via :meth:`_disable`) when:

        - PyYAML is not importable.
        - The map file does not exist.
        - The file cannot be parsed.
        - The ``error_maps`` list is absent or empty.
        - No valid entries remain after validation.
        """
        try:
            import yaml
        except Exception as exc:
            self._disable(
                "PyYAML is not available. Install with: pip install pyyaml (%s)" % exc
            )
            return

        map_path = self._map_path
        if not map_path or not os.path.exists(map_path):
            self._disable("failure map file not found at: %s" % map_path)
            return

        try:
            with open(map_path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception as exc:
            self._disable("failed loading failure map: %s" % exc)
            return

        raw_maps = data.get("error_maps") or []
        if not raw_maps:
            self._disable("error_maps section is empty in: %s" % map_path)
            return

        loaded_maps = []
        for idx, entry in enumerate(raw_maps, start=1):
            patterns = entry.get("target_logs") or entry.get("patterns") or []
            patterns = [str(pattern) for pattern in patterns if pattern is not None]
            if not patterns:
                warnings.warn(
                    "MariaDB failure tagger skipped map #%d due to empty target_logs" % idx
                )
                continue

            match_type = self._normalize_match_type(entry.get("match_type", "exact"))
            if match_type not in ("exact", "regex"):
                warnings.warn(
                    "MariaDB failure tagger skipped map #%d due to unsupported match_type '%s'"
                    % (idx, match_type)
                )
                continue

            defect = entry.get("defect") or {}
            defect_name = defect.get("name")
            if not defect_name:
                warnings.warn(
                    "MariaDB failure tagger skipped map #%d due to missing defect.name" % idx
                )
                continue

            try:
                compiled = [
                    re.compile(pattern) if match_type == "regex" else None
                    for pattern in patterns
                ]
            except re.error as exc:
                warnings.warn(
                    "MariaDB failure tagger skipped map #%d due to invalid regex: %s"
                    % (idx, exc)
                )
                continue

            loaded_maps.append(
                {
                    "patterns": patterns,
                    "match_type": match_type,
                    "compiled": compiled,
                    "defect_name": str(defect_name),
                }
            )

        if not loaded_maps:
            self._disable("no valid maps available from: %s" % map_path)
            return

        self._error_maps = loaded_maps

    def tag_failure(self, status, text_parts):
        """Return the first matching defect name for a failed/errored test.

        Concatenates all non-empty items in *text_parts* with double newlines
        and checks each loaded map entry in order.  The first match wins.

        Parameters
        ----------
        status : str
            Test status string (e.g. ``"fail"``, ``"error"``).  Statuses other
            than ``"fail"`` and ``"error"`` always return ``None``.
        text_parts : list
            Sequence of strings (or ``None``) to search.  Typically:
            ``[error_message, full_trace, captured_log, captured_stdout, captured_stderr]``.

        Returns
        -------
        str or None
            ``defect.name`` from the first matching map entry, or ``None`` when
            the tagger is disabled, the status is not a failure, or no pattern
            matches.
        """
        if status not in ("fail", "error"):
            return None
        if not self.enabled:
            return None

        full_text = "\n\n".join(str(part) for part in text_parts if part)
        if not full_text:
            return None

        for entry in self._error_maps:
            match_type = entry["match_type"]
            for pattern, compiled in zip(entry["patterns"], entry["compiled"]):
                if match_type == "regex":
                    if compiled and compiled.search(full_text):
                        return entry["defect_name"]
                else:
                    if pattern in full_text:
                        return entry["defect_name"]

        return None


# ---------------------------------------------------------------------------
# MariaDBReporter
# ---------------------------------------------------------------------------

class MariaDBReporter:
    """pytest plugin that persists test results to a MariaDB database.

    Lifecycle
    ---------

    ``pytest_sessionstart``
        Opens the database connection (lazily via :meth:`_ensure_connection`),
        optionally runs schema migrations (``--mariadb-init-schema``), and
        inserts a ``test_runs`` row.

    ``pytest_runtest_makereport`` (hook wrapper)
        Captures test metadata (host name, suite, class, markers) once per
        node ID the first time the hook fires.

    ``pytest_runtest_logreport``
        Buffers each phase report (``setup``, ``call``, ``teardown``).  On
        ``teardown`` merges all phases and appends a completed result dict to
        ``self._result_rows``.

    ``pytest_sessionfinish``
        Flushes ``self._result_rows`` to the database in a single transaction:
        upserts ``hosts`` and ``tests`` rows, inserts/updates ``test_results``,
        replaces ``test_result_markers``, and updates the ``test_runs`` summary
        counters.

    ``pytest_terminal_summary``
        Appends a short status block to the pytest terminal output summarising
        whether reporting was active, the ``run_id``, and failure-tagger state.

    Parameters
    ----------
    config : _pytest.config.Config

    Attributes
    ----------
    enabled : bool
        Starts as ``True`` when ``--mariadb-report`` was passed.  Set to
        ``False`` (with a warning) on the first unrecoverable error.
    """

    def __init__(self, config):
        self.config = config
        self.enabled = bool(config.getoption("--mariadb-report"))
        self._failure_tagger = config.pluginmanager.get_plugin("mariadb-failure-tagger")
        self._connection = None
        self._disabled_reason = None
        self._run_id = str(uuid.uuid4())
        self._run_started_at = _istnow_naive()
        #: ``{nodeid: {"setup": report, "call": report, "teardown": report}}``
        self._reports_by_nodeid = {}
        #: ``{nodeid: {test_uid, canonical_nodeid, test_name, …}}``
        self._metadata_by_nodeid = {}
        #: Accumulated result dicts flushed in ``pytest_sessionfinish``.
        self._result_rows = []

    def _disable(self, reason):
        """Disable the reporter and emit a one-time warning.

        Parameters
        ----------
        reason : str
        """
        if self.enabled:
            warnings.warn("MariaDB pytest reporter disabled: %s" % reason)
        self.enabled = False
        self._disabled_reason = reason

    def _resolve_host_name(self, item):
        """Determine the target host name for a pytest item.

        Resolution order:

        1. ``item.funcargs["host"].backend.get_hostname()`` — the testinfra
           ``host`` fixture if present.
        2. :func:`_extract_backend_host_from_nodeid` — parsed from the node ID.
        3. ``--hosts`` CLI option, passed through
           :func:`_normalize_backend_target`.
        4. :func:`socket.gethostname` — runner machine as last resort.

        Parameters
        ----------
        item : _pytest.python.Function

        Returns
        -------
        str
            Resolved host name, never ``None``.
        """
        # Prefer testinfra host fixture when available.
        host_obj = item.funcargs.get("host") if hasattr(item, "funcargs") else None
        if host_obj is not None:
            backend = getattr(host_obj, "backend", None)
            getter = getattr(backend, "get_hostname", None)
            if callable(getter):
                try:
                    host_name = getter()
                    if host_name:
                        return str(host_name)
                except Exception:
                    pass

        parsed_host = _extract_backend_host_from_nodeid(getattr(item, "nodeid", None))
        if parsed_host:
            return parsed_host

        hosts_opt = item.config.getoption("hosts", default=None)
        normalized_hosts_opt = _normalize_backend_target(hosts_opt)
        if normalized_hosts_opt:
            return normalized_hosts_opt
        return socket.gethostname()

    def _extract_sections(self, report):
        """Split a pytest report's sections into stdout / stderr / log buckets.

        Iterates ``report.sections`` (list of ``(name, content)`` tuples) and
        categorises each by checking whether the section name contains
        ``"stdout"``, ``"stderr"``, or ``"log"``.  Unrecognised section names
        are placed in ``captured_log`` with a ``[section_name]`` prefix.

        Parameters
        ----------
        report : _pytest.reports.BaseReport

        Returns
        -------
        dict
            Keys: ``"captured_stdout"``, ``"captured_stderr"``,
            ``"captured_log"``.  Values are joined strings or ``None``.
        """
        section_map = {
            "captured_stdout": [],
            "captured_stderr": [],
            "captured_log": [],
        }

        for sec_name, sec_content in getattr(report, "sections", []):
            if not sec_content:
                continue
            lower = sec_name.lower()
            if "stdout" in lower:
                section_map["captured_stdout"].append(sec_content)
            elif "stderr" in lower:
                section_map["captured_stderr"].append(sec_content)
            elif "log" in lower:
                section_map["captured_log"].append(sec_content)
            else:
                section_map["captured_log"].append("[%s]\\n%s" % (sec_name, sec_content))

        return {
            key: "\\n\\n".join(values) if values else None
            for key, values in section_map.items()
        }

    def _combined_longrepr(self, reports):
        """Concatenate ``longreprtext`` from all phase reports.

        Each chunk is prefixed with ``[when]`` (``setup``, ``call``, or
        ``teardown``) so the reader can tell which phase failed.

        Parameters
        ----------
        reports : list[_pytest.reports.BaseReport]

        Returns
        -------
        str or None
            Combined text, or ``None`` when no report has a non-empty
            ``longreprtext``.
        """
        longrepr_chunks = []
        for report in reports:
            text = getattr(report, "longreprtext", None)
            if text:
                longrepr_chunks.append("[%s]\\n%s" % (report.when, text))
        if not longrepr_chunks:
            return None
        return "\\n\\n".join(longrepr_chunks)

    def _determine_status(self, setup_report, call_report, teardown_report):
        """Derive a single canonical status string from all three phase reports.

        Priority rules:

        - ``xfail`` — call was skipped and has ``wasxfail`` attribute.
        - ``xpass`` — call passed/failed and has ``wasxfail`` attribute.
        - ``pass`` — call passed; ``error`` if teardown failed.
        - ``fail`` — call failed.
        - ``skipped`` — call or setup was skipped.
        - ``error`` — setup or teardown failed, or no call report.

        Parameters
        ----------
        setup_report : _pytest.reports.BaseReport or None
        call_report : _pytest.reports.BaseReport or None
        teardown_report : _pytest.reports.BaseReport or None

        Returns
        -------
        str
            One of: ``"pass"``, ``"fail"``, ``"skipped"``, ``"error"``,
            ``"xfail"``, ``"xpass"``.
        """
        if call_report is not None:
            if hasattr(call_report, "wasxfail"):
                if call_report.skipped:
                    return "xfail"
                if call_report.passed or call_report.failed:
                    return "xpass"
            if call_report.passed:
                if teardown_report is not None and teardown_report.failed:
                    return "error"
                return "pass"
            if call_report.failed:
                return "fail"
            if call_report.skipped:
                return "skipped"

        if setup_report is not None:
            if setup_report.skipped:
                return "skipped"
            if setup_report.failed:
                return "error"

        if teardown_report is not None and teardown_report.failed:
            return "error"

        return "error"

    def _ensure_connection(self):
        """Open the PyMySQL connection if not already open.

        Imports ``pymysql`` lazily so that the plugin can be loaded without the
        dependency installed (it will simply disable itself with a warning when
        ``--mariadb-report`` is actually used).

        Sets ``self._connection`` on success or calls :meth:`_disable` on any
        exception.
        """
        if not self.enabled:
            return

        if self._connection is not None:
            return

        try:
            import pymysql
        except Exception as exc:
            self._disable(
                "PyMySQL is not available. Install with: pip install PyMySQL (%s)" % exc
            )
            return

        try:
            self._connection = pymysql.connect(
                host=self.config.getoption("--mariadb-host"),
                port=self.config.getoption("--mariadb-port"),
                user=self.config.getoption("--mariadb-user"),
                password=self.config.getoption("--mariadb-password"),
                database=self.config.getoption("--mariadb-database"),
                charset="utf8mb4",
                autocommit=False,
            )
        except Exception as exc:
            self._disable("Failed connecting to MariaDB: %s" % exc)

    def _init_schema(self, cursor):
        """Run idempotent DDL to create or update the database schema.

        Executes two groups of SQL statements against *cursor*:

        **Base tables** (``CREATE TABLE IF NOT EXISTS``)
            ``hosts``, ``tests``, ``test_runs``, ``test_results``,
            ``test_result_markers``.  Safe to run against an empty or fully
            populated database.

        **Migrations** (``ALTER TABLE … ADD COLUMN IF NOT EXISTS``, etc.)
            Schema evolution statements for installations that were created
            before the ``test_uid`` column was introduced.  They are also
            idempotent (``IF NOT EXISTS`` / ``IF EXISTS`` guards).

        Parameters
        ----------
        cursor : pymysql.cursors.Cursor
            An open cursor on the target database.  The caller is responsible
            for committing or rolling back.
        """
        statements = [
            """
            CREATE TABLE IF NOT EXISTS hosts (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              host_name VARCHAR(255) NOT NULL,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              first_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              last_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              PRIMARY KEY (id),
              UNIQUE KEY uq_hosts_host_name (host_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS tests (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                            test_uid CHAR(40) NOT NULL,
              canonical_nodeid VARCHAR(255) NOT NULL,
              test_name VARCHAR(255) NOT NULL,
              test_suite VARCHAR(1024) NULL,
              test_class VARCHAR(255) NULL,
              PRIMARY KEY (id),
                            UNIQUE KEY uq_tests_test_uid (test_uid),
                            KEY idx_tests_canonical_nodeid (canonical_nodeid)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS test_runs (
              run_id CHAR(36) NOT NULL,
              trigger_source VARCHAR(64) NOT NULL DEFAULT 'local',
              suite_version VARCHAR(255) NULL,
              started_at DATETIME(6) NOT NULL,
              finished_at DATETIME(6) NULL,
              total_tests INT NOT NULL DEFAULT 0,
              passed_count INT NOT NULL DEFAULT 0,
              failed_count INT NOT NULL DEFAULT 0,
              skipped_count INT NOT NULL DEFAULT 0,
              errored_count INT NOT NULL DEFAULT 0,
              PRIMARY KEY (run_id),
              KEY idx_test_runs_started (started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS test_results (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              run_id CHAR(36) NOT NULL,
              host_id BIGINT UNSIGNED NOT NULL,
              test_id BIGINT UNSIGNED NOT NULL,
              status ENUM('pass', 'fail', 'skipped', 'error', 'xfail', 'xpass') NOT NULL,
                            failure_tag VARCHAR(255) NULL,
              duration_ms INT UNSIGNED NOT NULL DEFAULT 0,
              started_at DATETIME(6) NULL,
              finished_at DATETIME(6) NULL,
              error_type VARCHAR(128) NULL,
              error_message TEXT NULL,
              full_trace LONGTEXT NULL,
              captured_log LONGTEXT NULL,
              captured_stdout LONGTEXT NULL,
              captured_stderr LONGTEXT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uq_test_results_run_host_test (run_id, host_id, test_id),
              KEY idx_test_results_run_host (run_id, host_id),
              KEY idx_test_results_host_finished (host_id, finished_at),
              KEY idx_test_results_host_status_finished (host_id, status, finished_at),
              KEY idx_test_results_test_finished (test_id, finished_at),
              KEY idx_test_results_status_finished (status, finished_at),
                            KEY idx_test_results_failure_tag_finished (failure_tag, finished_at),
              CONSTRAINT fk_test_results_run FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE,
              CONSTRAINT fk_test_results_host FOREIGN KEY (host_id) REFERENCES hosts(id),
              CONSTRAINT fk_test_results_test FOREIGN KEY (test_id) REFERENCES tests(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
                        """
                        CREATE TABLE IF NOT EXISTS test_result_markers (
                            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                            test_result_id BIGINT UNSIGNED NOT NULL,
                            marker_name VARCHAR(128) NOT NULL,
                            marker_value VARCHAR(512) NULL,
                            PRIMARY KEY (id),
                            UNIQUE KEY uq_result_marker_name_value (test_result_id, marker_name, marker_value),
                            KEY idx_result_markers_name_value (marker_name, marker_value),
                            KEY idx_result_markers_name (marker_name),
                            CONSTRAINT fk_result_markers_result FOREIGN KEY (test_result_id) REFERENCES test_results(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                        """,
        ]

        for statement in statements:
            cursor.execute(statement)

                # Schema evolution for existing installations.
        migrations = [
            """
            ALTER TABLE tests
            ADD COLUMN IF NOT EXISTS test_uid CHAR(40) NULL AFTER id
            """,
            """
            ALTER TABLE tests
                        DROP INDEX IF EXISTS uq_tests_canonical_nodeid
            """,
            """
                        ALTER TABLE tests
                        DROP INDEX IF EXISTS uq_tests_test_uid
            """,
            """
                        CREATE TEMPORARY TABLE IF NOT EXISTS tests_dedupe_map (
                            drop_id BIGINT UNSIGNED NOT NULL,
                            keep_id BIGINT UNSIGNED NOT NULL,
                            PRIMARY KEY (drop_id)
                        )
            """,
            """
                        TRUNCATE TABLE tests_dedupe_map
            """,
            """
                        INSERT INTO tests_dedupe_map (drop_id, keep_id)
                        SELECT t.id AS drop_id, k.keep_id
                        FROM tests t
                        JOIN (
                            SELECT
                                SHA1(
                                    CONCAT_WS(
                                        '|',
                                        COALESCE(canonical_nodeid, ''),
                                        COALESCE(test_name, ''),
                                        COALESCE(test_suite, ''),
                                        COALESCE(test_class, '')
                                    )
                                ) AS dedupe_key,
                                MIN(id) AS keep_id
                            FROM tests
                            GROUP BY dedupe_key
                        ) k
                            ON SHA1(
                                     CONCAT_WS(
                                         '|',
                                         COALESCE(t.canonical_nodeid, ''),
                                         COALESCE(t.test_name, ''),
                                         COALESCE(t.test_suite, ''),
                                         COALESCE(t.test_class, '')
                                     )
                                 ) = k.dedupe_key
                        WHERE t.id <> k.keep_id
            """,
            """
                        UPDATE test_results tr
                        JOIN tests_dedupe_map m ON tr.test_id = m.drop_id
                        SET tr.test_id = m.keep_id
            """,
            """
                        DELETE t
                        FROM tests t
                        JOIN tests_dedupe_map m ON t.id = m.drop_id
            """,
            """
                        DROP TEMPORARY TABLE IF EXISTS tests_dedupe_map
                        """,
                        """
                        UPDATE tests
                        SET test_uid = SHA1(
                            CONCAT_WS(
                                '|',
                                COALESCE(canonical_nodeid, ''),
                                COALESCE(test_name, ''),
                                COALESCE(test_suite, ''),
                                COALESCE(test_class, '')
                            )
                        )
                        """,
                        """
                        ALTER TABLE tests
                        MODIFY COLUMN test_uid CHAR(40) NOT NULL
                        """,
                        """
                        ALTER TABLE tests
                        DROP COLUMN IF EXISTS nodeid
                        """,
                        """
                        ALTER TABLE tests
                        ADD UNIQUE KEY IF NOT EXISTS uq_tests_test_uid (test_uid)
                        """,
                        """
                        ALTER TABLE tests
                        ADD KEY IF NOT EXISTS idx_tests_canonical_nodeid (canonical_nodeid)
            """,
        ]

        for statement in migrations:
            cursor.execute(statement)

    def pytest_sessionstart(self, session):
        """Open the DB connection and insert the ``test_runs`` row.

        Called by pytest at the start of the test session, before any tests are
        collected or run.

        If ``--mariadb-init-schema`` is set, :meth:`_init_schema` is called
        first so tables are created/migrated automatically.

        Parameters
        ----------
        session : _pytest.main.Session
        """
        self._ensure_connection()
        if not self.enabled:
            return

        cursor = self._connection.cursor()
        try:
            if self.config.getoption("--mariadb-init-schema"):
                self._init_schema(cursor)

            cursor.execute(
                """
                INSERT INTO test_runs (
                  run_id,
                  trigger_source,
                  suite_version,
                                    started_at
                                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    self._run_id,
                    socket.getfqdn(),
                    self.config.getoption("--mariadb-suite-version"),
                    self._run_started_at,
                ),
            )
            self._connection.commit()
        except Exception as exc:
            self._connection.rollback()
            self._disable("Failed creating test_runs row: %s" % exc)
        finally:
            cursor.close()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        """Capture test metadata once per node ID before any phase report is stored.

        This is a *hook wrapper* so it runs around the default
        ``makereport`` implementation.  On the first invocation for a given
        ``nodeid`` it resolves and caches:

        - ``test_uid`` — stable SHA-1 identity fingerprint.
        - ``canonical_nodeid`` — node ID without bracket suffix.
        - ``test_name`` — cleaned display name.
        - ``test_suite`` — relative module path.
        - ``test_class`` — class name or ``None``.
        - ``host_name`` — resolved via :meth:`_resolve_host_name`.
        - ``markers`` — list of ``{name, value}`` dicts.

        Parameters
        ----------
        item : _pytest.python.Function
        call : _pytest.runner.CallInfo
        """
        outcome = yield
        report = outcome.get_result()

        if not self.enabled:
            return

        nodeid = report.nodeid
        if nodeid not in self._metadata_by_nodeid:
            canonical_nodeid = _canonical_nodeid(nodeid)
            test_name = _normalized_test_name(item, nodeid)
            test_suite = item.location[0] if hasattr(item, "location") else None
            test_class = item.cls.__name__ if getattr(item, "cls", None) else None
            self._metadata_by_nodeid[nodeid] = {
                "test_uid": _test_uid(canonical_nodeid, test_name, test_suite, test_class),
                "canonical_nodeid": canonical_nodeid,
                "test_name": test_name,
                "test_suite": test_suite,
                "test_class": test_class,
                "host_name": self._resolve_host_name(item),
                "markers": _extract_item_markers(item),
            }

    def pytest_runtest_logreport(self, report):
        """Buffer phase reports and flush a completed result on ``teardown``.

        Stores each phase report (``setup``, ``call``, ``teardown``) keyed by
        ``report.nodeid``.  When ``teardown`` arrives the three phases are
        merged to produce a single result dict that is appended to
        ``self._result_rows`` for bulk insertion in
        :meth:`pytest_sessionfinish`.

        The merged dict includes:

        - Status derived by :meth:`_determine_status`.
        - Timestamps (earliest ``start``, latest ``stop`` across all phases).
        - Total ``duration_ms`` (sum of all phase durations).
        - Merged ``captured_stdout``, ``captured_stderr``, ``captured_log``.
        - Combined ``full_trace`` from :meth:`_combined_longrepr`.
        - ``error_type`` / ``error_message`` for ``fail`` / ``error`` statuses.
        - ``failure_tag`` from :class:`MariaDBFailureTagger` when available.

        Parameters
        ----------
        report : _pytest.reports.BaseReport
        """
        if not self.enabled:
            return

        nodeid = report.nodeid
        phases = self._reports_by_nodeid.setdefault(nodeid, {})
        phases[report.when] = report

        if report.when != "teardown":
            return

        setup_report = phases.get("setup")
        call_report = phases.get("call")
        teardown_report = phases.get("teardown")

        phase_reports = [r for r in (setup_report, call_report, teardown_report) if r is not None]
        status = self._determine_status(setup_report, call_report, teardown_report)

        stops = [getattr(r, "stop", None) for r in phase_reports]
        starts = [getattr(r, "start", None) for r in phase_reports]

        started_at = (
            _epoch_to_ist_naive(min(s for s in starts if s is not None))
            if any(s is not None for s in starts)
            else None
        )
        finished_at = (
            _epoch_to_ist_naive(max(s for s in stops if s is not None))
            if any(s is not None for s in stops)
            else started_at
        )

        duration_ms = int(round(sum(float(getattr(r, "duration", 0.0) or 0.0) for r in phase_reports) * 1000))

        merged_sections = {
            "captured_stdout": [],
            "captured_stderr": [],
            "captured_log": [],
        }
        for phase_report in phase_reports:
            extracted = self._extract_sections(phase_report)
            for key in merged_sections:
                if extracted[key]:
                    merged_sections[key].append(extracted[key])

        metadata = self._metadata_by_nodeid.get(nodeid, {})

        error_type = None
        error_message = None
        full_trace_text = self._combined_longrepr(phase_reports)
        captured_log_text = (
            "\\n\\n".join(merged_sections["captured_log"])
            if merged_sections["captured_log"]
            else None
        )
        captured_stdout_text = (
            "\\n\\n".join(merged_sections["captured_stdout"])
            if merged_sections["captured_stdout"]
            else None
        )
        captured_stderr_text = (
            "\\n\\n".join(merged_sections["captured_stderr"])
            if merged_sections["captured_stderr"]
            else None
        )

        if status in ("fail", "error"):
            failure_report = call_report or setup_report or teardown_report
            if failure_report is not None:
                if getattr(failure_report, "longreprtext", None):
                    error_message = failure_report.longreprtext
                outcome = getattr(failure_report, "outcome", None)
                if outcome == "failed":
                    error_type = "assertion" if call_report is failure_report else "setup_or_teardown"
                elif outcome == "skipped":
                    error_type = "skipped"

        failure_tag = None
        if self._failure_tagger is not None:
            failure_tag = self._failure_tagger.tag_failure(
                status,
                [
                    error_message,
                    full_trace_text,
                    captured_log_text,
                    captured_stdout_text,
                    captured_stderr_text,
                ],
            )

        self._result_rows.append(
            {
                "nodeid": nodeid,
                "test_uid": metadata.get(
                    "test_uid",
                    _test_uid(
                        metadata.get("canonical_nodeid", _canonical_nodeid(nodeid)),
                        metadata.get("test_name", _normalized_test_name(None, nodeid)),
                        metadata.get("test_suite"),
                        metadata.get("test_class"),
                    ),
                ),
                "canonical_nodeid": metadata.get("canonical_nodeid", _canonical_nodeid(nodeid)),
                "test_name": metadata.get(
                    "test_name", _normalized_test_name(None, nodeid)
                ),
                "test_suite": metadata.get("test_suite"),
                "test_class": metadata.get("test_class"),
                "host_name": metadata.get("host_name") or socket.gethostname(),
                "status": status,
                "failure_tag": failure_tag,
                "duration_ms": duration_ms,
                "started_at": started_at,
                "finished_at": finished_at,
                "error_type": error_type,
                "error_message": error_message,
                "full_trace": full_trace_text,
                "captured_log": captured_log_text,
                "captured_stdout": captured_stdout_text,
                "captured_stderr": captured_stderr_text,
                "markers": metadata.get("markers", []),
            }
        )

    # ------------------------------------------------------------------
    # Database write helpers
    # ------------------------------------------------------------------

    def _upsert_host(self, cursor, host_name):
        """Insert or update a ``hosts`` row and return its primary key.

        Uses ``ON DUPLICATE KEY UPDATE`` to handle the case where the host
        already exists, updating ``last_seen`` and returning the existing
        ``id`` via ``LAST_INSERT_ID``.

        Parameters
        ----------
        cursor : pymysql.cursors.Cursor
        host_name : str

        Returns
        -------
        int
            ``hosts.id`` for *host_name*.
        """
        now = _istnow_naive()
        cursor.execute(
            """
            INSERT INTO hosts (host_name, first_seen, last_seen)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), last_seen = VALUES(last_seen)
            """,
            (host_name, now, now),
        )
        return int(cursor.lastrowid)

    def _upsert_test(self, cursor, row):
        """Insert or update a ``tests`` row and return its primary key.

        The unique key is ``test_uid`` (SHA-1 fingerprint).  On conflict the
        mutable fields (``canonical_nodeid``, ``test_name``, ``test_suite``,
        ``test_class``) are refreshed in case the test was renamed or moved.

        Parameters
        ----------
        cursor : pymysql.cursors.Cursor
        row : dict
            A result dict containing ``test_uid``, ``canonical_nodeid``,
            ``test_name``, ``test_suite``, and ``test_class``.

        Returns
        -------
        int
            ``tests.id`` for the upserted row.
        """
        cursor.execute(
            """
                        INSERT INTO tests (test_uid, canonical_nodeid, test_name, test_suite, test_class)
                        VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              id = LAST_INSERT_ID(id),
                            canonical_nodeid = VALUES(canonical_nodeid),
              test_name = VALUES(test_name),
              test_suite = VALUES(test_suite),
              test_class = VALUES(test_class)
            """,
            (
                                row["test_uid"],
                row["canonical_nodeid"],
                row["test_name"],
                row["test_suite"],
                row["test_class"],
            ),
        )
        return int(cursor.lastrowid)

    def _insert_result(self, cursor, host_id, test_id, row):
        """Insert or update a ``test_results`` row and return its primary key.

        The unique key is ``(run_id, host_id, test_id)``.  On conflict all
        columns are refreshed, which handles the case where a test is re-run
        within the same session (rare but possible with some plugins).

        Parameters
        ----------
        cursor : pymysql.cursors.Cursor
        host_id : int
            FK to ``hosts.id``.
        test_id : int
            FK to ``tests.id``.
        row : dict
            Completed result dict from ``self._result_rows``.

        Returns
        -------
        int
            ``test_results.id`` for the upserted row.
        """
        cursor.execute(
            """
            INSERT INTO test_results (
              run_id,
              host_id,
              test_id,
              status,
                            failure_tag,
              duration_ms,
              started_at,
              finished_at,
              error_type,
              error_message,
              full_trace,
              captured_log,
              captured_stdout,
              captured_stderr
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                            id = LAST_INSERT_ID(id),
              status = VALUES(status),
                            failure_tag = VALUES(failure_tag),
              duration_ms = VALUES(duration_ms),
                            started_at = VALUES(started_at),
              finished_at = VALUES(finished_at),
              error_type = VALUES(error_type),
              error_message = VALUES(error_message),
              full_trace = VALUES(full_trace),
              captured_log = VALUES(captured_log),
              captured_stdout = VALUES(captured_stdout),
              captured_stderr = VALUES(captured_stderr)
            """,
            (
                self._run_id,
                host_id,
                test_id,
                row["status"],
                row["failure_tag"],
                row["duration_ms"],
                row["started_at"],
                row["finished_at"],
                row["error_type"],
                row["error_message"],
                row["full_trace"],
                row["captured_log"],
                row["captured_stdout"],
                row["captured_stderr"],
            ),
        )
        return int(cursor.lastrowid)

    def _replace_result_markers(self, cursor, test_result_id, markers):
        """Replace all marker rows for a given ``test_result_id``.

        Deletes existing rows for *test_result_id* then bulk-inserts the new
        set.  This is simpler than diffing and is safe because the whole session
        is flushed in one transaction.

        Parameters
        ----------
        cursor : pymysql.cursors.Cursor
        test_result_id : int
            FK to ``test_results.id``.
        markers : list[dict]
            Each dict must have keys ``"name"`` and ``"value"``.  Entries with
            a ``None`` name are silently skipped.  Names are truncated to 128
            characters, values to 512 characters.
        """
        cursor.execute(
            """
            DELETE FROM test_result_markers
            WHERE test_result_id = %s
            """,
            (test_result_id,),
        )

        if not markers:
            return

        marker_rows = []
        for marker in markers:
            marker_name = _normalize_marker_value(marker.get("name"))
            marker_value = _normalize_marker_value(marker.get("value"))
            if marker_name is None:
                continue
            marker_rows.append(
                (
                    test_result_id,
                    marker_name[:128],
                    marker_value[:512] if marker_value else None,
                )
            )

        if not marker_rows:
            return

        cursor.executemany(
            """
            INSERT INTO test_result_markers (test_result_id, marker_name, marker_value)
            VALUES (%s, %s, %s)
            """,
            marker_rows,
        )

    def pytest_sessionfinish(self, session, exitstatus):
        """Flush all buffered results to the database and close the connection.

        Iterates ``self._result_rows`` and for each row:

        1. Upserts the ``hosts`` row (cached in memory to avoid redundant
           queries).
        2. Upserts the ``tests`` row (cached by ``test_uid``).
        3. Inserts/updates the ``test_results`` row.
        4. Replaces ``test_result_markers`` rows.

        After all rows are processed, updates the ``test_runs`` summary counters
        (``total_tests``, ``passed_count``, etc.) and commits.  On any exception
        the transaction is rolled back and a warning is emitted.

        Parameters
        ----------
        session : _pytest.main.Session
        exitstatus : int
            pytest exit code (not used directly, but required by the hook
            signature).
        """
        if not self.enabled:
            return

        if self._connection is None:
            return

        cursor = self._connection.cursor()
        host_cache = {}
        test_cache = {}

        try:
            for row in self._result_rows:
                host_name = row["host_name"]
                host_id = host_cache.get(host_name)
                if host_id is None:
                    host_id = self._upsert_host(cursor, host_name)
                    host_cache[host_name] = host_id

                test_uid = row["test_uid"]
                test_id = test_cache.get(test_uid)
                if test_id is None:
                    test_id = self._upsert_test(cursor, row)
                    test_cache[test_uid] = test_id

                test_result_id = self._insert_result(cursor, host_id, test_id, row)
                self._replace_result_markers(cursor, test_result_id, row.get("markers", []))

            total_tests = len(self._result_rows)
            passed_count = sum(1 for row in self._result_rows if row["status"] == "pass")
            failed_count = sum(1 for row in self._result_rows if row["status"] == "fail")
            skipped_count = sum(1 for row in self._result_rows if row["status"] == "skipped")
            errored_count = sum(1 for row in self._result_rows if row["status"] == "error")

            cursor.execute(
                """
                UPDATE test_runs
                SET
                  finished_at = %s,
                  total_tests = %s,
                  passed_count = %s,
                  failed_count = %s,
                  skipped_count = %s,
                  errored_count = %s
                WHERE run_id = %s
                """,
                (
                    _istnow_naive(),
                    total_tests,
                    passed_count,
                    failed_count,
                    skipped_count,
                    errored_count,
                    self._run_id,
                ),
            )

            self._connection.commit()
        except Exception as exc:
            self._connection.rollback()
            warnings.warn("MariaDB pytest reporter failed writing data: %s" % exc)
        finally:
            cursor.close()
            self._connection.close()
            self._connection = None

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        """Append a MariaDB reporter status block to the terminal output.

        Only runs when ``--mariadb-report`` was passed.  Prints:

        - Whether the reporter ended up enabled or disabled.
        - The ``run_id`` UUID (useful for cross-referencing Grafana).
        - The number of results buffered.
        - Failure tagger status and map path (when available).
        - The disabled reason if the reporter was disabled mid-run.

        Parameters
        ----------
        terminalreporter : _pytest.terminal.TerminalReporter
        exitstatus : int
        config : _pytest.config.Config
        """
        if not config.getoption("--mariadb-report"):
            return

        terminalreporter.write_sep("-", "MariaDB reporter")
        terminalreporter.write_line("enabled: %s" % ("yes" if self.enabled else "no"))
        terminalreporter.write_line("run_id: %s" % self._run_id)
        terminalreporter.write_line("results_buffered: %d" % len(self._result_rows))
        if self._failure_tagger is not None:
            terminalreporter.write_line(
                "failure_tagger_enabled: %s"
                % ("yes" if self._failure_tagger.enabled else "no")
            )
            terminalreporter.write_line(
                "failure_map_path: %s" % self.config.getoption("--mariadb-failure-map")
            )
            if self._failure_tagger._disabled_reason:
                terminalreporter.write_line(
                    "failure_tagger_disabled_reason: %s"
                    % self._failure_tagger._disabled_reason
                )
        if self._disabled_reason:
            terminalreporter.write_line("disabled_reason: %s" % self._disabled_reason)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register :class:`MariaDBFailureTagger` and :class:`MariaDBReporter` plugins.

    Called by pytest before option parsing and test collection.  Both plugins
    are registered only once (guarded by ``has_plugin`` checks) to support
    environments where ``conftest.py`` imports this module explicitly in
    addition to it being loaded as a plugin.

    Parameters
    ----------
    config : _pytest.config.Config
    """
    if not config.pluginmanager.has_plugin("mariadb-failure-tagger"):
        config.pluginmanager.register(MariaDBFailureTagger(config), "mariadb-failure-tagger")

    if config.pluginmanager.has_plugin("mariadb-reporter"):
        return
    config.pluginmanager.register(MariaDBReporter(config), "mariadb-reporter")
