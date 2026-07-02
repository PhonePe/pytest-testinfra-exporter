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

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import TestResultRecord, TestRunSummary


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
