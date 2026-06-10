import datetime as dt
import warnings
from typing import Dict, List, Optional

from ..backend import AbstractStorageBackend
from ..models import MarkerDef, TestResultRecord, TestRunSummary


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _istnow_naive() -> dt.datetime:
    return dt.datetime.now(IST).replace(tzinfo=None)


def _normalize_marker_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class MariaDBBackend(AbstractStorageBackend):
    def __init__(self):
        self.config = None
        self._connection = None
        self._enabled = True
        self._disabled_reason = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def disabled_reason(self) -> Optional[str]:
        return self._disabled_reason

    def initialize(self, config) -> None:
        self.config = config
        self._ensure_connection()

    def _disable(self, reason: str) -> None:
        if self._enabled:
            warnings.warn("MariaDB backend disabled: %s" % reason)
        self._enabled = False
        self._disabled_reason = reason

    def _ensure_connection(self) -> None:
        if not self._enabled:
            return

        if self._connection is not None:
            return

        try:
            import pymysql
        except Exception as exc:
            self._disable("PyMySQL is not available. Install with: pip install PyMySQL (%s)" % exc)
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

    def _init_schema(self, cursor) -> None:
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

    def session_start(self, run_summary: TestRunSummary) -> None:
        if not self._enabled:
            return

        if self._connection is None:
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

    def _upsert_test(self, cursor, row: TestResultRecord) -> int:
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
                row.test_uid,
                row.canonical_nodeid,
                row.test_name,
                row.test_suite,
                row.test_class,
            ),
        )
        return int(cursor.lastrowid)

    def _insert_result(self, cursor, run_id: str, host_id: int, test_id: int, row: TestResultRecord) -> int:
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
        return int(cursor.lastrowid)

    def _replace_result_markers(self, cursor, test_result_id: int, markers: List[MarkerDef]) -> None:
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
        if not self._enabled:
            return

        if self._connection is None:
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
            warnings.warn("MariaDB backend failed writing results: %s" % exc)

    def session_finish(self, run_id: str, counters: dict) -> None:
        if not self._enabled:
            return

        if self._connection is None:
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
            warnings.warn("MariaDB backend failed updating run summary: %s" % exc)
        finally:
            cursor.close()
            self._connection.close()
            self._connection = None
