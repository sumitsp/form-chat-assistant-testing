"""
Denali NQM matrix flow -> MySQL + Qdrant (architecture-aligned bootstrap).

This script follows the requested split:
- MySQL: structured gates (programs, ltv_matrix, tiers, geo, doc_requirements)
- Qdrant: non-matrix guideline prose only, one shared collection keyed by lender + program IDs

Usage:
  python ingest/lenders/denali_flow_to_mysql_qdrant.py --dry-run
  python ingest/lenders/denali_flow_to_mysql_qdrant.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import fitz
import pdfplumber
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


PDF_DEFAULT = ROOT / "input" / "Denali (NQM)" / "Matrices" / "NonQM and DSCR Matrices 02 13 2026.pdf"
EFFECTIVE_DATE = date(2026, 2, 13)
LENDER_CODE = "NQM"
LENDER_BRAND = "Denali"
LENDER_NAME = "NQM Funding"
QDRANT_COLLECTION = "mortgage_guidelines"
QDRANT_NQM_CONSOLIDATED = "mortgage_guidelines_nqm_all"


PROGRAM_REGISTRY: dict[str, dict[str, Any]] = {
    "FLEX SUPREME": {
        "program_code": "NQM_FLEX_SUPREME",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 680,
        "fico_max": 740,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "FLEX SELECT": {
        "program_code": "NQM_FLEX_SELECT",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3500000,
        "fico_min": 660,
        "fico_max": 760,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "SELECT ITIN": {
        "program_code": "NQM_SELECT_ITIN",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 1,
        "is_foreign_national": 0,
        "loan_amt_max": 2500000,
        "fico_min": 660,
        "fico_max": 740,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "SUPER JUMBO": {
        "program_code": "NQM_SUPER_JUMBO",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 5000000,
        "fico_min": 720,
        "fico_max": 740,
        "max_dti": 38.0,
        "dscr_min": None,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 0,
    },
    "SECOND LIEN SELECT": {
        "program_code": "NQM_SECOND_LIEN_SELECT",
        "is_dscr_program": 0,
        "is_second_lien": 1,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 1000000,
        "fico_min": 700,
        "fico_max": 720,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 0,
    },
    "DSCR SUPREME": {
        "program_code": "NQM_DSCR_SUPREME",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 2000000,
        "fico_min": 720,
        "fico_max": 740,
        "max_dti": None,
        "dscr_min": 1.00,
        "io_eligible": 1,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "INVESTOR DSCR": {
        "program_code": "NQM_INVESTOR_DSCR",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 660,
        "fico_max": 740,
        "max_dti": None,
        "dscr_min": 0.75,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 1,
    },
    "INVESTOR DSCR NO RATIO": {
        "program_code": "NQM_INVESTOR_DSCR_NO_RATIO",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 1500000,
        "fico_min": 700,
        "fico_max": 740,
        "max_dti": None,
        "dscr_min": 0.0,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 0,
    },
    "DSCR MULTI 5-8": {
        "program_code": "NQM_DSCR_MULTI_5_8",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 720,
        "fico_max": None,
        "max_dti": None,
        "dscr_min": 1.0,
        "io_eligible": 1,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "FOREIGN NATIONAL": {
        "program_code": "NQM_FOREIGN_NATIONAL",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 1,
        "loan_amt_max": 3000000,
        "fico_min": 700,
        "fico_max": None,
        "max_dti": None,
        "dscr_min": 1.0,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 0,
    },
}


_RE_GEOGRAPHIC = re.compile(r"GEOGRAPHIC\s+RESTRICTIONS\s*", re.IGNORECASE)
_RE_GENERAL_REQ = re.compile(r"GENERAL\s+REQUIREMENTS\s*", re.IGNORECASE)
_RE_PAGE_FOOTER = re.compile(r"^\s*[A-Za-z]?\d+\s*\|\s*P\s*a\s*g\s*e\s*$", re.IGNORECASE)
_RE_DATE_LINE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s*$")
_SECTION_HEADERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Flex\s+Supreme\s+Matrix\b", re.IGNORECASE), "FLEX SUPREME"),
    (re.compile(r"^Flex\s+Select\s+Matrix\b", re.IGNORECASE), "FLEX SELECT"),
    (re.compile(r"^ITIN\s+\d"), "SELECT ITIN"),
    (re.compile(r"^Super\s+Jumbo\s+\d"), "SUPER JUMBO"),
    (re.compile(r"^Second\s+Lien\s+Select\s+\d"), "SECOND LIEN SELECT"),
    (re.compile(r"^DSCR\s+Supreme\s+Matrix\b", re.IGNORECASE), "DSCR SUPREME"),
    (re.compile(r"^Investor\s+DSCR\s+Matrix\b", re.IGNORECASE), "INVESTOR DSCR"),
    (re.compile(r"^Investor\s+No\s+Ratio\s+Matrix\b", re.IGNORECASE), "INVESTOR DSCR NO RATIO"),
    (re.compile(r"^DSCR\s+Multi\s*\(\s*5\s*-\s*8\s*Unit\s*\)\s+Matrix\b", re.IGNORECASE), "DSCR MULTI 5-8"),
    (re.compile(r"^Foreign\s+National\s+Matrix\b", re.IGNORECASE), "FOREIGN NATIONAL"),
]


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\uf0a7", "•").replace("\uf0b7", "•")
    lines: list[str] = []
    for ln in text.split("\n"):
        s = ln.rstrip()
        if not s.strip():
            lines.append("")
            continue
        lines.append(re.sub(r"[ \t]+", " ", s.strip()))
    out: list[str] = []
    blank_run = 0
    for ln in lines:
        if ln == "":
            blank_run += 1
            if blank_run <= 2:
                out.append("")
        else:
            blank_run = 0
            out.append(ln)
    return "\n".join(out).strip()


def _split_page1_common(page1_text: str) -> tuple[str, str]:
    t = _normalize_whitespace(page1_text)
    m_geo = _RE_GEOGRAPHIC.search(t)
    m_gen = _RE_GENERAL_REQ.search(t)
    if not m_geo or not m_gen or m_gen.start() < m_geo.end():
        return "", t
    geographic = t[m_geo.end() : m_gen.start()].strip()
    general_global = t[m_gen.start() :].strip()
    return geographic, general_global


def _match_program_header(line: str) -> str | None:
    s = line.strip()
    if not s:
        return None
    for pat, name in _SECTION_HEADERS:
        if pat.search(s):
            return name
    return None


def _iter_program_sections(pages_text: list[str]) -> list[tuple[str, str, list[int]]]:
    if len(pages_text) < 2:
        return []
    current: str | None = None
    buf: list[str] = []
    current_pages: set[int] = set()
    sections: list[tuple[str, list[str], set[int]]] = []
    for page_idx, page in enumerate(pages_text[1:], start=2):
        for raw in page.splitlines():
            stripped = raw.strip()
            if not stripped:
                if current:
                    buf.append("")
                continue
            if _RE_DATE_LINE.match(stripped) or _RE_PAGE_FOOTER.match(stripped):
                continue
            prog = _match_program_header(stripped)
            if prog:
                if current is not None:
                    sections.append((current, buf, set(current_pages)))
                current = prog
                buf = [stripped]
                current_pages = {page_idx}
            elif current is not None:
                buf.append(stripped)
                current_pages.add(page_idx)
    if current is not None:
        sections.append((current, buf, set(current_pages)))

    merged: dict[str, list[str]] = {}
    merged_pages: dict[str, set[int]] = {}
    order: list[str] = []
    for prog, lines, pages in sections:
        if prog not in merged:
            merged[prog] = []
            merged_pages[prog] = set()
            order.append(prog)
        merged[prog].extend(lines)
        merged_pages[prog].update(pages)
    return [(p, _normalize_whitespace("\n".join(merged[p])), sorted(merged_pages[p])) for p in order]


def extract_denali_matrices_pdf(pdf_path: Path) -> list[dict[str, str]]:
    doc = fitz.open(pdf_path)
    try:
        pages = [(p.get_text("text") or "") for p in doc]
    finally:
        doc.close()
    if not pages:
        return []

    geo, gen_global = _split_page1_common(pages[0])
    common = _normalize_whitespace(
        "=== GEOGRAPHIC RESTRICTIONS ===\n" + geo + "\n\n=== GENERAL REQUIREMENTS (ALL PROGRAMS) ===\n" + gen_global
    ).strip()
    rows: list[dict[str, str]] = []
    for program_name, body, page_numbers in _iter_program_sections(pages):
        rows.append(
            {
                "investor_name": "Denali NQM",
                "program_name": program_name,
                "rules_common_all_programs": common,
                "program_matrices_and_rules": body,
                "page_numbers": page_numbers,
            }
        )
    return rows


def ensure_schema(conn: Any) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS lenders (
              id TINYINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              code VARCHAR(20) NOT NULL UNIQUE,
              brand_name VARCHAR(100) NOT NULL,
              lender_name VARCHAR(100) NOT NULL,
              effective_date DATE,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ltv_matrix (
              id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NULL,
              program_id SMALLINT UNSIGNED NOT NULL,
              fico_min SMALLINT UNSIGNED NOT NULL,
              fico_max SMALLINT UNSIGNED NULL,
              loan_amt_min INT UNSIGNED NOT NULL DEFAULT 0,
              loan_amt_max INT UNSIGNED NULL,
              occupancy ENUM('primary','second','investment','any') NOT NULL DEFAULT 'any',
              units ENUM('1','2','3','4','5-8','5-9','1-4','any') NOT NULL DEFAULT 'any',
              property_type VARCHAR(300) NULL,
              doc_type ENUM('full_doc','bank_stmt_12','bank_stmt_24','pl_only','pl_2mo_bs','wvoe','asset_util','1099','dscr_rental','itin','non_traditional','any') NOT NULL DEFAULT 'any',
              loan_purpose ENUM('purchase','rate_term','cash_out','any') NOT NULL DEFAULT 'any',
              dscr_band ENUM('gte_1_00','gte_0_75_lt_1_00','lt_0_75','any') DEFAULT 'any',
              is_io TINYINT(1) NOT NULL DEFAULT 0,
              is_str TINYINT(1) NOT NULL DEFAULT 0,
              is_fthb TINYINT(1) DEFAULT 0,
              state_override CHAR(2) NULL,
              max_ltv DECIMAL(4,1) NOT NULL,
              max_cltv DECIMAL(4,1) NULL,
              ltv_note VARCHAR(300) NULL,
              sort_order SMALLINT DEFAULT 0,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(id),
              INDEX idx_lender_prog (lender_id, program_id),
              INDEX idx_prog_fico (program_id, fico_min, fico_max),
              INDEX idx_prog_occ (program_id, occupancy)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    has_property_type = conn.execute(
        text(
            "SELECT COUNT(1) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ltv_matrix' AND COLUMN_NAME = 'property_type'"
        )
    ).scalar()
    if not has_property_type:
        conn.execute(text("ALTER TABLE ltv_matrix ADD COLUMN property_type VARCHAR(300) NULL AFTER units"))
    has_lender_id = conn.execute(
        text(
            "SELECT COUNT(1) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ltv_matrix' AND COLUMN_NAME = 'lender_id'"
        )
    ).scalar()
    if not has_lender_id:
        conn.execute(text("ALTER TABLE ltv_matrix ADD COLUMN lender_id TINYINT UNSIGNED NULL AFTER id"))
        conn.execute(text("ALTER TABLE ltv_matrix ADD INDEX idx_lender_prog (lender_id, program_id)"))
        conn.execute(
            text(
                "ALTER TABLE ltv_matrix ADD CONSTRAINT fk_ltv_lender "
                "FOREIGN KEY (lender_id) REFERENCES lenders(id)"
            )
        )
    conn.execute(
        text(
            "UPDATE ltv_matrix lm "
            "JOIN programs p ON p.id = lm.program_id "
            "SET lm.lender_id = p.lender_id "
            "WHERE lm.lender_id IS NULL"
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS credit_event_tiers (
              id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NULL,
              program_id SMALLINT UNSIGNED NOT NULL,
              min_months SMALLINT UNSIGNED NOT NULL,
              max_months SMALLINT UNSIGNED NULL,
              max_ltv_purchase DECIMAL(4,1) NULL,
              max_ltv_refi DECIMAL(4,1) NULL,
              max_loan_amount INT UNSIGNED NULL,
              ltv_reduction_pct DECIMAL(4,1) NULL,
              tier_note VARCHAR(300) NULL,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(id),
              INDEX idx_lender_prog_months (lender_id, program_id, min_months),
              INDEX idx_prog_months (program_id, min_months)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS doc_requirements (
              id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NULL,
              program_id SMALLINT UNSIGNED NOT NULL,
              doc_type ENUM('full_doc','bank_stmt_12','bank_stmt_24','pl_only','pl_2mo_bs','wvoe','asset_util','1099','dscr_rental','itin','non_traditional') NOT NULL,
              borrower_type ENUM('wage_earner','self_employed','retired','rental_income','foreign_national','itin','any') NOT NULL,
              documents JSON NOT NULL,
              min_months_history TINYINT UNSIGNED NULL,
              notes VARCHAR(1000) NULL,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(id),
              UNIQUE KEY uq_lender_prog_doc_btype (lender_id, program_id, doc_type, borrower_type),
              INDEX idx_lender_prog_doc (lender_id, program_id, doc_type),
              UNIQUE KEY uq_prog_doc_btype (program_id, doc_type, borrower_type),
              INDEX idx_prog_doc (program_id, doc_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    for table_name in ("credit_event_tiers", "doc_requirements"):
        has_lender_col = conn.execute(
            text(
                "SELECT COUNT(1) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = 'lender_id'"
            ),
            {"t": table_name},
        ).scalar()
        if not has_lender_col:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN lender_id TINYINT UNSIGNED NULL AFTER id"))
            conn.execute(
                text(
                    f"UPDATE {table_name} x "
                    "JOIN programs p ON p.id = x.program_id "
                    "SET x.lender_id = p.lender_id "
                    "WHERE x.lender_id IS NULL"
                )
            )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS programs (
              id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NOT NULL,
              program_code VARCHAR(60) NOT NULL UNIQUE,
              program_name VARCHAR(120) NOT NULL,
              is_second_lien TINYINT(1) NOT NULL DEFAULT 0,
              is_dscr_program TINYINT(1) NOT NULL DEFAULT 0,
              is_foreign_national TINYINT(1) NOT NULL DEFAULT 0,
              is_itin_program TINYINT(1) NOT NULL DEFAULT 0,
              loan_amt_min INT UNSIGNED NOT NULL DEFAULT 100000,
              loan_amt_max INT UNSIGNED NOT NULL,
              fico_min SMALLINT UNSIGNED NOT NULL,
              fico_max SMALLINT UNSIGNED NULL,
              max_dti DECIMAL(4,1) NULL,
              dscr_min DECIMAL(4,2) NULL,
              io_eligible TINYINT(1) DEFAULT 0,
              fthb_eligible TINYINT(1) DEFAULT 0,
              entity_vesting_ok TINYINT(1) DEFAULT 0,
              occupancy_types JSON NOT NULL,
              loan_purposes_allowed JSON NOT NULL,
              doc_types_allowed JSON NOT NULL,
              effective_date DATE NOT NULL,
              is_active TINYINT(1) DEFAULT 1,
              notes TEXT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              INDEX idx_lender (lender_id),
              INDEX idx_active (is_active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS geographic_restrictions (
              id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NULL,
              program_id SMALLINT UNSIGNED NULL,
              state CHAR(2) NOT NULL,
              county_city VARCHAR(200) NULL,
              restriction_type ENUM('ineligible','special_overlay') NOT NULL,
              restriction_detail VARCHAR(500) NULL,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(id),
              INDEX idx_state (state),
              INDEX idx_lender_state (lender_id, state)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )


def _parse_global_geographic(common_rules: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if "=== GEOGRAPHIC RESTRICTIONS ===" not in common_rules:
        return rows
    block = common_rules.split("=== GEOGRAPHIC RESTRICTIONS ===", 1)[1]
    if "=== GENERAL REQUIREMENTS" in block:
        block = block.split("=== GENERAL REQUIREMENTS", 1)[0]
    current_state: str | None = None
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"[A-Z]{2}", line):
            current_state = line
            continue
        if line.startswith("•") and current_state:
            rows.append((current_state, line.lstrip("• ").strip()))
    return rows


def _upsert_lender(conn: Any) -> int:
    row = conn.execute(text("SELECT id FROM lenders WHERE code = :code"), {"code": LENDER_CODE}).fetchone()
    if row:
        conn.execute(
            text(
                """
                UPDATE lenders
                SET brand_name=:brand_name, lender_name=:lender_name, effective_date=:effective_date
                WHERE id=:id
                """
            ),
            {"brand_name": LENDER_BRAND, "lender_name": LENDER_NAME, "effective_date": EFFECTIVE_DATE, "id": row[0]},
        )
        return int(row[0])

    result = conn.execute(
        text(
            """
            INSERT INTO lenders (code, brand_name, lender_name, effective_date)
            VALUES (:code, :brand_name, :lender_name, :effective_date)
            """
        ),
        {
            "code": LENDER_CODE,
            "brand_name": LENDER_BRAND,
            "lender_name": LENDER_NAME,
            "effective_date": EFFECTIVE_DATE,
        },
    )
    return int(result.lastrowid)


def _upsert_program(conn: Any, lender_id: int, program_name: str, notes: str) -> int:
    reg = PROGRAM_REGISTRY[program_name]
    row = conn.execute(text("SELECT id FROM programs WHERE program_code=:program_code"), {"program_code": reg["program_code"]}).fetchone()
    params = {
        "lender_id": lender_id,
        "program_code": reg["program_code"],
        "program_name": program_name.title(),
        "is_second_lien": reg["is_second_lien"],
        "is_dscr_program": reg["is_dscr_program"],
        "is_foreign_national": reg["is_foreign_national"],
        "is_itin_program": reg["is_itin_program"],
        "loan_amt_max": reg["loan_amt_max"],
        "fico_min": reg["fico_min"],
        "fico_max": reg["fico_max"],
        "max_dti": reg["max_dti"],
        "dscr_min": reg["dscr_min"],
        "io_eligible": reg["io_eligible"],
        "fthb_eligible": reg["fthb_eligible"],
        "entity_vesting_ok": reg["entity_vesting_ok"],
        "occupancy_types": json.dumps(["primary", "second", "investment"]),
        "loan_purposes_allowed": json.dumps(["purchase", "rate_term", "cash_out"]),
        "doc_types_allowed": json.dumps(["full_doc", "bank_stmt_12", "bank_stmt_24", "pl_only", "wvoe", "asset_util", "1099", "dscr_rental"]),
        "effective_date": EFFECTIVE_DATE,
        "notes": notes[:60000],
    }
    if row:
        conn.execute(
            text(
                """
                UPDATE programs
                SET lender_id=:lender_id, program_name=:program_name, is_second_lien=:is_second_lien,
                    is_dscr_program=:is_dscr_program, is_foreign_national=:is_foreign_national,
                    is_itin_program=:is_itin_program, loan_amt_max=:loan_amt_max, fico_min=:fico_min,
                    fico_max=:fico_max, max_dti=:max_dti, dscr_min=:dscr_min, io_eligible=:io_eligible,
                    fthb_eligible=:fthb_eligible, entity_vesting_ok=:entity_vesting_ok,
                    occupancy_types=:occupancy_types, loan_purposes_allowed=:loan_purposes_allowed,
                    doc_types_allowed=:doc_types_allowed, effective_date=:effective_date, notes=:notes
                WHERE id=:id
                """
            ),
            {**params, "id": row[0]},
        )
        return int(row[0])

    result = conn.execute(
        text(
            """
            INSERT INTO programs (
                lender_id, program_code, program_name, is_second_lien, is_dscr_program,
                is_foreign_national, is_itin_program, loan_amt_max, fico_min, fico_max, max_dti,
                dscr_min, io_eligible, fthb_eligible, entity_vesting_ok, occupancy_types,
                loan_purposes_allowed, doc_types_allowed, effective_date, notes
            ) VALUES (
                :lender_id, :program_code, :program_name, :is_second_lien, :is_dscr_program,
                :is_foreign_national, :is_itin_program, :loan_amt_max, :fico_min, :fico_max, :max_dti,
                :dscr_min, :io_eligible, :fthb_eligible, :entity_vesting_ok, :occupancy_types,
                :loan_purposes_allowed, :doc_types_allowed, :effective_date, :notes
            )
            """
        ),
        params,
    )
    return int(result.lastrowid)


def _replace_global_geo(
    conn: Any,
    lender_id: int,
    program_ids: list[int],
    entries: list[tuple[str, str]],
) -> None:
    # Global overlays are stored per-program (no NULL program_id rows) to keep downstream joins simple.
    conn.execute(
        text("DELETE FROM geographic_restrictions WHERE lender_id=:lender_id AND program_id IS NULL"),
        {"lender_id": lender_id},
    )
    if not program_ids:
        return
    for pid in program_ids:
        conn.execute(
            text(
                "DELETE FROM geographic_restrictions "
                "WHERE lender_id=:lender_id AND program_id=:program_id AND restriction_type='special_overlay'"
            ),
            {"lender_id": lender_id, "program_id": pid},
        )
    for pid in program_ids:
        for state, detail in entries:
            conn.execute(
                text(
                    """
                    INSERT INTO geographic_restrictions (
                        lender_id, program_id, state, county_city, restriction_type, restriction_detail
                    ) VALUES (
                        :lender_id, :program_id, :state, NULL, 'special_overlay', :detail
                    )
                    """
                ),
                {"lender_id": lender_id, "program_id": pid, "state": state, "detail": detail[:500]},
            )


def _norm_amount(raw: str) -> int:
    return int(re.sub(r"[^\d]", "", raw))


def _clean_cell(cell: Any) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _to_float_or_none(raw: str) -> float | None:
    s = raw.strip().upper().replace("%", "")
    if not s or "N/A" in s:
        return None
    if not re.fullmatch(r"\d{1,2}(?:\.\d+)?", s):
        return None
    return float(s)


def _max_amount_from_cell(raw: str) -> int | None:
    nums = re.findall(r"\d{1,3}(?:,\d{3})+", raw)
    if not nums:
        return None
    return max(_norm_amount(x) for x in nums)


def _normalize_property_type(raw: str) -> str:
    s = raw.replace("\n", " ")
    s = re.sub(r"\s*&\s*", ", ", s)
    s = re.sub(r"\s+and\s+", ", ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    parts = [p.strip(" ,") for p in s.split(",") if p.strip(" ,")]
    return ", ".join(parts)


def _extract_property_type_from_cells(cells: list[str]) -> str | None:
    keywords = (
        "SFR",
        "PUD",
        "CONDO",
        "CONDO",
        "UNIT",
        "SINGLE FAMILY",
        "TOWNHOME",
        "CO-OP",
        "MANUFACTURED",
        "WARRANTABLE",
        "CONDOTEL",
    )
    for c in cells:
        uc = c.upper()
        if "PROPERTY TYPE" in uc:
            continue
        if any(k in uc for k in keywords):
            out = _normalize_property_type(c)
            if out:
                return out
    return None


def _extract_ltv_rows(program_name: str, pdf_path: Path, page_numbers: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_occ = "any"
    current_units = "any"
    current_track_note = ""
    current_property_type: str | None = None
    current_dscr_band = "any"
    last_fico: int | None = None
    last_loan_amt: int | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in page_numbers:
            page = pdf.pages[page_num - 1]
            tables = page.extract_tables() or []
            for tbl in tables:
                if not tbl:
                    continue
                flat_table_text = " ".join(_clean_cell(c) for row in tbl for c in (row or []))
                if not re.search(r"(MAX\s+LTV|MAX\s+CLTV|LTV/CLTV|LTV\s*/\s*CLTV)", flat_table_text, re.IGNORECASE):
                    continue
                if not re.search(r"LOAN\s+AMOUNT", flat_table_text, re.IGNORECASE):
                    continue

                has_combined_pr_rt = bool(
                    re.search(r"PURCHASE\s*(?:/|&)\s*R(?:ATE)?\s*&?\s*TERM", flat_table_text, re.IGNORECASE)
                    or re.search(r"PURCHASE\s*&\s*RATE\s*/\s*TERM", flat_table_text, re.IGNORECASE)
                )
                has_full_alt_doc_cols = bool(
                    re.search(r"\bFULL\s+DOC\b", flat_table_text, re.IGNORECASE)
                    and re.search(r"\bALT\s+DOC\b", flat_table_text, re.IGNORECASE)
                )

                for r in tbl:
                    cells = [_clean_cell(c) for c in r]
                    line = " ".join(cells).upper()
                    ptype = _extract_property_type_from_cells(cells)
                    if ptype:
                        current_property_type = ptype

                    if "PRIMARY RESIDENCE" in line or re.fullmatch(r"PRIMARY", line):
                        current_occ = "primary"
                        current_units = "any"
                        current_track_note = ""
                        continue
                    if "SECOND HOME" in line:
                        current_occ = "second"
                        current_units = "any"
                        current_track_note = ""
                        continue
                    if "INVESTMENT PROPERTY" in line:
                        current_occ = "investment"
                        current_units = "any"
                        current_track_note = ""
                        continue

                    if "2-4 UNITS" in line:
                        current_units = "1-4"
                        current_track_note = "property_track=2-4_units"
                    elif "5-8" in line:
                        current_units = "5-8"
                        current_track_note = "property_track=5-8_units"
                    elif "5-9" in line:
                        current_units = "5-9"
                        current_track_note = "property_track=5-9_units"
                    elif ("SINGLE FAMILY" in line or "SFR" in line or "1 UNIT" in line) and "2-4 UNITS" not in line:
                        current_units = "1"
                        current_track_note = "property_track=1_unit"

                    if ">=1.00 DSCR" in line or "MINIMUM 1.00" in line:
                        current_dscr_band = "gte_1_00"
                    elif "0.75" in line and "DSCR" in line:
                        current_dscr_band = "gte_0_75_lt_1_00"
                    elif "NO RATIO" in line or "LT 0.75" in line:
                        current_dscr_band = "lt_0_75"
                    if "NO CREDIT SCORE" in line:
                        last_fico = 0

                    fico_idx = next((i for i, c in enumerate(cells) if re.fullmatch(r"\d{3}\+?", c)), None)
                    if fico_idx is not None:
                        m = re.match(r"(\d{3})", cells[fico_idx])
                        if m:
                            last_fico = int(m.group(1))

                    loan_idx = next(
                        (
                            i
                            for i, c in enumerate(cells)
                            if re.search(r"\d{1,3}(?:,\d{3})+", c)
                            and not re.fullmatch(r"\d{1,2}(?:\.\d+)?", c.strip())
                        ),
                        None,
                    )
                    if loan_idx is not None:
                        parsed_amt = _max_amount_from_cell(cells[loan_idx])
                        if parsed_amt is not None:
                            last_loan_amt = parsed_amt

                    if last_fico is None or last_loan_amt is None:
                        continue

                    start_idx = (loan_idx + 1) if loan_idx is not None else ((fico_idx + 1) if fico_idx is not None else 0)
                    numeric_after: list[float | None] = []
                    for c in cells[start_idx:]:
                        v = _to_float_or_none(c)
                        if v is not None or "N/A" in c.upper():
                            numeric_after.append(v)
                    if not numeric_after:
                        continue

                    purchase = numeric_after[0]
                    if purchase is None:
                        continue

                    if has_combined_pr_rt:
                        rate_term = purchase
                        cash_out = numeric_after[1] if len(numeric_after) > 1 else None
                    else:
                        if len(numeric_after) == 1:
                            rate_term = purchase
                            cash_out = None
                        elif len(numeric_after) == 2:
                            rate_term = numeric_after[1] if numeric_after[1] is not None else purchase
                            cash_out = None
                        else:
                            rate_term = numeric_after[1] if numeric_after[1] is not None else purchase
                            cash_out = numeric_after[2]

                    occ_for_row = current_occ
                    if occ_for_row == "any":
                        if program_name in {"DSCR SUPREME", "INVESTOR DSCR", "INVESTOR DSCR NO RATIO", "DSCR MULTI 5-8"}:
                            occ_for_row = "investment"
                        elif program_name == "SECOND LIEN SELECT":
                            occ_for_row = "primary"

                    def add_row(doc_type: str, p: float | None, rt: float | None, co: float | None) -> None:
                        if p is None:
                            return
                        rows.append(
                            {
                                "fico_min": last_fico,
                                "fico_max": None,
                                "loan_amt_min": 0,
                                "loan_amt_max": last_loan_amt,
                                "occupancy": occ_for_row,
                                "units": current_units,
                                "property_type": current_property_type,
                                "doc_type": doc_type,
                                "dscr_band": current_dscr_band,
                                "track_note": current_track_note,
                                "max_ltv_purchase": p,
                                "max_ltv_rate_term": rt,
                                "max_ltv_cashout": co,
                            }
                        )

                    if has_full_alt_doc_cols and len(numeric_after) >= 2:
                        full_purchase = numeric_after[0]
                        alt_purchase = numeric_after[1]
                        full_rate_term = full_purchase
                        alt_rate_term = alt_purchase
                        if len(numeric_after) >= 4:
                            full_cash = numeric_after[2]
                            alt_cash = numeric_after[3]
                        elif len(numeric_after) >= 3:
                            full_cash = numeric_after[2]
                            alt_cash = numeric_after[2]
                        else:
                            full_cash = None
                            alt_cash = None
                        add_row("full_doc", full_purchase, full_rate_term, full_cash)
                        add_row("non_traditional", alt_purchase, alt_rate_term, alt_cash)
                    else:
                        add_row("any", purchase, rate_term, cash_out)

    dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = (
            r.get("fico_min"),
            r.get("fico_max"),
            r.get("loan_amt_min"),
            r.get("loan_amt_max"),
            r.get("occupancy"),
            r.get("units", "any"),
            r.get("property_type", ""),
            r.get("doc_type", "any"),
            r.get("dscr_band", "any"),
            r.get("track_note", ""),
            r.get("max_ltv_purchase"),
            r.get("max_ltv_rate_term"),
            r.get("max_ltv_cashout"),
        )
        dedup[key] = r
    return list(dedup.values())


def _replace_ltv_matrix(
    conn: Any,
    lender_id: int,
    program_id: int,
    program_name: str,
    pdf_path: Path,
    page_numbers: list[int],
) -> int:
    conn.execute(text("DELETE FROM ltv_matrix WHERE program_id=:pid"), {"pid": program_id})
    rows = _extract_ltv_rows(program_name, pdf_path, page_numbers)
    if len(rows) < 2:
        raise RuntimeError(f"LTV parse coverage too low for {program_name}: only {len(rows)} rows extracted.")
    inserted = 0
    for i, row in enumerate(rows):
        # purchase
        conn.execute(
            text(
                """
                INSERT INTO ltv_matrix (
                  lender_id, program_id, fico_min, fico_max, loan_amt_min, loan_amt_max,
                  occupancy, units, property_type, doc_type, loan_purpose, dscr_band,
                  is_io, is_str, is_fthb, max_ltv, max_cltv, ltv_note, sort_order
                ) VALUES (
                  :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                  :occupancy, 'any', :property_type, :doc_type, 'purchase', :dscr_band,
                  0, 0, 0, :max_ltv, NULL, :ltv_note, :sort_order
                )
                """
            ),
            {
                "lender_id": lender_id,
                "program_id": program_id,
                "fico_min": row["fico_min"],
                "fico_max": row["fico_max"],
                "loan_amt_min": row["loan_amt_min"],
                "loan_amt_max": row["loan_amt_max"],
                "occupancy": row["occupancy"],
                "property_type": row.get("property_type"),
                "doc_type": row.get("doc_type", "any"),
                "dscr_band": row.get("dscr_band", "any"),
                "max_ltv": row["max_ltv_purchase"],
                "ltv_note": ("table-first parsed from matrix; " + row.get("track_note", "")).strip("; "),
                "sort_order": i,
            },
        )
        inserted += 1
        # rate/term
        if row.get("max_ltv_rate_term") is not None:
            conn.execute(
                text(
                    """
                    INSERT INTO ltv_matrix (
                      lender_id, program_id, fico_min, fico_max, loan_amt_min, loan_amt_max,
                      occupancy, units, property_type, doc_type, loan_purpose, dscr_band,
                      is_io, is_str, is_fthb, max_ltv, max_cltv, ltv_note, sort_order
                    ) VALUES (
                      :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                      :occupancy, 'any', :property_type, :doc_type, 'rate_term', :dscr_band,
                      0, 0, 0, :max_ltv, NULL, :ltv_note, :sort_order
                    )
                    """
                ),
                {
                    "lender_id": lender_id,
                    "program_id": program_id,
                    "fico_min": row["fico_min"],
                    "fico_max": row["fico_max"],
                    "loan_amt_min": row["loan_amt_min"],
                    "loan_amt_max": row["loan_amt_max"],
                    "occupancy": row["occupancy"],
                    "property_type": row.get("property_type"),
                    "doc_type": row.get("doc_type", "any"),
                    "dscr_band": row.get("dscr_band", "any"),
                    "max_ltv": row["max_ltv_rate_term"],
                    "ltv_note": ("table-first parsed from matrix; " + row.get("track_note", "")).strip("; "),
                    "sort_order": i + 500,
                },
            )
            inserted += 1
        # cash out
        if row["max_ltv_cashout"] is not None:
            conn.execute(
                text(
                    """
                    INSERT INTO ltv_matrix (
                      lender_id, program_id, fico_min, fico_max, loan_amt_min, loan_amt_max,
                      occupancy, units, property_type, doc_type, loan_purpose, dscr_band,
                      is_io, is_str, is_fthb, max_ltv, max_cltv, ltv_note, sort_order
                    ) VALUES (
                      :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                      :occupancy, 'any', :property_type, :doc_type, 'cash_out', :dscr_band,
                      0, 0, 0, :max_ltv, NULL, :ltv_note, :sort_order
                    )
                    """
                ),
                {
                    "lender_id": lender_id,
                    "program_id": program_id,
                    "fico_min": row["fico_min"],
                    "fico_max": row["fico_max"],
                    "loan_amt_min": row["loan_amt_min"],
                    "loan_amt_max": row["loan_amt_max"],
                    "occupancy": row["occupancy"],
                    "property_type": row.get("property_type"),
                    "doc_type": row.get("doc_type", "any"),
                    "dscr_band": row.get("dscr_band", "any"),
                    "max_ltv": row["max_ltv_cashout"],
                    "ltv_note": ("table-first parsed from matrix; " + row.get("track_note", "")).strip("; "),
                    "sort_order": i + 1000,
                },
            )
            inserted += 1
    return inserted


def _replace_credit_event_tiers(conn: Any, lender_id: int, program_id: int, program_name: str) -> int:
    conn.execute(
        text("DELETE FROM credit_event_tiers WHERE lender_id=:lid AND program_id=:pid"),
        {"lid": lender_id, "pid": program_id},
    )
    tiers: list[dict[str, Any]] = []
    if program_name == "FLEX SELECT":
        tiers = [
            {"min_months": 48, "max_months": None, "max_ltv_purchase": None, "max_ltv_refi": None, "max_loan_amount": None, "ltv_reduction_pct": None, "tier_note": "Base matrix applies"},
            {"min_months": 36, "max_months": 47, "max_ltv_purchase": 80.0, "max_ltv_refi": 80.0, "max_loan_amount": None, "ltv_reduction_pct": None, "tier_note": "Tiered credit event restriction"},
            {"min_months": 24, "max_months": 35, "max_ltv_purchase": 70.0, "max_ltv_refi": 70.0, "max_loan_amount": None, "ltv_reduction_pct": None, "tier_note": "Tiered credit event restriction"},
        ]
    for t in tiers:
        conn.execute(
            text(
                """
                INSERT INTO credit_event_tiers (
                  lender_id, program_id, min_months, max_months, max_ltv_purchase, max_ltv_refi,
                  max_loan_amount, ltv_reduction_pct, tier_note
                ) VALUES (
                  :lender_id, :program_id, :min_months, :max_months, :max_ltv_purchase, :max_ltv_refi,
                  :max_loan_amount, :ltv_reduction_pct, :tier_note
                )
                """
            ),
            {"lender_id": lender_id, "program_id": program_id, **t},
        )
    return len(tiers)


def _replace_doc_requirements(conn: Any, lender_id: int, program_id: int, program_name: str, body: str) -> int:
    conn.execute(
        text("DELETE FROM doc_requirements WHERE lender_id=:lid AND program_id=:pid"),
        {"lid": lender_id, "pid": program_id},
    )
    is_dscr = PROGRAM_REGISTRY[program_name]["is_dscr_program"] == 1
    base_docs = {
        "required": [],
        "conditional": [],
        "notes": "Auto-extracted baseline checklist from matrix prose.",
    }
    up = body.upper()
    if "PAYSTUB" in up:
        base_docs["required"].append("30 days paystubs")
    if "W-2" in up or "W2" in up:
        base_docs["required"].append("1-2 years W-2")
    if "4506" in up:
        base_docs["required"].append("IRS Form 4506-C")
    if "BANK STATEMENT" in up:
        base_docs["conditional"].append({"condition": "bank statement qualification", "docs": ["12 or 24 months bank statements"]})
    if "P&L" in up or "PROFIT" in up:
        base_docs["conditional"].append({"condition": "P&L method", "docs": ["YTD P&L statement"]})
    if "1099" in up:
        base_docs["conditional"].append({"condition": "1099 method", "docs": ["1-2 years 1099 forms"]})
    if "LEASE" in up or "RENTAL" in up:
        base_docs["conditional"].append({"condition": "rental income", "docs": ["Lease agreement", "Proof of rent receipt"]})

    inserts: list[tuple[str, str, dict[str, Any], int | None, str]] = []
    if is_dscr:
        dscr_docs = dict(base_docs)
        dscr_docs["required"] = list(set(dscr_docs.get("required", []) + ["DSCR calculation support", "Current lease or market rent (1007/1025/216 where required)"]))
        inserts.append(("dscr_rental", "rental_income", dscr_docs, None, "DSCR-focused checklist"))
    else:
        inserts.append(("full_doc", "wage_earner", base_docs, None, "Full doc checklist"))
        inserts.append(("bank_stmt_12", "self_employed", base_docs, 12, "12-month bank statement checklist"))
        inserts.append(("bank_stmt_24", "self_employed", base_docs, 24, "24-month bank statement checklist"))
        inserts.append(("pl_only", "self_employed", base_docs, None, "P&L checklist"))
        inserts.append(("1099", "any", base_docs, None, "1099 checklist"))

    for doc_type, borrower_type, docs, months, notes in inserts:
        conn.execute(
            text(
                """
                INSERT INTO doc_requirements (
                  lender_id, program_id, doc_type, borrower_type, documents, min_months_history, notes
                ) VALUES (
                  :lender_id, :program_id, :doc_type, :borrower_type, :documents, :min_months_history, :notes
                )
                """
            ),
            {
                "lender_id": lender_id,
                "program_id": program_id,
                "doc_type": doc_type,
                "borrower_type": borrower_type,
                "documents": json.dumps(docs),
                "min_months_history": months,
                "notes": notes,
            },
        )
    return len(inserts)


def _program_collection_name(program_code: str) -> str:
    return f"mortgage_guidelines_nqm_{program_code.lower()}"


def _ensure_qdrant_collection(client: QdrantClient, collection_name: str) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE, on_disk=True),
        )
    for field in ["lender_id", "program_ids", "section_type", "chunk_type", "effective_date", "is_superseded", "chunk_id"]:
        schema = models.PayloadSchemaType.KEYWORD
        if field == "is_superseded":
            schema = models.PayloadSchemaType.BOOL
        client.create_payload_index(collection_name=collection_name, field_name=field, field_schema=schema)


def _cleanup_old_nqm_qdrant(client: QdrantClient) -> None:
    collections = [c.name for c in client.get_collections().collections]
    # Remove legacy NQM points from shared collection.
    if QDRANT_COLLECTION in collections:
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="lender_id", match=models.MatchValue(value="NQM"))]
                )
            ),
            wait=True,
        )
    # Remove and recreate new NQM scoped collections for clean reload.
    for name in collections:
        if name == QDRANT_NQM_CONSOLIDATED or name.startswith("mortgage_guidelines_nqm_nqm_"):
            client.delete_collection(collection_name=name)


def _program_prose_only(raw_program_text: str) -> str:
    markers = [
        "GENERAL REQUIREMENTS",
        "BORROWER ELIGIBILITY",
        "CREDIT REQUIREMENTS",
        "INCOME REQUIREMENTS",
        "ASSET AND RESERVE REQUIREMENTS",
        "PROPERTY REQUIREMENTS",
        "DSCR RATIO AND RENTAL INCOME REQUIREMENTS",
    ]
    upper = raw_program_text.upper()
    starts = [upper.find(m) for m in markers if upper.find(m) >= 0]
    if not starts:
        return raw_program_text.strip()
    start = min(starts)
    return raw_program_text[start:].strip()


def _upsert_qdrant_chunks(rows: list[dict[str, str]], model: SentenceTransformer) -> tuple[int, int]:
    client = QdrantClient(url=config.QDRANT_URL)
    _cleanup_old_nqm_qdrant(client)
    _ensure_qdrant_collection(client, QDRANT_NQM_CONSOLIDATED)
    points_consolidated: list[models.PointStruct] = []
    per_program_points = 0

    for row in rows:
        program_name = row["program_name"]
        program_code = PROGRAM_REGISTRY[program_name]["program_code"]
        collection_name = _program_collection_name(program_code)
        _ensure_qdrant_collection(client, collection_name)
        text_body = _program_prose_only(row["program_matrices_and_rules"])
        chunk_id = f"NQM-{program_code}-PROSE-0001"
        vector = model.encode(text_body, normalize_embeddings=True).tolist()
        payload = {
            "chunk_id": chunk_id,
            "lender_id": "NQM",
            "program_ids": [program_code],
            "section_type": "general",
            "topic_tags": ["guideline_prose", "eligibility", "requirements"],
            "chunk_type": "rule",
            "text": text_body,
            "source_file": PDF_DEFAULT.name,
            "effective_date": "2026-02-13",
            "is_superseded": False,
        }
        point_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
        point = models.PointStruct(id=point_uuid, vector=vector, payload=payload)
        client.upsert(collection_name=collection_name, points=[point], wait=True)
        per_program_points += 1
        points_consolidated.append(point)

    common = rows[0]["rules_common_all_programs"] if rows else ""
    if common:
        vector = model.encode(common, normalize_embeddings=True).tolist()
        points_consolidated.append(
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, "NQM-GLOBAL-MATRIX-PAGE1")),
                vector=vector,
                payload={
                    "chunk_id": "NQM-GLOBAL-MATRIX-PAGE1",
                    "lender_id": "NQM",
                    "program_ids": [],
                    "section_type": "general",
                    "topic_tags": ["geographic_restrictions", "general_requirements"],
                    "chunk_type": "rule",
                    "text": common,
                    "source_file": PDF_DEFAULT.name,
                    "effective_date": "2026-02-13",
                    "is_superseded": False,
                },
            )
        )
    if points_consolidated:
        client.upsert(collection_name=QDRANT_NQM_CONSOLIDATED, points=points_consolidated, wait=True)
    return per_program_points, len(points_consolidated)


def run(pdf_path: Path, do_apply: bool) -> None:
    rows = extract_denali_matrices_pdf(pdf_path)
    if not rows:
        raise RuntimeError("No program rows parsed from PDF.")

    if not do_apply:
        print(f"Dry run: parsed {len(rows)} programs from {pdf_path.name}")
        print("Programs:", ", ".join(r["program_name"] for r in rows))
        return

    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    program_ids: dict[str, int] = {}
    ltv_inserted = 0
    credit_tiers_inserted = 0
    doc_reqs_inserted = 0
    with engine.begin() as conn:
        ensure_schema(conn)
        lender_id = _upsert_lender(conn)
        for row in rows:
            pname = row["program_name"]
            if pname not in PROGRAM_REGISTRY:
                continue
            pid = _upsert_program(conn, lender_id, pname, row["program_matrices_and_rules"])
            program_ids[pname] = pid
            ltv_inserted += _replace_ltv_matrix(conn, lender_id, pid, pname, pdf_path, row.get("page_numbers", []))
            credit_tiers_inserted += _replace_credit_event_tiers(conn, lender_id, pid, pname)
            doc_reqs_inserted += _replace_doc_requirements(conn, lender_id, pid, pname, row["program_matrices_and_rules"])
        geo = _parse_global_geographic(rows[0]["rules_common_all_programs"])
        _replace_global_geo(conn, lender_id, list(program_ids.values()), geo)

    model = SentenceTransformer(config.EMBEDDING_MODEL)
    q_program_count, q_consolidated_count = _upsert_qdrant_chunks(rows, model)
    print(f"MySQL upsert complete for {len(rows)} programs.")
    print(f"MySQL ltv_matrix rows upserted: {ltv_inserted}")
    print(f"MySQL credit_event_tiers rows upserted: {credit_tiers_inserted}")
    print(f"MySQL doc_requirements rows upserted: {doc_reqs_inserted}")
    print(f"Qdrant upsert complete: {q_program_count} program chunks + {q_consolidated_count} consolidated chunks.")


def audit_sql() -> None:
    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    with engine.begin() as conn:
        programs = conn.execute(
            text(
                "SELECT id, program_code, program_name FROM programs "
                "WHERE program_code LIKE 'NQM_%' ORDER BY id"
            )
        ).fetchall()
        for pid, pcode, pname in programs:
            print(f"\n=== {pid} {pcode} {pname}")
            total = conn.execute(text("SELECT COUNT(1) FROM ltv_matrix WHERE program_id=:pid"), {"pid": pid}).scalar()
            print(f"total_rows: {total}")
            by_purpose = conn.execute(
                text(
                    "SELECT loan_purpose, COUNT(1) "
                    "FROM ltv_matrix WHERE program_id=:pid "
                    "GROUP BY loan_purpose ORDER BY loan_purpose"
                ),
                {"pid": pid},
            ).fetchall()
            print("by_purpose:", by_purpose)
            by_doc = conn.execute(
                text(
                    "SELECT doc_type, COUNT(1) "
                    "FROM ltv_matrix WHERE program_id=:pid "
                    "GROUP BY doc_type ORDER BY doc_type"
                ),
                {"pid": pid},
            ).fetchall()
            print("by_doc:", by_doc)
            sample = conn.execute(
                text(
                    "SELECT occupancy, doc_type, loan_purpose, fico_min, loan_amt_max, max_ltv "
                    "FROM ltv_matrix WHERE program_id=:pid "
                    "ORDER BY sort_order, fico_min DESC, loan_amt_max LIMIT 20"
                ),
                {"pid": pid},
            ).fetchall()
            for row in sample:
                print(" ", row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Denali flow ETL to MySQL + Qdrant")
    ap.add_argument("--pdf", type=Path, default=PDF_DEFAULT, help="Path to Denali matrix PDF")
    ap.add_argument("--apply", action="store_true", help="Execute writes to MySQL and Qdrant")
    ap.add_argument("--dry-run", action="store_true", help="Parse and print without writes")
    ap.add_argument("--audit-sql", action="store_true", help="Print SQL audit summary for loaded NQM rows")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Use either --apply or --dry-run, not both.")
    if args.audit_sql:
        audit_sql()
        return
    run(args.pdf, do_apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    main()
