"""Data models used by the pytest storage reporter.

This module contains plain dataclasses that decouple pytest hook objects from
backend persistence implementations.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MarkerDef:
    """Represents a normalized pytest marker.

    :param name: Marker name, for example ``component``.
    :param value: Marker value (argument/kwarg), or ``None``.
    """

    name: str
    value: Optional[str] = None


@dataclass
class TestResultRecord:
    """Represents one completed test result to be persisted.

    :param nodeid: Full pytest node id.
    :param test_uid: Stable SHA-1 test identity fingerprint.
    :param canonical_nodeid: Node id without parametrization suffix.
    :param test_name: Normalized display name for the test.
    :param test_suite: Test module path.
    :param test_class: Test class name if available.
    :param host_name: Resolved testinfra target host.
    :param status: Final status string (``pass``, ``fail``, etc.).
    :param failure_tag: Failure classification tag if any.
    :param duration_ms: Total duration in milliseconds.
    :param started_at: Earliest phase start time (naive IST).
    :param finished_at: Latest phase stop time (naive IST).
    :param error_type: Error category derived from pytest report.
    :param error_message: Condensed failure text.
    :param full_trace: Merged traceback across phases.
    :param captured_log: Captured logs across phases.
    :param captured_stdout: Captured stdout across phases.
    :param captured_stderr: Captured stderr across phases.
    :param markers: Normalized marker list.
    """

    nodeid: str
    test_uid: str
    canonical_nodeid: str
    test_name: str
    test_suite: Optional[str]
    test_class: Optional[str]
    host_name: str
    status: str
    failure_tag: Optional[str] = None
    duration_ms: int = 0
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    full_trace: Optional[str] = None
    captured_log: Optional[str] = None
    captured_stdout: Optional[str] = None
    captured_stderr: Optional[str] = None
    markers: List[MarkerDef] = field(default_factory=list)


@dataclass
class TestRunSummary:
    """Represents lifecycle metadata for a pytest session.

    :param run_id: UUID string for the run.
    :param run_name: Human-readable name for the run.
    :param trigger_source: Trigger host/FQDN.
    :param suite_version: Optional suite version (for example git SHA).
    :param started_at: Session start timestamp (naive IST).
    :param finished_at: Session finish timestamp (naive IST).
    :param total_tests: Total processed tests.
    :param passed_count: Number of passing tests.
    :param failed_count: Number of failed tests.
    :param skipped_count: Number of skipped tests.
    :param errored_count: Number of errored tests.
    """

    run_id: str
    run_name: str
    trigger_source: str
    suite_version: Optional[str]
    started_at: dt.datetime
    finished_at: Optional[dt.datetime] = None
    total_tests: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errored_count: int = 0
