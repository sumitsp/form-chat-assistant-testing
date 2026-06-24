"""Apply migration 018 (scenario_description + origin) to form_history_scenario."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from backend.config import mysql_url

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "ingest" / "migrations" / "018_form_history_description_origin.sql"


def _existing_columns(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_history_scenario'"
        )
    ).fetchall()
    return {r[0] for r in rows}


def main() -> None:
    eng = create_engine(mysql_url())
    with eng.begin() as conn:
        cols = _existing_columns(conn)
        if "scenario_description" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE form_history_scenario "
                    "ADD COLUMN scenario_description VARCHAR(50) DEFAULT NULL "
                    "COMMENT 'User-edited label from Save Scenario dialog' "
                    "AFTER client_email"
                )
            )
        if "origin" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE form_history_scenario "
                    "ADD COLUMN origin VARCHAR(10) NOT NULL DEFAULT 'form' "
                    "COMMENT 'Intake mode when saved: form | chat' "
                    "AFTER scenario_description"
                )
            )
        conn.execute(
            text(
                """
                UPDATE form_history_scenario
                SET scenario_description = LEFT(
                        TRIM(JSON_UNQUOTE(JSON_EXTRACT(form_fields, '$._vaultScenarioDescription'))),
                        50
                    )
                WHERE scenario_description IS NULL
                  AND JSON_EXTRACT(form_fields, '$._vaultScenarioDescription') IS NOT NULL
                  AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(form_fields, '$._vaultScenarioDescription'))) <> ''
                """
            )
        )
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'form_history_scenario' "
                "ORDER BY ORDINAL_POSITION"
            )
        ).fetchall()
    cols = [r[0] for r in rows]
    print("Migration 018 applied.")
    print("form_history_scenario columns:", ", ".join(cols))
    for required in ("scenario_description", "origin"):
        if required not in cols:
            raise SystemExit(f"Missing column after migration: {required}")


if __name__ == "__main__":
    main()
