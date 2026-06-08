# MariaDB Reporter Plugin

A pytest plugin that publishes test results to a MariaDB database for
visualisation with Grafana dashboards.

---

## Table of contents

1. [Architecture](#architecture)
2. [Dependencies](#dependencies)
3. [Database schema](#database-schema)
4. [Quick start](#quick-start)
5. [CLI option reference](#cli-option-reference)
6. [Failure tagging](#failure-tagging)
7. [Pytest markers](#pytest-markers)
8. [Captured fields](#captured-fields)
9. [Grafana query examples](#grafana-query-examples)
10. [Terminal summary output](#terminal-summary-output)

---

## Architecture

Two plugins are registered by `pytest_configure`:

| Plugin name | Class | Responsibility |
|---|---|---|
| `mariadb-failure-tagger` | `MariaDBFailureTagger` | Load `failure_mapper/failure_map.yaml` and classify `fail`/`error` results with a human-readable tag. |
| `mariadb-reporter` | `MariaDBReporter` | Connect to MariaDB, buffer one result dict per test, flush to DB at session end. |

**Data flow:**

```
pytest session
  │
  ├─ pytest_sessionstart     → open connection, optional schema init, insert test_runs row
  ├─ pytest_runtest_makereport → capture metadata (host, suite, class, markers) per node ID
  ├─ pytest_runtest_logreport → buffer phase reports; on teardown merge → result dict
  ├─ pytest_sessionfinish    → bulk-flush result dicts to DB, update test_runs counters
  └─ pytest_terminal_summary → print run_id + reporter status to terminal
```

**Timezone:** All `DATETIME(6)` columns are stored as **naive IST (UTC+05:30)**
to match the Grafana data source timezone.

---

## Dependencies

| Package | Purpose | Install |
|---|---|---|
| `PyMySQL` | MariaDB wire protocol driver | `pip install PyMySQL` |
| `PyYAML` | Parse `failure_map.yaml` | `pip install pyyaml` |

The reporter gracefully disables itself (with a `warnings.warn`) when either
dependency is missing, so the rest of the test suite is unaffected.

---

## Database schema

Five tables are used:

| Table | Description |
|---|---|
| `hosts` | One row per unique host name seen across all runs. |
| `tests` | One row per unique test identity (keyed by SHA-1 `test_uid`). |
| `test_runs` | One row per pytest session (UUID `run_id`). |
| `test_results` | One fact row per (run, host, test) triple. |
| `test_result_markers` | Normalised pytest markers attached to each result. |

### Apply the schema manually

```bash
mysql -h 127.0.0.1 -P 3306 -u root -proot \
  -e "CREATE DATABASE IF NOT EXISTS testinfra_reports CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

mysql -h 127.0.0.1 -P 3306 -u root -proot testinfra_reports \
  < testinfra/plugins/mariadb_reporter/schema/db.sql
```

> **Warning:** `schema/db.sql` starts with `DROP TABLE IF EXISTS` statements.
> Running it against an existing database **destroys all data**.  Use
> `--mariadb-init-schema` instead for non-destructive creation/migration.

### Auto-init via pytest flag

Pass `--mariadb-init-schema` to have the plugin run idempotent
`CREATE TABLE IF NOT EXISTS` and `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
statements before the session starts.  Safe to run against an empty **or**
populated database.

---

## Quick start

### 1. Install dependencies

```bash
pip install PyMySQL pyyaml
```

### 2. Create the schema (first time only)

```bash
mysql -h 127.0.0.1 -P 3306 -u root -proot testinfra_reports \
  < testinfra/plugins/mariadb_reporter/schema/db.sql
```

### 3. Run tests and publish results

```bash
pytest testinfra/tests/infra/test_monitoring.py \
  --mariadb-report \
  --mariadb-host 127.0.0.1 \
  --mariadb-port 3306 \
  --mariadb-user testinfra_user \
  --mariadb-password password \
  --mariadb-database testinfra_reports \
  --mariadb-suite-version "$(git rev-parse --short HEAD)" \
  --mariadb-init-schema \
  --log-cli-level=info \
  --hosts 'salt://adv-host*'
```

---

## CLI option reference

All options belong to the `mariadb-reporting` group shown by `pytest --help`.

| Option | Type | Default | Description |
|---|---|---|---|
| `--mariadb-report` | flag | `False` | **Master switch.** Must be set to enable any DB activity. |
| `--mariadb-host` | string | `localhost` | MariaDB server hostname or IP. |
| `--mariadb-port` | int | `3306` | MariaDB server port. |
| `--mariadb-user` | string | `testinfra_user` | Database user. |
| `--mariadb-password` | string | `password` | Database password. |
| `--mariadb-database` | string | `testinfra_reports` | Target database / schema name. |
| `--mariadb-suite-version` | string | `None` | Stored in `test_runs.suite_version`; typically a git SHA or release tag. |
| `--mariadb-init-schema` | flag | `False` | Run idempotent DDL before the session starts (create tables / apply migrations). |
| `--mariadb-failure-map` | path | `failure_mapper/failure_map.yaml` | YAML file used to tag failed/errored tests (see [Failure tagging](#failure-tagging)). |

---

## Failure tagging

When a test has status `fail` or `error`, `MariaDBFailureTagger` searches
the combined failure text (error message + full traceback + captured logs)
against entries in `failure_map.yaml`.  The first matching entry's
`defect.name` is stored in `test_results.failure_tag`.

### `failure_map.yaml` format

```yaml
error_maps:
  - target_logs:
      - "Connection refused"
    match_type: exact          # "exact" (default) or "regex"
    defect:
      name: "network_error"

  - target_logs:
      - "AssertionError.*expected.*but got"
    match_type: regex
    defect:
      name: "assertion_mismatch"
```

| Field | Required | Description |
|---|---|---|
| `target_logs` | yes | List of pattern strings to match against failure output. |
| `match_type` | no | `exact` (substring, default) or `regex` (Python `re.search`). |
| `defect.name` | yes | Tag stored in `test_results.failure_tag`. |

The tagger is disabled (with a warning) when:
- PyYAML is not installed.
- The map file does not exist at the configured path.
- The `error_maps` list is absent or empty.
- All entries fail validation.

---

## Pytest markers

All pytest markers on a test (except `parametrize`) are recorded in the
`test_result_markers` table with their arguments.  This allows Grafana queries
to filter or group results by custom markers such as `@pytest.mark.component`
or `@pytest.mark.severity`.

Example query — count failures by marker value:

```sql
SELECT
    m.marker_value,
    COUNT(*) AS fail_count
FROM test_result_markers m
JOIN test_results tr ON tr.id = m.test_result_id
WHERE m.marker_name = 'component'
  AND tr.status = 'fail'
GROUP BY m.marker_value
ORDER BY fail_count DESC;
```

---

## Captured fields

| Column | Table | Description |
|---|---|---|
| `host_name` | `hosts` | Target host (from testinfra fixture, node ID, `--hosts`, or runner FQDN). |
| `status` | `test_results` | `pass`, `fail`, `skipped`, `error`, `xfail`, `xpass`. |
| `failure_tag` | `test_results` | Defect label from failure map, or `NULL`. |
| `duration_ms` | `test_results` | Total wall time across setup + call + teardown phases. |
| `started_at` | `test_results` | Earliest phase start time (naive IST). |
| `finished_at` | `test_results` | Latest phase stop time (naive IST). |
| `error_type` | `test_results` | `assertion`, `setup_or_teardown`, or `skipped`. |
| `error_message` | `test_results` | Short failure message from `longreprtext`. |
| `full_trace` | `test_results` | Full traceback from all failed phases (prefixed by phase name). |
| `captured_log` | `test_results` | Merged `captured log` sections from all phases. |
| `captured_stdout` | `test_results` | Merged `captured stdout` sections. |
| `captured_stderr` | `test_results` | Merged `captured stderr` sections. |
| `marker_name` / `marker_value` | `test_result_markers` | Pytest marker name and argument. |

---

## Grafana query examples

### Host-level status cards for latest run

```sql
SELECT
    h.host_name,
    SUM(CASE WHEN tr.status = 'pass'    THEN 1 ELSE 0 END) AS pass_count,
    SUM(CASE WHEN tr.status = 'fail'    THEN 1 ELSE 0 END) AS fail_count,
    SUM(CASE WHEN tr.status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
    SUM(CASE WHEN tr.status = 'error'   THEN 1 ELSE 0 END) AS error_count
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
WHERE tr.run_id = (
    SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
)
GROUP BY h.host_name
ORDER BY h.host_name;
```

### Tests for a selected host in the latest run

```sql
SELECT
    t.canonical_nodeid,
    t.test_name,
    tr.status,
    tr.failure_tag,
    tr.finished_at,
    tr.duration_ms
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
JOIN tests t  ON t.id = tr.test_id
WHERE h.host_name = ${host:sqlstring}
  AND tr.run_id = (
      SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
  )
ORDER BY tr.finished_at DESC;
```

### Logs for a selected host + test in the latest run

```sql
SELECT
    tr.status,
    tr.error_type,
    tr.finished_at,
    tr.full_trace,
    tr.captured_log,
    tr.captured_stdout,
    tr.captured_stderr
FROM test_results tr
JOIN hosts h ON h.id = tr.host_id
JOIN tests t  ON t.id = tr.test_id
WHERE h.host_name        = ${host:sqlstring}
  AND t.canonical_nodeid = ${test_nodeid:sqlstring}
  AND tr.run_id = (
      SELECT run_id FROM test_runs ORDER BY started_at DESC LIMIT 1
  )
LIMIT 1;
```

### Failure tag breakdown across all runs (last 7 days)

```sql
SELECT
    tr.failure_tag,
    COUNT(*) AS occurrences
FROM test_results tr
WHERE tr.status IN ('fail', 'error')
  AND tr.finished_at >= NOW() - INTERVAL 7 DAY
  AND tr.failure_tag IS NOT NULL
GROUP BY tr.failure_tag
ORDER BY occurrences DESC;
```

---

## Terminal summary output

When `--mariadb-report` is active, the plugin appends a block to pytest's
terminal output after all tests complete:

```
------------------ MariaDB reporter -------------------
enabled: yes
run_id: 3f2a1b4c-…
results_buffered: 42
failure_tagger_enabled: yes
failure_map_path: /path/to/failure_mapper/failure_map.yaml
```

The `run_id` UUID can be used directly in Grafana variable queries to drill
into a specific run.

---

## Related files

| Path | Description |
|---|---|
| [`schema/db.sql`](schema/db.sql) | Full schema DDL (destructive reset). |
| [`schema/README.md`](schema/README.md) | Schema reset and apply instructions. |
| [`failure_mapper/failure_map.yaml`](failure_mapper/failure_map.yaml) | Default failure classification rules. |
| [`grafana/`](../../../../grafana/) | Grafana dashboard JSON definitions. |
