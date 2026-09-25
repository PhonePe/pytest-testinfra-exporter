"""Programmatic Alembic migration and legacy-schema adoption support."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from .database import REPORTER_TABLES, metadata


VERSION_TABLE = "pytest_testinfra_exporter_alembic_version"
BASELINE_REVISION = "0001"


@dataclass
class MigrationResult:
    """Summary of a programmatic migration operation."""

    previous_revision: Optional[str]
    current_revision: Optional[str]
    adopted_existing: bool = False


def _alembic_config(connection) -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(files("pytest_testinfra_exporter").joinpath("alembic"))
    )
    config.attributes["connection"] = connection
    return config


def _current_revision(connection) -> Optional[str]:
    inspector = inspect(connection)
    if VERSION_TABLE not in inspector.get_table_names():
        return None
    return connection.execute(
        text("SELECT version_num FROM %s" % VERSION_TABLE)
    ).scalar_one_or_none()


def _column_sets(items):
    return {tuple(item["column_names"]) for item in items if item.get("column_names")}


def _validate_legacy_schema(connection) -> None:
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    reporter_existing = existing.intersection(REPORTER_TABLES)
    expected_tables = set(REPORTER_TABLES)
    if reporter_existing != expected_tables:
        missing = sorted(expected_tables - reporter_existing)
        present = sorted(reporter_existing)
        raise RuntimeError(
            "Cannot adopt partial reporter schema; present=%s missing=%s" % (present, missing)
        )

    for table in metadata.sorted_tables:
        actual_columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        expected_columns = {column.name: column for column in table.columns}
        if set(actual_columns) != set(expected_columns):
            raise RuntimeError(
                "Cannot adopt incompatible table '%s': expected columns %s, found %s"
                % (table.name, sorted(expected_columns), sorted(actual_columns))
            )
        for name, expected in expected_columns.items():
            actual = actual_columns[name]
            if bool(actual["nullable"]) != bool(expected.nullable):
                raise RuntimeError(
                    "Cannot adopt incompatible column '%s.%s': nullable mismatch"
                    % (table.name, name)
                )

        expected_pk = tuple(column.name for column in table.primary_key.columns)
        actual_pk = tuple(inspector.get_pk_constraint(table.name).get("constrained_columns") or ())
        if actual_pk != expected_pk:
            raise RuntimeError("Cannot adopt incompatible primary key on '%s'" % table.name)

        expected_unique = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        actual_unique = _column_sets(inspector.get_unique_constraints(table.name))
        if not expected_unique.issubset(actual_unique):
            raise RuntimeError("Cannot adopt incompatible unique constraints on '%s'" % table.name)

        expected_indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        actual_indexes = _column_sets(inspector.get_indexes(table.name))
        if not expected_indexes.issubset(actual_indexes):
            raise RuntimeError("Cannot adopt incompatible indexes on '%s'" % table.name)

        expected_fks = {
            (
                tuple(column.name for column in constraint.columns),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
        actual_fks = {
            (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table.name)
        }
        if not expected_fks.issubset(actual_fks):
            raise RuntimeError("Cannot adopt incompatible foreign keys on '%s'" % table.name)


@contextmanager
def _migration_lock(connection):
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(text("SELECT pg_advisory_lock(743972311)"))
        try:
            yield
        except Exception:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(743972311)"))
            connection.commit()
            raise
        finally:
            if connection.in_transaction():
                connection.execute(text("SELECT pg_advisory_unlock(743972311)"))
    elif dialect in ("mysql", "mariadb"):
        acquired = connection.execute(
            text("SELECT GET_LOCK('pytest_testinfra_exporter_migration', 60)")
        ).scalar_one()
        if acquired != 1:
            raise RuntimeError("Timed out waiting for the database migration lock")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('pytest_testinfra_exporter_migration')"))
    else:
        yield


def upgrade_database(engine, revision: str = "head", adopt_existing: bool = False) -> MigrationResult:
    """Upgrade a database, optionally adopting a verified pre-Alembic schema."""

    with engine.connect() as connection:
        with _migration_lock(connection):
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            previous = _current_revision(connection)
            adopted = False
            has_reporter_tables = bool(table_names.intersection(REPORTER_TABLES))

            config = _alembic_config(connection)
            if previous is None and has_reporter_tables:
                if not adopt_existing:
                    raise RuntimeError(
                        "An unversioned reporter schema already exists. Re-run with "
                        "--storage-adopt-existing after backing up the database."
                    )
                _validate_legacy_schema(connection)
                command.stamp(config, BASELINE_REVISION)
                adopted = True

            command.upgrade(config, revision)
            current = _current_revision(connection)
            connection.commit()
            return MigrationResult(previous, current, adopted)
