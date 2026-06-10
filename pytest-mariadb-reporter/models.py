import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MarkerDef:
    name: str
    value: Optional[str] = None


@dataclass
class TestResultRecord:
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
    run_id: str
    trigger_source: str
    suite_version: Optional[str]
    started_at: dt.datetime
    finished_at: Optional[dt.datetime] = None
    total_tests: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    errored_count: int = 0
