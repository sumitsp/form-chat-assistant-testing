"""
Summit (Verus) VMC LTV matrix flow -> MySQL + Qdrant.

Separate entry point from ``denali_flow_to_mysql_qdrant.py`` so Denali/NQM is unchanged.

Vectors are upserted into the shared Qdrant collection
(``mortgage_matrices``); filter payloads by ``lender_id`` (``VMC`` vs ``NQM``).

Re-run **does not** delete NQM points — only removes prior ``VMC`` points in that collection, then re-upserts.

Usage:
  python ingest/lenders/summit_verus_flow_to_mysql_qdrant.py --dry-run
  python ingest/lenders/summit_verus_flow_to_mysql_qdrant.py --apply
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


PDF_DEFAULT = (
    ROOT
    / "input"
    / "Summit (Verus)"
    / "Matrices"
    / "VMC-Correspondent-Non-Agency-Loan-LTV-Matrix-03.09.2026-Final.pdf"
)
EFFECTIVE_DATE = date(2026, 3, 9)
LENDER_CODE = "VMC"
LENDER_BRAND = "Summit"
LENDER_NAME = "Verus Mortgage Capital"
# Shared matrix collection; distinguish lenders with ``lender_id`` in payload.
QDRANT_COLLECTION = "mortgage_matrices"


def _lender_chunk_id_prefix() -> str:
    s = LENDER_BRAND.strip() + "_" + LENDER_NAME.strip()
    return re.sub(r"[^\w]+", "_", s).strip("_")


PROGRAM_REGISTRY: dict[str, dict[str, Any]] = {
    "PRIME ASCENT PLUS": {
        "program_code": "VMC_PRIME_ASCENT_PLUS",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3500000,
        "fico_min": 680,
        "fico_max": 760,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "PRIME ASCENT": {
        "program_code": "VMC_PRIME_ASCENT",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 4000000,
        "fico_min": 620,
        "fico_max": 760,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "ITIN": {
        "program_code": "VMC_ITIN",
        "is_dscr_program": 0,
        "is_second_lien": 0,
        "is_itin_program": 1,
        "is_foreign_national": 0,
        "loan_amt_max": 2500000,
        "fico_min": 660,
        "fico_max": 760,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 1,
        "fthb_eligible": 1,
        "entity_vesting_ok": 0,
    },
    "INVESTOR DSCR PLUS": {
        "program_code": "VMC_INVESTOR_DSCR_PLUS",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 660,
        "fico_max": 760,
        "max_dti": None,
        "dscr_min": 1.0,
        "io_eligible": 1,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "INVESTOR DSCR": {
        "program_code": "VMC_INVESTOR_DSCR",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 660,
        "fico_max": 760,
        "max_dti": None,
        "dscr_min": 0.75,
        "io_eligible": 1,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "INVESTOR DSCR MULTI": {
        "program_code": "VMC_INVESTOR_DSCR_MULTI",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 700,
        "fico_max": None,
        "max_dti": None,
        "dscr_min": 1.0,
        "io_eligible": 1,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "CROSS COLLATERAL DSCR": {
        "program_code": "VMC_CROSS_COLLATERAL_DSCR",
        "is_dscr_program": 1,
        "is_second_lien": 0,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 3000000,
        "fico_min": 660,
        "fico_max": 760,
        "max_dti": None,
        "dscr_min": 1.0,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 1,
    },
    "FOREIGN NATIONAL DSCR": {
        "program_code": "VMC_FOREIGN_NATIONAL_DSCR",
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
    "CLOSED END SECOND": {
        "program_code": "VMC_CLOSED_END_SECOND",
        "is_dscr_program": 0,
        "is_second_lien": 1,
        "is_itin_program": 0,
        "is_foreign_national": 0,
        "loan_amt_max": 1500000,
        "fico_min": 680,
        "fico_max": 760,
        "max_dti": 50.0,
        "dscr_min": None,
        "io_eligible": 0,
        "fthb_eligible": 0,
        "entity_vesting_ok": 0,
    },
}


_RE_STATE_ELIG = re.compile(r"STATE\s+ELIGIBILITY", re.IGNORECASE)
_RE_GEOGRAPHIC = re.compile(r"GEOGRAPHIC\s+RESTRICTIONS\s*", re.IGNORECASE)
_RE_GENERAL_REQ = re.compile(r"GENERAL\s+REQUIREMENTS\s*", re.IGNORECASE)
_RE_PAGE_FOOTER = re.compile(r"^\s*Page\s*\|", re.IGNORECASE)
_RE_DATE_LINE = re.compile(r"^\s*(?:\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\.\d{1,2}\.\d{4})\s*$")
# Product banners (Verus matrix). Order matters: more specific patterns first.
_SECTION_HEADERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Prime\s+Ascent\s+Plus\b", re.IGNORECASE), "PRIME ASCENT PLUS"),
    (re.compile(r"^Prime\s+Ascent\b(?!\s+Plus)", re.IGNORECASE), "PRIME ASCENT"),
    (re.compile(r"^ITIN\s+[–-]", re.IGNORECASE), "ITIN"),
    (re.compile(r"^Investor\s+Solutions\s+[–-]\s*DSCR\s+Plus\b", re.IGNORECASE), "INVESTOR DSCR PLUS"),
    (re.compile(r"^Investor\s+Solutions\s+[–-]\s*DSCR\s*\(\s*5-8", re.IGNORECASE), "INVESTOR DSCR MULTI"),
    (re.compile(r"^Investor\s+Solutions\s+[–-]\s*DSCR\b", re.IGNORECASE), "INVESTOR DSCR"),
    (re.compile(r"^Cross\s+Collateral\s+DSCR\b", re.IGNORECASE), "CROSS COLLATERAL DSCR"),
    (re.compile(r"^Foreign\s+National\s+DSCR\b", re.IGNORECASE), "FOREIGN NATIONAL DSCR"),
    (re.compile(r"^Closed\s+End\s+Second\b", re.IGNORECASE), "CLOSED END SECOND"),
]

_DSCR_DEFAULT_INVESTMENT_PROGRAMS = frozenset(
    {
        "INVESTOR DSCR PLUS",
        "INVESTOR DSCR",
        "INVESTOR DSCR MULTI",
        "CROSS COLLATERAL DSCR",
        "FOREIGN NATIONAL DSCR",
    }
)


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
    """Verus matrix p.1: State Eligibility + General Requirements (no Denali-style GEOGRAPHIC header)."""
    t = _normalize_whitespace(page1_text)
    m_state = _RE_STATE_ELIG.search(t)
    m_gen = _RE_GENERAL_REQ.search(t)
    if m_state and m_gen and m_gen.start() > m_state.start():
        block = t[m_state.start() : m_gen.start()].strip()
        general_global = t[m_gen.start() :].strip()
        return block, general_global
    m_geo = _RE_GEOGRAPHIC.search(t)
    if m_geo and m_gen and m_gen.start() > m_geo.end():
        block = t[m_geo.end() : m_gen.start()].strip()
        general_global = t[m_gen.start() :].strip()
        return block, general_global
    return "", t


def _match_program_header(line: str) -> str | None:
    s = line.strip()
    if not s:
        return None
    for pat, name in _SECTION_HEADERS:
        if pat.search(s):
            return name
    return None


def _iter_program_sections(pages_text: list[str]) -> list[tuple[str, str, list[int]]]:
    if not pages_text:
        return []
    current: str | None = None
    buf: list[str] = []
    current_pages: set[int] = set()
    sections: list[tuple[str, list[str], set[int]]] = []
    for page_idx, page in enumerate(pages_text, start=1):
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


def extract_summit_matrices_pdf(pdf_path: Path) -> list[dict[str, str]]:
    doc = fitz.open(pdf_path)
    try:
        pages = [(p.get_text("text") or "") for p in doc]
    finally:
        doc.close()
    if not pages:
        return []

    geo, gen_global = _split_page1_common(pages[0])
    common = _normalize_whitespace(
        "=== GEOGRAPHIC RESTRICTIONS ===\n"
        + (geo or "")
        + "\n\n=== GENERAL REQUIREMENTS (ALL PROGRAMS) ===\n"
        + gen_global
    ).strip()
    rows: list[dict[str, str]] = []
    for program_name, body, page_numbers in _iter_program_sections(pages):
        rows.append(
            {
                "investor_name": "Summit Verus",
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
              max_ltv DECIMAL(4,1) NOT NULL,
              max_cltv DECIMAL(4,1) NULL,
              ltv_note VARCHAR(300) NULL,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(program_id),
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
            "JOIN programs p ON p.program_id = lm.program_id "
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
              FOREIGN KEY (program_id) REFERENCES programs(program_id),
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
              FOREIGN KEY (program_id) REFERENCES programs(program_id),
              UNIQUE KEY uq_lender_prog_doc_btype (lender_id, program_id, doc_type, borrower_type),
              INDEX idx_lender_prog_doc (lender_id, program_id, doc_type),
              UNIQUE KEY uq_prog_doc_btype (program_id, doc_type, borrower_type),
              INDEX idx_prog_doc (program_id, doc_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS programs (
              program_id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
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
              FOREIGN KEY (program_id) REFERENCES programs(program_id),
              INDEX idx_state (state),
              INDEX idx_lender_state (lender_id, state)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS program_rule_snippets (
              id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
              lender_id TINYINT UNSIGNED NULL,
              program_id SMALLINT UNSIGNED NOT NULL,
              category VARCHAR(80) NOT NULL,
              content TEXT NOT NULL,
              sort_order SMALLINT DEFAULT 0,
              FOREIGN KEY (lender_id) REFERENCES lenders(id),
              FOREIGN KEY (program_id) REFERENCES programs(program_id),
              INDEX idx_lender_program_category (lender_id, program_id, category),
              INDEX idx_program_category (program_id, category)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    for table_name in ("credit_event_tiers", "doc_requirements", "program_rule_snippets"):
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
                    "JOIN programs p ON p.program_id = x.program_id "
                    "SET x.lender_id = p.lender_id "
                    "WHERE x.lender_id IS NULL"
                )
            )


_RULE_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("property_requirements", re.compile(r"^Property Requirements\b", re.I)),
    ("property_type", re.compile(r"^Property Type\b", re.I)),
    ("housing_history", re.compile(r"^Housing History\b", re.I)),
    ("credit_event_seasoning", re.compile(r"^Credit Event Seasoning\b", re.I)),
    ("state_eligibility", re.compile(r"^State Eligibility\b", re.I)),
    ("declining_market", re.compile(r"^Declining Market\b", re.I)),
    ("general_requirements", re.compile(r"^General Requirements\b", re.I)),
    ("long_term_rental", re.compile(r"^Long-Term Rental\b", re.I)),
    ("short_term_rental", re.compile(r"^Short-Term Rental\b", re.I)),
    ("long_term_rental_docs", re.compile(r"^Long-Term Rental Documentation\b", re.I)),
]

_US_STATE_ABBREV = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR VI GU AS MP".split()
)


def _extract_rule_sections(body: str) -> dict[str, str]:
    """Split matrix/prose body into named sections (investor-agnostic headers)."""
    lines = body.splitlines()
    markers: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        for key, pat in _RULE_SECTION_PATTERNS:
            if pat.match(s):
                markers.append((i, key))
                break
    out: dict[str, str] = {}
    for j, (start_line, key) in enumerate(markers):
        end_line = markers[j + 1][0] if j + 1 < len(markers) else len(lines)
        chunk = "\n".join(lines[start_line:end_line]).strip()
        if len(chunk) < 2:
            continue
        if key in out:
            out[key] = (out[key] + "\n\n" + chunk).strip()
        else:
            out[key] = chunk
    return out


def _parse_program_state_eligibility(section_text: str) -> list[tuple[str, str]]:
    """Bullet lines → (state_code, detail). Use ZZ when no US state code found in line."""
    rows: list[tuple[str, str]] = []
    for raw in section_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^State Eligibility\b", line, re.I):
            continue
        detail = re.sub(r"^[•▪◦oO\-]+\s*", "", line).strip()
        if not detail:
            continue
        upper = detail.upper()
        codes = [c for c in re.findall(r"\b([A-Z]{2})\b", upper) if c in _US_STATE_ABBREV]
        if codes:
            for c in sorted(set(codes)):
                rows.append((c, detail[:500]))
        else:
            rows.append(("ZZ", detail[:500]))
    dedup: dict[tuple[str, str], tuple[str, str]] = {}
    for st, det in rows:
        dedup[(st, det)] = (st, det)
    return list(dedup.values())


def _replace_program_rule_snippets(conn: Any, lender_id: int, program_id: int, sections: dict[str, str]) -> int:
    conn.execute(
        text("DELETE FROM program_rule_snippets WHERE lender_id=:lid AND program_id=:pid"),
        {"lid": lender_id, "pid": program_id},
    )
    order = 0
    n = 0
    for cat in sorted(sections.keys()):
        content = sections[cat].strip()
        if len(content) < 3:
            continue
        conn.execute(
            text(
                """
                INSERT INTO program_rule_snippets (lender_id, program_id, category, content, sort_order)
                VALUES (:lender_id, :program_id, :category, :content, :sort_order)
                """
            ),
            {
                "lender_id": lender_id,
                "program_id": program_id,
                "category": cat[:80],
                "content": content[:65000],
                "sort_order": order,
            },
        )
        order += 1
        n += 1
    return n


def _replace_program_geographic_restrictions(
    conn: Any, lender_id: int, program_id: int, entries: list[tuple[str, str]]
) -> int:
    conn.execute(
        text("DELETE FROM geographic_restrictions WHERE lender_id=:lid AND program_id=:pid"),
        {"lid": lender_id, "pid": program_id},
    )
    n = 0
    for state, detail in entries:
        st = (state or "ZZ")[:2].upper()
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
            {"lender_id": lender_id, "program_id": program_id, "state": st, "detail": detail[:500]},
        )
        n += 1
    return n


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


def _extract_program_loan_bounds(body: str, *, default_min: int, default_max: int) -> tuple[int, int]:
    text_body = body or ""
    up = text_body.upper()

    # Prefer parsing inside GENERAL REQUIREMENTS block when available.
    start = up.find("GENERAL REQUIREMENTS")
    search_text = text_body
    if start >= 0:
        tail = text_body[start:]
        stop_markers = [
            "PROPERTY REQUIREMENTS",
            "CREDIT EVENT SEASONING",
            "STATE ELIGIBILITY",
            "DECLINING MARKET",
            "LONG-TERM RENTAL",
            "SHORT-TERM RENTAL",
        ]
        end = len(tail)
        tail_up = tail.upper()
        for marker in stop_markers:
            idx = tail_up.find(marker, len("GENERAL REQUIREMENTS"))
            if idx >= 0:
                end = min(end, idx)
        search_text = tail[:end]

    mins = [int(x.replace(",", "")) for x in re.findall(r"MIN(?:IMUM)?\s+LOAN\s+AMOUNT(?:\s*\([^)]*\))?[^$0-9]{0,30}\$?\s*([\d,]{2,})", search_text, re.I)]
    maxs = [int(x.replace(",", "")) for x in re.findall(r"MAX(?:IMUM)?\s+LOAN\s+AMOUNT(?:\s*\([^)]*\))?[^$0-9]{0,30}\$?\s*([\d,]{2,})", search_text, re.I)]

    # Fallback to full body if not found in the preferred block.
    if not mins:
        mins = [int(x.replace(",", "")) for x in re.findall(r"MIN(?:IMUM)?\s+LOAN\s+AMOUNT(?:\s*\([^)]*\))?[^$0-9]{0,30}\$?\s*([\d,]{2,})", text_body, re.I)]
    if not maxs:
        maxs = [int(x.replace(",", "")) for x in re.findall(r"MAX(?:IMUM)?\s+LOAN\s+AMOUNT(?:\s*\([^)]*\))?[^$0-9]{0,30}\$?\s*([\d,]{2,})", text_body, re.I)]

    loan_min = min(mins) if mins else int(default_min)
    loan_max = max(maxs) if maxs else int(default_max)
    if loan_min <= 0:
        loan_min = int(default_min)
    if loan_max <= 0:
        loan_max = int(default_max)
    if loan_max < loan_min:
        loan_max = max(int(default_max), loan_min)
    return loan_min, loan_max


def _upsert_program(conn: Any, lender_id: int, program_name: str, notes: str) -> int:
    reg = PROGRAM_REGISTRY[program_name]
    row = conn.execute(
        text("SELECT program_id FROM programs WHERE program_code=:program_code"),
        {"program_code": reg["program_code"]},
    ).fetchone()
    loan_amt_min, loan_amt_max = _extract_program_loan_bounds(
        notes,
        default_min=100000,
        default_max=int(reg["loan_amt_max"]),
    )
    params = {
        "lender_id": lender_id,
        "program_code": reg["program_code"],
        "program_name": program_name.title(),
        "is_second_lien": reg["is_second_lien"],
        "is_dscr_program": reg["is_dscr_program"],
        "is_foreign_national": reg["is_foreign_national"],
        "is_itin_program": reg["is_itin_program"],
        "loan_amt_min": loan_amt_min,
        "loan_amt_max": loan_amt_max,
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
                    is_itin_program=:is_itin_program, loan_amt_min=:loan_amt_min, loan_amt_max=:loan_amt_max, fico_min=:fico_min,
                    fico_max=:fico_max, max_dti=:max_dti, dscr_min=:dscr_min, io_eligible=:io_eligible,
                    fthb_eligible=:fthb_eligible, entity_vesting_ok=:entity_vesting_ok,
                    occupancy_types=:occupancy_types, loan_purposes_allowed=:loan_purposes_allowed,
                    doc_types_allowed=:doc_types_allowed, effective_date=:effective_date, notes=:notes
                WHERE program_id=:program_id
                """
            ),
            {**params, "program_id": row[0]},
        )
        return int(row[0])

    result = conn.execute(
        text(
            """
            INSERT INTO programs (
                lender_id, program_code, program_name, is_second_lien, is_dscr_program,
                is_foreign_national, is_itin_program, loan_amt_min, loan_amt_max, fico_min, fico_max, max_dti,
                dscr_min, io_eligible, fthb_eligible, entity_vesting_ok, occupancy_types,
                loan_purposes_allowed, doc_types_allowed, effective_date, notes
            ) VALUES (
                :lender_id, :program_code, :program_name, :is_second_lien, :is_dscr_program,
                :is_foreign_national, :is_itin_program, :loan_amt_min, :loan_amt_max, :fico_min, :fico_max, :max_dti,
                :dscr_min, :io_eligible, :fthb_eligible, :entity_vesting_ok, :occupancy_types,
                :loan_purposes_allowed, :doc_types_allowed, :effective_date, :notes
            )
            """
        ),
        params,
    )
    return int(result.lastrowid)


