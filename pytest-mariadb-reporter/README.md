## MariaDB test report publishing

This repository includes a pytest plugin that can push run results to MariaDB for Grafana dashboards.

### 1. Install dependency

```bash
pip install PyMySQL
```

### 2. Create schema

Apply [schema/db.sql](../schema/db.sql) in your MariaDB instance. You can also let pytest create tables by passing `--mariadb-init-schema`.

### 3. Run tests and publish results

```bash
pytest --params-file drove/drovee_adv.yaml drove/test_drove.py::test_proxy_log_mount --log-cli-level=info --hosts 'salt://adv-drovee*' --mariadb-report
```

### Captured fields

- Host on which the test ran.
- Test identity and status (`pass` / `fail` / `skipped` / `error`).
- Auto-assigned failure tag (`failure_tag`) for `fail` / `error` tests using `failure_mapper/failure_map.yaml`.
- Test start and finish timestamps.
- Test logs (`captured_log`, `captured_stdout`, `captured_stderr`) and failure traceback (`longrepr`).

### Grafana query examples

Host-level status cards for latest run:

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

Tests for a selected host in latest run:

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
