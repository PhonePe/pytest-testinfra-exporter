import pytest
from sqlalchemy import create_engine, inspect

from pytest_testinfra_exporter.database import metadata
from pytest_testinfra_exporter.migrations import VERSION_TABLE, upgrade_database


def test_empty_database_upgrades_to_head(tmp_path):
    engine = create_engine("sqlite:///%s" % (tmp_path / "reports.db"))

    result = upgrade_database(engine)

    assert result.current_revision == "0001"
    assert result.adopted_existing is False
    assert set(metadata.tables).issubset(inspect(engine).get_table_names())
    assert VERSION_TABLE in inspect(engine).get_table_names()

    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM %s" % VERSION_TABLE
        ).scalar_one() == "0001"


def test_existing_unversioned_schema_requires_adoption(tmp_path):
    engine = create_engine("sqlite:///%s" % (tmp_path / "legacy.db"))
    metadata.create_all(engine)

    with pytest.raises(RuntimeError, match="--storage-adopt-existing"):
        upgrade_database(engine)


def test_complete_unversioned_schema_can_be_adopted(tmp_path):
    engine = create_engine("sqlite:///%s" % (tmp_path / "adopt.db"))
    metadata.create_all(engine)

    result = upgrade_database(engine, adopt_existing=True)

    assert result.current_revision == "0001"
    assert result.adopted_existing is True


def test_partial_schema_cannot_be_adopted(tmp_path):
    engine = create_engine("sqlite:///%s" % (tmp_path / "partial.db"))
    metadata.tables["hosts"].create(engine)

    with pytest.raises(RuntimeError, match="partial reporter schema"):
        upgrade_database(engine, adopt_existing=True)