_OCC_ORDER = {"primary": 0, "second": 1, "investment": 2, "any": 9}
_PURPOSE_ORDER = {"purchase": 0, "rate_term": 1, "cash_out": 2, "any": 9}
_DOC_ORDER = {
    "full_doc": 0,
    "bank_stmt_12": 1,
    "bank_stmt_24": 2,
    "pl_only": 3,
    "pl_2mo_bs": 4,
    "wvoe": 5,
    "asset_util": 6,
    "1099": 7,
    "dscr_rental": 8,
    "itin": 9,
    "non_traditional": 10,
    "any": 99,
}


def _extract_income_doc_types(body: str) -> list[str]:
    up = body.upper()
    start = up.find("INCOME REQUIREMENTS")
    if start < 0:
        return []
    stops = [
        "ASSET AND RESERVE REQUIREMENTS",
        "PROPERTY REQUIREMENTS",
        "CREDIT EVENT SEASONING",
        "STATE ELIGIBILITY",
        "GENERAL REQUIREMENTS",
        "PROPERTY TYPE",
        "DECLINING MARKET",
    ]
    end = len(body)
    for marker in stops:
        idx = up.find(marker, start + 1)
        if idx >= 0:
            end = min(end, idx)
    block = body[start:end]
    bup = block.upper()
    docs: set[str] = set()
    if re.search(r"\bFULL\s+DOC|\bFULL\s+DOCUMENT|\bW-2\b|\bTAX\s+RETURN", bup):
        docs.add("full_doc")
    if "BANK STATEMENT" in bup:
        if re.search(r"\b24\s*(?:MONTH|MO)|24M", bup):
            docs.add("bank_stmt_24")
        if re.search(r"\b12\s*(?:MONTH|MO)|12M", bup):
            docs.add("bank_stmt_12")
        if "bank_stmt_12" not in docs and "bank_stmt_24" not in docs:
            docs.add("bank_stmt_12")
    if re.search(r"\bP\s*&\s*L\b|PROFIT\s*&?\s*LOSS", bup):
        if re.search(r"\b2\s*(?:MONTH|MO)|2M", bup):
            docs.add("pl_2mo_bs")
        else:
            docs.add("pl_only")
    if "WVOE" in bup:
        docs.add("wvoe")
    if "ASSET UTIL" in bup:
        docs.add("asset_util")
    if re.search(r"\b1099\b", bup):
        docs.add("1099")
    if "DSCR" in bup or "RENTAL INCOME" in bup:
        docs.add("dscr_rental")
    if "ITIN" in bup:
        docs.add("itin")
    if "ALT DOC" in bup or "ALTERNATIVE DOC" in bup or "NON-TRADITIONAL" in bup:
        docs.add("non_traditional")
    return sorted(docs, key=lambda x: _DOC_ORDER.get(x, 999))


