SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS test_result_markers;
DROP TABLE IF EXISTS test_results;
DROP TABLE IF EXISTS test_runs;
DROP TABLE IF EXISTS tests;
DROP TABLE IF EXISTS hosts;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS hosts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  host_name VARCHAR(255) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  first_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  last_seen DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_hosts_host_name (host_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tests (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  test_uid CHAR(40) NOT NULL,
  canonical_nodeid VARCHAR(255) NOT NULL,
  test_name VARCHAR(255) NOT NULL,
  test_suite VARCHAR(1024) NULL,
  test_class VARCHAR(255) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tests_test_uid (test_uid),
  KEY idx_tests_canonical_nodeid (canonical_nodeid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS test_runs (
  run_id CHAR(36) NOT NULL,
  run_name VARCHAR(255) NOT NULL,
  trigger_source VARCHAR(64) NOT NULL DEFAULT 'local',
  suite_version VARCHAR(255) NULL,
  started_at DATETIME(6) NOT NULL,
  finished_at DATETIME(6) NULL,
  total_tests INT NOT NULL DEFAULT 0,
  passed_count INT NOT NULL DEFAULT 0,
  failed_count INT NOT NULL DEFAULT 0,
  skipped_count INT NOT NULL DEFAULT 0,
  errored_count INT NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id),
  KEY idx_test_runs_run_name (run_name),
  KEY idx_test_runs_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS test_results (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  run_id CHAR(36) NOT NULL,
  host_id BIGINT UNSIGNED NOT NULL,
  test_id BIGINT UNSIGNED NOT NULL,
  status ENUM('pass', 'fail', 'skipped', 'error', 'xfail', 'xpass') NOT NULL,
  failure_tag VARCHAR(255) NULL,
  duration_ms INT UNSIGNED NOT NULL DEFAULT 0,
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  error_type VARCHAR(128) NULL,
  error_message TEXT NULL,
  full_trace LONGTEXT NULL,
  captured_log LONGTEXT NULL,
  captured_stdout LONGTEXT NULL,
  captured_stderr LONGTEXT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_test_results_run_host_test (run_id, host_id, test_id),
  KEY idx_test_results_run_host (run_id, host_id),
  KEY idx_test_results_host_finished (host_id, finished_at),
  KEY idx_test_results_host_status_finished (host_id, status, finished_at),
  KEY idx_test_results_test_finished (test_id, finished_at),
  KEY idx_test_results_status_finished (status, finished_at),
  KEY idx_test_results_failure_tag_finished (failure_tag, finished_at),
  CONSTRAINT fk_test_results_run FOREIGN KEY (run_id) REFERENCES test_runs(run_id) ON DELETE CASCADE,
  CONSTRAINT fk_test_results_host FOREIGN KEY (host_id) REFERENCES hosts(id),
  CONSTRAINT fk_test_results_test FOREIGN KEY (test_id) REFERENCES tests(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS test_result_markers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  test_result_id BIGINT UNSIGNED NOT NULL,
  marker_name VARCHAR(128) NOT NULL,
  marker_value VARCHAR(512) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_result_marker_name_value (test_result_id, marker_name, marker_value),
  KEY idx_result_markers_name_value (marker_name, marker_value),
  KEY idx_result_markers_name (marker_name),
  CONSTRAINT fk_result_markers_result FOREIGN KEY (test_result_id) REFERENCES test_results(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
