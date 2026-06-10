"""Core pytest plugin with backend-agnostic reporting lifecycle.

This module contains the pytest hooks and testinfra-specific parsing logic.
Storage concerns are delegated to a backend strategy implementing
:class:`AbstractStorageBackend`.

The testinfra host and node-id parsing behavior is intentionally preserved.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import socket
import uuid
import warnings
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import pytest

from .backend import AbstractStorageBackend
from .backends.mariadb import MariaDBBackend
from .backends.postgres import PostgresBackend
from .models import MarkerDef, TestResultRecord, TestRunSummary


#: Fixed offset timezone for IST used when persisting naive datetimes.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _istnow_naive() -> dt.datetime:
    """Return current wall-clock time in IST as naive datetime.

    :return: Current IST datetime with ``tzinfo=None``.
    """

    return dt.datetime.now(IST).replace(tzinfo=None)


def _epoch_to_ist_naive(value):
    """Convert epoch seconds to naive IST datetime.

    :param value: POSIX epoch timestamp.
    :return: Naive IST datetime, or ``None`` if input is ``None``.
    """

    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).astimezone(IST).replace(
        tzinfo=None
    )


def _canonical_nodeid(nodeid):
    """Strip parametrization suffix from a pytest node id.

    :param nodeid: Full pytest node id.
    :return: Node id without ``[...]`` suffix.
    """

    return nodeid.split("[", 1)[0]


def _test_uid(canonical_nodeid, test_name, test_suite, test_class):
    """Build stable SHA-1 identifier for a test identity tuple.

    :param canonical_nodeid: Canonical node id.
    :param test_name: Normalized test name.
    :param test_suite: Test module path.
    :param test_class: Test class name.
    :return: SHA-1 hex digest.
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


def _is_backend_selector(value):
    """Check if value starts with a known testinfra backend scheme.

    :param value: Candidate value.
    :return: ``True`` when value looks like ``salt://...`` etc.
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
    """Check if text contains a testinfra backend selector substring.

    :param text: Candidate text.
    :return: ``True`` when any supported backend URI appears in text.
    """

    value = str(text or "")
    return bool(
        re.search(r"(?:salt|ssh|paramiko|docker|podman|local|ansible|chroot)://", value)
    )


def _has_dash_after_phonepe(text):
    """Return whether a dash exists after the last ``.phonepe`` token.

    :param text: Candidate text.
    :return: ``True`` when suffix includes ``-`` after ``.phonepe``.
    """

    value = str(text or "")
    idx = value.rfind(".phonepe")
    if idx == -1:
        return True
    return "-" in value[idx + len(".phonepe") :]


def _display_name_from_raw(raw_name):
    """Normalize pytest item name to display name.

    This preserves the existing testinfra-specific normalization behavior.

    :param raw_name: Raw item name.
    :return: Normalized display name.
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

    if ".phonepe" in text and not _has_dash_after_phonepe(text):
        return base_name

    if _contains_backend_selector(text) and not had_brackets:
        return base_name

    if _contains_backend_selector(text):
        last_segment = text.rsplit("-", 1)[-1]
        if not last_segment or "://" in last_segment or last_segment.endswith(">"):
            return base_name
        return "%s-%s" % (base_name, last_segment)

    return text


def _normalized_test_name(item, nodeid):
    """Resolve normalized test name from item or node id.

    :param item: Pytest item when available.
    :param nodeid: Full pytest node id fallback.
    :return: Display name.
    """

    item_name = getattr(item, "name", None) if item is not None else None
    if item_name:
        return _display_name_from_raw(item_name)

    node_suffix = str(nodeid).rsplit("::", 1)[-1]
    return _display_name_from_raw(node_suffix)


def _normalize_marker_value(value):
    """Normalize marker argument to stripped string or ``None``.

    :param value: Marker raw value.
    :return: Normalized value.
    """

    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _extract_item_markers(item):
    """Extract normalized marker definitions from pytest item.

    ``parametrize`` marker is intentionally excluded.

    :param item: Pytest item.
    :return: List of :class:`MarkerDef`.
    """

    markers: List[MarkerDef] = []
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
            markers.append(MarkerDef(name=marker_name, value=marker_value))

    return markers


