-- ============================================================
--  Migration 010 — Saved scenario profiles (form + results)
--  Run against newpoint_mortgage (or _dev).
-- ============================================================

CREATE TABLE IF NOT EXISTS form_history_scenario (
    id                  INT           AUTO_INCREMENT PRIMARY KEY,
    session_id          VARCHAR(36)   DEFAULT NULL,
    broker_name         VARCHAR(200)  NOT NULL DEFAULT '',
    client_name         VARCHAR(200)  NOT NULL DEFAULT '',
    client_phone        VARCHAR(40)   DEFAULT NULL,
    client_email        VARCHAR(255)  DEFAULT NULL,
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    form_fields         JSON          NOT NULL COMMENT 'Full wizard form payload at save time',
    accepted_programs   MEDIUMTEXT    COMMENT 'Matched programs (one per line)',
    rejected_programs   MEDIUMTEXT    COMMENT 'Rejected programs with layer + reason (one per line)',
    programs_matched    INT           DEFAULT 0,
    INDEX idx_fhs_session (session_id),
    INDEX idx_fhs_created (created_at),
    INDEX idx_fhs_client_name (client_name),
    INDEX idx_fhs_broker_name (broker_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
