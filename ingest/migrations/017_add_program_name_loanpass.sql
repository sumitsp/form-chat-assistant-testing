-- ============================================================
-- Migration — dim_programs.program_name_loanpass from CSV
-- Source: dim_programs.csv
-- LoanPASS product display name for pricing API matching
-- ============================================================

ALTER TABLE dim_programs
  ADD COLUMN program_name_loanpass VARCHAR(150) NULL
  COMMENT 'LoanPASS product name for execute-summary matching'
  AFTER program_name_np;

UPDATE dim_programs SET program_name_loanpass = 'Denali Prime Plus' WHERE program_id = 1;
UPDATE dim_programs SET program_name_loanpass = 'Denali Prime' WHERE program_id = 2;
UPDATE dim_programs SET program_name_loanpass = 'Denali ITIN' WHERE program_id = 3;
UPDATE dim_programs SET program_name_loanpass = 'Denali Super Jumbo' WHERE program_id = 4;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 5;
UPDATE dim_programs SET program_name_loanpass = 'Denali DSCR Prime Plus' WHERE program_id = 6;
UPDATE dim_programs SET program_name_loanpass = 'Denali Investor DSCR' WHERE program_id = 7;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 8;
UPDATE dim_programs SET program_name_loanpass = 'Denali DSCR Multi' WHERE program_id = 9;
UPDATE dim_programs SET program_name_loanpass = 'Denali Foreign National' WHERE program_id = 10;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 11;
UPDATE dim_programs SET program_name_loanpass = 'Summit Prime' WHERE program_id = 12;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 13;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 14;
UPDATE dim_programs SET program_name_loanpass = 'Summit DSCR' WHERE program_id = 15;
UPDATE dim_programs SET program_name_loanpass = 'Summit DSCR Multi' WHERE program_id = 16;
UPDATE dim_programs SET program_name_loanpass = 'Summit DSCR CC' WHERE program_id = 17;
UPDATE dim_programs SET program_name_loanpass = 'Summit Foreign National DSCR' WHERE program_id = 18;
UPDATE dim_programs SET program_name_loanpass = 'Summit Closed End Second' WHERE program_id = 19;
UPDATE dim_programs SET program_name_loanpass = 'Summit HELOC' WHERE program_id = 20;
UPDATE dim_programs SET program_name_loanpass = 'Everest DSCR' WHERE program_id = 21;
UPDATE dim_programs SET program_name_loanpass = 'Everest DSCR' WHERE program_id = 22;
UPDATE dim_programs SET program_name_loanpass = 'Everest Prime' WHERE program_id = 23;
UPDATE dim_programs SET program_name_loanpass = 'Everest Standard' WHERE program_id = 24;
UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = 25;
UPDATE dim_programs SET program_name_loanpass = 'Everest Expanded Prime Super Jumbo' WHERE program_id = 26;
UPDATE dim_programs SET program_name_loanpass = 'Everest 2nd Lien' WHERE program_id = 27;
UPDATE dim_programs SET program_name_loanpass = 'Everest 2nd Lien Plus' WHERE program_id = 28;
UPDATE dim_programs SET program_name_loanpass = 'Everest DSCR 2nd Lien' WHERE program_id = 29;
UPDATE dim_programs SET program_name_loanpass = 'Everest DSCR 5-9 Unit' WHERE program_id = 30;

-- Verify:
-- SELECT program_id, program_code, program_name_np, program_name_loanpass FROM dim_programs ORDER BY program_id;