def _is_concrete_host_value(value):
    """Check if value is a concrete single host target.

    :param value: Candidate host expression.
    :return: ``True`` for non-glob single host values.
    """

    if not value:
        return False

    text = str(value).strip()
    if not text:
        return False

    return not any(ch in text for ch in ("*", "?", "[", "]", ",", " "))


def _normalize_backend_target(value):
    """Normalize backend URI/plain host to host string.

    :param value: Backend URI or plain host.
    :return: Normalized host or ``None`` when not concrete.
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
    """Extract host from node id containing testinfra backend URI.

    :param nodeid: Pytest node id.
    :return: Normalized host or ``None``.
    """

    if not nodeid:
        return None

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


def _default_failure_map_path():
    """Return default failure map path.

    :return: Absolute path to ``failure_mapper/failure_map.yaml``.
    """

    package_root = os.path.dirname(__file__)
    return os.path.join(package_root, "failure_mapper", "failure_map.yaml")


def _build_backend(backend_name: str) -> AbstractStorageBackend:
    """Instantiate backend strategy by name.

    :param backend_name: Backend selector string.
    :return: Backend instance.
    :raises ValueError: If backend name is unsupported.
    """

    normalized = (backend_name or "mariadb").strip().lower()
    if normalized == "mariadb":
        return MariaDBBackend()
    if normalized == "postgres":
        return PostgresBackend()
    raise ValueError("Unsupported backend '%s'. Supported values: mariadb, postgres" % backend_name)


def pytest_addoption(parser):
    """Register CLI options for storage reporting.

    :param parser: Pytest parser object.
    """

    group = parser.getgroup("mariadb-reporting")
    group.addoption(
        "--mariadb-report",
        action="store_true",
        default=False,
        help="Enable writing pytest results to storage backend.",
    )
    group.addoption(
        "--report-backend",
        action="store",
        default="mariadb",
        help="Storage backend for reporting (currently supported: mariadb, postgres).",
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
    group.addoption("--postgres-host", action="store", default="localhost")
    group.addoption("--postgres-port", action="store", type=int, default=5432)
    group.addoption("--postgres-user", action="store", default="postgres")
    group.addoption("--postgres-password", action="store", default="password")
    group.addoption("--postgres-database", action="store", default="testinfra_reports")
    group.addoption(
        "--postgres-init-schema",
        action="store_true",
        default=False,
        help="Create/update required PostgreSQL schema before sending reports.",
    )


class FailureTagger:
    """Map failing/erroring tests to defect tags using YAML rules."""

    def __init__(self, config):
        """Initialize failure tagger.

        :param config: Pytest config object.
        """

        self.config = config
        self._disabled_reason = None
        self._error_maps = []
        self._map_path = config.getoption("--mariadb-failure-map")
        self._load_error_maps()

    @property
    def enabled(self):
        """Return whether at least one valid map rule is loaded."""

        return bool(self._error_maps)

    def _disable(self, reason):
        """Disable failure tagger and emit warning once.

        :param reason: Disable reason.
        """

        if not self._disabled_reason:
            warnings.warn("Failure tagger disabled: %s" % reason)
        self._disabled_reason = reason
        self._error_maps = []

    def _normalize_match_type(self, value):
        """Normalize map match type.

        :param value: Raw match type.
        :return: ``exact`` or lowercased string.
        """

        if not value:
            return "exact"
        return str(value).strip().lower()

    def _load_error_maps(self):
        """Load and validate error maps from YAML file."""

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
                warnings.warn("Failure tagger skipped map #%d due to empty target_logs" % idx)
                continue

            match_type = self._normalize_match_type(entry.get("match_type", "exact"))
            if match_type not in ("exact", "regex"):
                warnings.warn(
                    "Failure tagger skipped map #%d due to unsupported match_type '%s'"
                    % (idx, match_type)
                )
                continue

            defect = entry.get("defect") or {}
            defect_name = defect.get("name")
            if not defect_name:
                warnings.warn("Failure tagger skipped map #%d due to missing defect.name" % idx)
                continue

            try:
                compiled = [
                    re.compile(pattern) if match_type == "regex" else None
                    for pattern in patterns
                ]
            except re.error as exc:
                warnings.warn(
                    "Failure tagger skipped map #%d due to invalid regex: %s" % (idx, exc)
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
        """Return failure tag for failing/errored tests.

        :param status: Test status.
        :param text_parts: Failure text components.
        :return: Defect name or ``None``.
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


