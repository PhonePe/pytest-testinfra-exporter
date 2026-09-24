"""Alembic environment used by the programmatic migration runner."""

from alembic import context

from pytest_testinfra_exporter.database import metadata
from pytest_testinfra_exporter.migrations import VERSION_TABLE


connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Alembic migrations require a programmatic SQLAlchemy connection")

context.configure(
    connection=connection,
    target_metadata=metadata,
    version_table=VERSION_TABLE,
    transaction_per_migration=True,
)

with context.begin_transaction():
    context.run_migrations()
