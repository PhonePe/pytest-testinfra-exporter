"""PostgreSQL storage backend adapter.

This module provides :class:`PostgresBackend`, a concrete implementation of
:class:`pytest_mariadb_reporter.backend.AbstractStorageBackend`.

The adapter owns all PostgreSQL-specific concerns:

- psycopg2 import and connection management.
- Schema bootstrap/migration SQL execution.
- Upsert logic for hosts, tests, and test results.
- Marker normalization persistence.
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Dict, List, Optional

from ..backend import AbstractStorageBackend
from ..models import MarkerDef, TestResultRecord, TestRunSummary


#: Fixed IST timezone used for naive datetime persistence.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _istnow_naive() -> dt.datetime:
    """Return current IST wall clock as naive datetime.

    :return: Current datetime in IST with ``tzinfo=None``.
    """

    return dt.datetime.now(IST).replace(tzinfo=None)


def _normalize_marker_value(value):
    """Normalize a marker field to stripped string or ``None``.

    :param value: Input marker value.
    :return: Stripped string value or ``None``.
    """

    if value is None:
        return None
    text = str(value).strip()
    return text or None


class PostgresBackend(AbstractStorageBackend):
    """PostgreSQL adapter that persists test run metadata and results."""

    def __init__(self):
        """Initialize backend state."""

        self.config = None
        self._connection = None
        self._enabled = True
        self._disabled_reason = None

    @property
    def enabled(self) -> bool:
        """Return whether backend operations are enabled."""

        return self._enabled

    @property
    def disabled_reason(self) -> Optional[str]:
        """Return backend disable reason, if any."""

        return self._disabled_reason

    def initialize(self, config) -> None:
        """Initialize backend and open PostgreSQL connection.

        :param config: Pytest config object.
        """

        self.config = config
        self._ensure_connection()

    def _disable(self, reason: str) -> None:
        """Disable backend and emit one warning.

        :param reason: Human-readable disable reason.
        """

        if self._enabled:
            warnings.warn("PostgreSQL backend disabled: %s" % reason)
        self._enabled = False
        self._disabled_reason = reason

    def _ensure_connection(self) -> None:
        """Create DB connection lazily when enabled."""

        if not self._enabled:
            return

        if self._connection is not None:
            return

        try:
            import psycopg2
        except Exception as exc:
            self._disable(
                "psycopg2 is not available. Install with: pip install psycopg2-binary (%s)" % exc
            )
            return

        try:
            self._connection = psycopg2.connect(
                host=self.config.getoption("--postgres-host"),
                port=self.config.getoption("--postgres-port"),
                user=self.config.getoption("--postgres-user"),
                password=self.config.getoption("--postgres-password"),
                dbname=self.config.getoption("--postgres-database"),
            )
            self._connection.autocommit = False
        except Exception as exc:
            self._disable("Failed connecting to PostgreSQL: %s" % exc)

    def _init_schema(self, cursor) -> None:
        """Run idempotent schema creation and migration statements.

        :param cursor: Open DB cursor.
        """

        statements = [
            """
            CREATE TABLE IF NOT EXISTS hosts (
              id BIGSERIAL PRIMARY KEY,
              host_name VARCHAR(255) NOT NULL UNIQUE,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              first_seen TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_seen TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tests (
              id BIGSERIAL PRIMARY KEY,
              test_uid CHAR(40) NOT NULL UNIQUE,
              canonical_nodeid VARCHAR(255) NOT NULL,
              test_name VARCHAR(255) NOT NULL,
              test_suite VARCHAR(1024) NULL,
              test_class VARCHAR(255) NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_tests_canonical_nodeid
            ON tests (canonical_nodeid)
            """,
            """
            CREATE TABLE IF NOT EXISTS test_runs (
              run_id CHAR(36) PRIMARY KEY,
              trigger_source VARCHAR(64) NOT NULL DEFAULT 'local',
              suite_version VARCHAR(255) NULL,
              started_at TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL,
              finished_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
              total_tests INT NOT NULL DEFAULT 0,
              passed_count INT NOT NULL DEFAULT 0,
              failed_count INT NOT NULL DEFAULT 0,
              skipped_count INT NOT NULL DEFAULT 0,
              errored_count INT NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_runs_started
            ON test_runs (started_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS test_results (
              id BIGSERIAL PRIMARY KEY,
              run_id CHAR(36) NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
              host_id BIGINT NOT NULL REFERENCES hosts(id),
              test_id BIGINT NOT NULL REFERENCES tests(id),
              status VARCHAR(16) NOT NULL,
              failure_tag VARCHAR(255) NULL,
              duration_ms INT NOT NULL DEFAULT 0,
              started_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
              finished_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
              error_type VARCHAR(128) NULL,
              error_message TEXT NULL,
              full_trace TEXT NULL,
              captured_log TEXT NULL,
              captured_stdout TEXT NULL,
              captured_stderr TEXT NULL,
              CONSTRAINT uq_test_results_run_host_test UNIQUE (run_id, host_id, test_id),
              CONSTRAINT chk_test_results_status CHECK (status IN ('pass', 'fail', 'skipped', 'error', 'xfail', 'xpass'))
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_run_host
            ON test_results (run_id, host_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_host_finished
            ON test_results (host_id, finished_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_host_status_finished
            ON test_results (host_id, status, finished_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_test_finished
            ON test_results (test_id, finished_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_status_finished
            ON test_results (status, finished_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_test_results_failure_tag_finished
            ON test_results (failure_tag, finished_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS test_result_markers (
              id BIGSERIAL PRIMARY KEY,
              test_result_id BIGINT NOT NULL REFERENCES test_results(id) ON DELETE CASCADE,
              marker_name VARCHAR(128) NOT NULL,
              marker_value VARCHAR(512) NULL,
              CONSTRAINT uq_result_marker_name_value UNIQUE (test_result_id, marker_name, marker_value)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_result_markers_name_value
            ON test_result_markers (marker_name, marker_value)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_result_markers_name
            ON test_result_markers (marker_name)
            """,
        ]

        for statement in statements:
            cursor.execute(statement)

    def session_start(self, run_summary: TestRunSummary) -> None:
        """Persist test run start metadata.

        :param run_summary: Normalized run summary model.
        """

        if not self._enabled or self._connection is None:
            return

        cursor = self._connection.cursor()
        try:
            if self.config.getoption("--postgres-init-schema"):
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
                    run_summary.run_id,
                    run_summary.trigger_source,
                    run_summary.suite_version,
                    run_summary.started_at,
                ),
            )
            self._connection.commit()
        except Exception as exc:
            self._connection.rollback()
            self._disable("Failed creating test_runs row: %s" % exc)
        finally:
            cursor.close()

    def _upsert_host(self, cursor, host_name: str) -> int:
        """Upsert host and return ``hosts.id``.

        :param cursor: Open DB cursor.
        :param host_name: Hostname.
        :return: Host primary key.
        """

        now = _istnow_naive()
        cursor.execute(
            """
            INSERT INTO hosts (host_name, first_seen, last_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (host_name)
            DO UPDATE SET last_seen = EXCLUDED.last_seen
            RETURNING id
            """,
            (host_name, now, now),
        )
        return int(cursor.fetchone()[0])

    def _upsert_test(self, cursor, row: TestResultRecord) -> int:
        """Upsert test identity and return ``tests.id``.

        :param cursor: Open DB cursor.
        :param row: Test result record.
        :return: Test primary key.
        """

        cursor.execute(
            """
            INSERT INTO tests (test_uid, canonical_nodeid, test_name, test_suite, test_class)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (test_uid)
            DO UPDATE SET
              canonical_nodeid = EXCLUDED.canonical_nodeid,
              test_name = EXCLUDED.test_name,
              test_suite = EXCLUDED.test_suite,
              test_class = EXCLUDED.test_class
            RETURNING id
            """,
            (
                row.test_uid,
                row.canonical_nodeid,
                row.test_name,
                row.test_suite,
                row.test_class,
            ),
        )
        return int(cursor.fetchone()[0])

    def _insert_result(self, cursor, run_id: str, host_id: int, test_id: int, row: TestResultRecord) -> int:
        """Insert or update test result and return ``test_results.id``.

        :param cursor: Open DB cursor.
        :param run_id: Session run id.
        :param host_id: Host FK id.
        :param test_id: Test FK id.
        :param row: Result payload.
        :return: Result primary key.
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
            ON CONFLICT (run_id, host_id, test_id)
            DO UPDATE SET
              status = EXCLUDED.status,
              failure_tag = EXCLUDED.failure_tag,
              duration_ms = EXCLUDED.duration_ms,
              started_at = EXCLUDED.started_at,
              finished_at = EXCLUDED.finished_at,
              error_type = EXCLUDED.error_type,
              error_message = EXCLUDED.error_message,
              full_trace = EXCLUDED.full_trace,
              captured_log = EXCLUDED.captured_log,
              captured_stdout = EXCLUDED.captured_stdout,
              captured_stderr = EXCLUDED.captured_stderr
            RETURNING id
            """,
            (
                run_id,
                host_id,
                test_id,
                row.status,
                row.failure_tag,
                row.duration_ms,
                row.started_at,
                row.finished_at,
                row.error_type,
                row.error_message,
                row.full_trace,
                row.captured_log,
                row.captured_stdout,
                row.captured_stderr,
            ),
        )
        return int(cursor.fetchone()[0])

    def _replace_result_markers(self, cursor, test_result_id: int, markers: List[MarkerDef]) -> None:
        """Replace result marker rows for a test result.

        :param cursor: Open DB cursor.
        :param test_result_id: ``test_results.id``.
        :param markers: Marker definitions.
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
            marker_name = _normalize_marker_value(marker.name)
            marker_value = _normalize_marker_value(marker.value)
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

    def save_results(self, run_id: str, results: List[TestResultRecord]) -> None:
        """Persist result records in a transaction.

        :param run_id: Session run id.
        :param results: Result records.
        """

        if not self._enabled or self._connection is None:
            return

        cursor = self._connection.cursor()
        host_cache: Dict[str, int] = {}
        test_cache: Dict[str, int] = {}

        try:
            for row in results:
                host_id = host_cache.get(row.host_name)
                if host_id is None:
                    host_id = self._upsert_host(cursor, row.host_name)
                    host_cache[row.host_name] = host_id

                test_id = test_cache.get(row.test_uid)
                if test_id is None:
                    test_id = self._upsert_test(cursor, row)
                    test_cache[row.test_uid] = test_id

                test_result_id = self._insert_result(cursor, run_id, host_id, test_id, row)
                self._replace_result_markers(cursor, test_result_id, row.markers)

            self._connection.commit()
        except Exception as exc:
            self._connection.rollback()
            warnings.warn("PostgreSQL backend failed writing results: %s" % exc)
        finally:
            cursor.close()

    def session_finish(self, run_id: str, counters: dict) -> None:
        """Update run counters and close backend resources.

        :param run_id: Session run id.
        :param counters: Final run counters.
        """

        if not self._enabled or self._connection is None:
            return

        cursor = self._connection.cursor()
        try:
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
                    counters.get("finished_at") or _istnow_naive(),
                    int(counters.get("total_tests", 0)),
                    int(counters.get("passed_count", 0)),
                    int(counters.get("failed_count", 0)),
                    int(counters.get("skipped_count", 0)),
                    int(counters.get("errored_count", 0)),
                    run_id,
                ),
            )
            self._connection.commit()
        except Exception as exc:
            self._connection.rollback()
            warnings.warn("PostgreSQL backend failed updating run summary: %s" % exc)
        finally:
            cursor.close()
            self._connection.close()
            self._connection = None
