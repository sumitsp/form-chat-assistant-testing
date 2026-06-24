-- ============================================================
--  Migration 016 — Lifecycle status on saved scenarios
-- ============================================================
--  status: scenario lifecycle stage shown (and editable) in the
--          Scenario Vault. One of: draft | active | locked | closed
--          | archived. Replaces the earlier free-form `tags` concept
--          and supersedes the `is_archived` flag (archived is a status).
--          New scenarios default to 'draft'.
-- ============================================================

ALTER TABLE form_history_scenario
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft' AFTER programs_matched,
    ADD INDEX idx_fhs_status (status);
