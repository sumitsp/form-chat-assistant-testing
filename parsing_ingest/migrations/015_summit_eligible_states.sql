-- Eligible-state allowlists (restriction_type = eligible_state).
-- ONLY the states below may originate loans — all other US states/territories are blocked
-- in Layer 5 before county/city overlays.
--
-- Applies to all three lenders (Denali, Summit, Everest).

DELETE FROM map_geographic_restrictions
WHERE restriction_type = 'eligible_state'
  AND lender_id IN (1, 2, 3);

INSERT INTO map_geographic_restrictions
  (lender_id, program_id, state, restriction_type, restriction_detail)
SELECT p.lender_id, p.program_id, s.state, 'eligible_state', s.restriction_detail
FROM dim_programs p
CROSS JOIN (
  SELECT 'AZ' AS state, 'Eligible lending state' AS restriction_detail
  UNION ALL SELECT 'CA', 'Eligible lending state'
  UNION ALL SELECT 'CO', 'Eligible lending state'
  UNION ALL SELECT 'DE', 'Eligible lending state'
  UNION ALL SELECT 'DC', 'Eligible lending state'
  UNION ALL SELECT 'FL', 'Eligible lending state'
  UNION ALL SELECT 'GA', 'Eligible lending state'
  UNION ALL SELECT 'IL', 'Eligible lending state'
  UNION ALL SELECT 'MD', 'Eligible lending state'
  UNION ALL SELECT 'NV', 'Eligible lending state'
  UNION ALL SELECT 'NJ', 'Eligible lending state'
  UNION ALL SELECT 'NC', 'Eligible lending state'
  UNION ALL SELECT 'PA', 'Eligible lending state'
  UNION ALL SELECT 'TN', 'Eligible lending state'
  UNION ALL SELECT 'TX', 'Eligible lending state'
  UNION ALL SELECT 'VA', 'Eligible lending state'
) s
WHERE p.lender_id IN (1, 2, 3);
