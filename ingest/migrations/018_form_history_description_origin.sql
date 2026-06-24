-- ============================================================
--  Migration 018 — Scenario description + intake origin
--  Run against newpoint_mortgage (or _dev).
-- ============================================================

ALTER TABLE form_history_scenario
    ADD COLUMN scenario_description VARCHAR(50) DEFAULT NULL
        COMMENT 'User-edited label from Save Scenario dialog'
        AFTER client_email,
    ADD COLUMN origin VARCHAR(10) NOT NULL DEFAULT 'form'
        COMMENT 'Intake mode when saved: form | chat'
        AFTER scenario_description;

-- Backfill description from legacy JSON key inside form_fields.
UPDATE form_history_scenario
SET scenario_description = LEFT(
        TRIM(JSON_UNQUOTE(JSON_EXTRACT(form_fields, '$._vaultScenarioDescription'))),
        50
    )
WHERE scenario_description IS NULL
  AND JSON_EXTRACT(form_fields, '$._vaultScenarioDescription') IS NOT NULL
  AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(form_fields, '$._vaultScenarioDescription'))) <> '';
