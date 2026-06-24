-- ============================================================
--  Migration 015 — Archive flag + tags on saved scenarios
-- ============================================================
--  is_archived: soft archive flag for the Scenario Vault filter
--               ("All except archived" / "Archived" / "All").
--  tags:        free-form, space/comma-separated hashtags shown as
--               chips and included in vault search.
-- ============================================================

ALTER TABLE form_history_scenario
    ADD COLUMN is_archived TINYINT(1) NOT NULL DEFAULT 0 AFTER programs_matched,
    ADD COLUMN tags        VARCHAR(500) DEFAULT NULL AFTER is_archived,
    ADD INDEX idx_fhs_is_archived (is_archived);
