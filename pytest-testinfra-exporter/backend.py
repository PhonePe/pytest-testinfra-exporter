# Copyright (c) 2026 Original Author(s), PhonePe India Pvt. Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Abstract storage backend interface for pytest result persistence.

The reporter core depends only on :class:`AbstractStorageBackend` and never on a
concrete database client. This enables strategy-based backend selection.
"""

from __future__ import annotations

import os
import warnings
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import TestResultRecord, TestRunSummary


#: Connection settings recognized in the datastore config and as CLI overrides.
CONNECTION_KEYS = ("host", "port", "user", "password", "database")


def _default_datastore_config_path() -> str:
    """Return the path to the bundled default datastore config.

    :return: Absolute path to ``datastores/default.yaml``.
    """

    return os.path.join(os.path.dirname(__file__), "datastores", "default.yaml")


def _load_datastore_yaml(path: str, backend_name: str) -> Dict[str, Any]:
    """Load the ``datastore.<backend_name>`` section from a YAML file.

    :param path: Path to the datastore YAML file.
    :param backend_name: Normalized backend selector (e.g. ``mariadb``).
    :return: Mapping of connection settings, or empty dict on any problem.
    """

    try:
        import yaml
    except Exception as exc:  # pragma: no cover - depends on optional dep
        warnings.warn(
            "Datastore config ignored: PyYAML is not available. "
            "Install with: pip install pyyaml (%s)" % exc
        )
        return {}

    if not os.path.exists(path):
        warnings.warn("Datastore config ignored: file not found at: %s" % path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:
        warnings.warn("Datastore config ignored: failed loading %s (%s)" % (path, exc))
        return {}

    datastore = data.get("datastore")
    if not isinstance(datastore, dict):
        warnings.warn(
            "Datastore config ignored: missing top-level 'datastore' mapping in: %s" % path
        )
        return {}

    section = datastore.get(backend_name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        warnings.warn(
            "Datastore config ignored: 'datastore.%s' is not a mapping in: %s"
            % (backend_name, path)
        )
        return {}

    return dict(section)


def resolve_datastore_options(config, backend_name: str) -> Dict[str, Any]:
    """Resolve backend connection options from the datastore YAML and CLI.

    Precedence (lowest to highest):

    1. Values from the datastore YAML file, under ``datastore.<backend_name>``.
       The file given by ``--datastore-config`` is used, or the bundled
       ``datastores/default.yaml`` when the option is not set.
    2. Explicit CLI flags such as ``--mariadb-host`` / ``--postgres-port``.

    :param config: Pytest config object.
    :param backend_name: Backend selector string.
    :return: Mapping with resolved connection settings.
    """

    normalized = (backend_name or "mariadb").strip().lower()
    resolved: Dict[str, Any] = {}

    yaml_path = config.getoption("--datastore-config", default=None) or _default_datastore_config_path()
    for key, value in _load_datastore_yaml(yaml_path, normalized).items():
        if key in CONNECTION_KEYS and value is not None:
            resolved[key] = value

    for key in CONNECTION_KEYS:
        cli_value = config.getoption("--%s-%s" % (normalized, key), default=None)
        if cli_value is not None:
            resolved[key] = cli_value

    if resolved.get("port") is not None:
        try:
            resolved["port"] = int(resolved["port"])
        except (TypeError, ValueError):
            warnings.warn(
                "Invalid port %r for backend '%s'; ignoring" % (resolved["port"], normalized)
            )
            resolved.pop("port", None)

    return resolved


class AbstractStorageBackend(ABC):
    """Contract that all storage backend adapters must implement.

    Backend implementations should remain stateless with respect to pytest
    internals. They receive only normalized model objects.
    """

    @abstractmethod
    def initialize(self, config) -> None:
        """Initialize backend runtime state.

        Typical responsibilities:

        - Read backend-specific options from pytest config.
        - Open network/database clients.
        - Perform lightweight connection checks.

        :param config: Pytest config object.
        """

    @abstractmethod
    def session_start(self, run_summary: TestRunSummary) -> None:
        """Persist session start metadata.

        :param run_summary: Session-level metadata for the current run.
        """

    @abstractmethod
    def save_results(self, run_id: str, results: List[TestResultRecord]) -> None:
        """Persist a batch of test result records.

        :param run_id: Run identifier associated with all records.
        :param results: Normalized result records accumulated by the reporter.
        """

    @abstractmethod
    def session_finish(self, run_id: str, counters: Dict[str, Any]) -> None:
        """Persist run counters and finish backend resources.

        :param run_id: Run identifier.
        :param counters: Aggregated counts and timestamps.
        """
