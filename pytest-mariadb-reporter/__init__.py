import datetime as dt
import os
import re
import socket
from urllib.parse import urlsplit
import uuid
import warnings

import pytest


def _utcnow_naive():
    # Store UTC timestamps as naive DATETIME(6) for MariaDB compatibility.
    return dt.datetime.utcnow().replace(tzinfo=None)


def _epoch_to_utc_naive(value):
    if value is None:
        return None
    return dt.datetime.utcfromtimestamp(value).replace(tzinfo=None)


def _canonical_nodeid(nodeid):
    return nodeid.split("[", 1)[0]


def _normalized_test_name(item, nodeid):
    original_name = getattr(item, "originalname", None)
    if original_name:
        return str(original_name)

    item_name = getattr(item, "name", None)
    if item_name:
        return _canonical_nodeid(str(item_name))

    return _canonical_nodeid(str(nodeid).rsplit("::", 1)[-1])


def _normalize_marker_value(value):
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _extract_item_markers(item):
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


def _is_concrete_host_value(value):
    if not value:
        return False

    text = str(value).strip()
    if not text:
        return False

    # Reject globs and host lists; these are selectors, not a concrete host target.
    return not any(ch in text for ch in ("*", "?", "[", "]", ",", " "))


def _normalize_backend_target(value):
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


def _default_failure_map_path():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(repo_root, "failure_mapper", "failure_map.yaml")


def pytest_addoption(parser):
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


class MariaDBFailureTagger:
    def __init__(self, config):
        self.config = config
        self._disabled_reason = None
        self._error_maps = []
        self._map_path = config.getoption("--mariadb-failure-map")
        self._load_error_maps()

    @property
    def enabled(self):
        return bool(self._error_maps)

    def _disable(self, reason):
        if not self._disabled_reason:
            warnings.warn("MariaDB failure tagger disabled: %s" % reason)
        self._disabled_reason = reason
        self._error_maps = []

    def _normalize_match_type(self, value):
        if not value:
            return "exact"
        return str(value).strip().lower()

    def _load_error_maps(self):
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


class MariaDBReporter:
    def __init__(self, config):
        self.config = config
        self.enabled = bool(config.getoption("--mariadb-report"))
        self._failure_tagger = config.pluginmanager.get_plugin("mariadb-failure-tagger")
        self._connection = None
        self._disabled_reason = None
        self._run_id = str(uuid.uuid4())
        self._run_started_at = _utcnow_naive()
        self._reports_by_nodeid = {}
        self._metadata_by_nodeid = {}
        self._result_rows = []

    def _disable(self, reason):
        if self.enabled:
            warnings.warn("MariaDB pytest reporter disabled: %s" % reason)
        self.enabled = False
        self._disabled_reason = reason

    def _resolve_host_name(self, item):
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
        longrepr_chunks = []
        for report in reports:
            text = getattr(report, "longreprtext", None)
            if text:
                longrepr_chunks.append("[%s]\\n%s" % (report.when, text))
        if not longrepr_chunks:
            return None
        return "\\n\\n".join(longrepr_chunks)

    def _determine_status(self, setup_report, call_report, teardown_report):
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
        statements = [
            """
            CREATE TABLE IF NOT EXISTS hosts (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              host_name VARCHAR(255) NOT NULL,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              first_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              last_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              PRIMARY KEY (id),
              UNIQUE KEY uq_hosts_host_name (host_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS tests (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              canonical_nodeid VARCHAR(255) NOT NULL,
              test_name VARCHAR(255) NOT NULL,
              test_suite VARCHAR(1024) NULL,
              test_class VARCHAR(255) NULL,
              created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              PRIMARY KEY (id),
              UNIQUE KEY uq_tests_canonical_nodeid (canonical_nodeid)
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
              created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
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
              created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
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
                            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
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

    def pytest_sessionstart(self, session):
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
        outcome = yield
        report = outcome.get_result()

        if not self.enabled:
            return

        nodeid = report.nodeid
        if nodeid not in self._metadata_by_nodeid:
            self._metadata_by_nodeid[nodeid] = {
                "canonical_nodeid": _canonical_nodeid(nodeid),
                "test_name": _normalized_test_name(item, nodeid),
                "test_suite": item.location[0] if hasattr(item, "location") else None,
                "test_class": item.cls.__name__ if getattr(item, "cls", None) else None,
                "host_name": self._resolve_host_name(item),
                "markers": _extract_item_markers(item),
            }

    def pytest_runtest_logreport(self, report):
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

        # Keep one per-test timestamp representing when execution completed.
        run_at = _epoch_to_utc_naive(max(s for s in stops if s is not None)) if any(s is not None for s in stops) else None
        if run_at is None and any(s is not None for s in starts):
            run_at = _epoch_to_utc_naive(min(s for s in starts if s is not None))

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
                "canonical_nodeid": metadata.get("canonical_nodeid", _canonical_nodeid(nodeid)),
                "test_name": metadata.get(
                    "test_name", _canonical_nodeid(str(nodeid).rsplit("::", 1)[-1])
                ),
                "test_suite": metadata.get("test_suite"),
                "test_class": metadata.get("test_class"),
                "host_name": metadata.get("host_name") or socket.gethostname(),
                "status": status,
                "failure_tag": failure_tag,
                "duration_ms": duration_ms,
                "run_at": run_at,
                "error_type": error_type,
                "error_message": error_message,
                "full_trace": full_trace_text,
                "captured_log": captured_log_text,
                "captured_stdout": captured_stdout_text,
                "captured_stderr": captured_stderr_text,
                "markers": metadata.get("markers", []),
            }
        )

    def _upsert_host(self, cursor, host_name):
        now = _utcnow_naive()
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
        cursor.execute(
            """
            INSERT INTO tests (canonical_nodeid, test_name, test_suite, test_class)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              id = LAST_INSERT_ID(id),
              test_name = VALUES(test_name),
              test_suite = VALUES(test_suite),
              test_class = VALUES(test_class)
            """,
            (
                row["canonical_nodeid"],
                row["test_name"],
                row["test_suite"],
                row["test_class"],
            ),
        )
        return int(cursor.lastrowid)

    def _insert_result(self, cursor, host_id, test_id, row):
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
                            started_at = NULL,
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
                None,
                row["run_at"],
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
            marker_rows.append((test_result_id, marker_name[:128], marker_value[:512] if marker_value else None))

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

                canonical_nodeid = row["canonical_nodeid"]
                test_id = test_cache.get(canonical_nodeid)
                if test_id is None:
                    test_id = self._upsert_test(cursor, row)
                    test_cache[canonical_nodeid] = test_id

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
                    _utcnow_naive(),
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


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("mariadb-failure-tagger"):
        config.pluginmanager.register(MariaDBFailureTagger(config), "mariadb-failure-tagger")

    if config.pluginmanager.has_plugin("mariadb-reporter"):
        return
    config.pluginmanager.register(MariaDBReporter(config), "mariadb-reporter")
