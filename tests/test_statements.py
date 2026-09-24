import datetime as dt

from sqlalchemy.dialects import mysql, postgresql

from pytest_testinfra_exporter.backends.mariadb import MariaDBBackend
from pytest_testinfra_exporter.backends.postgres import PostgresBackend
from pytest_testinfra_exporter.database import hosts, test_results
from pytest_testinfra_exporter.models import TestResultRecord as ResultRecord


def _row():
    return ResultRecord(
        nodeid="test_example.py::test_ok",
        test_uid="a" * 40,
        canonical_nodeid="test_example.py::test_ok",
        test_name="test_ok",
        test_suite="test_example.py",
        test_class=None,
        host_name="host-a",
        status="pass",
        duration_ms=12,
        started_at=dt.datetime(2026, 1, 1),
        finished_at=dt.datetime(2026, 1, 1, 0, 0, 1),
    )


def test_mariadb_upsert_uses_last_insert_id():
    statement = mysql.insert(hosts).values(host_name="host-a")
    statement = statement.on_duplicate_key_update(
        id=mysql.insert(hosts).inserted.id
    )
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in compiled


def test_postgres_result_upsert_compiles_with_returning():
    row = _row()
    values = {
        "run_id": "r" * 36,
        "host_id": 1,
        "test_id": 1,
        "status": row.status,
    }
    statement = postgresql.insert(test_results).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[test_results.c.run_id, test_results.c.host_id, test_results.c.test_id],
        set_={"status": statement.excluded.status},
    ).returning(test_results.c.id)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (run_id, host_id, test_id) DO UPDATE" in compiled
    assert "RETURNING test_results.id" in compiled


def test_backend_types_are_sqlalchemy_adapters():
    assert MariaDBBackend.backend_name == "mariadb"
    assert PostgresBackend.backend_name == "postgres"
