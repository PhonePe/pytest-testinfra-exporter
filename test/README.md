# Manual integration environment

This environment starts MariaDB, PostgreSQL, Grafana, a Salt master, and a
Salt minion. Testinfra and the Python packaging tools are installed on the
Salt master. The repository is mounted at `/workspace`, so local source
changes are immediately available inside that container.

The credentials in this directory are intentionally fixed and are only for
the local, isolated test environment.

## Start the environment

From the repository root:

```bash
docker compose -f test/compose.yaml up -d --build
docker compose -f test/compose.yaml ps
```

Wait until all containers are healthy. Verify that the master can reach the
minion:

```bash
docker compose -f test/compose.yaml exec salt-master salt minion test.ping
```

The expected response is `True`. The master automatically accepts minion
keys because this is an isolated test environment.

Open a shell on the Salt master for the remaining commands:

```bash
docker compose -f test/compose.yaml exec salt-master bash
```

## Fast source testing

Use an editable installation while iterating on plugin code:

```bash
python -m pip install -e "/workspace[mariadb,postgres]"
```

Run the tests and report to MariaDB:

```bash
pytest -c /workspace/test/tests/pytest.ini /workspace/test/tests -v \
  --hosts=salt://minion \
  --storage-report \
  --report-backend=mariadb \
  --mariadb-host=mariadb \
  --mariadb-user=testinfra_user \
  --mariadb-password=password \
  --mariadb-database=testinfra_reports \
  --storage-migrate \
  --run-name="manual MariaDB test"
```

Run the tests and report to PostgreSQL:

```bash
pytest -c /workspace/test/tests/pytest.ini /workspace/test/tests -v \
  --hosts=salt://minion \
  --storage-report \
  --report-backend=postgres \
  --postgres-host=postgres \
  --postgres-user=postgres \
  --postgres-password=password \
  --postgres-database=testinfra_reports \
  --storage-migrate \
  --run-name="manual PostgreSQL test"
```

`--storage-migrate` applies non-destructive Alembic upgrades and is safe to use
on subsequent runs. For a database populated by version 0.4.x or earlier, back it up and
add `--storage-adopt-existing` once; the plugin validates the legacy schema
before recording the baseline revision. The old backend-specific init flags
are deprecated aliases and no longer reset data. To deliberately reset this
local environment, use `docker compose -f test/compose.yaml down --volumes`.

The pytest output should contain a `Storage reporter` section with
`enabled: yes`, the selected backend, and the number of buffered results.
The plugin disables reporting with a warning when a database connection or
schema operation fails, so do not rely only on pytest's process exit code.

## Test the built Python package

An editable installation does not prove that the wheel and source archive
contain all required files. Before release, build and validate both package
artifacts from the Salt master:

```bash
cd /workspace
rm -rf build dist src/*.egg-info
python -m build
python -m twine check dist/*
```

This validates `pyproject.toml`, package discovery, metadata, and artifact
creation. It also creates both an sdist and a wheel.

Install the wheel in a clean virtual environment so pytest cannot import the
editable checkout accidentally:

```bash
python -m venv /tmp/exporter-wheel-test
/tmp/exporter-wheel-test/bin/python -m pip install --upgrade pip
/tmp/exporter-wheel-test/bin/python -m pip install \
  "$(find /workspace/dist -name '*.whl' -print -quit)[mariadb,postgres]" \
  "pytest-testinfra[salt]==10.2.2"
```

Confirm the pytest plugin is discovered from the installed wheel:

```bash
cd /tmp
/tmp/exporter-wheel-test/bin/pytest --trace-config 2>&1 | grep pytest_testinfra_exporter
```

Run the same Testinfra checks with the clean environment. For MariaDB:

```bash
cd /tmp
/tmp/exporter-wheel-test/bin/pytest \
  -c /workspace/test/tests/pytest.ini /workspace/test/tests -v \
  --hosts=salt://minion \
  --storage-report \
  --report-backend=mariadb \
  --mariadb-host=mariadb \
  --mariadb-user=testinfra_user \
  --mariadb-password=password \
  --mariadb-database=testinfra_reports \
  --run-name="wheel MariaDB test"
```

For PostgreSQL:

```bash
cd /tmp
/tmp/exporter-wheel-test/bin/pytest \
  -c /workspace/test/tests/pytest.ini /workspace/test/tests -v \
  --hosts=salt://minion \
  --storage-report \
  --report-backend=postgres \
  --postgres-host=postgres \
  --postgres-user=postgres \
  --postgres-password=password \
  --postgres-database=testinfra_reports \
  --run-name="wheel PostgreSQL test"
```

Running from `/tmp` is intentional: it prevents the repository source tree
from shadowing the package installed in the virtual environment. These runs
also exercise the Alembic migrations, datastore defaults, and failure map
packaged inside the wheel.

To additionally prove the sdist can produce a wheel, use a separate output
directory:

```bash
mkdir -p /tmp/exporter-sdist-wheel
python -m pip wheel --no-deps \
  --wheel-dir /tmp/exporter-sdist-wheel \
  "$(find /workspace/dist -name '*.tar.gz' -print -quit)"
python -m twine check /tmp/exporter-sdist-wheel/*
```

## Inspect database results

From the host, inspect MariaDB:

```bash
docker compose -f test/compose.yaml exec mariadb \
  mariadb -utestinfra_user -ppassword testinfra_reports \
  -e "SELECT run_name, total_tests, passed_count, failed_count, errored_count FROM test_runs ORDER BY started_at DESC;"
```

Inspect PostgreSQL:

```bash
docker compose -f test/compose.yaml exec postgres \
  psql -U postgres -d testinfra_reports \
  -c "SELECT run_name, total_tests, passed_count, failed_count, errored_count FROM test_runs ORDER BY started_at DESC;"
```

## Test Grafana dashboards

Open [http://localhost:3000](http://localhost:3000) and log in with:

- Username: `admin`
- Password: `admin`

Grafana provisions both existing dashboard sets from the repository:

- `grafana/mariadb/` appears under the `Testinfra MariaDB` folder.
- `grafana/postgres/` appears under the `Testinfra PostgreSQL` folder.

The corresponding datasources are created automatically with the UIDs
required by the dashboard JSON:

- `testinfra-mariadb`, connected to `mariadb:3306`
- `testinfra-postgres`, connected to `postgres:5432`

In Grafana, open **Connections > Data sources** and use **Save & test** for
both datasources. Then open the matching dashboard folder and select the run
created by pytest. Test overview, suite, host, marker, and log drill-downs
after making relevant plugin changes.

Grafana scans the mounted dashboard directories every 10 seconds. Changes to
dashboard JSON files normally appear without rebuilding the container. Check
provisioning errors with:

```bash
docker compose -f test/compose.yaml logs grafana
```

## Create a failure result

To inspect failure storage and the Grafana failure views, temporarily add a
failing assertion to `test/tests/test_minion.py`, rerun one backend without
the schema initialization option, inspect the UI, and then revert your local
test edit.

## Stop or reset

Stop containers while retaining database and Grafana data:

```bash
docker compose -f test/compose.yaml down
```

Delete all test data, Grafana state, and Salt keys:

```bash
docker compose -f test/compose.yaml down --volumes
```

Rebuild the Salt image after changing its Dockerfile or pinned dependencies:

```bash
docker compose -f test/compose.yaml build --no-cache salt-master salt-minion
docker compose -f test/compose.yaml up -d
```
