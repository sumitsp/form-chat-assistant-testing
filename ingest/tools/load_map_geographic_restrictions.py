#!/usr/bin/env python3
"""Replace map_geographic_restrictions from a CSV export (schema-aligned columns).

Usage:
    python -m ingest.tools.load_map_geographic_restrictions /path/to/map_geographic_restrictions.csv
    python -m ingest.tools.load_map_geographic_restrictions /path/to/file.csv --apply

Dry-run by default (reports row counts only). Pass --apply to truncate + reload.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

EXPECTED_COLUMNS = (
    "id",
    "lender_id",
    "program_id",
    "state",
    "restriction_type",
    "effect",
    "effect_value",
    "conditions",
    "restriction_detail",
)


def _json_or_none(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    json.loads(s)  # validate
    return s


def _optional_str(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def _load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise SystemExit(
                f"Unexpected CSV header {reader.fieldnames!r}; expected {EXPECTED_COLUMNS}"
            )
        rows: list[dict] = []
        for i, row in enumerate(reader, start=2):
            try:
                rows.append(
                    {
                        "id": int(row["id"]),
                        "lender_id": int(row["lender_id"]),
                        "program_id": int(row["program_id"]),
                        "state": _optional_str(row["state"]),
                        "restriction_type": row["restriction_type"].strip(),
                        "effect": _optional_str(row["effect"]),
                        "effect_value": _json_or_none(row["effect_value"]),
                        "conditions": _json_or_none(row["conditions"]),
                        "restriction_detail": _optional_str(row["restriction_detail"]),
                    }
                )
            except (ValueError, json.JSONDecodeError) as exc:
                raise SystemExit(f"Invalid row {i} in {csv_path}: {exc}") from exc
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="map_geographic_restrictions.csv")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Truncate table and reload (default: dry-run only).",
    )
    args = parser.parse_args()

    csv_path = args.csv_path.expanduser().resolve()
    if not csv_path.is_file():
        raise SystemExit(f"File not found: {csv_path}")

    rows = _load_rows(csv_path)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate id values in CSV")

    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    with engine.connect() as conn:
        before = int(conn.execute(text("SELECT COUNT(*) FROM map_geographic_restrictions")).scalar() or 0)
    print(f"CSV rows: {len(rows)}")
    print(f"DB rows (before): {before}")
    print(f"id range in CSV: {min(ids)}..{max(ids)}")

    if not args.apply:
        print("Dry run — pass --apply to truncate and reload.")
        return

    insert_sql = text(
        """
        INSERT INTO map_geographic_restrictions (
          id, lender_id, program_id, state, restriction_type,
          effect, effect_value, conditions, restriction_detail
        ) VALUES (
          :id, :lender_id, :program_id, :state, :restriction_type,
          :effect, CAST(:effect_value AS JSON), CAST(:conditions AS JSON), :restriction_detail
        )
        """
    )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM map_geographic_restrictions"))
        for row in rows:
            conn.execute(insert_sql, row)
        conn.execute(text(f"ALTER TABLE map_geographic_restrictions AUTO_INCREMENT = {max(ids) + 1}"))

    with engine.connect() as conn:
        after = int(conn.execute(text("SELECT COUNT(*) FROM map_geographic_restrictions")).scalar() or 0)
    print(f"DB rows (after): {after}")


if __name__ == "__main__":
    main()
