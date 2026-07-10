# Grafana Dashboards for TestInfra MariaDB Reporting

This folder contains a 3-level drill-down dashboard set:

1. Host overview: pass/fail/skipped/error counts for all hosts.
2. Host details: all tests for selected host with status.
3. Test logs: logs and failure details for selected test.

It also includes a failure-focused dashboard:

4. Failure status: hosts with failure tags and per-host failure tag distribution.

And a per-suite coverage dashboard:

5. Suite test coverage: pass/fail/skipped coverage for selected tests within a single suite across selected runs.

## Files

- testinfra-overview-dashboard.json
- testinfra-suite-overview-dashboard.json
- testinfra-suite-test-coverage-dashboard.json
- Host View/testinfra-host-dashboard.json
- Host View/testinfra-test-logs-dashboard.json
- Test Suite View/testinfra-suite-host-dashboard.json
- provisioning/dashboards/testinfra-dashboards.yaml
- provisioning/datasources/testinfra-mariadb.yaml

## Provisioned datasource

The datasource is preconfigured as:

- Name: TestInfra MariaDB
- UID: testinfra-mariadb
- Host: localhost:3306
- Username: testinfra_user
- Password: <password>
- Database: testinfra_reports

## Import order

1. Import testinfra-overview-dashboard.json
2. Import testinfra-suite-overview-dashboard.json
3. Import testinfra-suite-test-coverage-dashboard.json
4. Create or select the Host View folder in Grafana.
5. Import Host View/testinfra-host-dashboard.json into the Host View folder.
6. Import Host View/testinfra-test-logs-dashboard.json into the Host View folder.
7. Create or select the Test Suite View folder in Grafana.
8. Import Test Suite View/testinfra-suite-host-dashboard.json into the Test Suite View folder.

## Dashboard provisioning

If you provision dashboards from files, copy provisioning/dashboards/testinfra-dashboards.yaml into Grafana's provisioning/dashboards directory and set its options.path to the absolute path of this grafana directory. Grafana will place dashboards under the Host View and Test Suite View folders by mirroring the filesystem layout.

## Datasource

Each dashboard uses a datasource variable named `ds` (type: mysql). Select your MariaDB datasource in Grafana after import.

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
