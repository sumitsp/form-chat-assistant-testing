-- =============================================================================
-- cleanup_programs.sql
-- Keeps ONLY the 30 approved programs (10 per lender).
-- Run in MySQL Workbench against the `newpoint_mortgage` database.
-- =============================================================================

USE newpoint_mortgage;

-- ---------------------------------------------------------------------------
-- STEP 1 — Preview: see what is currently stored vs. approved list.
--          Run this SELECT first so you can verify / correct any name mismatches
--          before the DELETEs.
-- ---------------------------------------------------------------------------
SELECT
    l.code                 AS lender_code,
    p.id                   AS program_id,
    p.name                 AS program_name,
    (SELECT COUNT(*) FROM ltv_matrix     WHERE program_id = p.id) AS matrix_rows,
    (SELECT COUNT(*) FROM program_requirements WHERE program_id = p.id) AS req_rows
FROM programs p
JOIN lenders l ON l.id = p.lender_id
ORDER BY l.code, p.name;


-- ---------------------------------------------------------------------------
-- STEP 2 — Build the approved name list.
--          Edit any names below to match what STEP 1 showed if they differ.
-- ---------------------------------------------------------------------------

-- Helper: programs to KEEP (used in every DELETE subquery)
--
-- DENALI  → code = 'denali'
-- EVEREST → code = 'everest'
-- SUMMIT  → code = 'summit'

-- Approved program names exactly as in nonqm_programs.csv:
--   DENALI:  Flex Supreme | Flex Select | Select ITIN | Super Jumbo |
--            Second Lien Select | DSCR Supreme | Investor DSCR |
--            Investor DSCR No Ratio | DSCR Multi (5-8 Units) | Foreign National
--
--   EVEREST: Expanded Prime | Expanded Prime Super Jumbo | Non-Prime | DSCR |
--            DSCR – BPL | ITIN | DSCR 5-9 Unit | Equity Advantage |
--            Equity Advantage Elite | Equity Advantage DSCR
--
--   SUMMIT:  Prime Ascent Plus | Prime Ascent | ITIN |
--            Investor Solutions – DSCR Plus | Investor Solutions – DSCR |
--            Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use) |
--            Cross Collateral DSCR | Foreign National DSCR |
--            Closed End Second | HELOC


-- ---------------------------------------------------------------------------
-- STEP 3 — Delete child-table rows for programs NOT in the approved list.
--          (No CASCADE on FKs → must clear children before the parent.)
-- ---------------------------------------------------------------------------

SET @del_ids = NULL;   -- marker; the subquery is repeated in each statement

-- 3-A  ltv_matrix
DELETE FROM ltv_matrix
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-B  program_requirements
DELETE FROM program_requirements
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-C  reserve_requirements
DELETE FROM reserve_requirements
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-D  credit_event_seasoning
DELETE FROM credit_event_seasoning
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-E  dscr_requirements
DELETE FROM dscr_requirements
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-F  io_features
DELETE FROM io_features
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-G  geographic_restrictions
DELETE FROM geographic_restrictions
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-H  overlay_rules
DELETE FROM overlay_rules
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-I  program_product_types
DELETE FROM program_product_types
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-J  program_borrower_types
DELETE FROM program_borrower_types
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);

-- 3-K  program_property_types
DELETE FROM program_property_types
WHERE program_id IN (
    SELECT p.id FROM programs p JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);


-- ---------------------------------------------------------------------------
-- STEP 4 — Delete the programs themselves (now that children are gone)
-- ---------------------------------------------------------------------------
DELETE FROM programs
WHERE id IN (
    SELECT p.id FROM (SELECT * FROM programs) p
    JOIN lenders l ON l.id = p.lender_id
    WHERE NOT (
        (l.code = 'denali'  AND p.name IN (
            'Flex Supreme','Flex Select','Select ITIN','Super Jumbo',
            'Second Lien Select','DSCR Supreme','Investor DSCR',
            'Investor DSCR No Ratio','DSCR Multi (5-8 Units)','Foreign National'))
        OR (l.code = 'everest' AND p.name IN (
            'Expanded Prime','Expanded Prime Super Jumbo','Non-Prime','DSCR',
            'DSCR – BPL','ITIN','DSCR 5-9 Unit','Equity Advantage',
            'Equity Advantage Elite','Equity Advantage DSCR'))
        OR (l.code = 'summit' AND p.name IN (
            'Prime Ascent Plus','Prime Ascent','ITIN',
            'Investor Solutions – DSCR Plus','Investor Solutions – DSCR',
            'Investor Solutions – DSCR (5-8 Unit / 2-8 Mixed Use)',
            'Cross Collateral DSCR','Foreign National DSCR',
            'Closed End Second','HELOC'))
    )
);


-- ---------------------------------------------------------------------------
-- STEP 5 — Verify: should show exactly 30 rows (10 per lender)
-- ---------------------------------------------------------------------------
SELECT l.code AS lender, COUNT(*) AS remaining_programs
FROM programs p
JOIN lenders l ON l.id = p.lender_id
GROUP BY l.code;

SELECT l.code, p.name
FROM programs p
JOIN lenders l ON l.id = p.lender_id
ORDER BY l.code, p.name;
