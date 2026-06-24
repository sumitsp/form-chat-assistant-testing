#!/usr/bin/env python3
"""Set ltv_matrix.loan_amt_min from programs.loan_amt_min for each program_id."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


def _programs_join_col(conn) -> str:
    has_program_id = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'programs' "
            "AND column_name = 'program_id'"
        )
    ).scalar()
    return "program_id" if has_program_id else "id"


def _count_mismatches(conn, join_col: str) -> int:
    return int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM ltv_matrix m
                INNER JOIN programs p ON p.{join_col} = m.program_id
                WHERE m.loan_amt_min <> p.loan_amt_min
                """
            )
        ).scalar()
        or 0
    )


def _count_orphans(conn, join_col: str) -> int:
    return int(
        conn.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM ltv_matrix m
                LEFT JOIN programs p ON p.{join_col} = m.program_id
                WHERE p.{join_col} IS NULL
                """
            )
        ).scalar()
        or 0
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy programs.loan_amt_min into ltv_matrix.loan_amt_min by program_id."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts only; do not UPDATE.",
    )
    args = parser.parse_args()

    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    with engine.connect() as conn:
        join_col = _programs_join_col(conn)
        mismatches_before = _count_mismatches(conn, join_col)
        orphans = _count_orphans(conn, join_col)

        print(f"programs join column: {join_col}")
        print(f"matrix rows with loan_amt_min != program: {mismatches_before}")
        print(f"matrix rows without matching program: {orphans}")

        if args.dry_run:
            print("Dry run — no changes applied.")
            return

        with conn.begin():
            result = conn.execute(
                text(
                    f"""
                    UPDATE ltv_matrix m
                    INNER JOIN programs p ON p.{join_col} = m.program_id
                    SET m.loan_amt_min = p.loan_amt_min
                    """
                )
            )
            updated = result.rowcount

        mismatches_after = _count_mismatches(conn, join_col)
        print(f"rows updated: {updated}")
        print(f"remaining mismatches: {mismatches_after}")


if __name__ == "__main__":
    main()
