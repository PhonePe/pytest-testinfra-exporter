"""PostgreSQL SQLAlchemy Core storage adapter."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as postgres_insert

from ..database import hosts, test_results, tests
from ..models import TestResultRecord
from .sqlalchemy import SQLAlchemyBackend


class PostgresBackend(SQLAlchemyBackend):
    """Persist reports through SQLAlchemy's PostgreSQL dialect."""

    backend_name = "postgres"
    display_name = "PostgreSQL"

    def _upsert_host(self, connection, host_name: str) -> int:
        now = self._now_naive()
        statement = postgres_insert(hosts).values(
            host_name=host_name, first_seen=now, last_seen=now
        )
        statement = statement.on_conflict_do_update(
            index_elements=[hosts.c.host_name],
            set_={"last_seen": statement.excluded.last_seen},
        ).returning(hosts.c.id)
        return int(connection.execute(statement).scalar_one())

    def _upsert_test(self, connection, row: TestResultRecord) -> int:
        values = {
            "test_uid": row.test_uid,
            "canonical_nodeid": row.canonical_nodeid,
            "test_name": row.test_name,
            "test_suite": row.test_suite,
            "test_class": row.test_class,
        }
        statement = postgres_insert(tests).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[tests.c.test_uid],
            set_={key: getattr(statement.excluded, key) for key in values if key != "test_uid"},
        ).returning(tests.c.id)
        return int(connection.execute(statement).scalar_one())

    def _upsert_result(
        self, connection, run_id: str, host_id: int, test_id: int, row: TestResultRecord
    ) -> int:
        values = {
            "run_id": run_id,
            "host_id": host_id,
            "test_id": test_id,
            "status": row.status,
            "failure_tag": row.failure_tag,
            "duration_ms": row.duration_ms,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error_type": row.error_type,
            "error_message": row.error_message,
            "full_trace": row.full_trace,
            "captured_log": row.captured_log,
            "captured_stdout": row.captured_stdout,
            "captured_stderr": row.captured_stderr,
        }
        statement = postgres_insert(test_results).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[
                test_results.c.run_id,
                test_results.c.host_id,
                test_results.c.test_id,
            ],
            set_={
                key: getattr(statement.excluded, key)
                for key in values
                if key not in ("run_id", "host_id", "test_id")
            },
        ).returning(test_results.c.id)
        return int(connection.execute(statement).scalar_one())
