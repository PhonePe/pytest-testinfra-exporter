"""MariaDB SQLAlchemy Core storage adapter."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from ..database import hosts, test_results, tests
from ..models import TestResultRecord
from .sqlalchemy import SQLAlchemyBackend


class MariaDBBackend(SQLAlchemyBackend):
    """Persist reports through SQLAlchemy's MariaDB/MySQL dialect."""

    backend_name = "mariadb"
    display_name = "MariaDB"

    def _upsert_host(self, connection, host_name: str) -> int:
        now = self._now_naive()
        statement = mysql_insert(hosts).values(
            host_name=host_name, first_seen=now, last_seen=now
        )
        statement = statement.on_duplicate_key_update(
            id=func.last_insert_id(hosts.c.id), last_seen=statement.inserted.last_seen
        )
        return int(connection.execute(statement).lastrowid)

    def _upsert_test(self, connection, row: TestResultRecord) -> int:
        statement = mysql_insert(tests).values(
            test_uid=row.test_uid,
            canonical_nodeid=row.canonical_nodeid,
            test_name=row.test_name,
            test_suite=row.test_suite,
            test_class=row.test_class,
        )
        statement = statement.on_duplicate_key_update(
            id=func.last_insert_id(tests.c.id),
            canonical_nodeid=statement.inserted.canonical_nodeid,
            test_name=statement.inserted.test_name,
            test_suite=statement.inserted.test_suite,
            test_class=statement.inserted.test_class,
        )
        return int(connection.execute(statement).lastrowid)

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
        statement = mysql_insert(test_results).values(**values)
        statement = statement.on_duplicate_key_update(
            id=func.last_insert_id(test_results.c.id),
            **{
                key: getattr(statement.inserted, key)
                for key in values
                if key not in ("run_id", "host_id", "test_id")
            },
        )
        return int(connection.execute(statement).lastrowid)