def _sync_program_allowed_fields(
    conn: Any,
    lender_id: int,
    program_id: int,
    body: str,
    *,
    doc_from_income_section: bool,
) -> None:
    occ_rows = conn.execute(
        text(
            "SELECT DISTINCT occupancy FROM ltv_matrix "
            "WHERE lender_id=:lid AND program_id=:pid AND occupancy <> 'any'"
        ),
        {"lid": lender_id, "pid": program_id},
    ).fetchall()
    purpose_rows = conn.execute(
        text(
            "SELECT DISTINCT loan_purpose FROM ltv_matrix "
            "WHERE lender_id=:lid AND program_id=:pid AND loan_purpose <> 'any'"
        ),
        {"lid": lender_id, "pid": program_id},
    ).fetchall()
    doc_rows = conn.execute(
        text(
            "SELECT DISTINCT doc_type FROM ltv_matrix "
            "WHERE lender_id=:lid AND program_id=:pid AND doc_type <> 'any'"
        ),
        {"lid": lender_id, "pid": program_id},
    ).fetchall()

    occupancies = sorted({str(r[0]) for r in occ_rows if r and r[0]}, key=lambda x: _OCC_ORDER.get(x, 99))
    purposes = sorted({str(r[0]) for r in purpose_rows if r and r[0]}, key=lambda x: _PURPOSE_ORDER.get(x, 99))
    docs_matrix = sorted({str(r[0]) for r in doc_rows if r and r[0]}, key=lambda x: _DOC_ORDER.get(x, 999))

    docs = _extract_income_doc_types(body) if doc_from_income_section else docs_matrix
    if doc_from_income_section and not docs:
        docs = docs_matrix

    if not occupancies:
        occupancies = ["any"]
    if not purposes:
        purposes = ["any"]
    if not docs:
        docs = ["any"]

    conn.execute(
        text(
            """
            UPDATE programs
            SET occupancy_types=:occupancy_types,
                loan_purposes_allowed=:loan_purposes_allowed,
                doc_types_allowed=:doc_types_allowed
            WHERE program_id=:program_id
            """
        ),
        {
            "occupancy_types": json.dumps(occupancies),
            "loan_purposes_allowed": json.dumps(purposes),
            "doc_types_allowed": json.dumps(docs),
            "program_id": program_id,
        },
    )


