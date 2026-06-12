# MariaDB Reporting Schema and Pytest Plugin

This document explains how to reset/apply the MariaDB schema and publish pytest results for Grafana drill-down dashboards.

## Files

- Schema: [db.sql](db.sql)
- Pytest plugin: [../tests/mariadb_reporter/__init__.py](../tests/mariadb_reporter/__init__.py)

## What gets stored

- Host where test ran
- Run identifier and human-readable run name
- Test identity and status (`pass`, `fail`, `skipped`, `error`)
- Auto-assigned failure tag (`failure_tag`) for `fail` / `error` tests based on `failure_mapper/failure_map.yaml`
- Test timestamps and duration
- Logs and traceback (`captured_log`, `captured_stdout`, `captured_stderr`, `longrepr`)

## Quick reset and apply schema

`db.sql` now includes DROP statements at the top, so each run can recreate schema from scratch.

Example:

```bash
mysql -h 127.0.0.1 -P 3306 -u root -proot -e "CREATE DATABASE IF NOT EXISTS testinfra_reports CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -h 127.0.0.1 -P 3306 -u root -proot testinfra_reports < db.sql
```

## Pytest publishing run

Install dependency:

```bash
pip install PyMySQL
```

Run tests and publish:

```bash
pytest infra/test_monitoring.py \
  --mariadb-report \
  --mariadb-host 127.0.0.1 \
  --mariadb-port 3306 \
  --mariadb-user root \
  --mariadb-password root \
  --mariadb-database testinfra_reports \
  --run-name "manual-run" \
  --failure-map ../failure_mapper/failure_map.yaml \
  --mariadb-trigger-source local \
  --mariadb-suite-version "manual-run" \
  --mariadb-init-schema
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
