# Grafana Dashboards for TestInfra MariaDB Reporting

This folder contains a 3-level drill-down dashboard set:

1. Host overview: pass/fail/skipped/error counts for all hosts.
2. Host details: all tests for selected host with status.
3. Test logs: logs and failure details for selected test.

It also includes a failure-focused dashboard:

4. Failure status: hosts with failure tags and per-host failure tag distribution.

## Files

- testinfra-overview-dashboard.json
- testinfra-suite-overview-dashboard.json
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
3. Create or select the Host View folder in Grafana.
4. Import Host View/testinfra-host-dashboard.json into the Host View folder.
5. Import Host View/testinfra-test-logs-dashboard.json into the Host View folder.
6. Create or select the Test Suite View folder in Grafana.
7. Import Test Suite View/testinfra-suite-host-dashboard.json into the Test Suite View folder.

## Dashboard provisioning

If you provision dashboards from files, copy provisioning/dashboards/testinfra-dashboards.yaml into Grafana's provisioning/dashboards directory and set its options.path to the absolute path of this grafana directory. Grafana will place dashboards under the Host View and Test Suite View folders by mirroring the filesystem layout.

## Datasource

Each dashboard uses a datasource variable named `ds` (type: mysql). Select your MariaDB datasource in Grafana after import.

## Drill-down flow

1. Open Host Overview and pick a run from `run_id`.
2. Click a host in "Host Status" table to open Host Details.
3. Click a test in Host Details to open Test Logs.
4. Open Test Suite Overview and click a point to open the suite-specific host drill-down dashboard.


## Notes

- Dashboards use `run_id` from table `test_runs`.
- Host and test variables are automatically scoped to selected run.
- Log panel shows `longrepr`, `captured_log`, `captured_stdout`, and `captured_stderr`.