def _replace_global_geo(conn: Any, lender_id: int, entries: list[tuple[str, str]]) -> None:
    conn.execute(text("DELETE FROM geographic_restrictions WHERE lender_id=:lender_id AND program_id IS NULL"), {"lender_id": lender_id})
    for state, detail in entries:
        conn.execute(
            text(
                """
                INSERT INTO geographic_restrictions (
                    lender_id, program_id, state, county_city, restriction_type, restriction_detail
                ) VALUES (
                    :lender_id, NULL, :state, NULL, 'special_overlay', :detail
                )
                """
            ),
            {"lender_id": lender_id, "state": state, "detail": detail[:500]},
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


def _sanitize_property_type_value(raw: str | None, max_len: int = 280) -> str | None:
    if not raw:
        return None
    allowed_keywords = (
        "SINGLE FAMILY",
        "SFR",
        "PUD",
        "CONDO",
        "CONDOMINIUM",
        "TOWNHOME",
        "CO-OP",
        "UNIT",
        "MANUFACTURED",
        "MIXED USE",
        "RURAL",
    )
    work = re.sub(r"\s+", " ", raw).strip(" ,;")
    work = re.sub(r"\b(?:MAX|OVERLAY|VERLAY|PURCHASE|REFINANCE|LOAN AMOUNT)\b.*", "", work, flags=re.I)
    work = re.sub(r"\s+and\s+", ", ", work, flags=re.I).strip(" ,;")
    if not work:
        return None
    if "INELIGIBLE" in work.upper() or "NOT ELIGIBLE" in work.upper():
        return None
    parts = [p.strip() for p in re.split(r"[;|]", work) if p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        uc = p.upper()
        if any(stop in uc for stop in ("MAX ", "LTV", "REFINANCE", "PURCHASE", "LOAN AMOUNT", "OVERLAY", "VERLAY", "$")):
            continue
        if not any(k in uc for k in allowed_keywords):
            continue
        cleaned = re.sub(r"\s+", " ", p).strip(" ,;")
        if not cleaned:
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    if not out:
        return None
    joined = ", ".join(out)
    return joined[:max_len]


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
    row_text = _normalize_property_type(", ".join([c for c in cells if c]))
    row_out = _sanitize_property_type_value(row_text)
    if row_out:
        return row_out

    for c in cells:
        uc = c.upper()
        if "PROPERTY TYPE" in uc:
            continue
        if any(k in uc for k in keywords):
            out = _sanitize_property_type_value(_normalize_property_type(c))
            if out:
                return out
    return None


def _extract_property_type_from_table(tbl: list[list[Any]]) -> str | None:
    """
    Verus often places a separate Property Type block directly after the main matrix table.
    Parse that block even when it is its own smaller table.
    """
    lines: list[str] = []
    for row in tbl:
        row_cells = [_clean_cell(c) for c in (row or []) if _clean_cell(c)]
        if not row_cells:
            continue
        lines.append(" ".join(row_cells))
    if not lines:
        return None

    has_property_header = any("PROPERTY TYPE" in ln.upper() for ln in lines)
    if not has_property_header:
        return None

    stop_headers = (
        "HOUSING HISTORY",
        "CREDIT EVENT",
        "STATE ELIGIBILITY",
        "DECLINING MARKET",
        "GENERAL REQUIREMENTS",
        "PRODUCT TYPE",
        "OCCUPANCY",
    )
    wanted = ("SINGLE FAMILY", "SFR", "PUD", "CONDO", "TOWNHOME", "CO-OP", "MANUFACTURED", "UNIT")

    collecting = False
    props: list[str] = []
    for ln in lines:
        uc = ln.upper()
        if "PROPERTY TYPE" in uc:
            collecting = True
            continue
        if not collecting:
            continue
        if any(h in uc for h in stop_headers):
            break
        cleaned = re.sub(r"^[•▪◦\-\*oO]+\s*", "", ln).strip()
        if not cleaned:
            continue
        if "INELIGIBLE" in uc or "NOT ELIGIBLE" in uc:
            continue
        if any(k in uc for k in wanted):
            pnorm = _sanitize_property_type_value(_normalize_property_type(cleaned))
            if pnorm:
                props.append(pnorm)

    if not props:
        return None
    seen: set[str] = set()
    merged: list[str] = []
    for p in props:
        key = p.upper()
        if key in seen:
            continue
        seen.add(key)
        merged.append(p)
    if not merged:
        return None
    return _sanitize_property_type_value(", ".join(merged))


def _extract_property_type_from_page_text(page_text: str) -> str | None:
    """
    Fallback parser for pages where Property Type appears as plain text lines
    instead of a detectable pdfplumber table.
    """
    if not page_text:
        return None
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return None

    stop_headers = (
        "HOUSING HISTORY",
        "CREDIT EVENT",
        "STATE ELIGIBILITY",
        "DECLINING MARKET",
        "GENERAL REQUIREMENTS",
        "PRODUCT TYPE",
        "OCCUPANCY",
    )
    collecting = False
    props: list[str] = []
    for ln in lines:
        uc = ln.upper()
        if "PROPERTY TYPE" in uc:
            collecting = True
            continue
        if not collecting:
            continue
        if any(h in uc for h in stop_headers):
            break
        cleaned = re.sub(r"^[•▪◦\-\*oO]+\s*", "", ln).strip()
        if not cleaned:
            continue
        if "INELIGIBLE" in cleaned.upper():
            continue
        norm = _sanitize_property_type_value(_normalize_property_type(cleaned))
        if norm:
            props.append(norm)
    if not props:
        return None
    return _sanitize_property_type_value(", ".join(props))


def _extract_ltv_rows(program_name: str, pdf_path: Path, page_numbers: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_occ = "any"
    current_units = "any"
    current_track_note = ""
    current_property_type: str | None = None
    program_property_type: str | None = None
    current_dscr_band = "any"
    last_fico: int | None = None
    last_loan_amt: int | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in page_numbers:
            page = pdf.pages[page_num - 1]
            page_prop = _extract_property_type_from_page_text(page.extract_text() or "")
            if page_prop:
                current_property_type = page_prop
                program_property_type = page_prop
            tables = page.extract_tables() or []
            for tbl in tables:
                if not tbl:
                    continue
                table_property_type = _extract_property_type_from_table(tbl)
                if table_property_type:
                    current_property_type = table_property_type
                    program_property_type = table_property_type
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

                    summit_dual_sh = bool(
                        re.search(r"SECOND\s+HOME\s*/\s*INVESTMENT", flat_table_text, re.I)
                        and not re.search(r"STANDARD,\s*BANK\s+STATEMENT", flat_table_text, re.I)
                        and not has_full_alt_doc_cols
                        and len(numeric_after) == 6
                    )

                    occ_for_row = current_occ
                    if occ_for_row == "any":
                        if program_name in _DSCR_DEFAULT_INVESTMENT_PROGRAMS:
                            occ_for_row = "investment"
                        elif program_name == "CLOSED END SECOND":
                            occ_for_row = "primary"

                    def add_row(
                        doc_type: str,
                        p: float | None,
                        rt: float | None,
                        co: float | None,
                        *,
                        occ_override: str | None = None,
                    ) -> None:
                        if p is None:
                            return
                        rows.append(
                            {
                                "fico_min": last_fico,
                                "fico_max": None,
                                "loan_amt_min": 0,
                                "loan_amt_max": last_loan_amt,
                                "occupancy": occ_override or occ_for_row,
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

                    if summit_dual_sh:
                        p0, rt0, co0 = numeric_after[0], numeric_after[1], numeric_after[2]
                        p1, rt1, co1 = numeric_after[3], numeric_after[4], numeric_after[5]
                        if p0 is None:
                            continue
                        if not has_full_alt_doc_cols:
                            add_row("any", p0, rt0, co0, occ_override="primary")
                            add_row("any", p1, rt1, co1, occ_override="second")
                            add_row("any", p1, rt1, co1, occ_override="investment")
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
    out_rows = list(dedup.values())
    if program_property_type:
        for r in out_rows:
            if not r.get("property_type"):
                r["property_type"] = program_property_type
    return out_rows


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
                  max_ltv, max_cltv, ltv_note
                ) VALUES (
                  :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                  :occupancy, 'any', :property_type, :doc_type, 'purchase', :dscr_band,
                  :max_ltv, NULL, :ltv_note
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
                      max_ltv, max_cltv, ltv_note
                    ) VALUES (
                      :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                      :occupancy, 'any', :property_type, :doc_type, 'rate_term', :dscr_band,
                      :max_ltv, NULL, :ltv_note
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
                      max_ltv, max_cltv, ltv_note
                    ) VALUES (
                      :lender_id, :program_id, :fico_min, :fico_max, :loan_amt_min, :loan_amt_max,
                      :occupancy, 'any', :property_type, :doc_type, 'cash_out', :dscr_band,
                      :max_ltv, NULL, :ltv_note
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
                },
            )
            inserted += 1
    return inserted


def _extract_credit_event_tiers_from_body(body: str) -> list[dict[str, Any]]:
    up = body.upper()
    start = up.find("CREDIT EVENT SEASONING")
    if start < 0:
        start = up.find("BK/FC/SS")
    if start < 0:
        return []
    tail = body[start:]
    stop_markers = [
        "STATE ELIGIBILITY",
        "DECLINING MARKET",
        "GENERAL REQUIREMENTS",
        "INCOME REQUIREMENTS",
        "PROPERTY TYPE",
    ]
    cut = len(tail)
    up_tail = tail.upper()
    for marker in stop_markers:
        idx = up_tail.find(marker)
        if idx > 0:
            cut = min(cut, idx)
    block = tail[:cut]
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return []

    months = [int(x) for x in re.findall(r">=\s*(\d+)\s*(?:MO|MONTH)", block.upper())]
    if not months:
        years = [int(x) for x in re.findall(r">=\s*(\d+)\s*Y", block.upper())]
        months = [y * 12 for y in years]
    if not months:
        return []
    months = sorted(set(months), reverse=True)
    n = len(months)

    def _capture_row_tokens(label_pat: str, kind: str) -> tuple[list[Any], bool]:
        label_idx = next((i for i, ln in enumerate(lines) if re.search(label_pat, ln, re.IGNORECASE)), None)
        if label_idx is None:
            return ([], False)
        out: list[Any] = []
        had_see_matrix = False
        j = label_idx
        while j < len(lines) and len(out) < n:
            ln = lines[j]
            uc = ln.upper()
            if j > label_idx and re.search(
                r"^(MAX\s+LTV|MAX\s+LOAN|BK/FC|FORBEARANCE|STATE ELIGIBILITY|DECLINING MARKET|GENERAL REQUIREMENTS)",
                ln,
                re.IGNORECASE,
            ):
                break
            if "SEE MATRIX ABOVE" in uc:
                had_see_matrix = True
                out.append(None)
            if re.search(r"\bNA\b", uc):
                out.append(None)
            if kind == "percent":
                pct_hits = re.findall(r"(\d{1,2}(?:\.\d+)?)\s*%", ln)
                for x in pct_hits:
                    out.append(float(x))
                if not pct_hits:
                    # Some extracted tables lose the '%' sign and leave plain values like "80", "70", "NA".
                    bare_hits = re.findall(r"\b(\d{1,2}(?:\.\d+)?)\b", ln)
                    for x in bare_hits:
                        out.append(float(x))
            elif kind == "amount":
                for x in re.findall(r"\$?\s*(\d{1,3}(?:,\d{3})+)", ln):
                    out.append(int(re.sub(r"[^\d]", "", x)))
            j += 1
        return (out[:n], had_see_matrix)

    p_vals, p_see = _capture_row_tokens(r"MAX\s+LTV\s*/\s*CLTV.*PURCHASE|MAX\s+LTV.*PURCHASE", "percent")
    r_vals, r_see = _capture_row_tokens(r"MAX\s+LTV\s*/\s*CLTV.*REFINANCE|MAX\s+LTV.*CASH[\s\-]*OUT|MAX\s+LTV.*REFI", "percent")
    a_vals, a_see = _capture_row_tokens(r"MAX\s+LOAN\s+AMT|MAX\s+LOAN\s+AMOUNT", "amount")
    fb_line = next((ln for ln in lines if re.search(r"FORBEARANCE|MODIFICATION|DEFERRAL", ln, re.IGNORECASE)), "")

    tiers: list[dict[str, Any]] = []
    for i, mm in enumerate(months):
        max_months = (months[i - 1] - 1) if i > 0 else None
        note_bits = ["Parsed from Verus credit event seasoning section"]
        if fb_line:
            note_bits.append(re.sub(r"\s+", " ", fb_line).strip())
        if p_see or r_see or a_see:
            note_bits.append("Some tier values reference 'See matrix above'")
        tiers.append(
            {
                "min_months": mm,
                "max_months": max_months,
                "max_ltv_purchase": p_vals[i] if i < len(p_vals) else None,
                "max_ltv_refi": r_vals[i] if i < len(r_vals) else None,
                "max_loan_amount": a_vals[i] if i < len(a_vals) else None,
                "ltv_reduction_pct": None,
                "tier_note": " | ".join(note_bits)[:300],
            }
        )
    return tiers


def _replace_credit_event_tiers(
    conn: Any,
    lender_id: int,
    program_id: int,
    program_name: str,
    body: str,
) -> int:
    conn.execute(
        text("DELETE FROM credit_event_tiers WHERE lender_id=:lid AND program_id=:pid"),
        {"lid": lender_id, "pid": program_id},
    )
    tiers = _extract_credit_event_tiers_from_body(body)
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


def _ensure_qdrant_collection(client: QdrantClient, collection_name: str) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE, on_disk=True),
        )
    for field in [
        "lender_id",
        "program_ids",
        "lender_mysql_id",
        "program_mysql_id",
        "section_type",
        "chunk_type",
        "effective_date",
        "is_superseded",
        "chunk_id",
    ]:
        schema = models.PayloadSchemaType.KEYWORD
        if field == "is_superseded":
            schema = models.PayloadSchemaType.BOOL
        elif field in ("lender_mysql_id", "program_mysql_id"):
            schema = models.PayloadSchemaType.INTEGER
        try:
            client.create_payload_index(collection_name=collection_name, field_name=field, field_schema=schema)
        except Exception:
            pass


def create_denali_qdrant_collection() -> None:
    """Create shared matrix collection + indexes if missing (does not delete NQM points)."""
    client = QdrantClient(url=config.QDRANT_URL)
    _ensure_qdrant_collection(client, QDRANT_COLLECTION)
    print(f"Qdrant collection ready: '{QDRANT_COLLECTION}' ({config.QDRANT_URL}).")
    print("Note: use --apply on summit or denali script to add lender-specific vectors.")


def _remove_summit_vectors_from_shared_collection(client: QdrantClient) -> None:
    """Delete only VMC points from the shared matrix collection; never drop NQM points."""
    collections = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in collections:
        return
    flt = models.Filter(
        must=[models.FieldCondition(key="lender_id", match=models.MatchValue(value=LENDER_CODE))]
    )
    try:
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(filter=flt),
            wait=True,
        )
    except Exception:
        pass


