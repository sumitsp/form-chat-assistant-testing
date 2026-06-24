#!/usr/bin/env python3
"""
Add / reload dim_programs.program_name_loanpass from Documents/Schema/dim_programs.csv.

Usage (from repo root, venv active):
  python ingest/tools/sync_dim_programs_program_name_loanpass.py --apply
  python ingest/tools/sync_dim_programs_program_name_loanpass.py --csv /path/to/dim_programs.csv
  python ingest/tools/sync_dim_programs_program_name_loanpass.py --write-migration ingest/migrations/017_...
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DEFAULT_CSV = Path("/Volumes/Extreme SSD/NewPoint Mortgage/Documents/Schema/dim_programs.csv")


def _sql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "''")


def load_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def migration_sql(rows: list[dict[str, str]]) -> str:
    lines = [
        "-- ============================================================",
        "-- Migration — dim_programs.program_name_loanpass from CSV",
        f"-- Source: {DEFAULT_CSV.name}",
        "-- LoanPASS product display name for pricing API matching",
        "-- ============================================================",
        "",
        "ALTER TABLE dim_programs",
        "  ADD COLUMN program_name_loanpass VARCHAR(150) NULL",
        "  COMMENT 'LoanPASS product name for execute-summary matching'",
        "  AFTER program_name_np;",
        "",
    ]
    for row in rows:
        pid = int(row["program_id"])
        raw = (row.get("program_name_loanpass") or "").strip()
        if not raw:
            lines.append(f"UPDATE dim_programs SET program_name_loanpass = NULL WHERE program_id = {pid};")
        else:
            esc = _sql_escape(raw)
            lines.append(
                f"UPDATE dim_programs SET program_name_loanpass = '{esc}' WHERE program_id = {pid};"
            )
    lines.extend(
        [
            "",
            "-- Verify:",
            "-- SELECT program_id, program_code, program_name_np, program_name_loanpass FROM dim_programs ORDER BY program_id;",
            "",
        ]
    )
    return "\n".join(lines)


def apply_to_db(rows: list[dict[str, str]]) -> None:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    import pymysql

    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", 3306)),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        autocommit=False,
    )
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dim_programs' "
            "AND COLUMN_NAME = 'program_name_loanpass'"
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE dim_programs "
                "ADD COLUMN program_name_loanpass VARCHAR(150) NULL "
                "COMMENT 'LoanPASS product name for execute-summary matching' "
                "AFTER program_name_np"
            )
        for row in rows:
            pid = int(row["program_id"])
            raw = (row.get("program_name_loanpass") or "").strip() or None
            cur.execute(
                "UPDATE dim_programs SET program_name_loanpass = %s WHERE program_id = %s",
                (raw, pid),
            )
        conn.commit()
        print(
            f"Updated program_name_loanpass for {len(rows)} programs on {os.environ['MYSQL_DATABASE']}"
        )
        cur.execute(
            "SELECT program_id, program_code, program_name_np, program_name_loanpass "
            "FROM dim_programs ORDER BY program_id"
        )
        for r in cur.fetchall():
            lp = r[3] or "(null)"
            print(f"  {r[0]:>2} {r[1]:<28} np={r[2]!r:35} lp={lp}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--write-migration", type=Path, help="Write SQL file and exit")
    parser.add_argument("--apply", action="store_true", help="Apply updates to MySQL (.env)")
    args = parser.parse_args()

    if not args.csv.is_file():
        sys.exit(f"CSV not found: {args.csv}")

    rows = load_csv(args.csv)
    if args.write_migration:
        args.write_migration.parent.mkdir(parents=True, exist_ok=True)
        args.write_migration.write_text(migration_sql(rows), encoding="utf-8")
        print(f"Wrote {args.write_migration}")
        return

    if args.apply:
        apply_to_db(rows)
        return

    parser.print_help()
    sys.exit("Pass --write-migration PATH and/or --apply")


if __name__ == "__main__":
    main()
