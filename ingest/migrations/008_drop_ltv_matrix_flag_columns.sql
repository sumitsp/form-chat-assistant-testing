-- ============================================================
-- Migration 008 — drop unused ltv_matrix flag / ordering columns
--
--   mysql -u root -p newpoint_mortgage_dev < ingest/migrations/008_drop_ltv_matrix_flag_columns.sql
-- ============================================================

ALTER TABLE ltv_matrix
  DROP COLUMN is_io,
  DROP COLUMN is_str,
  DROP COLUMN is_fthb,
  DROP COLUMN state_override,
  DROP COLUMN sort_order;
