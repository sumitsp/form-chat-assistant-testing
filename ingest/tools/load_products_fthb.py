#!/usr/bin/env python3
"""Load products.csv and fthb_eligibility.csv into MySQL (run migration 004 first)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

PRODUCTS_CSV = ROOT / "products.csv"
FTHB_CSV = ROOT / "fthb_eligibility.csv"


def _load_products(conn) -> int:
    conn.execute(text("DELETE FROM products"))
    n = 0
    with PRODUCTS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            conn.execute(
                text(
                    """
                    INSERT INTO products (
                      product_id, program_id, program_name, product_name, io_flag, is_fthb_eligible
                    ) VALUES (
                      :product_id, :program_id, :program_name, :product_name, :io_flag, :is_fthb_eligible
                    )
                    """
                ),
                {
                    "product_id": int(row["product_id"]),
                    "program_id": int(row["program_id"]),
                    "program_name": row["program_name"].strip(),
                    "product_name": row["product_name"].strip(),
                    "io_flag": int(row["io_flag"] or 0),
                    "is_fthb_eligible": int(row["is_fthb_eligible"] or 0),
                },
            )
            n += 1
    return n


def _load_fthb(conn) -> int:
    conn.execute(text("DELETE FROM fthb_eligibility"))
    n = 0
    with FTHB_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cap = (row.get("fthb_max_loan_cap") or "").strip()
            conn.execute(
                text(
                    """
                    INSERT INTO fthb_eligibility (
                      program_id, program_name, is_fthb_eligible, fthb_max_loan_cap
                    ) VALUES (
                      :program_id, :program_name, :is_fthb_eligible, :fthb_max_loan_cap
                    )
                    """
                ),
                {
                    "program_id": int(row["program_id"]),
                    "program_name": row["program_name"].strip(),
                    "is_fthb_eligible": int(row["is_fthb_eligible"] or 0),
                    "fthb_max_loan_cap": int(cap) if cap else None,
                },
            )
            n += 1
    return n


def main() -> None:
    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    with engine.begin() as conn:
        pc = _load_products(conn)
        fc = _load_fthb(conn)
    print(f"Loaded {pc} products, {fc} fthb_eligibility rows.")


if __name__ == "__main__":
    main()
