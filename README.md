# pytest-testinfra-exporter

`pytest-testinfra-exporter` is a pytest plugin that captures the results of [Testinfra](https://testinfra.readthedocs.io/en/latest/) test runs, stores them in a database, and displays in Grafana with test-suite aware and host-aware drill-downs.

![Executive summary Grafana dashboard](assets/executive-summary-dashboard.png)

[Testinfra](https://testinfra.readthedocs.io/en/latest/) is excellent for proving that your infra is in the correct state. But once a run finishes, the useful context is often trapped in terminal output or CI logs, making trends, host-specific failures, and recurring issues hard to see.

This project is a lightweight reporting path for infrastructure tests: run your existing pytest-based checks, keep a durable history of what happened, and give engineers a detailed Grafana view of which hosts passed, which failed, and why. And all this without making any changes to your test suites. 

## What It Helps With

- Show Testinfra results in Grafana instead of digging through raw test logs.
- Compare pass, fail, skipped, and error counts across hosts and test suites.
- Drill from a run overview into a host, a test, and the captured failure output.
- Preserve important pytest and testinfra contexts such as hostname, connection backend, markers and more. 
- Dynamically tag common failure patterns so recurring issues are easier to spot.
- Store results in MariaDB or PostgreSQL through SQLAlchemy Core adapters.
- Apply non-destructive, version-controlled schema upgrades with bundled Alembic migrations.

## How It Works

`pytest-testinfra-exporter` installs as a pytest plugin. When reporting is enabled, it listens to the normal pytest lifecycle, normalizes each test result, and writes run, host, test, status, timing, log, and failure-tag data to the selected database backend.

Grafana dashboards then query that stored data to provide an overview-first workflow:

1. Start at the latest run or a selected run.
2. See host and suite health at a glance.
3. Drill into the affected host or suite.
4. Open the exact test logs, traceback, stdout, stderr, and failure details.

A reporting run looks like this:

```bash
pytest tests/ \
  --storage-report \
  --storage-migrate \
  --report-backend mariadb
```

## Database Migrations

`--storage-migrate` runs the bundled Alembic migrations against the selected
database before results are written. It creates the reporting tables in an
empty database or upgrades an already versioned database to the latest schema.
Migrations are non-destructive: they do not drop the reporting tables or erase
test history.

The flag does not need to be specified on every pytest run. Once the database
is at the latest revision, a normal reporting run can omit it:

```bash
pytest tests/ \
  --storage-report \
  --report-backend mariadb
```

Including `--storage-migrate` on every run is also safe. Alembic checks the
current database revision and performs no schema changes when it is already at
`head`. This is convenient for CI and disposable environments, although it adds
a migration lock and revision check at startup.

For controlled environments, apply migrations once as part of deployment:

```bash
pytest --collect-only \
  --storage-migrate \
  --report-backend mariadb \
  --datastore-config datastore.yaml
```

Subsequent test runs can then use `--storage-report` without
`--storage-migrate`.

### Existing Databases From Version 0.4.x or Earlier

Databases created by version 0.4.x or earlier have the reporting tables but no
Alembic version record. Back up the database and perform a one-time verified
adoption:

```bash
pytest --collect-only \
  --storage-migrate \
  --storage-adopt-existing \
  --report-backend mariadb \
  --datastore-config datastore.yaml
```

The plugin validates the legacy tables, columns, keys, relationships, and
required indexes before recording the baseline revision. Use
`--storage-adopt-existing` only for this first adoption; it is not needed for
routine runs after the database is versioned.

| Situation | Required migration flags |
| --- | --- |
| New empty database | `--storage-migrate` |
| Existing database from version 0.4.x or earlier | `--storage-migrate --storage-adopt-existing` once |
| Already migrated database | No migration flag required |
| Automatically ensure the latest schema on every run | `--storage-migrate` |

The package also supports PostgreSQL as a storage backend. The bundled Grafana dashboards are currently built around the MariaDB/MySQL datasource flow.

Test changes locally with `docker compose -f test/compose.yaml up -d --build`; see the [`test/` directory](test/README.md) for the complete testing workflow.

## What's Included

- A pytest plugin that records testinfra results without requiring `conftest.py` changes.
- MariaDB and PostgreSQL storage backends.
- Alembic migrations for stored test runs, hosts, tests, results, logs, and failure metadata.
- Grafana dashboards for run, suite, host, and test-log views.
- Failure-mapping rules for classifying common failure output.

## Where To Go Next

- [CONTRIBUTING.md](CONTRIBUTING.md) - install the plugin for development and review the validation workflow.
- [grafana/README.md](grafana/README.md) - import or provision the Grafana dashboards and datasource.
- [src/pytest_testinfra_exporter/schema/README.md](src/pytest_testinfra_exporter/schema/README.md) - migrate new databases or adopt a pre-Alembic schema.
- [docs/usage.rst](docs/usage.rst) - read the usage guide for complete installation and command examples.

---

If your infrastructure tests already answer "is this host correct?", this project helps answer the next questions: "where is it failing?", "has this happened before?", and "how do I categorize and track a long list of failures to plan my fixes efficiently?". It keeps pytest as the execution engine, keeps Grafana as the visualization layer, and adds enough reporting to make Testinfra results visible, searchable, and useful. Add it to your existing test workflow, and every run becomes a Grafana-ready reporting source with minimal ceremony.
