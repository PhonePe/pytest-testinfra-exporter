-- PostgreSQL schema for pytest-mariadb-reporter storage backend
-- Drops existing reporter tables before recreating schema.

DROP TABLE IF EXISTS test_result_markers;
DROP TABLE IF EXISTS test_results;
DROP TABLE IF EXISTS test_runs;
DROP TABLE IF EXISTS tests;
DROP TABLE IF EXISTS hosts;

CREATE TABLE IF NOT EXISTS hosts (
  id BIGSERIAL PRIMARY KEY,
  host_name VARCHAR(255) NOT NULL UNIQUE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  first_seen TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tests (
  id BIGSERIAL PRIMARY KEY,
  test_uid CHAR(40) NOT NULL UNIQUE,
  canonical_nodeid VARCHAR(255) NOT NULL,
  test_name VARCHAR(255) NOT NULL,
  test_suite VARCHAR(1024) NULL,
  test_class VARCHAR(255) NULL
);

CREATE INDEX IF NOT EXISTS idx_tests_canonical_nodeid
ON tests (canonical_nodeid);

CREATE TABLE IF NOT EXISTS test_runs (
  run_id CHAR(36) PRIMARY KEY,
  run_name VARCHAR(255) NOT NULL,
  trigger_source VARCHAR(64) NOT NULL DEFAULT 'local',
  suite_version VARCHAR(255) NULL,
  started_at TIMESTAMP(6) WITHOUT TIME ZONE NOT NULL,
  finished_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
  total_tests INT NOT NULL DEFAULT 0,
  passed_count INT NOT NULL DEFAULT 0,
  failed_count INT NOT NULL DEFAULT 0,
  skipped_count INT NOT NULL DEFAULT 0,
  errored_count INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_test_runs_started
ON test_runs (started_at);

CREATE INDEX IF NOT EXISTS idx_test_runs_run_name
ON test_runs (run_name);

CREATE TABLE IF NOT EXISTS test_results (
  id BIGSERIAL PRIMARY KEY,
  run_id CHAR(36) NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
  host_id BIGINT NOT NULL REFERENCES hosts(id),
  test_id BIGINT NOT NULL REFERENCES tests(id),
  status VARCHAR(16) NOT NULL,
  failure_tag VARCHAR(255) NULL,
  duration_ms INT NOT NULL DEFAULT 0,
  started_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
  finished_at TIMESTAMP(6) WITHOUT TIME ZONE NULL,
  error_type VARCHAR(128) NULL,
  error_message TEXT NULL,
  full_trace TEXT NULL,
  captured_log TEXT NULL,
  captured_stdout TEXT NULL,
  captured_stderr TEXT NULL,
  CONSTRAINT uq_test_results_run_host_test UNIQUE (run_id, host_id, test_id),
  CONSTRAINT chk_test_results_status CHECK (status IN ('pass', 'fail', 'skipped', 'error', 'xfail', 'xpass'))
);

CREATE INDEX IF NOT EXISTS idx_test_results_run_host
ON test_results (run_id, host_id);

CREATE INDEX IF NOT EXISTS idx_test_results_host_finished
ON test_results (host_id, finished_at);

CREATE INDEX IF NOT EXISTS idx_test_results_host_status_finished
ON test_results (host_id, status, finished_at);

CREATE INDEX IF NOT EXISTS idx_test_results_test_finished
ON test_results (test_id, finished_at);

CREATE INDEX IF NOT EXISTS idx_test_results_status_finished
ON test_results (status, finished_at);

CREATE INDEX IF NOT EXISTS idx_test_results_failure_tag_finished
ON test_results (failure_tag, finished_at);

CREATE TABLE IF NOT EXISTS test_result_markers (
  id BIGSERIAL PRIMARY KEY,
  test_result_id BIGINT NOT NULL REFERENCES test_results(id) ON DELETE CASCADE,
  marker_name VARCHAR(128) NOT NULL,
  marker_value VARCHAR(512) NULL,
  CONSTRAINT uq_result_marker_name_value UNIQUE (test_result_id, marker_name, marker_value)
);

CREATE INDEX IF NOT EXISTS idx_result_markers_name_value
ON test_result_markers (marker_name, marker_value);

CREATE INDEX IF NOT EXISTS idx_result_markers_name
ON test_result_markers (marker_name);
