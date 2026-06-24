-- ============================================================
-- Migration 003 — ltv_matrix.max_cltv := max_ltv
--
-- Backfill existing rows so combined LTV cap matches the matrix LTV cap.
-- Safe to re-run (idempotent).
--
--   mysql -u root -p newpoint_mortgage < ingest/migrations/003_ltv_matrix_max_cltv_from_max_ltv.sql
-- ============================================================

UPDATE ltv_matrix
SET max_cltv = max_ltv;

-- Optional: verify mismatches (should return 0 rows)
-- SELECT id, program_id, loan_purpose, max_ltv, max_cltv
-- FROM ltv_matrix
-- WHERE (max_ltv IS NULL AND max_cltv IS NOT NULL)
--    OR (max_ltv IS NOT NULL AND (max_cltv IS NULL OR max_cltv <> max_ltv))
-- LIMIT 50;
