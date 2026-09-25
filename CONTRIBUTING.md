# Contributing

Thanks for helping improve `pytest-testinfra-exporter`. This project sits between pytest, Testinfra, database storage, and Grafana, so good contributions usually keep those boundaries clear and easy to reason about.

## Development Setup

Install the plugin in editable mode from the repository root:

```bash
pip install -e .
```

Install the backend extras you need for local testing:

```bash
pip install -e ".[mariadb]"
pip install -e ".[postgres]"
```

The package supports Python 3.9 and newer.

## Making Changes

- Keep changes focused on one problem at a time.
- Preserve existing pytest and Testinfra behavior, especially host, backend, marker, and nodeid handling.
- Keep storage-specific behavior inside the relevant backend adapter.
- Update the relevant README when you change setup steps, CLI options, schemas, or dashboards.
- Avoid committing generated local files, credentials, database dumps, or Grafana environment state.

## Areas Of The Project

- `src/pytest_testinfra_exporter/` contains the pytest plugin, backend interface, storage adapters, models, and bundled runtime resources.
- `grafana/mariadb/` and `grafana/postgres/` contain importable and provisionable Grafana dashboards for the MariaDB and PostgreSQL backends respectively (see `grafana/README.md`).
- `src/pytest_testinfra_exporter/database.py` defines shared SQLAlchemy metadata; `src/pytest_testinfra_exporter/alembic/` contains immutable schema revisions.
- `docs/` contains the Sphinx package documentation.

For more detail, start with the root [README.md](README.md), then follow the README inside the area you are changing.

## Validation Checklist

Before opening a pull request, run the checks that match your change:

- For plugin changes, run a representative `pytest` command with `--storage-report` and the backend you changed.
- For MariaDB or PostgreSQL changes, migrate an empty database, adopt a compatible legacy database, and confirm a reporting run writes results successfully.
- Never edit a released Alembic revision; add a new revision and test upgrades from the prior revision.
- Build a wheel and verify the bundled Alembic revisions work outside the source checkout.
- For Grafana changes, import or provision the affected dashboard and confirm drill-down links, variables, and panels still work.
- For documentation changes, verify links point to existing files and examples use current CLI flags.

A minimal reporting run looks like this:

```bash
pytest tests/ \
  --storage-report \
  --storage-migrate \
  --report-backend mariadb
```

## Pull Requests

When opening a pull request, include:

- What changed and why.
- Which backend, dashboard, or documentation area is affected.
- How you validated the change.
- Any follow-up work or known limitations.

Small, well-scoped pull requests are easiest to review and merge.