def _program_prose_only(raw_program_text: str) -> str:
    markers = [
        "GENERAL REQUIREMENTS",
        "BORROWER ELIGIBILITY",
        "CREDIT REQUIREMENTS",
        "INCOME REQUIREMENTS",
        "ASSET AND RESERVE REQUIREMENTS",
        "PROPERTY REQUIREMENTS",
        "DSCR RATIO AND RENTAL INCOME REQUIREMENTS",
        "LONG-TERM RENTAL",
        "SHORT-TERM RENTAL",
    ]
    upper = raw_program_text.upper()
    starts = [upper.find(m) for m in markers if upper.find(m) >= 0]
    if not starts:
        return raw_program_text.strip()
    start = min(starts)
    return raw_program_text[start:].strip()


def _program_between_matrix_and_general(raw_program_text: str) -> str:
    """
    Capture the section between the main matrix grid and General Requirements.
    This usually includes Property Type, Housing History, Credit Event Seasoning,
    State Eligibility, and Declining Market.
    """
    markers = [
        "PROPERTY TYPE",
        "HOUSING HISTORY",
        "CREDIT EVENT SEASONING",
        "STATE ELIGIBILITY",
        "DECLINING MARKET",
    ]
    up = raw_program_text.upper()
    gen_idx = up.find("GENERAL REQUIREMENTS")
    if gen_idx < 0:
        return ""
    starts = [up.find(m) for m in markers if up.find(m) >= 0 and up.find(m) < gen_idx]
    if not starts:
        return ""
    start = min(starts)
    return raw_program_text[start:gen_idx].strip()


