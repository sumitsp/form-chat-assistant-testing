-- ============================================================
-- Migration 006 — programs.program_name + programs.doc_types_allowed
-- Source: programs_v2.csv (repo root)
--
--   mysql -u root -p newpoint_mortgage_dev < ingest/migrations/006_update_programs_name_doc_types_from_csv.sql
-- ============================================================

UPDATE programs SET program_name = 'Flex Supreme', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_2mo_bs","asset_util","1099","dscr_rental","non_traditional"]' WHERE program_id = 1;
UPDATE programs SET program_name = 'Flex Select', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_2mo_bs","asset_util","1099","wvoe","dscr_rental","non_traditional"]' WHERE program_id = 2;
UPDATE programs SET program_name = 'Select ITIN', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_2mo_bs","asset_util","1099","dscr_rental","itin","non_traditional"]' WHERE program_id = 3;
UPDATE programs SET program_name = 'Super Jumbo', doc_types_allowed = '["full_doc","bank_stmt_24","bank_stmt_business","pl_2mo_bs","asset_util","dscr_rental","non_traditional"]' WHERE program_id = 4;
UPDATE programs SET program_name = 'Second Lien Select', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_2mo_bs","asset_util","1099","dscr_rental"]' WHERE program_id = 5;
UPDATE programs SET program_name = 'DSCR Supreme', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 6;
UPDATE programs SET program_name = 'Investor DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 7;
UPDATE programs SET program_name = 'Investor DSCR No Ratio', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 8;
UPDATE programs SET program_name = 'DSCR Multi 5-8 Unit', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 9;
UPDATE programs SET program_name = 'Foreign National', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","asset_util","dscr_rental","non_traditional"]' WHERE program_id = 10;
UPDATE programs SET program_name = 'Prime Ascent Plus', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business"]' WHERE program_id = 11;
UPDATE programs SET program_name = 'Prime Ascent', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_only","pl_2mo_bs","wvoe","1099","asset_util"]' WHERE program_id = 12;
UPDATE programs SET program_name = 'ITIN', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","1099","wvoe","itin"]' WHERE program_id = 13;
UPDATE programs SET program_name = 'Investor DSCR Plus', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 14;
UPDATE programs SET program_name = 'Investor DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 15;
UPDATE programs SET program_name = 'Investor DSCR Multi', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 16;
UPDATE programs SET program_name = 'Cross Collateral DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 17;
UPDATE programs SET program_name = 'Foreign National DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 18;
UPDATE programs SET program_name = 'Closed End Second', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_24","bank_stmt_business","pl_only","pl_2mo_bs","wvoe","1099"]' WHERE program_id = 19;
UPDATE programs SET program_name = 'Foreign National DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 21;
UPDATE programs SET program_name = 'DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 22;
UPDATE programs SET program_name = 'Expanded Prime', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_business","1099","pl_only","asset_util"]' WHERE program_id = 23;
UPDATE programs SET program_name = 'Non-Prime', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_business","1099","pl_only"]' WHERE program_id = 24;
UPDATE programs SET program_name = 'ITIN', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_business","1099","pl_only","itin"]' WHERE program_id = 25;
UPDATE programs SET program_name = 'Expanded Prime Super Jumbo', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_business","1099"]' WHERE program_id = 26;
UPDATE programs SET program_name = 'Equity Advantage', doc_types_allowed = '["full_doc","bank_stmt_12","bank_stmt_business","pl_only"]' WHERE program_id = 27;
UPDATE programs SET program_name = 'Equity Advantage Elite', doc_types_allowed = '["full_doc"]' WHERE program_id = 28;
UPDATE programs SET program_name = 'Equity Advantage DSCR', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 29;
UPDATE programs SET program_name = 'DSCR Multi', doc_types_allowed = '["dscr_rental"]' WHERE program_id = 30;

-- Verify (optional):
-- SELECT program_id, program_code, program_name, doc_types_allowed FROM programs ORDER BY program_id;
