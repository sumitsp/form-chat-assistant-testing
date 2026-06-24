-- ============================================================
--  Migration 011 — Broker / client fields on saved scenarios
-- ============================================================

ALTER TABLE form_history_scenario
    ADD COLUMN broker_name   VARCHAR(200) NOT NULL DEFAULT '' AFTER session_id,
    ADD COLUMN client_name   VARCHAR(200) NOT NULL DEFAULT '' AFTER broker_name,
    ADD COLUMN client_phone  VARCHAR(40)   DEFAULT NULL AFTER client_name,
    ADD COLUMN client_email  VARCHAR(255)  DEFAULT NULL AFTER client_phone,
    ADD INDEX idx_fhs_client_name (client_name),
    ADD INDEX idx_fhs_broker_name (broker_name);
