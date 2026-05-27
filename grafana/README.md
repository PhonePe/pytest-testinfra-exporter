# Grafana Dashboards for TestInfra MariaDB Reporting

This folder contains a 3-level drill-down dashboard set:

1. Host overview: pass/fail/skipped/error counts for all hosts.
2. Host details: all tests for selected host with status.
3. Test logs: logs and failure details for selected test.

It also includes a failure-focused dashboard:

4. Failure status: hosts with failure tags and per-host failure tag distribution.

## Files

- testinfra-overview-dashboard.json
- testinfra-host-dashboard.json
- testinfra-test-logs-dashboard.json
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
2. Import testinfra-host-dashboard.json
3. Import testinfra-test-logs-dashboard.json

## Datasource

Each dashboard uses a datasource variable named `ds` (type: mysql). Select your MariaDB datasource in Grafana after import.

## Drill-down flow

1. Open Host Overview and pick a run from `run_id`.
2. Click a host in "Host Status" table to open Host Details.
3. Click a test in Host Details to open Test Logs.

## Notes

- Dashboards use `run_id` from table `test_runs`.
- Host and test variables are automatically scoped to selected run.
- Log panel shows `longrepr`, `captured_log`, `captured_stdout`, and `captured_stderr`.