def _program_qdrant_text(raw_program_text: str) -> str:
    mid = _program_between_matrix_and_general(raw_program_text)
    tail = _program_prose_only(raw_program_text)
    if mid and tail:
        return f"{mid}\n\n{tail}".strip()
    return (mid or tail).strip()


def _upsert_qdrant_chunks(
    rows: list[dict[str, str]],
    model: SentenceTransformer,
    lender_mysql_id: int,
    program_mysql_ids: dict[str, int],
) -> int:
    client = QdrantClient(url=config.QDRANT_URL)
    print(
        f"Qdrant {config.QDRANT_URL}: removing prior '{LENDER_CODE}' points from {QDRANT_COLLECTION!r}, "
        f"then upserting (MySQL lender_id={lender_mysql_id}, programs={len(program_mysql_ids)})."
    )
    _remove_summit_vectors_from_shared_collection(client)
    _ensure_qdrant_collection(client, QDRANT_COLLECTION)
    points: list[models.PointStruct] = []

    for row in rows:
        program_name = row["program_name"]
        if program_name not in PROGRAM_REGISTRY or program_name not in program_mysql_ids:
            continue
        program_code = PROGRAM_REGISTRY[program_name]["program_code"]
        prog_db_id = int(program_mysql_ids[program_name])
        text_body = _program_qdrant_text(row["program_matrices_and_rules"])
        chunk_id = f"{_lender_chunk_id_prefix()}-{program_code}-PROSE-0001"
        vector = model.encode(text_body, normalize_embeddings=True).tolist()
        payload: dict[str, Any] = {
            "chunk_id": chunk_id,
            "lender_id": LENDER_CODE,
            "lender_mysql_id": lender_mysql_id,
            "program_mysql_id": prog_db_id,
            "program_ids": [program_code],
            "section_type": "general",
            "topic_tags": ["guideline_prose", "eligibility", "requirements"],
            "chunk_type": "rule",
            "text": text_body,
            "source_file": PDF_DEFAULT.name,
            "effective_date": EFFECTIVE_DATE.isoformat(),
            "is_superseded": False,
        }
        point_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
        points.append(models.PointStruct(id=point_uuid, vector=vector, payload=payload))

    common = rows[0]["rules_common_all_programs"] if rows else ""
    if common:
        vector = model.encode(common, normalize_embeddings=True).tolist()
        gchunk = f"{_lender_chunk_id_prefix()}-GLOBAL-MATRIX-PAGE1-{EFFECTIVE_DATE.isoformat()}"
        points.append(
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, gchunk)),
                vector=vector,
                payload={
                    "chunk_id": gchunk,
                    "lender_id": LENDER_CODE,
                    "lender_mysql_id": lender_mysql_id,
                    "program_ids": [],
                    "section_type": "general",
                    "topic_tags": ["geographic_restrictions", "general_requirements"],
                    "chunk_type": "rule",
                    "text": common,
                    "source_file": PDF_DEFAULT.name,
                    "effective_date": EFFECTIVE_DATE.isoformat(),
                    "is_superseded": False,
                },
            )
        )

    if points:
        client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
    return len(points)


