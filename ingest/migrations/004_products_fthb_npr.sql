-- ============================================================
-- Migration 004 — products, fthb_eligibility, programs.is_npr_program
--
-- Load row data from repo-root CSVs after running:
--   python ingest/tools/load_products_fthb.py
-- ============================================================

ALTER TABLE programs
  ADD COLUMN is_npr_program TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '1 = Non-Permanent Resident Alien program'
  AFTER is_itin_program;

CREATE TABLE IF NOT EXISTS fthb_eligibility (
  program_id           SMALLINT UNSIGNED NOT NULL PRIMARY KEY,
  program_name         VARCHAR(120) NOT NULL,
  is_fthb_eligible     TINYINT(1) NOT NULL DEFAULT 0,
  fthb_max_loan_cap    INT UNSIGNED NULL,
  updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS products (
  product_id           INT UNSIGNED NOT NULL PRIMARY KEY,
  program_id           SMALLINT UNSIGNED NOT NULL,
  program_name         VARCHAR(120) NOT NULL,
  product_name         VARCHAR(120) NOT NULL,
  io_flag              TINYINT(1) NOT NULL DEFAULT 0,
  is_fthb_eligible     TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_products_program (program_id),
  INDEX idx_products_fthb (program_id, is_fthb_eligible)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
