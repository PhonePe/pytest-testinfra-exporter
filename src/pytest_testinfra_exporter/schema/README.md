# Database Schema Migrations

The reporting schema is managed by bundled, version-controlled Alembic
migrations. MariaDB and PostgreSQL use the same SQLAlchemy Core metadata while
retaining the physical types required by each database.

The old `mariadb.sql` and `postgres.sql` reset scripts are no longer schema
authorities. In particular, schema setup no longer drops reporting history.

## What gets stored

- Host where test ran
- Run identifier and human-readable run name
- Test identity and status (`pass`, `fail`, `skipped`, `error`)
- Auto-assigned failure tag (`failure_tag`) for `fail` / `error` tests based on `failure_mapper/failure_map.yaml`
- Test timestamps and duration
- Logs and traceback (`captured_log`, `captured_stdout`, `captured_stderr`, `longrepr`)

## New Database

For an empty database, run the migrations before or together with the first
reporting run:

```bash
pytest tests/ --storage-report --storage-migrate --report-backend mariadb
```

Use `--report-backend postgres` for PostgreSQL. Connection settings can be
provided by `--datastore-config` or the backend-specific CLI options.

Migration-only operation is also supported:

```bash
pytest --storage-migrate --report-backend mariadb
```

## Existing Pre-Alembic Database

Databases created by version 0.4.x and earlier contain the five reporting tables but no
Alembic version record. Back up the database, then perform the one-time verified
adoption:

```bash
pytest --storage-migrate --storage-adopt-existing --report-backend mariadb
```

The plugin verifies the complete table set, columns, primary keys, unique
constraints, foreign keys, and required indexes before stamping the baseline.
Partial or incompatible schemas are rejected without dropping or modifying
their reporting tables. After adoption, normal runs need only
`--storage-migrate` when schema upgrades should be applied.

`--mariadb-init-schema` and `--postgres-init-schema` remain temporarily as
deprecated aliases for `--storage-migrate`. They are now non-destructive and do
not implicitly adopt an existing unversioned schema.

## Pytest publishing run

Install the matching backend extra:

```bash
pip install "pytest-testinfra-exporter[mariadb]"
```

Run tests and publish:

```bash
pytest infra/test_monitoring.py \
  --storage-report \
  --mariadb-host 127.0.0.1 \
  --mariadb-port 3306 \
  --mariadb-user root \
  --mariadb-password root \
  --mariadb-database testinfra_reports \
  --run-name "manual-run" \
  --failure-map ../failure_mapper/failure_map.yaml \
  --suite-version "manual-run" \
  --storage-migrate
```

## Grafana drill-down starter queries

Host summary for latest run:

```sql
SELECT
  h.host_name,
  SUM(CASE WHEN tr.status = 'pass' THEN 1 ELSE 0 END) AS pass_count,
  SUM(CASE WHEN tr.status = 'fail' THEN 1 ELSE 0 END) AS fail_count,
  SUM(CASE WHEN tr.status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
  SUM(CASE WHEN tr.status = 'error' THEN 1 ELSE 0 END) AS error_count
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
WHERE tr.run_id = (
  SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
)
GROUP BY h.host_name
ORDER BY h.host_name;
```

Tests for selected host in latest run:

```sql
SELECT
  t.canonical_nodeid,
  t.test_name,
  tr.status,
  tr.finished_at,
  tr.duration_ms
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
JOIN tests t ON t.id = tr.test_id
WHERE h.host_name = ${host:sqlstring}
  AND tr.run_id = (
    SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
  )
ORDER BY tr.finished_at DESC;
```

Logs for selected host + test in latest run:

```sql
SELECT
  tr.status,
  tr.finished_at,
  tr.longrepr,
  tr.captured_log,
  tr.captured_stdout,
  tr.captured_stderr
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
JOIN tests t ON t.id = tr.test_id
WHERE h.host_name = ${host:sqlstring}
  AND t.canonical_nodeid = ${test_nodeid:sqlstring}
  AND tr.run_id = (
    SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
  )
LIMIT 1;
```