def run(pdf_path: Path, do_apply: bool) -> None:
    rows = extract_summit_matrices_pdf(pdf_path)
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
    snippets_inserted = 0
    prog_geo_inserted = 0
    with engine.begin() as conn:
        ensure_schema(conn)
        lender_id = _upsert_lender(conn)
        for row in rows:
            pname = row["program_name"]
            if pname not in PROGRAM_REGISTRY:
                continue
            pid = _upsert_program(conn, lender_id, pname, row["program_matrices_and_rules"])
            program_ids[pname] = pid
            body = row["program_matrices_and_rules"]
            sections = _extract_rule_sections(body)
            snippets_inserted += _replace_program_rule_snippets(conn, lender_id, pid, sections)
            se = sections.get("state_eligibility", "")
            if se.strip():
                prog_geo_inserted += _replace_program_geographic_restrictions(
                    conn, lender_id, pid, _parse_program_state_eligibility(se)
                )
            ltv_inserted += _replace_ltv_matrix(conn, lender_id, pid, pname, pdf_path, row.get("page_numbers", []))
            _sync_program_allowed_fields(
                conn,
                lender_id,
                pid,
                body,
                doc_from_income_section=True,
            )
            credit_tiers_inserted += _replace_credit_event_tiers(conn, lender_id, pid, pname, body)
            doc_reqs_inserted += _replace_doc_requirements(conn, lender_id, pid, pname, body)
        geo = _parse_global_geographic(rows[0]["rules_common_all_programs"])
        _replace_global_geo(conn, lender_id, geo)

    model = SentenceTransformer(config.EMBEDDING_MODEL)
    q_count = _upsert_qdrant_chunks(rows, model, lender_id, program_ids)
    print(f"MySQL upsert complete for {len(rows)} programs.")
    print(f"MySQL ltv_matrix rows upserted: {ltv_inserted}")
    print(f"MySQL program_rule_snippets rows upserted: {snippets_inserted}")
    print(f"MySQL geographic_restrictions (program-scoped) rows upserted: {prog_geo_inserted}")
    print(f"MySQL credit_event_tiers rows upserted: {credit_tiers_inserted}")
    print(f"MySQL doc_requirements rows upserted: {doc_reqs_inserted}")
    print(f"Qdrant upsert complete: {q_count} points in '{QDRANT_COLLECTION}'.")


