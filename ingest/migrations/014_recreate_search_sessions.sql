-- ============================================================
-- Migration 014 — Replace legacy search_sessions (id + scenario_json)
-- with session-logging schema from 001.
--
-- Legacy table had only: id CHAR(36), created_at, scenario_json
-- API expects: session_id, occupancy, loan_purpose, ... eligible_programs JSON
--
-- Safe when search_sessions is empty (no audit history to preserve).
--   mysql -u ... -p newpoint_mortgage < ingest/migrations/014_recreate_search_sessions.sql
-- ============================================================

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS search_sessions;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE search_sessions (
    id                    INT           AUTO_INCREMENT PRIMARY KEY,
    session_id            VARCHAR(36)   NOT NULL UNIQUE,
    created_at            DATETIME      DEFAULT CURRENT_TIMESTAMP,
    occupancy             VARCHAR(80),
    loan_purpose          VARCHAR(80),
    state                 VARCHAR(10),
    value_sales_price     DECIMAL(15,2),
    loan_amount           DECIMAL(15,2),
    ltv                   DECIMAL(5,2),
    estimated_dti         DECIMAL(5,2),
    documentation_type    VARCHAR(80),
    prepayment_terms      VARCHAR(80),
    property_type         VARCHAR(80),
    citizenship           VARCHAR(80),
    decision_credit_score INT,
    existing_first_lien   DECIMAL(15,2),
    cltv                  DECIMAL(5,2),
    dscr                  DECIMAL(5,3),
    credit_event          TEXT,
    total_screened        INT,
    programs_matched      INT,
    eligible_programs     JSON          COMMENT 'Full list of matched programs as JSON'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE chat_messages (
    id               INT           AUTO_INCREMENT PRIMARY KEY,
    session_id       VARCHAR(36)   NOT NULL,
    created_at       DATETIME      DEFAULT CURRENT_TIMESTAMP,
    role             ENUM('user','assistant') NOT NULL,
    content          TEXT          NOT NULL,
    selected_program VARCHAR(300)  DEFAULT NULL,
    INDEX idx_chat_session (session_id),
    CONSTRAINT fk_chat_session FOREIGN KEY (session_id)
        REFERENCES search_sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
