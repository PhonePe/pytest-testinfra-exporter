Usage Guide
===========

Overview
--------

The plugin uses a strategy backend selected by ``--report-backend``.
Supported values:

- ``mariadb``
- ``postgres``

Common runtime flow:

1. Start pytest session and initialize selected backend.
2. Collect metadata per test node.
3. Accumulate normalized result records in memory.
4. Persist results and final counters at session finish.

CLI Options
-----------

Core options:

- ``--storage-report``
- ``--report-backend``
- ``--datastore-config``
- ``--run-name``
- ``--failure-map``
- ``--suite-version``
- ``--storage-migrate``
- ``--storage-migration-revision``
- ``--storage-adopt-existing``

MariaDB options:

- ``--mariadb-host``
- ``--mariadb-port``
- ``--mariadb-user``
- ``--mariadb-password``
- ``--mariadb-database``
- ``--mariadb-suite-version`` (deprecated alias for ``--suite-version``)
- ``--mariadb-init-schema`` (deprecated alias for ``--storage-migrate``)

PostgreSQL options:

- ``--postgres-host``
- ``--postgres-port``
- ``--postgres-user``
- ``--postgres-password``
- ``--postgres-database``
- ``--postgres-init-schema`` (deprecated alias for ``--storage-migrate``)

Example: MariaDB
----------------

.. code-block:: bash

   pytest tests/ \
     --storage-report \
     --storage-migrate \
     --report-backend mariadb \
    --run-name "manual-run" \
     --mariadb-host 127.0.0.1 \
     --mariadb-port 3306 \
     --mariadb-user testinfra_user \
     --mariadb-password password \
     --mariadb-database testinfra_reports

Example: PostgreSQL
-------------------

.. code-block:: bash

   pytest tests/ \
     --storage-report \
     --storage-migrate \
     --report-backend postgres \
     --postgres-host 127.0.0.1 \
     --postgres-port 5432 \
     --postgres-user postgres \
     --postgres-password password \
     --postgres-database testinfra_reports

Datastore Config File
---------------------

Connection settings can be supplied via a YAML file instead of repeating CLI
flags. The section under ``datastore.<report-backend>`` is selected based on
``--report-backend``.

.. code-block:: yaml

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

.. code-block:: bash

   pytest tests/ \
     --storage-report \
     --report-backend mariadb \
     --datastore-config datastore.yaml

Precedence (highest to lowest): explicit CLI options (for example
``--mariadb-user``), then values from the YAML file. When ``--datastore-config``
is omitted, the bundled ``datastores/default.yaml`` is used.

Schema Migrations
-----------------

The package uses SQLAlchemy Core for persistence and bundled Alembic revisions
for schema changes. ``--storage-migrate`` upgrades an empty or already-versioned
database to ``head`` without dropping reporting history. A migration can be run
without result reporting:

.. code-block:: bash

   pytest --storage-migrate --report-backend postgres \
     --datastore-config datastore.yaml

Databases created by releases before Alembic have the reporting tables but no
version record. Back up such a database and perform one verified adoption:

.. code-block:: bash

   pytest --storage-migrate --storage-adopt-existing \
     --report-backend postgres --datastore-config datastore.yaml

Adoption succeeds only when all legacy tables, columns, keys, relationships,
and required indexes match the released schema. Partial or incompatible
schemas are left untouched. The old backend-specific init flags are temporary,
non-destructive aliases and do not bypass this adoption check.

Passwords supplied as command-line options may be visible in shell history or
process listings. Prefer a protected datastore YAML file in shared or
production environments.

Failure Tagging
---------------

Failure tags are loaded from ``failure_mapper/failure_map.yaml`` and applied
to ``fail`` and ``error`` outcomes using first-match rules.
