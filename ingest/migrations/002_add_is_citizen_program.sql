-- ============================================================
-- Migration 002 — programs.is_citizen_program
--
-- Citizen-oriented programs (US Citizen, PRA, DACA, etc.) = 1
-- Dedicated Foreign National or ITIN programs = 0
--
-- Run once against newpoint_mortgage (or your MYSQL_DATABASE):
--   mysql -u root -p newpoint_mortgage < ingest/migrations/002_add_is_citizen_program.sql
-- ============================================================

-- Add column (omit this block if you already added it and only need the UPDATE)
ALTER TABLE programs
  ADD COLUMN is_citizen_program TINYINT(1) NOT NULL DEFAULT 1
    COMMENT '1 = general citizen/PRA/DACA-style; 0 = ITIN-only or Foreign National program'
  AFTER is_itin_program;

-- ITIN-only and Foreign National programs are not "citizen" programs
UPDATE programs
SET is_citizen_program = 0
WHERE is_foreign_national = 1
   OR is_itin_program = 1;

-- Everything else stays / becomes 1
UPDATE programs
SET is_citizen_program = 1
WHERE is_foreign_national = 0
  AND is_itin_program = 0;

-- Optional: verify
-- SELECT program_name, is_foreign_national, is_itin_program, is_citizen_program
-- FROM programs
-- ORDER BY is_citizen_program, program_name;
