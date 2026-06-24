#!/usr/bin/env python3
"""
Reload dim_product_types, map_program_products, and dim_programs from Documents/Schema CSVs.

Changes applied (2026-03):
  - dim_product_types: HELOC draw-period variants (ids 19–30)
  - map_program_products: +loanpass_product_name/code/id JSON columns; 189 rows
  - dim_programs: program_name_loanpass as JSON name list; full row refresh

Usage (repo root, venv active):
  python ingest/tools/sync_program_product_catalog.py --apply
  python ingest/tools/sync_program_product_catalog.py --apply --csv-dir /path/to/Schema
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DEFAULT_CSV_DIR = Path("/Volumes/Extreme SSD/NewPoint Mortgage/Documents/Schema")

PRODUCT_TYPE_COLS = [
    "id",
    "code",
    "name",
    "io_period_years",
    "amort_period_years",
    "total_term_years",
]

MAP_PRODUCT_COLS = [
    "id",
    "program_id",
    "product_type_id",
    "io_flag",
    "is_fthb_eligible",
    "loanpass_product_name",
    "loanpass_product_code",
    "loanpass_product_id",
]

PROGRAM_COLS = [
    "program_id",
    "lender_id",
    "program_code",
    "program_name",
    "program_name_np",
    "program_name_loanpass",
    "effective_date",
    "is_second_lien",
    "second_lien_details",
    "is_dscr_program",
    "citizenship_types",
    "loan_amt_min",
    "loan_amt_max",
    "fico_min",
    "fico_max",
    "max_dti",
    "dscr_min_long_term",
    "dscr_min_short_term",
    "occupancy_types",
    "property_type",
    "loan_purposes_allowed",
    "doc_types_allowed",
    "is_active",
    "notes",
]


def _clean(val: str | None):
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _int_or_none(val: str | None) -> int | None:
    s = _clean(val)
    if s is None:
        return None
    return int(float(s))


def _float_or_none(val: str | None) -> float | None:
    s = _clean(val)
    if s is None:
        return None
    return float(s)


def _bool_int(val: str | None) -> int:
    s = (_clean(val) or "0").lower()
    return 1 if s in {"1", "true", "yes", "y"} else 0


def _parse_date(val: str | None):
    s = _clean(val)
    if not s:
        return None
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unparseable date: {val!r}")


def _json_or_none(val: str | None):
    s = _clean(val)
    if s is None:
        return None
    json.loads(s)  # validate
    return s


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cur.fetchone()[0] > 0


def ensure_schema(cur) -> None:
    if not _column_exists(cur, "dim_programs", "program_name_loanpass"):
        cur.execute(
            "ALTER TABLE dim_programs "
            "ADD COLUMN program_name_loanpass VARCHAR(255) NULL "
            "COMMENT 'LoanPASS program name(s) JSON array for execute-summary matching' "
            "AFTER program_name_np"
        )
    else:
        cur.execute(
            "ALTER TABLE dim_programs "
            "MODIFY COLUMN program_name_loanpass VARCHAR(255) NULL "
            "COMMENT 'LoanPASS program name(s) JSON array for execute-summary matching'"
        )

    for col in ("loanpass_product_name", "loanpass_product_code", "loanpass_product_id"):
        if not _column_exists(cur, "map_program_products", col):
            cur.execute(
                f"ALTER TABLE map_program_products "
                f"ADD COLUMN `{col}` JSON NULL "
                f"COMMENT 'LoanPASS product mapping from CSV' "
                f"AFTER is_fthb_eligible"
            )


def reload_product_types(cur, rows: list[dict[str, str]]) -> int:
    cur.execute("DELETE FROM map_program_products")
    cur.execute("DELETE FROM dim_product_types")
    sql = (
        "INSERT INTO dim_product_types "
        "(id, code, name, io_period_years, amort_period_years, total_term_years) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    for row in rows:
        cur.execute(
            sql,
            (
                int(row["id"]),
                row["code"],
                row["name"],
                int(row["io_period_years"]),
                int(row["amort_period_years"]),
                int(row["total_term_years"]),
            ),
        )
    return len(rows)


def reload_map_products(cur, rows: list[dict[str, str]]) -> int:
    sql = (
        "INSERT INTO map_program_products "
        "(id, program_id, product_type_id, io_flag, is_fthb_eligible, "
        " loanpass_product_name, loanpass_product_code, loanpass_product_id) "
        "VALUES (%s, %s, %s, %s, %s, "
        " CAST(%s AS JSON), CAST(%s AS JSON), CAST(%s AS JSON))"
    )
    for row in rows:
        cur.execute(
            sql,
            (
                int(row["id"]),
                int(row["program_id"]),
                int(row["product_type_id"]),
                _bool_int(row.get("io_flag")),
                _bool_int(row.get("is_fthb_eligible")),
                _json_or_none(row.get("loanpass_product_name")),
                _json_or_none(row.get("loanpass_product_code")),
                _json_or_none(row.get("loanpass_product_id")),
            ),
        )
    return len(rows)


def update_programs(cur, rows: list[dict[str, str]]) -> int:
    sql = """
        UPDATE dim_programs SET
            lender_id = %s,
            program_code = %s,
            program_name = %s,
            program_name_np = %s,
            program_name_loanpass = %s,
            effective_date = %s,
            is_second_lien = %s,
            second_lien_details = CAST(%s AS JSON),
            is_dscr_program = %s,
            citizenship_types = CAST(%s AS JSON),
            loan_amt_min = %s,
            loan_amt_max = %s,
            fico_min = %s,
            fico_max = %s,
            max_dti = %s,
            dscr_min_long_term = %s,
            dscr_min_short_term = %s,
            occupancy_types = CAST(%s AS JSON),
            property_type = CAST(%s AS JSON),
            loan_purposes_allowed = CAST(%s AS JSON),
            doc_types_allowed = CAST(%s AS JSON),
            is_active = %s,
            notes = %s
        WHERE program_id = %s
    """
    for row in rows:
        cur.execute(
            sql,
            (
                int(row["lender_id"]),
                row["program_code"],
                row["program_name"],
                _clean(row.get("program_name_np")),
                _json_or_none(row.get("program_name_loanpass")),
                _parse_date(row.get("effective_date")),
                _bool_int(row.get("is_second_lien")),
                _json_or_none(row.get("second_lien_details")),
                _bool_int(row.get("is_dscr_program")),
                _json_or_none(row.get("citizenship_types")),
                _int_or_none(row.get("loan_amt_min")),
                _int_or_none(row.get("loan_amt_max")),
                _int_or_none(row.get("fico_min")),
                _int_or_none(row.get("fico_max")),
                _float_or_none(row.get("max_dti")),
                _float_or_none(row.get("dscr_min_long_term")),
                _float_or_none(row.get("dscr_min_short_term")),
                _json_or_none(row.get("occupancy_types")),
                _json_or_none(row.get("property_type")),
                _json_or_none(row.get("loan_purposes_allowed")),
                _json_or_none(row.get("doc_types_allowed")),
                _bool_int(row.get("is_active")),
                _clean(row.get("notes")),
                int(row["program_id"]),
            ),
        )
    return len(rows)


def apply(csv_dir: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    import pymysql

    pt_rows = load_csv(csv_dir / "dim_product_types.csv")
    map_rows = load_csv(csv_dir / "map_program_products.csv")
    prog_rows = load_csv(csv_dir / "dim_programs.csv")

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
        ensure_schema(cur)
        n_pt = reload_product_types(cur, pt_rows)
        n_map = reload_map_products(cur, map_rows)
        n_prog = update_programs(cur, prog_rows)
        conn.commit()
        print(f"Synced on {os.environ['MYSQL_DATABASE']} @ {os.environ['MYSQL_HOST']}")
        print(f"  dim_product_types:     {n_pt} rows")
        print(f"  map_program_products:  {n_map} rows")
        print(f"  dim_programs:          {n_prog} rows updated")

        cur.execute("SELECT COUNT(*) FROM dim_product_types")
        print(f"  verify dim_product_types count: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM map_program_products")
        print(f"  verify map_program_products count: {cur.fetchone()[0]}")
        cur.execute(
            "SELECT COUNT(*) FROM map_program_products "
            "WHERE loanpass_product_id IS NOT NULL"
        )
        print(f"  map rows with loanpass_product_id: {cur.fetchone()[0]}")
        cur.execute(
            "SELECT program_id, program_code, program_name_loanpass "
            "FROM dim_programs WHERE program_id IN (12, 20) ORDER BY program_id"
        )
        print("  sample program_name_loanpass (JSON):")
        for r in cur.fetchall():
            print(f"    {r[0]} {r[1]}: {r[2]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR)
    parser.add_argument("--apply", action="store_true", help="Apply to MySQL (.env)")
    args = parser.parse_args()

    for name in ("dim_product_types.csv", "map_program_products.csv", "dim_programs.csv"):
        if not (args.csv_dir / name).is_file():
            sys.exit(f"Missing CSV: {args.csv_dir / name}")

    if not args.apply:
        parser.print_help()
        sys.exit("Pass --apply to write to MySQL")

    apply(args.csv_dir)


if __name__ == "__main__":
    main()
