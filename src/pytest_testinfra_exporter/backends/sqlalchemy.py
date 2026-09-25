"""Shared SQLAlchemy Core storage backend implementation."""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Dict, List, Optional

from sqlalchemy import delete, insert, update

from ..backend import AbstractStorageBackend, resolve_report_tz_spec
from ..database import hosts, test_result_markers, test_results, test_runs, tests
from ..engine import create_backend_engine
from ..models import MarkerDef, TestResultRecord, TestRunSummary
from ..timezones import DEFAULT_TZ, now_naive, parse_timezone


def _normalize_marker_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SQLAlchemyBackend(AbstractStorageBackend):
    """Common transaction and persistence behavior for SQL backends."""

    backend_name = ""
    display_name = "Database"

    def __init__(self):
        self.config = None
        self._engine = None
        self._enabled = True
        self._disabled_reason = None
        self._results_saved = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def disabled_reason(self) -> Optional[str]:
        return self._disabled_reason

    def _disable(self, reason: str) -> None:
        if self._enabled:
            warnings.warn("%s backend disabled: %s" % (self.display_name, reason))
        self._enabled = False
        self._disabled_reason = reason

    def initialize(self, config) -> None:
        self.config = config
        self._report_tz = parse_timezone(resolve_report_tz_spec(config, self.backend_name))
        try:
            self._engine = create_backend_engine(config, self.backend_name)
            with self._engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except Exception as exc:
            self.close()
            self._disable("Failed connecting: %s" % exc)

    def _now_naive(self) -> dt.datetime:
        return now_naive(getattr(self, "_report_tz", None) or DEFAULT_TZ)

    def migrate(self, revision: str = "head", adopt_existing: bool = False):
        from ..migrations import upgrade_database

        if self._engine is None:
            raise RuntimeError("Backend is not connected")
        return upgrade_database(self._engine, revision=revision, adopt_existing=adopt_existing)

    def session_start(self, run_summary: TestRunSummary) -> None:
        if not self._enabled or self._engine is None:
            return
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(test_runs).values(
                        run_id=run_summary.run_id,
                        run_name=run_summary.run_name,
                        trigger_source=run_summary.trigger_source,
                        suite_version=run_summary.suite_version,
                        started_at=run_summary.started_at,
                    )
                )
        except Exception as exc:
            self._disable("Failed creating test_runs row: %s" % exc)

    def _upsert_host(self, connection, host_name: str) -> int:
        raise NotImplementedError

    def _upsert_test(self, connection, row: TestResultRecord) -> int:
        raise NotImplementedError

    def _upsert_result(
        self, connection, run_id: str, host_id: int, test_id: int, row: TestResultRecord
    ) -> int:
        raise NotImplementedError

    def _replace_result_markers(
        self, connection, test_result_id: int, markers: List[MarkerDef]
    ) -> None:
        connection.execute(
            delete(test_result_markers).where(
                test_result_markers.c.test_result_id == test_result_id
            )
        )
        marker_rows = []
        for marker in markers:
            marker_name = _normalize_marker_value(marker.name)
            marker_value = _normalize_marker_value(marker.value)
            if marker_name is not None:
                marker_rows.append(
                    {
                        "test_result_id": test_result_id,
                        "marker_name": marker_name[:128],
                        "marker_value": marker_value[:512] if marker_value else None,
                    }
                )
        if marker_rows:
            connection.execute(insert(test_result_markers), marker_rows)

    def save_results(self, run_id: str, results: List[TestResultRecord]) -> None:
        if not self._enabled or self._engine is None:
            return
        host_cache: Dict[str, int] = {}
        test_cache: Dict[str, int] = {}
        try:
            with self._engine.begin() as connection:
                for row in results:
                    host_id = host_cache.get(row.host_name)
                    if host_id is None:
                        host_id = self._upsert_host(connection, row.host_name)
                        host_cache[row.host_name] = host_id
                    test_id = test_cache.get(row.test_uid)
                    if test_id is None:
                        test_id = self._upsert_test(connection, row)
                        test_cache[row.test_uid] = test_id
                    result_id = self._upsert_result(connection, run_id, host_id, test_id, row)
                    self._replace_result_markers(connection, result_id, row.markers)
            self._results_saved = True
        except Exception as exc:
            self._results_saved = False
            warnings.warn("%s backend failed writing results: %s" % (self.display_name, exc))

    def session_finish(self, run_id: str, counters: dict) -> None:
        if not self._enabled or self._engine is None:
            self.close()
            return
        try:
            if self._results_saved:
                with self._engine.begin() as connection:
                    connection.execute(
                        update(test_runs)
                        .where(test_runs.c.run_id == run_id)
                        .values(
                            finished_at=counters.get("finished_at") or self._now_naive(),
                            total_tests=int(counters.get("total_tests", 0)),
                            passed_count=int(counters.get("passed_count", 0)),
                            failed_count=int(counters.get("failed_count", 0)),
                            skipped_count=int(counters.get("skipped_count", 0)),
                            errored_count=int(counters.get("errored_count", 0)),
                        )
                    )
        except Exception as exc:
            warnings.warn("%s backend failed updating run summary: %s" % (self.display_name, exc))
        finally:
            self.close()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
