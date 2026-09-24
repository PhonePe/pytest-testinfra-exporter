"""Shared SQLAlchemy Core schema for storage backends."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    true,
    text,
)
from sqlalchemy.dialects import mysql, postgresql


metadata = MetaData()

id_type = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
duration_type = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")
timestamp_type = DateTime(timezone=False).with_variant(
    mysql.DATETIME(fsp=6), "mysql"
).with_variant(postgresql.TIMESTAMP(precision=6, timezone=False), "postgresql")
capture_text_type = Text().with_variant(mysql.LONGTEXT(), "mysql")
status_type = String(16).with_variant(
    mysql.ENUM("pass", "fail", "skipped", "error", "xfail", "xpass"), "mysql"
)

hosts = Table(
    "hosts",
    metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("host_name", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=true()),
    Column("first_seen", timestamp_type, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_seen", timestamp_type, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("host_name", name="uq_hosts_host_name"),
)

tests = Table(
    "tests",
    metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("test_uid", CHAR(40), nullable=False),
    Column("canonical_nodeid", String(255), nullable=False),
    Column("test_name", String(255), nullable=False),
    Column("test_suite", String(1024)),
    Column("test_class", String(255)),
    UniqueConstraint("test_uid", name="uq_tests_test_uid"),
)
Index("idx_tests_canonical_nodeid", tests.c.canonical_nodeid)

test_runs = Table(
    "test_runs",
    metadata,
    Column("run_id", CHAR(36), primary_key=True),
    Column("run_name", String(255), nullable=False),
    Column("trigger_source", String(64), nullable=False, server_default=text("'local'")),
    Column("suite_version", String(255)),
    Column("started_at", timestamp_type, nullable=False),
    Column("finished_at", timestamp_type),
    Column("total_tests", Integer, nullable=False, server_default=text("0")),
    Column("passed_count", Integer, nullable=False, server_default=text("0")),
    Column("failed_count", Integer, nullable=False, server_default=text("0")),
    Column("skipped_count", Integer, nullable=False, server_default=text("0")),
    Column("errored_count", Integer, nullable=False, server_default=text("0")),
)
Index("idx_test_runs_run_name", test_runs.c.run_name)
Index("idx_test_runs_started", test_runs.c.started_at)

status_check = CheckConstraint(
    "status IN ('pass', 'fail', 'skipped', 'error', 'xfail', 'xpass')",
    name="chk_test_results_status",
).ddl_if(dialect="postgresql")

test_results = Table(
    "test_results",
    metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("run_id", CHAR(36), ForeignKey("test_runs.run_id", name="fk_test_results_run", ondelete="CASCADE"), nullable=False),
    Column("host_id", id_type, ForeignKey("hosts.id", name="fk_test_results_host"), nullable=False),
    Column("test_id", id_type, ForeignKey("tests.id", name="fk_test_results_test"), nullable=False),
    Column("status", status_type, nullable=False),
    Column("failure_tag", String(255)),
    Column("duration_ms", duration_type, nullable=False, server_default=text("0")),
    Column("started_at", timestamp_type),
    Column("finished_at", timestamp_type),
    Column("error_type", String(128)),
    Column("error_message", Text),
    Column("full_trace", capture_text_type),
    Column("captured_log", capture_text_type),
    Column("captured_stdout", capture_text_type),
    Column("captured_stderr", capture_text_type),
    UniqueConstraint("run_id", "host_id", "test_id", name="uq_test_results_run_host_test"),
    status_check,
)
Index("idx_test_results_run_host", test_results.c.run_id, test_results.c.host_id)
Index("idx_test_results_host_finished", test_results.c.host_id, test_results.c.finished_at)
Index("idx_test_results_host_status_finished", test_results.c.host_id, test_results.c.status, test_results.c.finished_at)
Index("idx_test_results_test_finished", test_results.c.test_id, test_results.c.finished_at)
Index("idx_test_results_status_finished", test_results.c.status, test_results.c.finished_at)
Index("idx_test_results_failure_tag_finished", test_results.c.failure_tag, test_results.c.finished_at)

test_result_markers = Table(
    "test_result_markers",
    metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column(
        "test_result_id",
        id_type,
        ForeignKey("test_results.id", name="fk_result_markers_result", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("marker_name", String(128), nullable=False),
    Column("marker_value", String(512)),
    UniqueConstraint(
        "test_result_id", "marker_name", "marker_value", name="uq_result_marker_name_value"
    ),
)
Index("idx_result_markers_name_value", test_result_markers.c.marker_name, test_result_markers.c.marker_value)
Index("idx_result_markers_name", test_result_markers.c.marker_name)

REPORTER_TABLES = tuple(metadata.tables)
