-- ============================================================
-- Migration 007 — programs.loan_amt_max
-- Source: product owner matrix (program_id → max loan)
--
--   mysql -u root -p newpoint_mortgage_dev < ingest/migrations/007_update_programs_loan_amt_max.sql
-- ============================================================

UPDATE programs SET loan_amt_max = 3000000 WHERE program_id = 1;
UPDATE programs SET loan_amt_max = 3500000 WHERE program_id = 2;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 3;
UPDATE programs SET loan_amt_max = 5000000 WHERE program_id = 4;
UPDATE programs SET loan_amt_max = 1000000 WHERE program_id = 5;
UPDATE programs SET loan_amt_max = 2000000 WHERE program_id = 6;
UPDATE programs SET loan_amt_max = 3000000 WHERE program_id = 7;
UPDATE programs SET loan_amt_max = 1500000 WHERE program_id = 8;
UPDATE programs SET loan_amt_max = 3000000 WHERE program_id = 9;
UPDATE programs SET loan_amt_max = 3000000 WHERE program_id = 10;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 11;
UPDATE programs SET loan_amt_max = 4000000 WHERE program_id = 12;
UPDATE programs SET loan_amt_max = 1500000 WHERE program_id = 13;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 14;
UPDATE programs SET loan_amt_max = 3500000 WHERE program_id = 15;
UPDATE programs SET loan_amt_max = 3000000 WHERE program_id = 16;
UPDATE programs SET loan_amt_max = 4000000 WHERE program_id = 17;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 18;
UPDATE programs SET loan_amt_max = 750000 WHERE program_id = 19;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 21;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 22;
UPDATE programs SET loan_amt_max = 3500000 WHERE program_id = 23;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 24;
UPDATE programs SET loan_amt_max = 1500000 WHERE program_id = 25;
UPDATE programs SET loan_amt_max = 5000000 WHERE program_id = 26;
UPDATE programs SET loan_amt_max = 1000000 WHERE program_id = 27;
UPDATE programs SET loan_amt_max = 500000 WHERE program_id = 28;
UPDATE programs SET loan_amt_max = 500000 WHERE program_id = 29;
UPDATE programs SET loan_amt_max = 2500000 WHERE program_id = 30;
