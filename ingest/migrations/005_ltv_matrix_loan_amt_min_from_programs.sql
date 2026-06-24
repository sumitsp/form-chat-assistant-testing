-- ============================================================
-- Migration 005 — ltv_matrix.loan_amt_min := programs.loan_amt_min
--
-- Copies program-level minimum loan amount onto every matrix row
-- for the same program_id. Safe to re-run (idempotent).
--
-- Requires programs.program_id (if your PK is programs.id only, use
-- sync_ltv_matrix_loan_amt_min.py instead — it auto-detects the join key).
--
--   mysql -u root -p newpoint_mortgage_dev < ingest/migrations/005_ltv_matrix_loan_amt_min_from_programs.sql
-- ============================================================

UPDATE ltv_matrix m
INNER JOIN programs p ON p.program_id = m.program_id
SET m.loan_amt_min = p.loan_amt_min;

-- Optional: rows whose matrix min still differs from program (should be 0)
-- SELECT m.id, m.program_id, m.loan_amt_min AS matrix_min, p.loan_amt_min AS program_min
-- FROM ltv_matrix m
-- INNER JOIN programs p ON p.program_id = m.program_id
-- WHERE m.loan_amt_min <> p.loan_amt_min
-- LIMIT 50;

-- Optional: matrix rows with no matching program (orphans)
-- SELECT m.id, m.program_id, m.loan_amt_min
-- FROM ltv_matrix m
-- LEFT JOIN programs p ON p.program_id = m.program_id
-- WHERE p.program_id IS NULL
-- LIMIT 50;
