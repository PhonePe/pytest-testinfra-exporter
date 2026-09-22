# Grafana Dashboards for TestInfra Reporting

This folder contains a 3-level drill-down dashboard set, in two flavors:

- `mariadb/` — MySQL/MariaDB SQL, for `--report-backend mariadb`
- `postgres/` — PostgreSQL SQL, for `--report-backend postgres`

Dashboard UIDs and titles are suffixed per variant (`-mariadb` / `-pg`, e.g.
`testinfra-overview-mariadb` and `testinfra-overview-pg`), so both variants
can be provisioned into the same Grafana instance without collisions. All
drill-down links stay within their own variant.

Both trees have the same layout and contain a 3-level drill-down dashboard set:

1. Host overview: pass/fail/skipped/error counts for all hosts.
2. Host details: all tests for selected host with status.
3. Test logs: logs and failure details for selected test.

It also includes a failure-focused dashboard:

4. Failure status: hosts with failure tags and per-host failure tag distribution.

And a per-suite coverage dashboard:

5. Suite test coverage: pass/fail/skipped coverage for selected tests within a single suite across selected runs.

## PostgreSQL variant

`postgres/` contains Postgres-native copies of all seven dashboards, plus
its own provisioning (`postgres/provisioning/`) with a `TestInfra Postgres`
datasource (uid `testinfra-postgres`). Use these when the plugin reports to
the PostgreSQL backend (`--report-backend postgres`). The PostgreSQL
dashboards assume timestamps persisted in IST (the plugin default); if you
run the plugin with a different `--report-tz`, regenerate them with
`convert_to_postgres.py --tz <same-timezone>`.

## Files

- mariadb/ — MariaDB dashboards + provisioning
  - mariadb/testinfra-overview-dashboard.json
  - mariadb/testinfra-suite-overview-dashboard.json
  - mariadb/testinfra-suite-test-coverage-dashboard.json
  - mariadb/Host View/testinfra-host-dashboard.json
  - mariadb/Host View/testinfra-test-logs-dashboard.json
  - mariadb/Test Suite View/testinfra-suite-host-dashboard.json
  - mariadb/provisioning/dashboards/testinfra-dashboards.yaml
  - mariadb/provisioning/datasources/testinfra-mariadb.yaml
- postgres/ — Postgres dashboards + provisioning (same layout, Postgres SQL)
- convert_to_postgres.py (regenerates postgres/ from the mariadb/ dashboards)

## Provisioned datasource (mariadb)

The datasource is preconfigured as:

- Name: TestInfra MariaDB
- UID: testinfra-mariadb
- Host: localhost:3306
- Username: testinfra_user
- Password: <password>
- Database: testinfra_reports

## Import order

1. Import mariadb/testinfra-overview-dashboard.json
2. Import mariadb/testinfra-suite-overview-dashboard.json
3. Import mariadb/testinfra-suite-test-coverage-dashboard.json
4. Create or select the Host View folder in Grafana.
5. Import mariadb/Host View/testinfra-host-dashboard.json into the Host View folder.
6. Import mariadb/Host View/testinfra-test-logs-dashboard.json into the Host View folder.
7. Create or select the Test Suite View folder in Grafana.
8. Import mariadb/Test Suite View/testinfra-suite-host-dashboard.json into the Test Suite View folder.

## Dashboard provisioning

If you provision dashboards from files, copy `mariadb/provisioning/dashboards/testinfra-dashboards.yaml` (or `postgres/provisioning/dashboards/testinfra-dashboards.yaml` for the Postgres variant) into Grafana's provisioning/dashboards directory and set its options.path to the absolute path of that variant's directory. Grafana will place dashboards under the Host View and Test Suite View folders by mirroring the filesystem layout.

## Datasource

Each dashboard uses a datasource variable named `ds` (type: mysql). Select your MariaDB datasource in Grafana after import. The PostgreSQL dashboards under `postgres/` use the same variable retargeted to Postgres datasources (type: postgres).

## Drill-down flow

1. Open Host Overview and pick a run from `run_id`.
2. Click a host in "Host Status" table to open Host Details.
3. Click a test in Host Details to open Test Logs.
4. Open Test Suite Overview and click a point to open the suite-specific host drill-down dashboard.
5. Open Suite Test Coverage, pick a suite, tests, and runs and view individual run details with a drill-down approach.

## Suite Test Coverage dashboard

The Suite Test Coverage dashboard (`testinfra-suite-test-coverage`) shows pass/fail/skipped coverage for individual tests inside one suite:

- `suite`: single-select test suite (for example `percona/test_percona.py`).
- `test`: multi-select (or All) individual tests in the suite, keyed by `canonical_nodeid` (for example `percona/test_percona.py::test_mysql_conf`).
- `run_id`: multi-select (or All) runs, filtered by the dashboard time range.
- A bar chart breaks down Passed / Failed / Skipped per run for the current selection.
- The results table lists every matching run/host/test row; each row's "details" link opens the Test Logs dashboard for that exact run, host, and test (canonical node).


## Notes

- Dashboards use `run_id` from table `test_runs`.
- Host and test variables are automatically scoped to selected run.
- Log panel shows `longrepr`, `captured_log`, `captured_stdout`, and `captured_stderr`.
