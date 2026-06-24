-- 019_geo_structured_conditions.sql
--
-- Structured geo-restriction model (see docs/GEO_REFACTOR_SCOPE.md).
--
-- Replaces free-text parsing of `restriction_detail` with explicit, typed
-- predicates so the eligibility engine blocks a program ONLY when the actual
-- condition (sub-location AND occupancy AND any other qualifier) is met — and
-- never on a dimension the app cannot evaluate (channel / TRID / sub-prime).
--
-- New columns (NULLable so the table works before the backfill runs; the engine
-- falls back to the legacy text matcher for any row whose `effect` is NULL):
--   effect        — ineligible | cap | note | eligible_state   (authoritative)
--   effect_value  — JSON cap payload when effect='cap'
--                   e.g. {"ltv":{"purchase":75,"refinance":70},
--                         "cltv":{...}, "hcltv":80,
--                         "loan_amount":2000000, "min_fico":720,
--                         "max_term_years":15}
--   conditions    — JSON AND-of-predicates; absent key = "applies to all":
--                   {"location":{"kind":"city|county|zip|territory|state",
--                                "any":["paterson"]},
--                    "occupancy":["investment"],
--                    "purpose":["purchase","refinance"],
--                    "property_type":["two_to_four_family"],
--                    "product":["dscr"], "lien":["second_lien"],
--                    "borrower":{"citizenship":["non_permanent_resident"],
--                                "country_of_origin":["china"]},
--                    "unevaluable":["channel:wholesale","loan_program:trid"]}
--
-- Run AFTER 018. Re-runnable: column adds guarded; backfill is a separate
-- idempotent script (ingest/tools/backfill_geo_conditions.py).

ALTER TABLE map_geographic_restrictions
  ADD COLUMN effect       VARCHAR(24) NULL
    COMMENT 'ineligible | cap | note | eligible_state (authoritative; NULL = use legacy text matcher)'
    AFTER restriction_type,
  ADD COLUMN effect_value JSON NULL
    COMMENT 'cap payload when effect=cap (ltv/cltv/hcltv/loan_amount/min_fico/max_term_years; purpose-keyed allowed)'
    AFTER effect,
  ADD COLUMN conditions   JSON NULL
    COMMENT 'AND-of-predicates gating the effect; see docs/GEO_REFACTOR_SCOPE.md'
    AFTER effect_value;

-- Reconcile the type dictionary with values actually present in the live table.
-- (restriction_type is not FK-constrained; these rows are documentation only.)
INSERT INTO dim_geo_restriction_types (id, code, name, description) VALUES
  (5, 'others',        'Other / Channel',  'Channel- or process-scoped note (wholesale/retail/correspondent); never a hard block in-app'),
  (6, 'eligible_state','Eligible State',   'Positive state allowlist row; program ineligible in states NOT listed'),
  (7, 'note',          'Overlay Note',     'Informational caution surfaced on the program; never a hard block')
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description);
