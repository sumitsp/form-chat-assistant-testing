-- 020_dscr_eligible_states.sql
--
-- NewPoint's licensing footprint is BROADER for DSCR scenarios than for
-- standard (non-DSCR) documentation. Step 1 of the geo layer
-- (_eligible_state_set in backend/eligibility.py) picks the allowlist by the
-- scenario doc type:
--   * standard docs -> restriction_detail = 'Eligible lending state'        (16 states)
--   * DSCR scenario -> restriction_detail = 'Eligible lending state (DSCR)' (39 states)
--
-- The DSCR list is NOT a strict superset of the standard list (AZ/NC/NV are
-- standard-only and absent from DSCR), so it is stored as its own marker.
--
-- map_geographic_restrictions.program_id / lender_id are NOT NULL FKs, so we
-- mirror the existing per-program 'eligible_state' rows and attach one row per
-- (program x DSCR-eligible state). _eligible_state_set() reads DISTINCT state,
-- so per-program duplication is harmless. Idempotent via NOT EXISTS.

INSERT INTO map_geographic_restrictions
    (lender_id, program_id, state, restriction_type, effect, restriction_detail)
SELECT p.lender_id, p.program_id, s.code,
       'eligible_state', 'eligible_state', 'Eligible lending state (DSCR)'
FROM dim_programs p
CROSS JOIN (
    SELECT 'FL' AS code UNION ALL SELECT 'VA' UNION ALL SELECT 'CA' UNION ALL
    SELECT 'NE' UNION ALL SELECT 'IL' UNION ALL SELECT 'MI' UNION ALL
    SELECT 'NY' UNION ALL SELECT 'NJ' UNION ALL SELECT 'AL' UNION ALL
    SELECT 'CO' UNION ALL SELECT 'WA' UNION ALL SELECT 'MT' UNION ALL
    SELECT 'WY' UNION ALL SELECT 'KS' UNION ALL SELECT 'OK' UNION ALL
    SELECT 'TX' UNION ALL SELECT 'IA' UNION ALL SELECT 'MO' UNION ALL
    SELECT 'AR' UNION ALL SELECT 'LA' UNION ALL SELECT 'MS' UNION ALL
    SELECT 'GA' UNION ALL SELECT 'SC' UNION ALL SELECT 'TN' UNION ALL
    SELECT 'KY' UNION ALL SELECT 'IN' UNION ALL SELECT 'OH' UNION ALL
    SELECT 'PA' UNION ALL SELECT 'MD' UNION ALL SELECT 'DC' UNION ALL
    SELECT 'WI' UNION ALL SELECT 'CT' UNION ALL SELECT 'RI' UNION ALL
    SELECT 'NH' UNION ALL SELECT 'ME' UNION ALL SELECT 'MA' UNION ALL
    SELECT 'HI' UNION ALL SELECT 'DE' UNION ALL SELECT 'ID'
) s
WHERE NOT EXISTS (
    SELECT 1 FROM map_geographic_restrictions m
    WHERE m.program_id = p.program_id
      AND m.state = s.code
      AND m.restriction_type = 'eligible_state'
      AND m.restriction_detail = 'Eligible lending state (DSCR)'
);