class TestinfraStorageReporter:
    """Backend-agnostic pytest reporter with preserved testinfra parsing logic."""

    def __init__(self, config):
        """Initialize reporter state.

        :param config: Pytest config.
        """

        self.config = config
        self.enabled = bool(config.getoption("--mariadb-report"))
        self._failure_tagger = config.pluginmanager.get_plugin("failure-tagger")
        self._disabled_reason = None
        self._backend_name = config.getoption("--report-backend")
        self._backend: Optional[AbstractStorageBackend] = None
        self._run_id = str(uuid.uuid4())
        self._run_started_at = _istnow_naive()
        self._reports_by_nodeid: Dict[str, Dict[str, object]] = {}
        self._metadata_by_nodeid: Dict[str, Dict[str, object]] = {}
        self._result_rows: List[TestResultRecord] = []

    @property
    def backend(self) -> Optional[AbstractStorageBackend]:
        """Return active backend strategy instance."""

        return self._backend

    def _disable(self, reason):
        """Disable reporter and emit warning.

        :param reason: Disable reason.
        """

        if self.enabled:
            warnings.warn("Storage pytest reporter disabled: %s" % reason)
        self.enabled = False
        self._disabled_reason = reason

    def _resolve_host_name(self, item):
        """Resolve testinfra host name with existing precedence rules.

        Resolution order is unchanged:

        1. ``item.funcargs['host'].backend.get_hostname()``
        2. Node-id backend URI parsing
        3. ``--hosts`` option normalization
        4. Local runner hostname

        :param item: Pytest item.
        :return: Host name.
        """

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
        """Extract captured stdout/stderr/log sections from pytest report.

        :param report: Pytest report object.
        :return: Dict with captured section text.
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
                section_map["captured_log"].append("[%s]\n%s" % (sec_name, sec_content))

        return {
            key: "\n\n".join(values) if values else None
            for key, values in section_map.items()
        }

    def _combined_longrepr(self, reports):
        """Combine ``longreprtext`` from reports with phase prefixes.

        :param reports: Phase report list.
        :return: Combined traceback text or ``None``.
        """

        longrepr_chunks = []
        for report in reports:
            text = getattr(report, "longreprtext", None)
            if text:
                longrepr_chunks.append("[%s]\n%s" % (report.when, text))
        if not longrepr_chunks:
            return None
        return "\n\n".join(longrepr_chunks)

    def _determine_status(self, setup_report, call_report, teardown_report):
        """Determine final result status from setup/call/teardown phases.

        :param setup_report: Setup phase report.
        :param call_report: Call phase report.
        :param teardown_report: Teardown phase report.
        :return: Canonical status string.
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

    def pytest_sessionstart(self, session):
        """Initialize backend and create run start record.

        :param session: Pytest session.
        """

        if not self.enabled:
            return

        try:
            self._backend = _build_backend(self._backend_name)
        except Exception as exc:
            self._disable(str(exc))
            return

        self._backend.initialize(self.config)
        if hasattr(self._backend, "enabled") and not getattr(self._backend, "enabled"):
            self._disable(getattr(self._backend, "disabled_reason", "backend disabled"))
            return

        run_summary = TestRunSummary(
            run_id=self._run_id,
            trigger_source=socket.getfqdn(),
            suite_version=self.config.getoption("--mariadb-suite-version"),
            started_at=self._run_started_at,
        )
        self._backend.session_start(run_summary)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        """Capture metadata once per node id during report generation.

        :param item: Pytest item.
        :param call: Pytest call object.
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
        """Accumulate phase reports and emit one normalized result at teardown.

        :param report: Pytest phase report.
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

        duration_ms = int(
            round(sum(float(getattr(r, "duration", 0.0) or 0.0) for r in phase_reports) * 1000)
        )

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
            "\n\n".join(merged_sections["captured_log"])
            if merged_sections["captured_log"]
            else None
        )
        captured_stdout_text = (
            "\n\n".join(merged_sections["captured_stdout"])
            if merged_sections["captured_stdout"]
            else None
        )
        captured_stderr_text = (
            "\n\n".join(merged_sections["captured_stderr"])
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
            TestResultRecord(
                nodeid=nodeid,
                test_uid=metadata.get(
                    "test_uid",
                    _test_uid(
                        metadata.get("canonical_nodeid", _canonical_nodeid(nodeid)),
                        metadata.get("test_name", _normalized_test_name(None, nodeid)),
                        metadata.get("test_suite"),
                        metadata.get("test_class"),
                    ),
                ),
                canonical_nodeid=metadata.get("canonical_nodeid", _canonical_nodeid(nodeid)),
                test_name=metadata.get("test_name", _normalized_test_name(None, nodeid)),
                test_suite=metadata.get("test_suite"),
                test_class=metadata.get("test_class"),
                host_name=metadata.get("host_name") or socket.gethostname(),
                status=status,
                failure_tag=failure_tag,
                duration_ms=duration_ms,
                started_at=started_at,
                finished_at=finished_at,
                error_type=error_type,
                error_message=error_message,
                full_trace=full_trace_text,
                captured_log=captured_log_text,
                captured_stdout=captured_stdout_text,
                captured_stderr=captured_stderr_text,
                markers=metadata.get("markers", []),
            )
        )

    def pytest_sessionfinish(self, session, exitstatus):
        """Flush accumulated records and finalize run summary via backend.

        :param session: Pytest session.
        :param exitstatus: Pytest exit code.
        """

        if not self.enabled:
            return

        if self._backend is None:
            return

        total_tests = len(self._result_rows)
        passed_count = sum(1 for row in self._result_rows if row.status == "pass")
        failed_count = sum(1 for row in self._result_rows if row.status == "fail")
        skipped_count = sum(1 for row in self._result_rows if row.status == "skipped")
        errored_count = sum(1 for row in self._result_rows if row.status == "error")

        counters = {
            "finished_at": _istnow_naive(),
            "total_tests": total_tests,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "errored_count": errored_count,
        }

        self._backend.save_results(self._run_id, self._result_rows)
        self._backend.session_finish(self._run_id, counters)

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        """Print storage reporter summary in pytest terminal output.

        :param terminalreporter: Pytest terminal reporter.
        :param exitstatus: Pytest exit code.
        :param config: Pytest config.
        """

        if not config.getoption("--mariadb-report"):
            return

        terminalreporter.write_sep("-", "Storage reporter")
        terminalreporter.write_line("enabled: %s" % ("yes" if self.enabled else "no"))
        terminalreporter.write_line("backend: %s" % self._backend_name)
        terminalreporter.write_line("run_id: %s" % self._run_id)
        terminalreporter.write_line("results_buffered: %d" % len(self._result_rows))
        if self._failure_tagger is not None:
            terminalreporter.write_line(
                "failure_tagger_enabled: %s" % ("yes" if self._failure_tagger.enabled else "no")
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


def pytest_configure(config):
    """Register plugin components with pytest plugin manager.

    :param config: Pytest config.
    """

    if not config.pluginmanager.has_plugin("failure-tagger"):
        config.pluginmanager.register(FailureTagger(config), "failure-tagger")

    if config.pluginmanager.has_plugin("storage-reporter"):
        return
    config.pluginmanager.register(TestinfraStorageReporter(config), "storage-reporter")
