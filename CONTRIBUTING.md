# Contributing

Thanks for helping improve `pytest-testinfra-exporter`. This project sits between pytest, Testinfra, database storage, and Grafana, so good contributions usually keep those boundaries clear and easy to reason about.

## Development Setup

Install the plugin in editable mode from the repository root:

```bash
pip install -e ./pytest-testinfra-exporter
```

Install the backend extras you need for local testing:

```bash
pip install -e "./pytest-testinfra-exporter[mariadb]"
pip install -e "./pytest-testinfra-exporter[postgres]"
```

The package supports Python 3.9 and newer.

## Making Changes

- Keep changes focused on one problem at a time.
- Preserve existing pytest and Testinfra behavior, especially host, backend, marker, and nodeid handling.
- Keep storage-specific behavior inside the relevant backend adapter.
- Update the relevant README when you change setup steps, CLI options, schemas, or dashboards.
- Avoid committing generated local files, credentials, database dumps, or Grafana environment state.

## Areas Of The Project

- `pytest-testinfra-exporter/` contains the pytest plugin, backend interface, storage adapters, models, schemas, and package documentation.
- `grafana/mariadb/` and `grafana/postgres/` contain importable and provisionable Grafana dashboards for the MariaDB and PostgreSQL backends respectively (see `grafana/README.md`).
- `pytest-testinfra-exporter/schema/` contains database schema files and schema usage notes.

For more detail, start with the root [README.md](README.md), then follow the README inside the area you are changing.

## Validation Checklist

Before opening a pull request, run the checks that match your change:

- For plugin changes, run a representative `pytest` command with `--storage-report` and the backend you changed.
- For MariaDB or PostgreSQL changes, apply the matching schema and confirm a reporting run writes results successfully.
- For Grafana changes, import or provision the affected dashboard and confirm drill-down links, variables, and panels still work.
- For documentation changes, verify links point to existing files and examples use current CLI flags.

A minimal reporting run looks like this:

```bash
pytest tests/ \
  --storage-report \
  --report-backend mariadb
```

## Pull Requests

When opening a pull request, include:

- What changed and why.
- Which backend, dashboard, or documentation area is affected.
- How you validated the change.
- Any follow-up work or known limitations.

Small, well-scoped pull requests are easiest to review and merge.