def audit_sql() -> None:
    engine = create_engine(config.mysql_url(), pool_pre_ping=True)
    with engine.begin() as conn:
        programs = conn.execute(
            text(
                "SELECT program_id, program_code, program_name FROM programs "
                "WHERE program_code LIKE 'VMC_%' ORDER BY program_id"
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
                    "ORDER BY fico_min DESC, loan_amt_max LIMIT 20"
                ),
                {"pid": pid},
            ).fetchall()
            for row in sample:
                print(" ", row)


def main() -> None:
    ap = argparse.ArgumentParser(description="Summit (Verus) VMC matrix ETL to MySQL + Qdrant (shared matrix collection)")
    ap.add_argument("--pdf", type=Path, default=PDF_DEFAULT, help="Path to Verus LTV matrix PDF")
    ap.add_argument("--apply", action="store_true", help="MySQL + embed and upsert vectors into Qdrant (populates points)")
    ap.add_argument("--dry-run", action="store_true", help="Parse and print without writes")
    ap.add_argument("--audit-sql", action="store_true", help="Print SQL audit summary for loaded VMC rows")
    ap.add_argument(
        "--init-qdrant",
        action="store_true",
        help=(
            "Create empty Qdrant collection '%s' + payload indexes only — 0 points / no embeddings. "
            "Run --apply afterward to load vectors."
        )
        % QDRANT_COLLECTION,
    )
    args = ap.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Use either --apply or --dry-run, not both.")
    if args.init_qdrant:
        if args.apply or args.dry_run:
            raise SystemExit("--init-qdrant cannot be used with --apply or --dry-run.")
        create_denali_qdrant_collection()
        return
    if args.audit_sql:
        audit_sql()
        return
    run(args.pdf, do_apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    main()
