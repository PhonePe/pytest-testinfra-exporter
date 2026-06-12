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
- ``--run-name``
- ``--failure-map``

MariaDB options:

- ``--mariadb-host``
- ``--mariadb-port``
- ``--mariadb-user``
- ``--mariadb-password``
- ``--mariadb-database``
- ``--mariadb-suite-version``
- ``--mariadb-init-schema``

PostgreSQL options:

- ``--postgres-host``
- ``--postgres-port``
- ``--postgres-user``
- ``--postgres-password``
- ``--postgres-database``
- ``--postgres-init-schema``

Example: MariaDB
----------------

.. code-block:: bash

   pytest tests/ \
     --storage-report \
     --report-backend mariadb \
    --run-name "manual-run" \
     --mariadb-host 127.0.0.1 \
     --mariadb-port 3306 \
     --mariadb-user testinfra_user \
     --mariadb-password password \
     --mariadb-database testinfra_reports \
     --mariadb-init-schema

Example: PostgreSQL
-------------------

.. code-block:: bash

   pytest tests/ \
     --storage-report \
     --report-backend postgres \
     --postgres-host 127.0.0.1 \
     --postgres-port 5432 \
     --postgres-user postgres \
     --postgres-password password \
     --postgres-database testinfra_reports \
     --postgres-init-schema

Schema Files
------------

- MariaDB: ``schema/db.sql``
- PostgreSQL: ``schema/postgres.sql``

Failure Tagging
---------------

Failure tags are loaded from ``failure_mapper/failure_map.yaml`` and applied
to ``fail`` and ``error`` outcomes using first-match rules.
