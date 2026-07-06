# pytest-testinfra-exporter

Backend-pluggable pytest reporting plugin with preserved `pytest-testinfra` host parsing behavior.

## Installation

From the `pytest-testinfra-exporter` directory, install the plugin in editable mode:

```bash
pip install -e .
```

If you are standing one level above this directory, use:

```bash
pip install -e ./pytest-testinfra-exporter
```

Pytest will auto-discover the plugin through the package's `pytest11` entry point, so no `conftest.py` changes are required.

Install a database driver if you plan to use a backend:

```bash
pip install -e ".[mariadb]"
```

or:

```bash
pip install -e ".[postgres]"
```

## Usage

Once installed, enable reporting directly from the pytest command line:

```bash
pytest --storage-report --report-backend=mariadb
```

The plugin is loaded automatically during pytest startup.

## Overview

This project now follows a **Strategy + Adapter** architecture:

- Core pytest hooks are implemented in `plugin.py`.
- Storage is delegated through `AbstractStorageBackend` in `backend.py`.
- MariaDB support is implemented as `MariaDBBackend` in `backends/mariadb.py`.
- PostgreSQL support is implemented as `PostgresBackend` in `backends/postgres.py`.
- Normalized payloads are represented by dataclasses in `models.py`.

The testinfra-specific logic is intentionally unchanged (including parsing of `salt://`, `ssh://`, fixture host resolution, and nodeid parsing).

---

## Package structure

- `models.py` — dataclasses used by the core reporter and backends.
- `backend.py` — backend interface contract.
- `backends/mariadb.py` — MariaDB adapter implementation.
- `backends/postgres.py` — PostgreSQL adapter implementation.
- `plugin.py` — pytest hooks, helper utilities, and failure tagging.
- `schema/db.sql` — full MariaDB schema reset/apply script.
- `failure_mapper/failure_map.yaml` — failure tag mapping rules.
- `docs/index.rst` — Sphinx documentation entry point.
- `docs/usage.rst` — Sphinx usage guide.
- `docs/api.rst` — Sphinx API reference.

---

## Runtime architecture

```text
pytest lifecycle hooks (plugin.py)
  -> build backend strategy (--report-backend)
  -> accumulate TestResultRecord objects in memory
  -> backend.save_results(run_id, results)
  -> backend.session_finish(run_id, counters)
```

### Hook flow

1. `pytest_sessionstart`
   - creates run metadata (`TestRunSummary`)
   - initializes selected backend
   - persists session start
2. `pytest_runtest_makereport`
   - captures metadata per nodeid (test name, suite, class, host, markers)
3. `pytest_runtest_logreport`
   - merges phase reports (`setup/call/teardown`)
   - computes final status and captured artifacts
   - appends `TestResultRecord`
4. `pytest_sessionfinish`
   - computes final counters
   - delegates persistence to backend
5. `pytest_terminal_summary`
   - prints reporter and tagger status

---

## CLI options

| Option | Default | Description |
|---|---|---|
| `--storage-report` | `False` | Enable reporting pipeline. |
| `--report-backend` | `mariadb` | Storage backend strategy selector (`mariadb`, `postgres`). |
| `--datastore-config` | `datastores/default.yaml` | Path to a YAML file with datastore connection settings. The `datastore.<report-backend>` section is used. Explicit CLI options override its values. |
| `--run-name` | Start datetime | Human-readable run name stored with the run. Defaults to `YYYY-MM-DD HH:MM:SS`. |
| `--mariadb-host` | `localhost` | MariaDB host. |
| `--mariadb-port` | `3306` | MariaDB port. |
| `--mariadb-user` | `testinfra_user` | MariaDB username. |
| `--mariadb-password` | `password` | MariaDB password. |
| `--mariadb-database` | `testinfra_reports` | MariaDB database name. |
| `--mariadb-suite-version` | `None` | Suite version string (for example git SHA). |
| `--mariadb-init-schema` | `False` | Run idempotent schema creation/migrations. |
| `--failure-map` | `failure_mapper/failure_map.yaml` | Failure tagging rules file. |
| `--postgres-host` | `localhost` | PostgreSQL host. |
| `--postgres-port` | `5432` | PostgreSQL port. |
| `--postgres-user` | `postgres` | PostgreSQL username. |
| `--postgres-password` | `password` | PostgreSQL password. |
| `--postgres-database` | `testinfra_reports` | PostgreSQL database name. |
| `--postgres-init-schema` | `False` | Run idempotent PostgreSQL schema creation. |

---

## Example usage

### MariaDB

```bash
pytest tests/ \
  --storage-report \
  --report-backend mariadb \
   --run-name "manual-run" \
  --mariadb-host 127.0.0.1 \
  --mariadb-port 3306 \
  --mariadb-user testinfra_user \
  --mariadb-password password \
  --mariadb-database testinfra_reports \
  --mariadb-suite-version "$(git rev-parse --short HEAD)" \
  --mariadb-init-schema
```

### PostgreSQL

```bash
pytest tests/ \
  --storage-report \
  --report-backend postgres \
  --postgres-host 127.0.0.1 \
  --postgres-port 5432 \
  --postgres-user postgres \
  --postgres-password password \
  --postgres-database testinfra_reports \
  --postgres-init-schema
```

### Datastore config file

Instead of passing connection flags on every invocation, define them once in a
YAML file and select the backend with `--report-backend`. When
`--datastore-config` is not supplied, the bundled
[`datastores/default.yaml`](datastores/default.yaml) is used:

```yaml
datastore:
  mariadb:
    host: 'localhost'
    port: 3306
    user: 'testinfra_user'
    password: 'password'
    database: 'testinfra_reports'
  postgres:
    host: 'localhost'
    port: 5432
    user: 'postgres'
    password: 'password'
    database: 'testinfra_reports'
```

```bash
pytest tests/ \
  --storage-report \
  --report-backend mariadb \
  --datastore-config datastore.yaml
```

Any explicit CLI option (for example `--mariadb-user`, `--mariadb-port`)
overrides the corresponding value from the YAML file.

---

## Dependencies

- MariaDB backend: `PyMySQL`
- PostgreSQL backend: `psycopg2-binary`
- Failure tagging: `PyYAML`

```bash
pip install PyMySQL psycopg2-binary pyyaml
```

---

## Failure tagging

`FailureTagger` loads YAML rules from `failure_mapper/failure_map.yaml` and applies first-match classification for `fail`/`error` results.

Supported matching:

- `match_type: exact` (substring)
- `match_type: regex` (`re.search`)

---

## Sphinx documentation

A Sphinx-ready docs tree is now included:

- `docs/index.rst`
- `docs/usage.rst`
- `docs/api.rst`

These files use autodoc directives for:

- `models`
- `backend`
- `plugin`
- `backends.mariadb`
- `backends.postgres`

Example minimal `docs/conf.py`:

```python
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]
```

Build docs from project root:

```bash
sphinx-build -b html docs docs/_build/html
```

---

## Notes

- Timestamp persistence remains naive IST for compatibility with existing dashboards.
- Existing testinfra host extraction behavior is preserved.
- Current backend implementations: `mariadb`, `postgres`.
