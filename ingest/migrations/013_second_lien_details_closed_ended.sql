-- ============================================================
-- Migration — dim_programs.second_lien_details from CSV
-- Source: dim_programs.csv
-- Tags: heloc, closed_ended, piggyback
-- ============================================================

ALTER TABLE dim_programs
  MODIFY COLUMN second_lien_details JSON NULL
  COMMENT 'Second-lien structure: heloc, closed_ended, piggyback (JSON array)';

UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 1;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 2;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 3;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 4;
UPDATE dim_programs SET second_lien_details = CAST('["closed_ended", "piggyback"]' AS JSON) WHERE program_id = 5;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 6;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 7;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 8;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 9;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 10;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 11;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 12;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 13;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 14;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 15;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 16;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 17;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 18;
UPDATE dim_programs SET second_lien_details = CAST('["closed_ended", "piggyback"]' AS JSON) WHERE program_id = 19;
UPDATE dim_programs SET second_lien_details = CAST('["heloc"]' AS JSON) WHERE program_id = 20;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 21;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 22;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 23;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 24;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 25;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 26;
UPDATE dim_programs SET second_lien_details = CAST('["closed_ended"]' AS JSON) WHERE program_id = 27;
UPDATE dim_programs SET second_lien_details = CAST('["closed_ended"]' AS JSON) WHERE program_id = 28;
UPDATE dim_programs SET second_lien_details = CAST('["closed_ended"]' AS JSON) WHERE program_id = 29;
UPDATE dim_programs SET second_lien_details = NULL WHERE program_id = 30;

-- Verify:
-- SELECT program_id, program_code, is_second_lien, second_lien_details FROM dim_programs ORDER BY program_id;
