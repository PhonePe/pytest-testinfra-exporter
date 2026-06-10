from abc import ABC, abstractmethod
from typing import Dict, List, Any

from .models import TestResultRecord, TestRunSummary


class AbstractStorageBackend(ABC):
    @abstractmethod
    def initialize(self, config) -> None:
        pass

    @abstractmethod
    def session_start(self, run_summary: TestRunSummary) -> None:
        pass

    @abstractmethod
    def save_results(self, run_id: str, results: List[TestResultRecord]) -> None:
        pass

    @abstractmethod
    def session_finish(self, run_id: str, counters: Dict[str, Any]) -> None:
        pass
