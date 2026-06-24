-- Migration 009: intake session tables for server-side chat intake
-- Apply: mysql -u root -p newpoint_mortgage < ingest/migrations/009_intake_tables.sql

CREATE TABLE IF NOT EXISTS intake_sessions (
  session_id          VARCHAR(36)  NOT NULL PRIMARY KEY,
  portfolio_json      JSON         NOT NULL,
  scenario_notes_json JSON,
  turns_json          JSON,
  question_count      INT          NOT NULL DEFAULT 0,
  combined_streak     INT          NOT NULL DEFAULT 0,
  single_streak       INT          NOT NULL DEFAULT 0,
  preview_shown       TINYINT(1)   NOT NULL DEFAULT 0,
  last_action         VARCHAR(32),
  last_target_slots   JSON,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS intake_turns (
  turn_id      BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  session_id   VARCHAR(36)  NOT NULL,
  role         ENUM('user','bot') NOT NULL,
  text         TEXT         NOT NULL,
  payload_json JSON,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_session_time (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS intake_scenario_notes (
  note_id      BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  session_id   VARCHAR(36)  NOT NULL,
  text         TEXT         NOT NULL,
  related_slot VARCHAR(64),
  paraphrase   VARCHAR(255),
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
