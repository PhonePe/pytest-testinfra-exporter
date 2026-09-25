from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from pytest_testinfra_exporter.database import metadata


def test_metadata_contains_reporter_tables():
    assert set(metadata.tables) == {
        "hosts",
        "tests",
        "test_runs",
        "test_results",
        "test_result_markers",
    }


def test_schema_compiles_for_supported_dialects():
    results = metadata.tables["test_results"]

    mariadb_sql = str(CreateTable(results).compile(dialect=mysql.dialect()))
    postgres_sql = str(CreateTable(results).compile(dialect=postgresql.dialect()))

    assert "ENUM('pass','fail','skipped','error','xfail','xpass')" in mariadb_sql
    assert "LONGTEXT" in mariadb_sql
    assert "chk_test_results_status" in postgres_sql
    assert "ON DELETE CASCADE" in postgres_sql
