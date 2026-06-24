"""Eligibility capability — models, trace, engine, service and routes in one module.

Merged from the former backend/eligibility/ package per the planned flat layout.
Order matters: models → trace → engine → service → routes.
"""
from __future__ import annotations



# ===== models ==============================================================

"""Pydantic models for POST /api/eligibility/full and /api/eligibility/quick."""


from pydantic import BaseModel


class EligibilityRequest(BaseModel):
    # First-pass fields
    occupancy: str = ""
    loanPurpose: str = ""
    state: str = ""
    valueSalesPrice: str = ""
    loanAmount: str = ""
    ltv: str = ""
    estimatedDti: str = ""
    documentationType: str = ""
    prepaymentTerms: str = ""
    propertyType: str = ""
    citizenship: str = ""
    decisionCreditScore: str = ""
    # Second-pass (optional)
    existingFirstLien: str = ""
    cltv: str = ""
    dscr: str = ""
    creditEvent: str = ""
    creditEventType: str = ""
    yearsSinceEvent: str = ""
    firstTimeHomebuyer: str = ""
    rentalType: str = ""
    qualificationPath: str = ""
    paymentHistory: str = ""
    firstTimeInvestor: str = ""
    establishedPrimaryRes: str = ""
    isSecondLien: str = ""
    # Sub-location fields (used for county/city/zip geo restrictions)
    stateCounty: str = ""
    stateCity: str = ""
    stateBorough: str = ""
    stateZipCode: str = ""
    isInBaltimoreCity: str = ""
    isInIndianapolis: str = ""
    isInPhiladelphia: str = ""
    isInMemphis: str = ""
    isInLubbock: str = ""
    # Step-5 product preferences
    loanTerm: str = ""
    interestOnlyPref: str = ""
    rateTypePref: str = ""
    # v2 lien + loan purpose fields
    lienPosition: str = ""
    primaryLoanPurpose: str = ""
    secondLienProduct: str = ""
    # collateral
    hiLavaZone: str = ""
    isRuralProperty: str = ""
    acreage: str = ""
    # NOCB
    nonOccupantCoBorrower: str = ""
    combinedDti: str = ""
    # Scenario considerations (Basics → Conditions)
    visaType: str = ""
    visaCategory: str = ""
    ofacSanctioned: str = ""
    hasUsCredit: str = ""
    investmentIncomePath: str = ""
    prepayStepdown: str = ""
    listingSeasoning: str = ""
    powerOfAttorney: str = ""
    nonArmsLength: str = ""


class QuickEligibilityRequest(EligibilityRequest):
    """Same payload as full eligibility; optional lightweight program rows for previews."""

    include_programs: bool = False


class BestMatchMetrics(BaseModel):
    min_fico: int | None = None
    min_loan: int | None = None
    max_loan: int | None = None
    max_ltv_purchase: float | None = None
    max_ltv_rate_term: float | None = None
    max_ltv_cashout: float | None = None
    max_dti: float | None = None
    min_dscr: float | None = None


class EligibleProgram(BaseModel):
    investor: str
    investor_name: str
    program_name: str
    program_name_np: str | None = None
    program_type: str | None = None
    is_dscr: bool = False
    is_itin: bool = False
    is_foreign_nat: bool = False
    min_fico: int | None = None
    min_loan: int | None = None
    max_loan: int | None = None
    max_ltv_purchase: float | None = None
    max_ltv_rate_term: float | None = None
    max_ltv_cashout: float | None = None
    max_dti: float | None = None
    min_dscr: float | None = None
    best_match: BestMatchMetrics | None = None
    doc_type: str | None = None
    occupancy: str | None = None
    occupancy_types: list[str] | None = None
    property_types: list[str] | None = None
    loan_purposes_allowed: list[str] | None = None
    doc_types_allowed: str | None = None
    program_notes: str | None = None
    is_active: bool = True
    products_available: str | None = None
    products: list[str] | None = None
    products_matching: list[str] | None = None
    special_overlay: str | None = None
    rag_notes: list[str] | None = None
    program_id: int | None = None


class NearMissProgram(EligibleProgram):
    near_miss_hint: str
    near_miss_type: str | None = None
    near_miss_suggestion: str | None = None
    suggested_ltv: float | None = None
    suggested_loan: int | None = None


class ProgramExclusion(BaseModel):
    program_name: str
    reason: str


class EligibilityResponse(BaseModel):
    session_id: str
    eligible: list[EligibleProgram]
    near_misses: list[NearMissProgram] = []
    geo_blocked_count: int
    overlay_blocked_count: int
    geo_exclusions: list[ProgramExclusion] = []
    overlay_exclusions: list[ProgramExclusion] = []
    rag_ineligible: list[dict]
    total_screened: int
    available: bool


class QuickEligibilityResponse(BaseModel):
    count: int
    program_names: list[str]
    available: bool = True
    eligible: list[EligibleProgram] | None = None
    total_screened: int | None = None
    geo_blocked_count: int | None = None
    overlay_blocked_count: int | None = None


# ===== trace ===============================================================

"""Per-program eligibility trace logging (time + form/session id)."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.connections.logging import prune_trace_logs, trace_file_enabled

_log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = REPO_ROOT / "logs"

LAYER1 = "Layer 1 (dim_programs)"
LAYER2 = "Layer 2 (map_ltv_matrix)"
LAYER3 = "Layer 3 (FTHB)"
LAYER4 = "Layer 4 (products)"
LAYER4B = "Layer 4b (product prefs)"
LAYER5 = "Layer 5 (geo)"
LAYER6 = "Layer 6 (credit seasoning)"
LAYER7 = "Layer 7 (housing history)"
LAYER8 = "Layer 8 (rule guidelines)"
LAYER10 = "Layer 10 (Qdrant verify)"


def _program_label(prog: dict[str, Any]) -> str:
    lender = prog.get("brand_name") or prog.get("lender_name") or "?"
    name = prog.get("program_name_np") or prog.get("program_name") or "?"
    return f"{lender} — {name}"


def load_all_active_programs(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT p.program_id, p.lender_id, p.program_name, p.program_name_np,
                   p.is_dscr_program, p.is_second_lien, p.second_lien_details,
                   p.fico_min, p.fico_max,
                   p.max_dti, p.dscr_min_long_term, p.dscr_min_short_term,
                   p.loan_amt_min, p.loan_amt_max, p.doc_types_allowed,
                   p.occupancy_types, p.property_type, p.citizenship_types,
                   l.brand_name, l.lender_name, l.code AS lender_code
            FROM dim_programs p
            JOIN dim_lenders l ON l.id = p.lender_id
            WHERE p.is_active = 1
            ORDER BY p.lender_id, p.program_id
            """
        )
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def explain_layer1_rejection(prog: dict[str, Any], form: dict[str, Any]) -> str:

    if form["is_second_lien"] and not prog.get("is_second_lien"):
        return "Not a second-lien program (scenario is first lien only)"
    if not form["is_second_lien"] and prog.get("is_second_lien"):
        return "Second-lien program only (scenario is first lien)"

    # second_lien_details — check product structure match
    detail = prog.get("second_lien_details")
    if detail is not None and form.get("is_second_lien"):
        lien_pos = form.get("lienPosition", "")
        product = form.get("secondLienProduct", "")
        is_pig = lien_pos == "second_lien_piggyback"
        if is_pig:
            required = "piggyback"
        elif product == "heloc":
            required = "heloc"
        elif product == "heloan":
            required = "closed_ended"
        else:
            required = ""
        if required:
            if isinstance(detail, list):
                detail_list = [str(x) for x in detail]
            elif isinstance(detail, str):
                try:
                    parsed = json.loads(detail)
                    detail_list = [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]
                except Exception:
                    detail_list = re.split(r"[,;|]", detail)
            else:
                detail_list = []
            if required not in detail_list:
                return f"Second-lien structure mismatch: program supports {detail_list}, scenario requires '{required}'"

    if form["doc_type"] == "dscr_rental":
        if not prog.get("is_dscr_program"):
            return "Not a DSCR program (scenario uses DSCR / rental income path)"
        dscr = form.get("dscr")
        if dscr is not None:
            if form.get("is_short_term_rental"):
                min_d = prog.get("dscr_min_short_term")
                label = "dscr_min_short_term"
            else:
                min_d = prog.get("dscr_min_long_term")
                label = "dscr_min_long_term"
            if min_d is not None and float(min_d) > float(dscr):
                return f"Program {label} {min_d} > scenario DSCR {dscr}"
    elif prog.get("is_dscr_program"):
        return "DSCR-only program (scenario is income-documentation path)"

    fico = form.get("fico")
    if fico is not None and prog.get("fico_min") is not None and int(prog["fico_min"]) > int(fico):
        return f"Min FICO {prog['fico_min']} > scenario FICO {fico}"
    if fico is not None and prog.get("fico_max") is not None and int(prog["fico_max"]) < int(fico):
        return f"Max FICO {prog['fico_max']} < scenario FICO {fico}"

    loan = form.get("loan_amount")
    if loan is not None:
        if prog.get("loan_amt_min") is not None and int(prog["loan_amt_min"]) > int(loan):
            return f"Min loan ${int(prog['loan_amt_min']):,} > scenario ${int(loan):,}"
        if prog.get("loan_amt_max") is not None and int(prog["loan_amt_max"]) < int(loan):
            return f"Max loan ${int(prog['loan_amt_max']):,} < scenario ${int(loan):,}"

    if not _json_field_contains(prog.get("citizenship_types"), form["citizenship_code"]):
        allowed = prog.get("citizenship_types") or "any"
        return f"Citizenship {form['citizenship_code']!r} not allowed (program: {allowed})"

    if not _json_field_contains(prog.get("occupancy_types"), form["occupancy"]):
        return f"Occupancy {form['occupancy']!r} not in program occupancy_types"

    if not _json_field_contains(prog.get("property_type"), form["property_type_code"]):
        return f"Property type {form['property_type_code']!r} not allowed"

    if form["doc_type"] not in ("any", "dscr_rental"):
        allowed = prog.get("doc_types_allowed") or "any"
        if not _json_field_contains(allowed, form["doc_type"]) and not _json_field_contains(allowed, "any"):
            return f"Doc type {form['doc_type']!r} not in program doc_types_allowed"

    return "Failed program-level gate (dim_programs)"


def explain_layer2_rejection(
    prog: dict[str, Any],
    matrix_rows: list[dict[str, Any]],
    form: dict[str, Any],
) -> str:

    if not matrix_rows:
        return "No LTV matrix rows match scenario (FICO / DSCR / occupancy / doc filters)"

    valid = _rows_passing_ltv(matrix_rows, form["ltv"], form["cltv"], form["is_second_lien"])
    if not valid:
        max_ltvs = [r.get("max_ltv") for r in matrix_rows if r.get("max_ltv") is not None]
        cap = max(max_ltvs) if max_ltvs else None
        if cap is not None:
            return (
                f"LTV gate failed — scenario LTV {form['ltv']}% exceeds all matrix rows "
                f"(best max LTV {cap}%, {len(matrix_rows)} rows checked)"
            )
        return f"LTV gate failed — scenario LTV {form['ltv']}% exceeds all matrix rows"

    loan_amount = form.get("loan_amount")
    tier_valid = _rows_matching_loan_tier(valid, loan_amount)
    if not tier_valid:
        if loan_amount is not None:
            return (
                f"No matrix tier contains loan amount ${int(loan_amount):,} "
                f"after LTV/doc filters ({len(valid)} rows checked)"
            )
        return "No matrix tier matches loan amount"

    best = _pick_scenario_matrix_row(
        tier_valid, form["doc_type"], form["occupancy"], loan_amount, form.get("fico")
    )
    if best is None:
        return "No matrix row fits doc type + occupancy combination"

    scenario_max = _row_max_loan_cap(best, prog)
    if scenario_max is not None and loan_amount is not None and loan_amount > scenario_max:
        return (
            f"Loan amount ${int(loan_amount):,} exceeds scenario tier max "
            f"${int(scenario_max):,}"
        )

    return "Failed LTV matrix gate"


def explain_selection(cand: dict[str, Any], form: dict[str, Any]) -> str:
    parts: list[str] = ["Passed all eligibility layers"]
    if cand.get("is_dscr"):
        dscr = form.get("dscr")
        if dscr is not None:
            parts.append(f"DSCR path with scenario DSCR {dscr}")
    ltv = form.get("ltv")
    if ltv is not None:
        parts.append(f"matrix supports {ltv}% LTV")
    max_loan = cand.get("max_loan")
    if max_loan is not None:
        parts.append(f"max loan ${int(max_loan):,}")
    min_fico = cand.get("min_fico")
    if min_fico:
        parts.append(f"qualifying tier min FICO {min_fico}")
    bm = cand.get("best_match") or {}
    if isinstance(bm, dict):
        if bm.get("max_ltv_purchase") is not None:
            parts.append(f"purchase LTV cap {bm['max_ltv_purchase']}%")
    overlay = cand.get("special_overlay")
    if overlay:
        parts.append(f"overlay note: {str(overlay)[:120]}")
    return "; ".join(parts)


class EligibilityTraceCollector:
    """Tracks per-program matched/rejected status through the pipeline."""

    def __init__(self, all_programs: list[dict[str, Any]]) -> None:
        self._entries: dict[int, dict[str, Any]] = {}
        for prog in all_programs:
            pid = int(prog["program_id"])
            self._entries[pid] = {
                "program_id": pid,
                "lender_id": int(prog.get("lender_id") or 0),
                "lender": str(prog.get("brand_name") or prog.get("lender_name") or "?"),
                "label": _program_label(prog),
                "status": "pending",
                "layer": "",
                "reason": "",
            }
        self._prog_by_id = {int(p["program_id"]): p for p in all_programs}

    def reject(self, program_id: int, layer: str, reason: str) -> None:
        entry = self._entries.get(int(program_id))
        if entry is None or entry["status"] != "pending":
            return
        entry["status"] = "rejected"
        entry["layer"] = layer
        entry["reason"] = reason

    def mark_layer1_failures(self, layer1_ids: set[int], form: dict[str, Any]) -> None:
        for pid, prog in self._prog_by_id.items():
            if pid not in layer1_ids:
                self.reject(pid, LAYER1, explain_layer1_rejection(prog, form))

    def mark_layer2_failure(
        self,
        program_id: int,
        matrix_rows: list[dict[str, Any]],
        form: dict[str, Any],
    ) -> None:
        prog = self._prog_by_id.get(int(program_id), {})
        self.reject(int(program_id), LAYER2, explain_layer2_rejection(prog, matrix_rows, form))

    def is_pending(self, program_id: int) -> bool:
        entry = self._entries.get(int(program_id))
        return entry is not None and entry["status"] == "pending"

    def mark_layer_removals(
        self,
        before: set[int],
        after: set[int],
        layer: str,
        reason_map: dict[int, str],
        *,
        default_reason: str,
    ) -> None:
        for pid in before - after:
            self.reject(pid, layer, reason_map.get(pid, default_reason))

    def finalize_matches(self, candidates: list[dict[str, Any]], form: dict[str, Any]) -> None:
        matched_ids = {int(c["program_id"]) for c in candidates}
        for pid in matched_ids:
            entry = self._entries.get(pid)
            if entry is None:
                continue
            cand = next(c for c in candidates if int(c["program_id"]) == pid)
            entry["status"] = "matched"
            entry["layer"] = "Final"
            entry["reason"] = explain_selection(cand, form)

    def to_dict(self) -> dict[str, Any]:
        return {"programs": list(self._entries.values())}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EligibilityTraceCollector":
        collector = cls([])
        for entry in data.get("programs") or []:
            pid = int(entry["program_id"])
            collector._entries[pid] = dict(entry)
        return collector


def format_accepted_programs_string(trace: EligibilityTraceCollector) -> str:
    """One line per matched program: [id] Lender — Program."""
    entries = trace.to_dict()["programs"]
    matched = [e for e in entries if e["status"] == "matched"]
    lines = [
        f"[{e['program_id']:>2}] {e['label']}"
        for e in sorted(matched, key=lambda x: (x["lender_id"], x["program_id"]))
    ]
    return "\n".join(lines)


def format_rejected_programs_string(trace: EligibilityTraceCollector) -> str:
    """One line per rejected program: [id] Lender — Program — layer: reason."""
    entries = trace.to_dict()["programs"]
    rejected = [e for e in entries if e["status"] == "rejected"]
    lines = [
        f"[{e['program_id']:>2}] {e['label']} — {e['layer']}: {e['reason']}"
        for e in sorted(rejected, key=lambda x: (x["lender_id"], x["program_id"]))
    ]
    return "\n".join(lines)


def _format_scenario_summary(raw_form: dict[str, Any], form: dict[str, Any]) -> list[str]:
    lines = [
        f"  occupancy: {form.get('occupancy')}",
        f"  loan_purpose: {form.get('loan_purpose')}",
        f"  state: {form.get('state') or '(not set)'}",
        f"  loan_amount: {form.get('loan_amount')}",
        f"  ltv: {form.get('ltv')}%",
        f"  fico: {form.get('fico')}",
        f"  doc_type: {form.get('doc_type')}",
        f"  dscr: {form.get('dscr')}",
        f"  dti: {form.get('dti')}",
        f"  property_type: {form.get('property_type_code')}",
        f"  citizenship: {form.get('citizenship_code')}",
        f"  is_second_lien: {form.get('is_second_lien')}",
        f"  first_time_investor: {form.get('first_time_investor')}",
        f"  payment_history: {form.get('payment_history')}",
    ]
    if raw_form.get("prepaymentTerms"):
        lines.append(f"  prepayment_terms: {raw_form.get('prepaymentTerms')}")
    if raw_form.get("rentalType"):
        lines.append(f"  rental_type: {raw_form.get('rentalType')}")
    return lines


def format_trace_text(
    trace: EligibilityTraceCollector,
    *,
    form_id: str,
    raw_form: dict[str, Any],
    form: dict[str, Any],
    matched_count: int,
    total_active: int,
    quick: bool = False,
) -> str:
    entries = trace.to_dict()["programs"]
    matched = [e for e in entries if e["status"] == "matched"]
    rejected = [e for e in entries if e["status"] == "rejected"]
    pending = [e for e in entries if e["status"] == "pending"]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [
        "ELIGIBILITY TRACE LOG",
        f"Timestamp: {ts}",
        f"Form / session id: {form_id}",
        f"Mode: {'quick (SQL only)' if quick else 'full (SQL + Qdrant verify)'}",
        f"Result: {matched_count} matched / {total_active} active programs",
        "",
        "SCENARIO (normalized)",
        *_format_scenario_summary(raw_form, form),
        "",
    ]

    if matched:
        lines.append(f"MATCHED PROGRAMS ({len(matched)})")
        lines.append("-" * 72)
        for e in sorted(matched, key=lambda x: (x["lender_id"], x["program_id"])):
            lines.append(f"[MATCHED]  [{e['program_id']:>2}] {e['label']}")
            lines.append(f"           Why selected: {e['reason']}")
            lines.append("")
    else:
        lines.append("MATCHED PROGRAMS: none")
        lines.append("")

    by_lender: dict[str, list[dict[str, Any]]] = {}
    for e in sorted(rejected, key=lambda x: (x["lender_id"], x["program_id"])):
        by_lender.setdefault(e["lender"], []).append(e)

    lines.append(f"REJECTED PROGRAMS ({len(rejected)})")
    lines.append("-" * 72)
    for lender, items in by_lender.items():
        lines.append(f"\n{lender}")
        lines.append("-" * len(lender))
        for e in items:
            lines.append(f"[REJECTED] [{e['program_id']:>2}] {e['label']}")
            lines.append(f"           {e['layer']}: {e['reason']}")

    if pending:
        lines.append(f"\nUNRESOLVED ({len(pending)}) — no final status recorded")
        for e in pending:
            lines.append(f"  [{e['program_id']}] {e['label']}")

    from collections import Counter

    layer_counts = Counter(e["layer"] for e in rejected if e["layer"])
    if layer_counts:
        lines.append("\nREJECTION SUMMARY BY LAYER")
        for layer, count in layer_counts.most_common():
            lines.append(f"  {layer}: {count}")

    lines.append("")
    return "\n".join(lines)


def write_eligibility_trace_log(
    trace: EligibilityTraceCollector,
    *,
    form_id: str,
    raw_form: dict[str, Any],
    form: dict[str, Any],
    matched_count: int,
    total_active: int,
    quick: bool = False,
    logs_dir: Path | None = None,
) -> Path | None:
    """Write trace to logs/{timestamp}_{form_id}.txt. Returns path or None.

    The .txt side-file is a debug artifact, gated by ELIGIBILITY_TRACE (off by
    default) so logs/ cannot grow unbounded. The in-memory trace returned via
    result["program_trace"] (used by form-history / PDF) is unaffected.
    """
    if not trace_file_enabled():
        return None
    try:
        out_dir = logs_dir or LOGS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_id = re.sub(r"[^\w\-]+", "_", form_id)[:64]
        path = out_dir / f"{stamp}_{safe_id}.txt"
        body = format_trace_text(
            trace,
            form_id=form_id,
            raw_form=raw_form,
            form=form,
            matched_count=matched_count,
            total_active=total_active,
            quick=quick,
        )
        path.write_text(body, encoding="utf-8")
        prune_trace_logs(out_dir)
        _log.info("Eligibility trace written to %s", path)
        return path
    except Exception as exc:
        _log.warning("Failed to write eligibility trace log: %s", exc)
        return None


# ===== engine ==============================================================

"""
Eligibility engine — dim_* / map_* schema (9-layer shortlisting + Qdrant verify).

Layers:
  1) dim_programs filter          — program-level gates (Basics)
  2) map_ltv_matrix filter        — LTV/FICO/loan-amount matrix rows (Basics)
  2b) map_program_rule_guideline  — Basics overlay gates (citizenship/NPRA, property type, FTHB, …)
  3) map_program_fthb_eligibility — FTHB gate (when is_fthb=True)
  4) map_program_products         — product types
  5) map_geographic_restrictions  — state geo blocks / overlays
  6) map_credit_history_seasoning — credit event seasoning tiers
  7) map_housing_history_seasoning — housing payment history overlays
  7b) map_program_rule_guideline  — Extended overlay gates (rural, acreage, NOCB, conditions, …)
  8) map_program_rule_guideline   — informational notes (rag_notes)
  9) map_program_prepayment_options — prepayment terms (investment only)
 10) Qdrant mortgage_matrices     — cross-check chunks; hard-block on clear
                                     contradictions; surface ambiguous rules as
                                     "Additional considerations" (rag_notes)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

RuleOverlayStage = Literal["basics", "extended"]

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.connections.db import get_engine as _get_db_engine  # noqa: E402
from backend.connections.openai import get_openai  # noqa: E402
from backend.connections.qdrant import get_qdrant  # noqa: E402
from backend.eligibility_tolerance import (  # noqa: E402
    ELIG_DSCR_TOLERANCE,
    ELIG_FICO_TOLERANCE,
    ELIG_LOAN_TOLERANCE,
    ELIG_PCT_TOLERANCE,
    exceeds_loan,
    exceeds_pct,
    fico_meets_min,
    loan_within_tier,
)
from backend.utilities.notes import filter_notes_for_summarize  # noqa: E402


# Near-miss discovery widths — how far below a program's requirement the scenario
# can fall and still surface as a human-fixable "Just Missed" (rather than a hard
# exclusion). Kept modest so the hint is realistically actionable.
NEAR_MISS_FICO_RANGE = 40  # FICO points
NEAR_MISS_DTI_RANGE = 10.0  # DTI percentage points

_engine: Engine | None = None

# ---------------------------------------------------------------------------
# Doc-type constants (kept for upstream compatibility)
# ---------------------------------------------------------------------------

BANK_STMT_COMBINED_CODE = "bank_stmt_12_or_24"
BANK_STMT_MATRIX_CODES = frozenset({"bank_stmt_12", "bank_stmt_24"})

CANONICAL_DOC_TYPES = frozenset(
    {
        "full_doc",
        "bank_stmt_12",
        "bank_stmt_24",
        BANK_STMT_COMBINED_CODE,
        "bank_stmt_business",
        "pl_only",
        "pl_2mo_bs",
        "asset_util",
        "asset_qualifier",
        "1099",
        "wvoe",
        "dscr_rental",
        "itin",
        "non_traditional",
        "any",
    }
)

ALL_DOC_TYPE_DISPLAY_CODES = (
    "full_doc",
    BANK_STMT_COMBINED_CODE,
    "bank_stmt_business",
    "pl_only",
    "pl_2mo_bs",
    "asset_util",
    "asset_qualifier",
    "1099",
    "wvoe",
    "dscr_rental",
    "itin",
    "non_traditional",
)

DOC_TYPE_LABELS: dict[str, str] = {
    "full_doc": "Full Documentation",
    "bank_stmt_12": "Bank Statements (12 or 24 Months)",
    "bank_stmt_24": "Bank Statements (12 or 24 Months)",
    BANK_STMT_COMBINED_CODE: "Bank Statements (12 or 24 Months)",
    "bank_stmt_business": "Bank Statements (Business)",
    "pl_only": "P&L Only",
    "pl_2mo_bs": "P&L with 2 month Bank Statement",
    "wvoe": "WVOE Only",
    "asset_util": "Asset Utilization",
    "asset_qualifier": "Asset Qualifier",
    "1099": "1099",
    "dscr_rental": "Rental Income",
    "itin": "ITIN",
    "non_traditional": "Alternative Documentation",
    "any": "Any",
}

# ---------------------------------------------------------------------------
# Lender brand mapping
# ---------------------------------------------------------------------------

LENDER_BRAND: dict[int, str] = {1: "Denali", 2: "Summit", 3: "Everest"}


# ---------------------------------------------------------------------------
# Engine singleton
# ---------------------------------------------------------------------------


def _get_engine() -> Engine:
    return _get_db_engine()


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).replace(",", "").replace("$", "").replace("%", "").strip()))
    except (TypeError, ValueError):
        return default


def _to_int_opt(v: Any) -> int | None:
    try:
        s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        if not s:
            return None
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _to_float_opt(v: Any) -> float | None:
    try:
        s = str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        if not s:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _extract_years(value: str) -> float | None:
    """Parse a years-since-event value from a bucket string or combined credit event string.

    Bucket strings come from the frontend: "<1 year", "1-2 years", "2-3 years", etc.
    When parsing a combined string like "Bankruptcy Chapter 7 2-3 years" we must match the
    trailing years pattern, not the first digit (which would grab 7 from "Chapter 7").
    """
    if not value:
        return None
    s = value.strip()
    # "<1 year" / legacy "<2 years" — treat conservatively as just-under
    if re.search(r"<\s*1\s+year", s, re.IGNORECASE):
        return 0.5
    if re.search(r"<\s*2\s+year", s, re.IGNORECASE):
        return 1.0
    # "N+ years" — "7+ years" (legacy payloads may still use "4+ years")
    m = re.search(r"(\d+)\s*\+\s*year", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # "N-M years" — "2-3 years", "3-4 years" → use lower bound
    m = re.search(r"(\d+)\s*[-–]\s*\d+\s*year", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # "N years"
    m = re.search(r"(\d+(?:\.\d+)?)\s+year", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Last resort: first standalone number (avoid grabbing from "Chapter 7" etc.)
    m = re.search(r"(?<!\w)(\d+(?:\.\d+)?)(?!\s*(?:chapter|unit|year|%|,|\.))", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _resolve_doc_type(raw_doc: str, doc_map: dict[str, str]) -> str:
    """Normalize API/UI documentation string to matrix doc_type code."""
    key = (raw_doc or "").strip().lower()
    if not key:
        return "any"
    code_key = key.replace(" ", "_")
    if code_key in CANONICAL_DOC_TYPES:
        return code_key
    return doc_map.get(key, "any")


def _canonical_doc_type(raw: str) -> str | None:
    s = (raw or "").strip().lower().replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    mapping = {
        "full_doc": "full_doc",
        "full doc": "full_doc",
        "full documentation": "full_doc",
        BANK_STMT_COMBINED_CODE: BANK_STMT_COMBINED_CODE,
        "bank_stmt_12": "bank_stmt_12",
        "bank_stmt_24": "bank_stmt_24",
        "bank statement 12": BANK_STMT_COMBINED_CODE,
        "bank statement 24": BANK_STMT_COMBINED_CODE,
        "bank statement": BANK_STMT_COMBINED_CODE,
        "bank statements": BANK_STMT_COMBINED_CODE,
        "bank statements (12 or 24)": BANK_STMT_COMBINED_CODE,
        "bank statements (12 or 24 months)": BANK_STMT_COMBINED_CODE,
        "12 month bank statements": BANK_STMT_COMBINED_CODE,
        "24 month bank statements": BANK_STMT_COMBINED_CODE,
        "bank_stmt_business": "bank_stmt_business",
        "bank statements (business)": "bank_stmt_business",
        "bank statements business": "bank_stmt_business",
        "bank statement business": "bank_stmt_business",
        "pl_only": "pl_only",
        "pl_2mo_bs": "pl_2mo_bs",
        "pl 2mo bs": "pl_2mo_bs",
        "p&l only": "pl_only",
        "p&l": "pl_only",
        "p and l": "pl_only",
        "profit and loss": "pl_only",
        "p&l with 2 month bank statement": "pl_2mo_bs",
        "p&l with 2 month bank statements": "pl_2mo_bs",
        "p&l with 2 month bank statements": "pl_2mo_bs",
        "wvoe": "wvoe",
        "asset_util": "asset_util",
        "asset util": "asset_util",
        "asset utilization": "asset_util",
        "asset_qualifier": "asset_qualifier",
        "asset qualifier": "asset_qualifier",
        "1099": "1099",
        "dscr_rental": "dscr_rental",
        "dscr": "dscr_rental",
        "itin": "itin",
        "non_traditional": "non_traditional",
        "alt doc": "non_traditional",
        "alternative documentation": "non_traditional",
        "any": "any",
    }
    if s in mapping:
        return mapping[s]
    if "bank" in s and "statement" in s:
        return BANK_STMT_COMBINED_CODE
    if "dscr" in s:
        return "dscr_rental"
    if "itin" in s:
        return "itin"
    if "alt" in s or "non traditional" in s:
        return "non_traditional"
    return None


def _parse_allowed_docs(raw: Any) -> set[str]:
    vals: list[str] = []
    if raw is None:
        return set()
    if isinstance(raw, list):
        vals = [str(x) for x in raw]
    elif isinstance(raw, str):
        txt = raw.strip()
        if not txt:
            return set()
        try:
            j = json.loads(txt)
            if isinstance(j, list):
                vals = [str(x) for x in j]
            else:
                vals = [txt]
        except Exception:
            vals = re.split(r"[,;|]", txt)
    else:
        vals = [str(raw)]
    out: set[str] = set()
    for v in vals:
        c = _canonical_doc_type(v)
        if c:
            out.add(c)
    return out


_OCC_CODE_TO_DISPLAY: dict[str, str] = {
    "primary": "Primary Residence",
    "second": "Second Home",
    "secondary": "Second Home",
    "investment": "Investment Property",
}


def _parse_occupancy_types(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        codes = raw
    elif isinstance(raw, str):
        try:
            codes = json.loads(raw)
        except Exception:
            return []
    else:
        return []
    return [_OCC_CODE_TO_DISPLAY.get(str(c).lower(), str(c).title()) for c in codes if c]


def _parse_json_code_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt or txt == "[]":
            return []
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, list):
                return [str(c).strip() for c in parsed if str(c).strip()]
        except Exception:
            return re.split(r"[,;|]", txt)
    return []


_PURPOSE_CODE_TO_DISPLAY: dict[str, str] = {
    "purchase": "Purchase",
    "rate_term": "Rate & Term",
    "cash_out": "Cash-Out Refinance",
}


def _parse_loan_purposes_allowed(raw: Any) -> list[str]:
    return [
        _PURPOSE_CODE_TO_DISPLAY.get(c.lower(), c.replace("_", " ").title())
        for c in _parse_json_code_list(raw)
    ]


def _parse_property_types_allowed(raw: Any) -> list[str]:
    codes = _parse_json_code_list(raw)
    if not codes:
        return []
    return [c.replace("_", " ").title() for c in codes]


def _format_doc_types_allowed(raw: Any) -> str:
    allowed = _parse_allowed_docs(raw)
    if not allowed or "any" in allowed:
        return _all_doc_types_display()
    display_codes: list[str] = []
    has_bank = bool(allowed & BANK_STMT_MATRIX_CODES) or BANK_STMT_COMBINED_CODE in allowed
    for c in sorted(allowed):
        if c in BANK_STMT_MATRIX_CODES or c == BANK_STMT_COMBINED_CODE:
            continue
        display_codes.append(c)
    if has_bank:
        display_codes.append(BANK_STMT_COMBINED_CODE)
    labels = [DOC_TYPE_LABELS.get(c, c.replace("_", " ").title()) for c in display_codes]
    return ", ".join(labels)


def _all_doc_types_display() -> str:
    labels = [DOC_TYPE_LABELS.get(c, c.replace("_", " ").title()) for c in ALL_DOC_TYPE_DISPLAY_CODES]
    return ", ".join(labels)


def _format_product_acronyms(name: str) -> str:
    out = " ".join(w.title() for w in (name or "").split())
    return re.sub(r"\bItin\b", "ITIN", re.sub(r"\bDscr\b", "DSCR", out))


def _format_products_label(names: list[str]) -> str:
    return ", ".join(_format_product_acronyms(n) for n in names)


def _matrix_doc_type_matches_form(matrix_doc: str | None, form_doc: str) -> bool:
    if form_doc == "any":
        return True
    md = (matrix_doc or "").strip().lower()
    if md == "same as program":
        return True
    if form_doc == BANK_STMT_COMBINED_CODE:
        return md in BANK_STMT_MATRIX_CODES or md == "any"
    return md == form_doc or md == "any"


def _display_program_name(row: dict) -> str:
    """NewPoint program label — prefer program_name_np verbatim (no lender prefix)."""
    return (row.get("program_name_np") or "").strip() or (row.get("program_name") or "").strip()


# ---------------------------------------------------------------------------
# _normalise_form
# ---------------------------------------------------------------------------


def _loan_purpose_for_matching(
    raw_purpose: str,
    purpose_map: dict[str, str],
    *,
    second_lien_product: str,
    lien_position: str,
    is_second_lien: bool,
) -> str:
    """
    Map UI loan purpose to dim_programs / matrix codes.

    Standalone HELOC is cash-out-only in the catalog (e.g. VMC_HELOC). When the
  wizard still shows Rate & Term Refinance, treat purpose as cash_out for matching
  only — UI labels are unchanged.
    """
    purpose = purpose_map.get(raw_purpose, "purchase")
    standalone_heloc = second_lien_product == "heloc" and (
        lien_position == "second_lien" or (not lien_position and is_second_lien)
    )
    if standalone_heloc and purpose == "rate_term":
        return "cash_out"
    return purpose


def _resolved_visa_type(raw: dict[str, Any]) -> str:
    """Visa code from wizard — uses free-text when category is Other / Not Listed."""
    cat = str(raw.get("visaCategory") or "").strip().lower()
    if "other" in cat and "listed" in cat:
        return str(raw.get("visaTypeOther") or raw.get("visaType") or "").strip()
    return str(raw.get("visaType") or "").strip()


def _parse_yes_no(val: object) -> bool | None:
    v = str(val or "").strip().lower()
    if v in {"yes", "y", "true", "1"}:
        return True
    if v in {"no", "n", "false", "0"}:
        return False
    return None


def _empty_eligibility_result(form: dict[str, Any]) -> dict[str, Any]:
    return {
        "eligible": [],
        "near_misses": [],
        "geo_blocked": {},
        "overlay_blocked": {},
        "geo_exclusions": [],
        "overlay_exclusions": [],
        "rag_ineligible": [],
        "total_screened": 0,
        "form": form,
        "program_trace": None,
    }


def _normalise_form(raw: dict[str, Any]) -> dict[str, Any]:
    occupancy_map = {
        "primary residence": "primary",
        "primary": "primary",
        "second home": "second",
        "second": "second",
        "investment property": "investment",
        "investment": "investment",
        "non-owner": "investment",
    }
    purpose_map = {
        "purchase": "purchase",
        "refinance": "rate_term",
        "rate & term refinance": "rate_term",
        "rate & term refi": "rate_term",
        "rate and term refinance": "rate_term",
        "r&t refi": "rate_term",
        "rate term": "rate_term",
        "rate_term": "rate_term",
        "cash-out refinance": "cash_out",
        "cash out refinance": "cash_out",
        "cash out": "cash_out",
        "cash_out": "cash_out",
        "debt consolidation": "cash_out",
    }
    doc_map = {
        "full documentation": "full_doc",
        "full doc": "full_doc",
        "full": "full_doc",
        "bank statements": BANK_STMT_COMBINED_CODE,
        "bank statements (12 or 24)": BANK_STMT_COMBINED_CODE,
        "bank statements (12 or 24 months)": BANK_STMT_COMBINED_CODE,
        "bank statement": BANK_STMT_COMBINED_CODE,
        "12-month bank statements": BANK_STMT_COMBINED_CODE,
        "12 month bank statements": BANK_STMT_COMBINED_CODE,
        "bank statement 12": BANK_STMT_COMBINED_CODE,
        "24-month bank statements": BANK_STMT_COMBINED_CODE,
        "24 month bank statements": BANK_STMT_COMBINED_CODE,
        "bank statement 24": BANK_STMT_COMBINED_CODE,
        BANK_STMT_COMBINED_CODE: BANK_STMT_COMBINED_CODE,
        "bank_stmt_12_or_24": BANK_STMT_COMBINED_CODE,
        "bank statements (business)": "bank_stmt_business",
        "bank statements business": "bank_stmt_business",
        "bank statement business": "bank_stmt_business",
        "bank_stmt_business": "bank_stmt_business",
        "p&l only": "pl_only",
        "profit and loss": "pl_only",
        "p&l": "pl_only",
        "p and l": "pl_only",
        "pl only": "pl_only",
        "pl_2mo_bs": "pl_2mo_bs",
        "pl 2mo bs": "pl_2mo_bs",
        "p&l with 2 month bank statement": "pl_2mo_bs",
        "p&l with 2-month bank statements": "pl_2mo_bs",
        "p&l with 2 month bank statements": "pl_2mo_bs",
        "wvoe only": "wvoe",
        "wvoe": "wvoe",
        "asset utilization": "asset_util",
        "asset util": "asset_util",
        "asset qualifier": "asset_qualifier",
        "asset qualifer": "asset_qualifier",
        "1099": "1099",
        "dscr": "dscr_rental",
        "itin": "itin",
        "alternative documentation": "non_traditional",
        "alt doc": "non_traditional",
        "non traditional": "non_traditional",
        "non-traditional": "non_traditional",
        "any": "any",
    }

    # citizenship → DB code
    citizenship_map = {
        "u.s. citizen": "us_citizen",
        "us citizen": "us_citizen",
        "u.s citizen": "us_citizen",
        "": "us_citizen",
        "permanent resident": "perm_resident",
        "permanent resident alien": "perm_resident",
        "non-permanent resident alien": "non_perm_resident",
        "non-permanent": "non_perm_resident",
        "non permanent resident alien": "non_perm_resident",
        "non permanent": "non_perm_resident",
        "foreign national": "foreign_national",
        "itin": "itin",
        "daca": "daca",
    }

    # property type form label → DB code (canonical codes pass through)
    property_type_map = {
        "single_family": "single_family",
        "single family": "single_family",
        "single family residence": "single_family",
        "sfr": "single_family",
        "pud": "pud",
        "townhouse": "townhouse",
        "townhome": "townhouse",
        "condo_warrantable": "condo_warrantable",
        "warrantable condo": "condo_warrantable",
        "condo warrantable": "condo_warrantable",
        "condominium warrantable": "condo_warrantable",
        "condo_non_warrantable": "condo_non_warrantable",
        "non-warrantable condo": "condo_non_warrantable",
        "condo non warrantable": "condo_non_warrantable",
        "condominium non-warrantable": "condo_non_warrantable",
        "condotel": "condotel",
        "two_to_four_family": "two_to_four_family",
        "2-4 unit": "two_to_four_family",
        "2-4unit": "two_to_four_family",
        "2 4 unit": "two_to_four_family",
        "multi-family": "two_to_four_family",
        "multi family": "two_to_four_family",
        "multifamily": "two_to_four_family",
        "five_to_eight_unit": "five_to_eight_unit",
        "five_to_eight_family": "five_to_eight_unit",
        "5-8 unit": "five_to_eight_unit",
        "5-8unit": "five_to_eight_unit",
        "5 8 unit": "five_to_eight_unit",
        "5-9 unit multifamily": "five_to_eight_unit",
        "mixed_use": "mixed_use",
        "mixed use": "mixed_use",
        "mixed-use": "mixed_use",
        "manufactured_home": "manufactured_home",
        "manufactured": "manufactured_home",
        "cooperative": "cooperative",
        "commercial": "commercial",
    }

    # credit event type → DB event_type code
    credit_event_map = {
        "bankruptcy": "bk",
        "chapter 7": "bk",
        "chapter 13": "bk",
        "bk": "bk",
        "foreclosure": "fc",
        "fc": "fc",
        "short sale": "ss",
        "ss": "ss",
        "deed in lieu": "dl",
        "dil": "dl",
        "dl": "dl",
        "mortgage late": "late",
        "late": "late",
        "lates": "late",
        "settlement": "settlement",
        "pre-fc": "fc",
        "pre-foreclosure": "fc",
        "charge-off": "mc",
        "mortgage charge-off": "mc",
        "mc": "mc",
        "loan modification": "mo",
        "mo": "mo",
        "nod": "nd",
        "notice of default": "nd",
        "nd": "nd",
        "forbearance": "fd",
        "fd": "fd",
        "deferral": "fd",
    }

    raw_occ = (raw.get("occupancy") or "").strip().lower()
    raw_purpose = (raw.get("primaryLoanPurpose") or raw.get("loanPurpose") or "").strip().lower()
    raw_doc = (raw.get("documentationType") or "").strip().lower()
    raw_credit_event = (raw.get("creditEvent") or "").strip()
    raw_credit_event_type = (raw.get("creditEventType") or "").strip()
    raw_years_since_event = (raw.get("yearsSinceEvent") or "").strip()
    raw_citizenship = (raw.get("citizenship") or "").strip()
    raw_second_lien = (raw.get("isSecondLien") or "").strip().lower()
    # Prefer lienPosition if provided
    _lien_pos = (raw.get("lienPosition") or "").strip().lower()
    if _lien_pos:
        raw_second_lien = "yes" if _lien_pos in {"second_lien", "second_lien_piggyback"} else "no"
    raw_fthb = (raw.get("firstTimeHomebuyer") or "").strip().lower()
    raw_first_time_investor = (raw.get("firstTimeInvestor") or "").strip().lower()
    raw_rental_type = (raw.get("rentalType") or "").strip().lower()
    raw_payment_history = (raw.get("paymentHistory") or "").strip()

    # Normalize property type
    raw_prop = (raw.get("propertyType") or "").strip()
    prop_lower = raw_prop.lower()
    # Legacy normalization: 2-unit / 3-4 unit → two_to_four_family
    if prop_lower in ("2-unit", "3-4 unit"):
        raw_prop = "two_to_four_family"
        prop_lower = "two_to_four_family"
    property_type_code = property_type_map.get(prop_lower, prop_lower)

    # Citizenship code
    cit_lower = raw_citizenship.lower()
    citizenship_code = citizenship_map.get(cit_lower, "us_citizen")

    # Credit event type code
    cet_lower = raw_credit_event_type.lower()
    credit_event_type_code = credit_event_map.get(cet_lower)
    if credit_event_type_code is None and cet_lower:
        # Try partial match
        for key, val in credit_event_map.items():
            if key in cet_lower:
                credit_event_type_code = val
                break
    # Also check creditEvent field if creditEventType is empty
    if not credit_event_type_code and raw_credit_event:
        cre_lower = raw_credit_event.lower()
        credit_event_type_code = credit_event_map.get(cre_lower)
        if credit_event_type_code is None:
            for key, val in credit_event_map.items():
                if key in cre_lower:
                    credit_event_type_code = val
                    break

    years_since_event = _extract_years(raw_years_since_event)
    if years_since_event is None and raw_credit_event:
        years_since_event = _extract_years(raw_credit_event)

    # ── Step-5 product preferences ────────────────────────────────────────────
    raw_loan_term = (raw.get("loanTerm") or "").strip()
    raw_io_pref = (raw.get("interestOnlyPref") or "").strip().lower()
    raw_rate_type = (raw.get("rateTypePref") or "").strip().lower()

    loan_term_years_list = _parse_loan_term_years_list(raw_loan_term)
    loan_term_years: int | None = loan_term_years_list[0] if len(loan_term_years_list) == 1 else None

    io_pref: bool | None = None
    if raw_io_pref == "yes":
        io_pref = True
    elif raw_io_pref == "no":
        io_pref = False

    rate_type_pref: str | None = None
    if "arm" in raw_rate_type or "adjustable" in raw_rate_type:
        rate_type_pref = "arm"
    elif "flat" in raw_rate_type or "fixed" in raw_rate_type:
        rate_type_pref = "fixed"

    lien_pos_norm = _lien_pos  # already normalised to lowercase above
    second_lien_product = (raw.get("secondLienProduct") or "").strip().lower()
    is_second_lien_bool = raw_second_lien in {"yes", "y", "true", "1"}
    loan_purpose = _loan_purpose_for_matching(
        raw_purpose,
        purpose_map,
        second_lien_product=second_lien_product,
        lien_position=lien_pos_norm,
        is_second_lien=is_second_lien_bool,
    )

    raw_nocb = (raw.get("nonOccupantCoBorrower") or "").strip().lower()
    nocb_active = raw_nocb in {"yes", "y", "true", "1"}
    raw_combined_dti = (raw.get("combinedDti") or "").strip()
    if nocb_active and raw_combined_dti:
        effective_dti = _to_float(raw_combined_dti)
    else:
        effective_dti = _to_float(raw.get("estimatedDti"))

    raw_has_us = (raw.get("hasUsCredit") or "").strip().lower()
    if citizenship_code == "foreign_national" and raw_has_us == "no":
        fico_opt: int | None = None
    else:
        fico_opt = _to_int_opt(raw.get("decisionCreditScore"))

    income_path_raw = (
        (raw.get("investmentIncomePath") or raw.get("qualificationPath") or "").strip().lower()
    )
    income_path: str | None = None
    if income_path_raw in {"dscr", "income"}:
        income_path = income_path_raw

    resolved_doc = _resolve_doc_type(raw_doc, doc_map)
    if income_path == "dscr":
        resolved_doc = "dscr_rental"

    return {
        "loan_amount": _to_int_opt(raw.get("loanAmount")),
        "ltv": _to_float(raw.get("ltv")),
        "cltv": _to_float(raw.get("cltv") or raw.get("ltv")),
        "fico": fico_opt,
        "dti": effective_dti,
        "dscr": _to_float_opt(raw.get("dscr")),
        "occupancy": occupancy_map.get(raw_occ, "any"),
        "loan_purpose": loan_purpose,
        "property_type": raw_prop,           # original label (kept for display)
        "property_type_code": property_type_code,  # DB code
        "state": (raw.get("state") or "").strip().upper(),
        "county": (raw.get("stateCounty") or raw.get("county") or "").strip(),
        "city": (raw.get("stateCity") or raw.get("stateBorough") or raw.get("city") or "").strip(),
        "zip_code": (raw.get("stateZipCode") or "").strip(),
        "is_in_baltimore_city": (raw.get("isInBaltimoreCity") or "").strip().lower() in {"yes", "y", "true", "1", "baltimore city"},
        "is_in_philadelphia": (raw.get("isInPhiladelphia") or "").strip().lower() in {"yes", "y", "true", "1", "philadelphia"},
        "is_in_indianapolis": (raw.get("isInIndianapolis") or "").strip().lower() in {"yes", "y", "true", "1", "indianapolis"},
        "is_in_memphis": (raw.get("isInMemphis") or "").strip().lower() in {"yes", "y", "true", "1", "memphis"},
        "is_in_lubbock": (
            (raw.get("isInLubbock") or "").strip().lower() in {"yes", "y", "true", "1", "lubbock"}
            or (raw.get("stateCity") or "").strip().lower() == "lubbock"
        ),
        "is_in_paterson": (
            (raw.get("isInPaterson") or "").strip().lower() in {"yes", "y", "true", "1", "paterson", "patterson"}
            or (raw.get("stateCity") or "").strip().lower() in {"paterson", "patterson"}
        ),
        # Hawaii lava zone — Zones 1 & 2 are program-ineligible
        "hi_lava_zone": (raw.get("hiLavaZone") or "").strip(),
        "hi_lava_blocked": (raw.get("hiLavaZone") or "").strip() in {"Zone 1", "Zone 2"},
        # Rural property and NOCB — stored for notes/overlays
        "is_rural_property": (raw.get("isRuralProperty") or "").strip().lower() in {"yes", "true", "1"},
        "acreage": _to_float_opt(raw.get("acreage")),
        "non_occupant_cob": nocb_active,
        "doc_type": resolved_doc,
        "citizenship_raw": raw_citizenship,
        "citizenship_code": citizenship_code,
        # Legacy bucket kept for backward compat
        "citizenship_bucket": citizenship_code,
        "is_second_lien": raw_second_lien in {"yes", "y", "true", "1"},
        # Legacy key alias
        "is_second_lien_req": raw_second_lien in {"yes", "y", "true", "1"},
        "is_fthb": raw_fthb in {"yes", "y", "true", "1"},
        # Legacy key alias
        "is_fthb_req": raw_fthb in {"yes", "y", "true", "1"},
        "first_time_investor": raw_first_time_investor in {"yes", "y", "true", "1"},
        "is_short_term_rental": "short" in raw_rental_type,
        "payment_history": raw_payment_history,
        "credit_event": raw_credit_event,
        "credit_event_type": raw_credit_event_type,
        "credit_event_type_code": credit_event_type_code,
        "years_since_event": years_since_event,
        # Product preference filters
        "loan_term_years": loan_term_years,
        "loan_term_years_list": loan_term_years_list,
        "io_pref": io_pref,
        "rate_type_pref": rate_type_pref,
        # Second-lien product detail (Step B3)
        "second_lien_product": second_lien_product,
        "is_piggyback": lien_pos_norm == "second_lien_piggyback" if lien_pos_norm else False,
        "lien_position": lien_pos_norm,
        # Scenario consideration fields (Basics → Conditions)
        "ofac_sanctioned": _parse_yes_no(raw.get("ofacSanctioned")) is True,
        "has_us_credit": _parse_yes_no(raw.get("hasUsCredit")),
        "visa_type": _resolved_visa_type(raw),
        "established_primary_res": _parse_yes_no(raw.get("establishedPrimaryRes")),
        "income_path": income_path,
        "income_path_only": income_path == "income",
        "prepay_stepdown": (raw.get("prepayStepdown") or "").strip(),
        "prepayment_terms_raw": (raw.get("prepaymentTerms") or "").strip(),
        "listing_seasoning_yes": _parse_yes_no(raw.get("listingSeasoning")) is True,
        "power_of_attorney": _parse_yes_no(raw.get("powerOfAttorney")),
        "non_arms_length": _parse_yes_no(raw.get("nonArmsLength")),
        "visa_is_custom": _visa_is_custom(raw, _resolved_visa_type(raw)),
    }


# ---------------------------------------------------------------------------
# Layer 1 — dim_programs filter
# ---------------------------------------------------------------------------


def _layer1_programs(conn: Any, form: dict[str, Any], quick: bool = False) -> list[dict[str, Any]]:
    """
    Filter dim_programs by program-level gates.
    Returns list of row dicts (one per program).
    """
    is_second_lien = 1 if form["is_second_lien"] else 0
    citizenship_code = form["citizenship_code"]
    occupancy = form["occupancy"]
    loan_purpose = form["loan_purpose"]
    property_type_code = form["property_type_code"]
    doc_type = form["doc_type"]
    loan_amount = form["loan_amount"]
    fico = form["fico"]
    dti = form["dti"]
    dscr = form["dscr"]
    is_short_term = form.get("is_short_term_rental", False)

    # Citizenship clause — quick scan skips until user picks citizenship
    if quick and form.get("quick_skip_citizenship"):
        cit_clause = "1=1"
    else:
        cit_clause = (
            "(p.citizenship_types IS NULL "
            " OR JSON_CONTAINS(p.citizenship_types, JSON_QUOTE(:citizenship_code)))"
        )

    # Occupancy clause — quick mode skips when not provided
    if quick and occupancy in ("any", ""):
        occ_clause = "1=1"
    else:
        occ_clause = (
            "(p.occupancy_types IS NULL "
            " OR JSON_CONTAINS(p.occupancy_types, JSON_QUOTE(:occupancy)))"
        )

    # Loan purpose clause — quick mode skips when not provided
    if quick and loan_purpose in ("any", ""):
        purpose_clause = "1=1"
    else:
        purpose_clause = (
            "(p.loan_purposes_allowed IS NULL "
            " OR JSON_CONTAINS(p.loan_purposes_allowed, JSON_QUOTE(:loan_purpose)))"
        )

    # Property type clause — quick mode skips when not provided
    if quick and not property_type_code:
        prop_clause = "1=1"
    else:
        prop_clause = (
            "(p.property_type IS NULL "
            " OR JSON_CONTAINS(p.property_type, JSON_QUOTE(:property_type_code)))"
        )

    # Doc type clause
    if doc_type in ("any", "dscr_rental"):
        doc_clause = "1=1"
    elif doc_type == BANK_STMT_COMBINED_CODE:
        doc_clause = (
            "(p.doc_types_allowed IS NULL OR p.doc_types_allowed = '' OR p.doc_types_allowed = '[]' "
            " OR JSON_CONTAINS(p.doc_types_allowed, JSON_QUOTE('any')) "
            " OR JSON_CONTAINS(p.doc_types_allowed, JSON_QUOTE('bank_stmt_12')) "
            " OR JSON_CONTAINS(p.doc_types_allowed, JSON_QUOTE('bank_stmt_24')))"
        )
    else:
        doc_clause = (
            "(p.doc_types_allowed IS NULL OR p.doc_types_allowed = '' OR p.doc_types_allowed = '[]' "
            " OR JSON_CONTAINS(p.doc_types_allowed, JSON_QUOTE('any')) "
            f" OR JSON_CONTAINS(p.doc_types_allowed, JSON_QUOTE('{doc_type}')))"
        )

    # DSCR vs income path
    if form.get("income_path_only") and not form.get("quick_skip_dscr"):
        dscr_clause = "p.is_dscr_program = 0"
    elif doc_type == "dscr_rental" or (dscr is not None and dti == 0.0):
        # DSCR path
        if is_short_term:
            dscr_clause = (
                "p.is_dscr_program = 1 "
                "AND (p.dscr_min_short_term IS NULL OR :dscr IS NULL OR p.dscr_min_short_term <= :dscr)"
            )
        else:
            dscr_clause = (
                "p.is_dscr_program = 1 "
                "AND (p.dscr_min_long_term IS NULL OR :dscr IS NULL OR p.dscr_min_long_term <= :dscr)"
            )
    else:
        dscr_clause = (
            "p.is_dscr_program = 0 "
            "AND (p.max_dti IS NULL OR :dti IS NULL OR p.max_dti >= :dti)"
        )

    # Second-lien clause — quick mode skips when isSecondLien was not explicitly set
    if quick and form.get("quick_skip_lien"):
        second_lien_clause = "1=1"
    else:
        second_lien_clause = "p.is_second_lien = :is_second_lien"

    # DSCR clause — quick mode includes both DSCR and non-DSCR when income path not set
    if quick and form.get("quick_skip_dscr"):
        dscr_clause = "1=1"

    sql_str = f"""
        SELECT
            p.program_id,
            p.lender_id,
            p.program_code,
            p.program_name,
            p.program_name_np,
            p.is_second_lien,
            p.second_lien_details,
            p.is_dscr_program,
            p.citizenship_types,
            p.loan_amt_min,
            p.loan_amt_max,
            p.fico_min,
            p.fico_max,
            p.max_dti,
            p.dscr_min_long_term,
            p.dscr_min_short_term,
            p.doc_types_allowed,
            p.occupancy_types,
            p.property_type,
            p.loan_purposes_allowed,
            p.is_active,
            p.notes,
            l.code AS lender_code,
            l.brand_name,
            l.lender_name
        FROM dim_programs p
        INNER JOIN dim_lenders l ON l.id = p.lender_id
        WHERE p.is_active = 1
          AND {second_lien_clause}
          AND {cit_clause}
          AND {occ_clause}
          AND {purpose_clause}
          AND {prop_clause}
          AND {doc_clause}
          AND (:loan_amount IS NULL OR p.loan_amt_min IS NULL OR p.loan_amt_min <= :loan_amount_for_min)
          AND (:loan_amount IS NULL OR p.loan_amt_max IS NULL OR p.loan_amt_max >= :loan_amount_for_max)
          AND (:fico IS NULL OR p.fico_min IS NULL OR p.fico_min <= :fico_for_min)
          AND {dscr_clause}
    """

    params: dict[str, Any] = {
        "is_second_lien": is_second_lien,
        "citizenship_code": citizenship_code,
        "occupancy": occupancy,
        "loan_purpose": loan_purpose,
        "property_type_code": property_type_code,
        "loan_amount": loan_amount,
        "loan_amount_for_min": (
            int(loan_amount) + ELIG_LOAN_TOLERANCE if loan_amount is not None else None
        ),
        "loan_amount_for_max": (
            int(loan_amount) - ELIG_LOAN_TOLERANCE if loan_amount is not None else None
        ),
        "fico": fico,
        "fico_for_min": int(fico) + ELIG_FICO_TOLERANCE if fico is not None else None,
        "dti": float(dti) - ELIG_PCT_TOLERANCE if dti is not None else None,
        "dscr": float(dscr) - ELIG_DSCR_TOLERANCE if dscr is not None else None,
    }

    try:
        rows = conn.execute(text(sql_str), params).fetchall()
    except Exception as exc:
        if "second_lien_details" in str(exc):
            # Migration 012 not yet applied — retry without the column
            sql_str = sql_str.replace("\n            p.second_lien_details,", "")
            rows = conn.execute(text(sql_str), params).fetchall()
        else:
            raise
    programs = [dict(r._mapping) for r in rows]

    # ── Second-lien product detail filter (Step B4) ───────────────────────────
    # Only applies when the scenario is a second-lien request.
    if form.get("is_second_lien"):
        product = form.get("second_lien_product", "")   # "heloc" | "heloan" | ""
        is_piggyback = form.get("is_piggyback", False)

        # Map scenario → required tag (dim_programs.second_lien_details JSON):
        #   heloan  = closed_ended (closed-end second)
        #   heloc   = heloc
        #   piggyback = piggyback
        if is_piggyback:
            required_tag = "piggyback"
        elif product == "heloc":
            required_tag = "heloc"
        elif product == "heloan":
            required_tag = "closed_ended"
        else:
            required_tag = ""   # product not specified — no product filter

        # Filter when we know structure or product (piggyback has no second_lien_product)
        if required_tag and (
            form.get("is_piggyback")
            or form.get("second_lien_product")
            or form.get("lien_position")
        ):
            filtered: list[dict[str, Any]] = []
            for prog in programs:
                detail = prog.get("second_lien_details")
                if detail is None:
                    # NULL = no restriction → pass
                    filtered.append(prog)
                    continue
                if isinstance(detail, list):
                    detail_list = [str(x) for x in detail]
                else:
                    try:
                        parsed = json.loads(detail)
                        detail_list = [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]
                    except Exception:
                        detail_list = re.split(r"[,;|]", str(detail))
                if required_tag in detail_list:
                    filtered.append(prog)
            programs = filtered

    return programs


# ---------------------------------------------------------------------------
# Layer 2 — map_ltv_matrix filter
# ---------------------------------------------------------------------------


def _dscr_band_ok(band: str | None, dscr: float | None) -> bool:
    """Return True if the form DSCR satisfies the matrix dscr_band."""
    b = (band or "").strip().lower()
    if not b:
        return True
    if dscr is None:
        return True
    if b == "gte_1_00":
        return dscr >= 1.0
    if b == "gte_0_75_lt_1_00":
        return 0.75 <= dscr < 1.0
    if b == "lt_0_75":
        return dscr < 0.75
    return True


def _json_field_contains(raw: Any, value: str) -> bool:
    """
    Python-side equivalent of JSON_CONTAINS for a JSON array field.
    raw may be None, '', '[]', a JSON list string, or a plain string.
    Returns True when field is null/empty (treat as unrestricted) or when value is found.
    """
    if raw is None:
        return True
    s = str(raw).strip()
    if not s or s == "[]":
        return True
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return value in [str(x) for x in parsed]
        # Scalar JSON value
        return str(parsed) == value
    except Exception:
        # Plain string
        return s == value or value in re.split(r"[,;|]", s)


def _matrix_doc_ok(matrix_doc: Any, form_doc: str) -> bool:
    """
    True if the matrix row's doc_type field is compatible with the form doc type.
    "same as program" or NULL/empty → unrestricted (match anything).
    JSON arrays (e.g. '["full_doc","bank_stmt_12"]') use the same rules as occupancy/purpose.
    """
    if matrix_doc is None:
        return True
    md = str(matrix_doc).strip()
    if not md:
        return True
    md_lower = md.lower()
    if md_lower == "same as program":
        return True
    if form_doc == "any":
        return True
    if md.startswith("[") or isinstance(matrix_doc, list):
        if form_doc == BANK_STMT_COMBINED_CODE:
            return any(_json_field_contains(matrix_doc, code) for code in BANK_STMT_MATRIX_CODES)
        return _json_field_contains(matrix_doc, form_doc) or _json_field_contains(matrix_doc, "any")
    if form_doc == BANK_STMT_COMBINED_CODE:
        return md_lower in BANK_STMT_MATRIX_CODES or md_lower == "any"
    return md_lower == form_doc or md_lower == "any"


def _matrix_property_ok(
    matrix_property: Any,
    form_property_code: str,
    program_property: Any,
) -> bool:
    """
    True when the scenario property type fits the matrix row.
    Matrix value "same as program" → use dim_programs.property_type (program allow-list).
    """
    if not form_property_code:
        return True
    if matrix_property is None:
        return True
    mp = str(matrix_property).strip().lower()
    if not mp or mp == "same as program":
        return _json_field_contains(program_property, form_property_code)
    return _json_field_contains(matrix_property, form_property_code)


def _layer2_ltv_matrix(
    conn: Any,
    form: dict[str, Any],
    program_ids: list[int],
    quick: bool = False,
    prog_by_id: dict[int, dict[str, Any]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """
    Query map_ltv_matrix for the candidate program_ids and return a dict
    program_id → list of matching rows (Python-filtered).
    """
    if not program_ids:
        return {}

    q = text(
        """
        SELECT id, lender_id, program_id, fico_min, loan_amt_min, loan_amt_max,
               dscr_band, occupancy_type, property_type, loan_purpose,
               borrower_type, doc_type, max_ltv, max_cltv, special_overlays
        FROM map_ltv_matrix
        WHERE program_id IN :ids
          AND (:fico IS NULL OR fico_min IS NULL OR fico_min <= :fico_for_min)
        ORDER BY program_id, fico_min DESC, loan_amt_max DESC
        """
    ).bindparams(bindparam("ids", expanding=True))

    fico = form["fico"]
    params: dict[str, Any] = {
        "ids": program_ids,
        "fico": fico,
        "fico_for_min": (
            int(fico) + ELIG_FICO_TOLERANCE if fico is not None else None
        ),
    }

    rows = conn.execute(q, params).fetchall()

    occupancy = form["occupancy"]
    property_type_code = form["property_type_code"]
    loan_purpose = form["loan_purpose"]
    doc_type = form["doc_type"]
    dscr = form["dscr"]

    by_program: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        r = dict(row._mapping)
        # Occupancy — quick mode skips when not provided
        if not (quick and occupancy in ("any", "")):
            if not _json_field_contains(r.get("occupancy_type"), occupancy):
                continue
        # Property type — quick mode skips when not provided
        if not (quick and not property_type_code):
            prog = (prog_by_id or {}).get(int(r["program_id"]))
            program_property = prog.get("property_type") if prog else None
            if not _matrix_property_ok(
                r.get("property_type"), property_type_code, program_property
            ):
                continue
        # Loan purpose — quick mode skips when not provided
        if not (quick and loan_purpose in ("any", "")):
            if not _json_field_contains(r.get("loan_purpose"), loan_purpose):
                continue
        # Doc type
        if not _matrix_doc_ok(r.get("doc_type"), doc_type):
            continue
        # DSCR band
        if not _dscr_band_ok(r.get("dscr_band"), dscr):
            continue
        pid = int(r["program_id"])
        by_program.setdefault(pid, []).append(r)

    return by_program


# ---------------------------------------------------------------------------
# Layer 2 — LTV / CLTV gate and candidate assembly
# ---------------------------------------------------------------------------


def _rows_passing_ltv(
    rows: list[dict[str, Any]],
    ltv: float,
    cltv: float,
    is_second_lien: bool,
) -> list[dict[str, Any]]:
    """Keep only rows where scenario leverage fits within the matrix cap."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if is_second_lien:
            cap = r.get("max_cltv")
            if cap is not None and exceeds_pct(cltv, float(cap)):
                continue
        else:
            cap = r.get("max_ltv")
            if cap is not None and exceeds_pct(ltv, float(cap)):
                continue
        out.append(r)
    return out


def _rows_matching_loan_tier(
    rows: list[dict[str, Any]], loan_amount: int | float | None
) -> list[dict[str, Any]]:
    """
    Keep matrix tiers whose loan band contains the scenario amount.
    Program-level min/max gates use dim_programs (Layer 1); matrix mins here
    only select the correct tier row, not whether the program qualifies.
    """
    if loan_amount is None:
        return rows
    amt = int(loan_amount)
    out: list[dict[str, Any]] = []
    for r in rows:
        row_min = r.get("loan_amt_min")
        row_max = r.get("loan_amt_max")
        if not loan_within_tier(amt, row_min, row_max):
            continue
        out.append(r)
    return out


def _pick_best_matrix_row(
    rows: list[dict[str, Any]], form_doc: str, occupancy: str
) -> dict[str, Any] | None:
    """Legacy alias — prefer _pick_scenario_matrix_row."""
    return _pick_scenario_matrix_row(rows, form_doc, occupancy, loan_amount=None)


def _pick_scenario_matrix_row(
    rows: list[dict[str, Any]],
    form_doc: str,
    occupancy: str,
    loan_amount: int | float | None,
    fico: int | None = None,
) -> dict[str, Any] | None:
    """
    Pick the matrix tier row for this scenario: highest FICO band the borrower
    qualifies for, then the tightest loan_amt_max tier containing the loan amount,
    then best doc/occupancy fit.
    """
    if not rows:
        return None

    scoped = rows
    if fico is not None:
        qualifying = [r for r in scoped if int(r.get("fico_min") or 0) <= int(fico)]
        if qualifying:
            top_fico = max(int(r.get("fico_min") or 0) for r in qualifying)
            scoped = [r for r in qualifying if int(r.get("fico_min") or 0) == top_fico]

    if loan_amount is not None:
        amt = int(loan_amount)
        ceilings = [
            int(r["loan_amt_max"])
            for r in scoped
            if r.get("loan_amt_max") is not None
            and (r.get("loan_amt_min") is None or int(r["loan_amt_min"]) <= amt)
            and int(r["loan_amt_max"]) >= amt
        ]
        if ceilings:
            tier_ceiling = min(ceilings)
            scoped = [r for r in scoped if int(r.get("loan_amt_max") or 0) == tier_ceiling]

    if not scoped:
        return None

    return sorted(
        scoped,
        key=lambda x: (
            1 if _matrix_doc_ok(x.get("doc_type"), form_doc) else 0,
            1 if _json_field_contains(x.get("occupancy_type"), occupancy) else 0,
            int(x.get("fico_min") or 0),
        ),
        reverse=True,
    )[0]


def _program_max_loan(prog: dict[str, Any]) -> int | None:
    """Program ceiling from dim_programs.loan_amt_max."""
    v = prog.get("loan_amt_max")
    return int(v) if v is not None else None


def _pick_max_loan_matrix_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Among rows, pick the tier with the highest loan_amt_max (legacy helper)."""
    if not rows:
        return None
    return max(
        rows,
        key=lambda r: (
            int(r.get("loan_amt_max") or 0),
            float(r.get("max_ltv") or 0),
            -int(r.get("fico_min") or 9999),
        ),
    )


def _row_max_loan_cap(row: dict[str, Any], program_row: dict[str, Any]) -> int | None:
    """Scenario max loan: matrix tier cap, capped by dim_programs.loan_amt_max."""
    prog_max = program_row.get("loan_amt_max")
    v = row.get("loan_amt_max")
    if v is None:
        return int(prog_max) if prog_max is not None else None
    cap = int(v)
    if prog_max is not None:
        cap = min(cap, int(prog_max))
    return cap


def _ltv_from_row_for_purpose(
    row: dict[str, Any],
    purpose: str,
    is_second_lien: bool,
) -> float | None:
    if not _json_field_contains(row.get("loan_purpose"), purpose):
        return None
    cap_key = "max_cltv" if is_second_lien else "max_ltv"
    raw = _to_float_opt(row.get(cap_key))
    return float(round(raw)) if raw is not None else None


def _parse_max_dti_from_rule_text(text: str) -> float | None:
    """Extract a program-level max DTI percent from rule snippet content."""
    if not text:
        return None
    patterns = (
        r"max\s+dti\s*:?\s*(?:<=?\s*)?(\d+(?:\.\d+)?)\s*%",
        r"max\s+(\d+(?:\.\d+)?)\s*%\s*;",
        r"max\s+(\d+(?:\.\d+)?)\s*%",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return float(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _rule_snippet_max_dti_by_program(
    conn: Any,
    program_ids: list[int],
) -> dict[int, float | None]:
    """Program-level max DTI from map_program_rule_guideline (Best Match column)."""
    if not program_ids:
        return {}

    q = text(
        """
        SELECT program_id, category, content
        FROM map_program_rule_guideline
        WHERE program_id IN :ids
        ORDER BY program_id, category
        """
    ).bindparams(bindparam("ids", expanding=True))

    try:
        rows = conn.execute(q, {"ids": program_ids}).fetchall()
    except Exception:
        return {}

    parsed_by_pid: dict[int, list[tuple[int, float]]] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        category = (r.get("category") or "").strip().lower()
        content = (r.get("content") or "").strip()
        parsed = _parse_max_dti_from_rule_text(content)
        if parsed is None:
            continue
        if category == "dti requirements":
            priority = 0
        elif "dti" in category:
            priority = 1
        else:
            priority = 2
        parsed_by_pid.setdefault(pid, []).append((priority, parsed))

    out: dict[int, float | None] = {}
    for pid, items in parsed_by_pid.items():
        items.sort(key=lambda x: (x[0], x[1]))
        out[pid] = items[0][1]
    return out


def _build_layer2_candidate(
    *,
    prog: dict[str, Any],
    pid: int,
    best: dict[str, Any],
    valid_rows: list[dict[str, Any]],
    form: dict[str, Any],
    rule_dti_by_pid: dict[int, float | None],
    filtered_ltv_caps: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Assemble a program candidate from a qualifying matrix tier row."""
    is_second_lien = form["is_second_lien"]
    loan_purpose = form["loan_purpose"]
    occupancy = form["occupancy"]
    if filtered_ltv_caps is None:
        filtered_ltv_caps = _ltv_caps_by_purpose(valid_rows, loan_purpose, is_second_lien)

    cit_code = form["citizenship_code"]
    is_itin = cit_code == "itin"
    is_foreign_nat = cit_code == "foreign_national"

    is_short = form.get("is_short_term_rental", False)
    min_dscr: float | None = None
    if prog.get("is_dscr_program"):
        raw_d = prog.get("dscr_min_short_term") if is_short else prog.get("dscr_min_long_term")
        min_dscr = _to_float_opt(raw_d)

    dim_dti = _to_float_opt(prog.get("max_dti"))
    rule_dti = rule_dti_by_pid.get(pid)
    program_limit_dti = dim_dti if dim_dti is not None else rule_dti
    best_match_dti = rule_dti if rule_dti is not None else dim_dti

    return {
        "program_id": pid,
        "lender_id": int(prog.get("lender_id") or 0),
        "lender": str(prog.get("lender_code") or ""),
        "lender_name": str(prog.get("brand_name") or prog.get("lender_name") or ""),
        "program_name": _display_program_name(prog),
        "program_name_np": (prog.get("program_name_np") or "").strip() or None,
        "is_dscr": bool(prog.get("is_dscr_program")),
        "is_itin": is_itin,
        "is_foreign_nat": is_foreign_nat,
        "min_fico": int(prog["fico_min"]) if prog.get("fico_min") is not None else None,
        "min_loan": int(prog["loan_amt_min"]) if prog.get("loan_amt_min") is not None else None,
        "max_loan": _program_max_loan(prog),
        "max_ltv_purchase": filtered_ltv_caps["purchase"],
        "max_ltv_refi": filtered_ltv_caps["rate_term"],
        "max_ltv_cashout": filtered_ltv_caps["cash_out"],
        "max_dti": program_limit_dti,
        "min_dscr": min_dscr,
        "occupancy_types": _parse_occupancy_types(prog.get("occupancy_types")),
        "property_types": _parse_property_types_allowed(prog.get("property_type")),
        "loan_purposes_allowed": _parse_loan_purposes_allowed(prog.get("loan_purposes_allowed")),
        "doc_types_allowed": _format_doc_types_allowed(prog.get("doc_types_allowed")),
        "program_notes": (prog.get("notes") or "").strip() or None,
        "is_active": bool(prog.get("is_active", 1)),
        "best_match": _best_match_metrics(
            best,
            is_second_lien=is_second_lien,
            max_dti=best_match_dti,
            min_dscr=min_dscr if prog.get("is_dscr_program") else None,
        ),
        "occupancy_code": occupancy,
        "products_available": [],
        "products_available_label": "—",
        "prepayment_options": [],
        "rule_notes": [],
        "special_overlay": str(best.get("special_overlays") or "") or None,
        "_prog": prog,
    }


def _apply_eligibility_layers_after_matrix(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    quick: bool,
) -> list[dict[str, Any]]:
    """Layers 2b–10 after a program passes the LTV matrix gate."""
    candidates, _ = _layer_rule_snippet_overlays(conn, form, candidates, stage="basics")
    if form.get("is_fthb"):
        candidates = _layer3_fthb(conn, form, candidates)
    candidates = _layer4_products(conn, form, candidates)
    candidates = _layer4b_product_prefs(conn, form, candidates)
    candidates, _, _ = _layer5_geo(conn, form, candidates)
    candidates, _ = _layer6_credit_seasoning(conn, form, candidates)
    candidates = _layer7_housing_history(conn, form, candidates)
    candidates, _ = _layer_rule_snippet_overlays(conn, form, candidates, stage="extended")
    if not quick:
        candidates = _layer8_rule_guidelines(conn, candidates, form)
    candidates = _layer9_prepayment(conn, form, candidates)
    candidates = _filter_prepay_stepdown(candidates, form)
    if not quick:
        candidates, _ = _layer10_qdrant_verify(form, candidates)
    return candidates


def _near_miss_gap(
    miss_type: str,
    form: dict[str, Any],
    suggested_ltv: float | None,
    suggested_loan: int | None,
) -> float:
    if miss_type == "ltv":
        leverage = form["cltv"] if form["is_second_lien"] else form["ltv"]
        if leverage is None or suggested_ltv is None:
            return 9999.0
        return max(0.0, float(leverage) - float(suggested_ltv))
    loan_amount = form.get("loan_amount")
    if loan_amount is None:
        return 9999.0
    if miss_type == "loan_high" and suggested_loan is not None:
        return max(0.0, float(loan_amount - suggested_loan))
    if miss_type == "loan_low" and suggested_loan is not None:
        return max(0.0, float(suggested_loan - loan_amount))
    return 9999.0


def _evaluate_near_miss(
    prog: dict[str, Any],
    pid: int,
    matrix_rows: list[dict[str, Any]],
    form: dict[str, Any],
    rule_dti_by_pid: dict[int, float | None],
) -> dict[str, Any] | None:
    """
    True near-miss: program fails only on user-controllable LTV or loan amount
    while all other matrix filters (FICO/doc/occ/purpose) already match.
    """
    if not matrix_rows:
        return None

    is_second_lien = form["is_second_lien"]
    leverage = form["cltv"] if is_second_lien else form["ltv"]
    cap_key = "max_cltv" if is_second_lien else "max_ltv"
    leverage_label = "CLTV" if is_second_lien else "LTV"
    loan_amount = form.get("loan_amount")
    doc_type = form["doc_type"]
    occupancy = form["occupancy"]
    fico = form.get("fico")

    ltv_rows = _rows_passing_ltv(matrix_rows, form["ltv"], form["cltv"], is_second_lien)
    miss_type: str | None = None
    suggested_ltv: float | None = None
    suggested_loan: int | None = None
    hint = ""
    working_rows = matrix_rows
    pick_loan = loan_amount

    if not ltv_rows:
        caps = [_to_float_opt(r.get(cap_key)) for r in matrix_rows]
        caps = [c for c in caps if c is not None]
        if not caps or leverage is None:
            return None
        suggested_ltv = max(caps)
        working_rows = [
            r
            for r in matrix_rows
            if _to_float_opt(r.get(cap_key)) is not None
            and float(_to_float_opt(r.get(cap_key)) or 0) >= suggested_ltv - ELIG_PCT_TOLERANCE
        ]
        miss_type = "ltv"
        hint = (
            f"Lower {leverage_label} to {suggested_ltv:g}% "
            f"(your scenario: {float(leverage):g}%)"
        )
    else:
        working_rows = ltv_rows
        tier_rows = _rows_matching_loan_tier(ltv_rows, loan_amount)
        if not tier_rows:
            if loan_amount is None:
                return None
            amt = int(loan_amount)
            max_tiers = [
                int(r["loan_amt_max"])
                for r in ltv_rows
                if r.get("loan_amt_max") is not None
            ]
            min_tiers = [
                int(r["loan_amt_min"])
                for r in ltv_rows
                if r.get("loan_amt_min") is not None
            ]
            if max_tiers and amt > max(max_tiers):
                suggested_loan = max(max_tiers)
                working_rows = [
                    r
                    for r in ltv_rows
                    if r.get("loan_amt_max") is not None
                    and int(r["loan_amt_max"]) == suggested_loan
                ]
                pick_loan = suggested_loan
                miss_type = "loan_high"
                hint = (
                    f"Lower loan amount to ${suggested_loan:,} "
                    f"(your scenario: ${amt:,})"
                )
            elif min_tiers:
                qualifying_mins = [m for m in min_tiers if m > amt]
                if qualifying_mins:
                    suggested_loan = min(qualifying_mins)
                    working_rows = [
                        r
                        for r in ltv_rows
                        if r.get("loan_amt_min") is not None
                        and int(r["loan_amt_min"]) == suggested_loan
                    ]
                    pick_loan = suggested_loan
                    miss_type = "loan_low"
                    hint = (
                        f"Increase loan amount to ${suggested_loan:,} "
                        f"(your scenario: ${amt:,})"
                    )
                else:
                    return None
            else:
                return None
        else:
            best = _pick_scenario_matrix_row(tier_rows, doc_type, occupancy, loan_amount, fico)
            if best is None:
                return None
            scenario_max = _row_max_loan_cap(best, prog)
            if (
                scenario_max is None
                or loan_amount is None
                or int(loan_amount) <= int(scenario_max)
            ):
                return None
            suggested_loan = int(scenario_max)
            pick_loan = suggested_loan
            working_rows = tier_rows
            miss_type = "loan_high"
            hint = (
                f"Lower loan amount to ${suggested_loan:,} "
                f"(your scenario: ${int(loan_amount):,})"
            )

    best = _pick_scenario_matrix_row(working_rows, doc_type, occupancy, pick_loan, fico)
    if best is None:
        return None

    candidate = _build_layer2_candidate(
        prog=prog,
        pid=pid,
        best=best,
        valid_rows=working_rows,
        form=form,
        rule_dti_by_pid=rule_dti_by_pid,
    )
    return {
        "candidate": candidate,
        "hint": hint,
        "miss_type": miss_type or "",
        "suggested_ltv": suggested_ltv,
        "suggested_loan": suggested_loan,
        "gap": _near_miss_gap(miss_type or "", form, suggested_ltv, suggested_loan),
    }


def _evaluate_human_near_miss(
    prog: dict[str, Any],
    pid: int,
    matrix_rows: list[dict[str, Any]],
    form: dict[str, Any],
    rule_dti_by_pid: dict[int, float | None],
) -> dict[str, Any] | None:
    """
    Human-fixable near-miss: the program passes LTV + loan tier on the real
    scenario but the borrower falls short on EXACTLY ONE of FICO or DTI — the two
    levers a loan officer can realistically move. Returns None for anything else
    (LTV/loan misses are handled by `_evaluate_near_miss`; multi-factor misses are
    too far to be "just missed").
    """
    if not matrix_rows:
        return None

    is_second_lien = form["is_second_lien"]
    ltv = form["ltv"]
    cltv = form["cltv"]
    loan_amount = form.get("loan_amount")
    doc_type = form["doc_type"]
    occupancy = form["occupancy"]
    fico = form.get("fico")
    dti = form.get("dti")

    # Must clear LTV + loan tier on the real scenario; otherwise it's an LTV/loan
    # miss (or a multi-factor miss) and not a clean FICO/DTI near-miss.
    ltv_rows = _rows_passing_ltv(matrix_rows, ltv, cltv, is_second_lien)
    if not ltv_rows:
        return None
    tier_rows = _rows_matching_loan_tier(ltv_rows, loan_amount)
    if not tier_rows:
        return None

    # Lowest FICO requirement among the LTV/loan-passing tiers.
    fico_mins = [int(r["fico_min"]) for r in tier_rows if r.get("fico_min") is not None]
    prog_fico_min: int | None = None
    if fico_mins:
        prog_fico_min = min(fico_mins)
    elif prog.get("fico_min") is not None:
        prog_fico_min = int(prog["fico_min"])

    # Effective DTI cap (rule snippet preferred, else dim_programs).
    prog_max_dti = rule_dti_by_pid.get(pid)
    if prog_max_dti is None:
        prog_max_dti = _to_float_opt(prog.get("max_dti"))

    fico_fail = (
        fico is not None
        and prog_fico_min is not None
        and int(prog_fico_min) > int(fico) + ELIG_FICO_TOLERANCE
    )
    dti_fail = (
        dti is not None
        and float(dti) > 0
        and prog_max_dti is not None
        and float(prog_max_dti) < float(dti) - ELIG_PCT_TOLERANCE
    )

    miss_type: str | None = None
    hint = ""
    suggestion: str | None = None
    gap = 9999.0
    pick_fico = fico

    if fico_fail and not dti_fail and prog_fico_min is not None and fico is not None:
        if int(prog_fico_min) - int(fico) > NEAR_MISS_FICO_RANGE:
            return None
        miss_type = "fico"
        gap = float(int(prog_fico_min) - int(fico))
        hint = f"Raise credit score to {int(prog_fico_min)} (your scenario: {int(fico)})"
        pick_fico = int(prog_fico_min)
    elif dti_fail and not fico_fail and prog_max_dti is not None and dti is not None:
        if float(dti) - float(prog_max_dti) > NEAR_MISS_DTI_RANGE:
            return None
        miss_type = "dti"
        gap = float(dti) - float(prog_max_dti)
        hint = (
            f"Reduce DTI to {float(prog_max_dti):g}% "
            f"(your scenario: {float(dti):g}%)"
        )
        suggestion = "Try adding a co-borrower — their income can lower the qualifying DTI."
    else:
        return None

    best = _pick_scenario_matrix_row(tier_rows, doc_type, occupancy, loan_amount, pick_fico)
    if best is None:
        return None

    candidate = _build_layer2_candidate(
        prog=prog,
        pid=pid,
        best=best,
        valid_rows=tier_rows,
        form=form,
        rule_dti_by_pid=rule_dti_by_pid,
    )
    return {
        "candidate": candidate,
        "hint": hint,
        "miss_type": miss_type or "",
        "suggestion": suggestion,
        "suggested_ltv": None,
        "suggested_loan": None,
        "gap": gap,
    }


def _find_fico_dti_near_misses(
    conn: Any,
    form: dict[str, Any],
    *,
    matched_pids: set[int],
    exclude_pids: set[int],
) -> list[tuple[float, dict[str, Any]]]:
    """
    Discover programs that the borrower misses only on FICO or DTI. These are
    filtered out by the strict Layer-1 gates, so re-run Layer 1 + the matrix with
    a relaxed FICO/DTI envelope, then classify each candidate against the REAL
    scenario. Returns scored (gap, candidate) tuples (caller sorts/caps).
    """
    fico = form.get("fico")
    dti = form.get("dti")
    # Nothing to relax against if we have neither lever.
    if fico is None and not (dti is not None and float(dti) > 0):
        return []

    near_form = dict(form)
    if fico is not None:
        near_form["fico"] = int(fico) + NEAR_MISS_FICO_RANGE
    if dti is not None and float(dti) > 0:
        near_form["dti"] = max(1.0, float(dti) - NEAR_MISS_DTI_RANGE)

    try:
        relaxed_progs = _layer1_programs(conn, near_form, quick=False)
    except Exception:
        return []
    relaxed_by_id = {int(p["program_id"]): p for p in relaxed_progs}
    relaxed_pids = [
        pid
        for pid in relaxed_by_id
        if pid not in matched_pids and pid not in exclude_pids
    ]
    if not relaxed_pids:
        return []

    relaxed_matrix = _layer2_ltv_matrix(
        conn, near_form, relaxed_pids, quick=False, prog_by_id=relaxed_by_id
    )
    relaxed_rule_dti = _rule_snippet_max_dti_by_program(conn, relaxed_pids)

    scored: list[tuple[float, dict[str, Any]]] = []
    for pid in relaxed_pids:
        prog = relaxed_by_id.get(pid)
        if prog is None:
            continue
        matrix_rows = relaxed_matrix.get(pid, [])
        evaluated = _evaluate_human_near_miss(prog, pid, matrix_rows, form, relaxed_rule_dti)
        if evaluated is None:
            continue
        survivors = _apply_eligibility_layers_after_matrix(
            conn, form, [evaluated["candidate"]], quick=False
        )
        if not survivors:
            continue
        cand = dict(survivors[0])
        cand.pop("_prog", None)
        cand["near_miss"] = True
        cand["near_miss_hint"] = evaluated["hint"]
        cand["near_miss_type"] = evaluated["miss_type"]
        cand["near_miss_suggestion"] = evaluated.get("suggestion")
        cand["suggested_ltv"] = None
        cand["suggested_loan"] = None
        scored.append((float(evaluated["gap"]), cand))

    return scored


def _find_near_miss_programs(
    conn: Any,
    form: dict[str, Any],
    *,
    quick: bool,
    matched_pids: set[int],
    layer2_pass_pids: set[int],
    prog_by_id: dict[int, dict[str, Any]],
    ltv_rows_by_prog: dict[int, list[dict[str, Any]]],
    rule_dti_by_pid: dict[int, float | None],
    limit: int = 2,
) -> list[dict[str, Any]]:
    """
    Up to `limit` "Just Missed" programs the borrower can realistically reach.
    Covers LTV/loan (in-pool) plus the human-fixable FICO/DTI levers (which the
    strict Layer-1 gates would otherwise hide). LTV/loan misses are listed first.
    """
    if quick or limit <= 0:
        return []

    # Programs that passed LTV/loan but failed geo/credit/etc. are not near-misses.
    failed_after_layer2 = layer2_pass_pids - matched_pids
    pool_pids = set(prog_by_id.keys()) - matched_pids - failed_after_layer2

    scored: list[tuple[float, dict[str, Any]]] = []
    for pid in pool_pids:
        prog = prog_by_id.get(pid)
        if prog is None:
            continue
        matrix_rows = ltv_rows_by_prog.get(pid, [])
        evaluated = _evaluate_near_miss(prog, pid, matrix_rows, form, rule_dti_by_pid)
        if evaluated is None:
            continue
        survivors = _apply_eligibility_layers_after_matrix(
            conn,
            form,
            [evaluated["candidate"]],
            quick=quick,
        )
        if not survivors:
            continue
        cand = dict(survivors[0])
        cand.pop("_prog", None)
        cand["near_miss"] = True
        cand["near_miss_hint"] = evaluated["hint"]
        cand["near_miss_type"] = evaluated["miss_type"]
        cand["near_miss_suggestion"] = None
        cand["suggested_ltv"] = evaluated["suggested_ltv"]
        cand["suggested_loan"] = evaluated["suggested_loan"]
        scored.append((float(evaluated["gap"]), cand))

    scored.sort(key=lambda x: x[0])
    ranked: list[dict[str, Any]] = [c for _, c in scored]
    seen_pids = {int(c["program_id"]) for c in ranked}

    # FICO / DTI levers — programs Layer 1 dropped because the borrower fell short
    # on credit score or DTI. Appended after LTV/loan misses, each sorted by gap.
    if len(ranked) < limit:
        human_scored = _find_fico_dti_near_misses(
            conn,
            form,
            matched_pids=matched_pids,
            exclude_pids=seen_pids,
        )
        human_scored.sort(key=lambda x: x[0])
        for _, cand in human_scored:
            cpid = int(cand["program_id"])
            if cpid in seen_pids:
                continue
            seen_pids.add(cpid)
            ranked.append(cand)

    return ranked[:limit]


def _best_match_metrics(
    row: dict[str, Any],
    *,
    is_second_lien: bool,
    max_dti: float | None = None,
    min_dscr: float | None = None,
) -> dict[str, Any]:
    """Scenario tier metrics from map_ltv_matrix + program-level DTI/DSCR from rules/dim."""
    return {
        "min_fico": int(row["fico_min"]) if row.get("fico_min") is not None else None,
        "min_loan": int(row["loan_amt_min"]) if row.get("loan_amt_min") is not None else None,
        "max_loan": int(row["loan_amt_max"]) if row.get("loan_amt_max") is not None else None,
        "max_ltv_purchase": _ltv_from_row_for_purpose(row, "purchase", is_second_lien),
        "max_ltv_rate_term": _ltv_from_row_for_purpose(row, "rate_term", is_second_lien),
        "max_ltv_cashout": _ltv_from_row_for_purpose(row, "cash_out", is_second_lien),
        "max_dti": max_dti,
        "min_dscr": min_dscr,
    }


def _ltv_caps_by_purpose(
    rows: list[dict[str, Any]],
    loan_purpose: str,
    is_second_lien: bool,
) -> dict[str, float | None]:
    """
    From LTV-passing rows, derive the max achievable LTV for each purpose bucket.
    Only populates the requested loan_purpose bucket (rows are already purpose-filtered).
    """
    cap_key = "max_cltv" if is_second_lien else "max_ltv"
    caps: dict[str, float | None] = {
        "purchase": None,
        "rate_term": None,
        "cash_out": None,
    }
    for r in rows:
        lp_raw = r.get("loan_purpose")
        row_purposes: list[str] = []
        if lp_raw is None:
            row_purposes = ["purchase", "rate_term", "cash_out"]
        else:
            s = str(lp_raw).strip()
            if not s or s == "[]":
                row_purposes = ["purchase", "rate_term", "cash_out"]
            else:
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        row_purposes = [str(x) for x in parsed]
                    else:
                        row_purposes = [str(parsed)]
                except Exception:
                    row_purposes = re.split(r"[,;|]", s)

        cap_v = r.get(cap_key)
        if cap_v is None:
            continue
        cap_f = float(cap_v)
        for lp in row_purposes:
            lp = lp.strip()
            if lp in caps:
                prev = caps[lp]
                caps[lp] = max(cap_f, prev) if prev is not None else cap_f

    return caps


def _effective_max_loan(
    valid_rows: list[dict[str, Any]],
    program_row: dict[str, Any],
) -> int | None:
    """Legacy: max loan_amt_max across rows. Prefer _row_max_loan_cap on the best tier row."""
    caps: list[int] = []
    for r in valid_rows:
        v = r.get("loan_amt_max")
        if v is not None:
            caps.append(int(v))
    if not caps:
        return None
    ceiling = max(caps)
    prog_max = program_row.get("loan_amt_max")
    if prog_max is not None:
        ceiling = min(ceiling, int(prog_max))
    return ceiling


# ---------------------------------------------------------------------------
# Layer 3 — FTHB eligibility
# ---------------------------------------------------------------------------


def _layer3_fthb(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter candidates by FTHB eligibility. Mutates candidates to add fthb_max_loan_cap."""
    if not form.get("is_fthb"):
        return candidates
    if not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    q = text(
        """
        SELECT program_id, is_fthb_eligible, fthb_max_loan_cap
        FROM map_program_fthb_eligibility
        WHERE program_id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))

    rows = conn.execute(q, {"ids": ids}).fetchall()
    fthb_by_prog: dict[int, dict[str, Any]] = {}
    for row in rows:
        r = dict(row._mapping)
        fthb_by_prog[int(r["program_id"])] = r

    out: list[dict[str, Any]] = []
    loan_amount = form["loan_amount"]
    for cand in candidates:
        pid = int(cand["program_id"])
        fthb = fthb_by_prog.get(pid)
        if fthb is None:
            # No FTHB record — program does not support FTHB
            continue
        if not fthb.get("is_fthb_eligible"):
            continue
        cap = fthb.get("fthb_max_loan_cap")
        if cap is not None and loan_amount is not None and loan_amount > int(cap):
            continue
        # Store cap for downstream capping of effective_max_loan
        if cap is not None:
            cand["fthb_max_loan_cap"] = int(cap)
            if cand.get("max_loan") is not None:
                cand["max_loan"] = min(cand["max_loan"], int(cap))
            bm = cand.get("best_match")
            if isinstance(bm, dict) and bm.get("max_loan") is not None:
                bm["max_loan"] = min(int(bm["max_loan"]), int(cap))
        out.append(cand)
    return out


def _parse_loan_term_years_list(raw: str) -> list[int]:
    """Parse loan term from single select or comma/pipe-separated multi-select."""
    raw = (raw or "").strip()
    if not raw or raw.lower() == "no preference":
        return []
    terms: list[int] = []
    for part in re.split(r"[,|]", raw):
        part = part.strip()
        if not part:
            continue
        try:
            terms.append(int(float(part)))
        except (ValueError, TypeError):
            continue
    return sorted(set(terms))


def _loan_term_years_list_from_form(form: dict[str, Any]) -> list[int]:
    terms = list(form.get("loan_term_years_list") or [])
    if terms:
        return [int(t) for t in terms]
    single = form.get("loan_term_years")
    if single is not None:
        return [int(single)]
    return []


def _product_pref_sql_clauses(form: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """AND clauses for map_program_products + dim_product_types (no program_id clause)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    terms = _loan_term_years_list_from_form(form)
    if terms:
        clauses.append("pt.total_term_years IN :terms")
        params["terms"] = terms

    io_pref = form.get("io_pref")
    if io_pref is True:
        clauses.append("pp.io_flag = 1")
    elif io_pref is False:
        clauses.append("pp.io_flag = 0")

    if form.get("is_fthb"):
        clauses.append("pp.is_fthb_eligible = 1")

    rate_type = form.get("rate_type_pref")
    if rate_type == "fixed":
        clauses.append("(pt.code LIKE 'FIXED_%' OR pt.code LIKE 'IO_FIXED_%')")
    elif rate_type == "arm":
        clauses.append("(pt.code LIKE 'ARM_%' OR pt.code LIKE 'IO_ARM_%')")

    return clauses, params


def _has_product_pref_filters(form: dict[str, Any]) -> bool:
    return bool(
        _loan_term_years_list_from_form(form)
        or form.get("io_pref") is not None
        or form.get("rate_type_pref")
        or form.get("is_fthb")
    )


# ---------------------------------------------------------------------------
# Layer 4 — products
# ---------------------------------------------------------------------------


def _products_by_program(
    conn: Any,
    program_ids: list[int],
    *,
    pref_clauses: list[str] | None = None,
    pref_params: dict[str, Any] | None = None,
) -> dict[int, list[str]]:
    """Return program_id → product display names from map_program_products."""
    if not program_ids:
        return {}

    where_parts = ["pp.program_id IN :ids"]
    params: dict[str, Any] = {"ids": program_ids}
    if pref_clauses:
        where_parts.extend(pref_clauses)
        params.update(pref_params or {})

    q = text(
        f"""
            SELECT pp.program_id, pt.code, pt.name
            FROM map_program_products pp
            INNER JOIN dim_product_types pt ON pt.id = pp.product_type_id
            WHERE {" AND ".join(where_parts)}
            ORDER BY pp.program_id, pt.code
            """
    )
    binders = [bindparam("ids", expanding=True)]
    if "terms" in params:
        binders.append(bindparam("terms", expanding=True))
    q = q.bindparams(*binders)

    by_prog: dict[int, list[str]] = {}
    for row in conn.execute(q, params).fetchall():
        r = dict(row._mapping)
        pid = int(r["program_id"])
        label = str(r.get("name") or r.get("code") or "").strip()
        if label:
            by_prog.setdefault(pid, []).append(label)
    return by_prog


def _layer4_products(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach all program products plus user-matching subset (map_program_products)."""
    if not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    all_by_prog = _products_by_program(conn, ids)
    table_has_data = bool(all_by_prog)

    has_prefs = _has_product_pref_filters(form)
    if has_prefs:
        pref_clauses, pref_params = _product_pref_sql_clauses(form)
        match_by_prog = _products_by_program(
            conn, ids, pref_clauses=pref_clauses, pref_params=pref_params
        )
    else:
        match_by_prog = all_by_prog

    for cand in candidates:
        pid = int(cand["program_id"])
        all_names = all_by_prog.get(pid, [])
        match_names = match_by_prog.get(pid, []) if has_prefs else all_names
        cand["products_all"] = all_names
        cand["products_matching"] = match_names
        cand["products_available"] = all_names
        cand["products_available_label"] = _format_products_label(all_names) if all_names else "—"

    if not table_has_data:
        for cand in candidates:
            cand.setdefault("products_all", [])
            cand.setdefault("products_matching", [])
            cand.setdefault("products_available", [])
            cand.setdefault("products_available_label", "—")

    return candidates


# ---------------------------------------------------------------------------
# Layer 4b — product preference filter (term, IO, rate type, FTHB)
# ---------------------------------------------------------------------------


def _layer4b_product_prefs(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Filter candidates by step-5 product preferences.
    Programs that have product entries but none matching all active preferences are eliminated.
    Programs with no product entries at all pass through (no data = no constraint).
    """
    if not _has_product_pref_filters(form):
        return candidates

    if not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    pref_clauses, pref_params = _product_pref_sql_clauses(form)
    clauses: list[str] = ["pp.program_id IN :ids", *pref_clauses]
    params: dict[str, Any] = {"ids": ids, **pref_params}

    where = " AND ".join(clauses)
    binders = [bindparam("ids", expanding=True)]
    if "terms" in pref_params:
        binders.append(bindparam("terms", expanding=True))
    match_sql = text(f"""
        SELECT DISTINCT pp.program_id
        FROM map_program_products pp
        JOIN dim_product_types pt ON pt.id = pp.product_type_id
        WHERE {where}
    """).bindparams(*binders)

    matched_ids = {int(r[0]) for r in conn.execute(match_sql, params).fetchall()}

    # Which programs have any product entries at all?
    covered_sql = text(
        "SELECT DISTINCT program_id FROM map_program_products WHERE program_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    covered_ids = {int(r[0]) for r in conn.execute(covered_sql, {"ids": ids}).fetchall()}

    out: list[dict[str, Any]] = []
    for cand in candidates:
        pid = int(cand["program_id"])
        if pid not in covered_ids:
            out.append(cand)          # No product data → pass through
        elif pid in matched_ids:
            out.append(cand)          # Has a matching product → keep
        # else: has product data but none match → eliminate

    return out


# ---------------------------------------------------------------------------
# Layer 5 — geographic restrictions
# ---------------------------------------------------------------------------


def _exclusions_from_blocked(
    blocked: dict[int, str],
    name_by_pid: dict[int, str],
    prog_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, str]]:
    """Program-level exclusion list for API/UI (exact restriction clause in reason)."""
    out: list[dict[str, str]] = []
    for pid, reason in blocked.items():
        name = name_by_pid.get(pid)
        if not name and pid in prog_by_id:
            name = _display_program_name(prog_by_id[pid])
        out.append(
            {
                "program_name": name or f"Program {pid}",
                "reason": (reason or "").strip() or "Restriction applies",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Pure-LLM geographic restriction layer (single source of truth for form + chat).
#
# Two steps, no hardcoded rule parsing:
#   Step 1 (deterministic) — NewPoint's overall licensing footprint. A state is
#     eligible iff it appears in map_geographic_restrictions with
#     restriction_type='eligible_state' AND the matching restriction_detail for
#     the scenario doc type: 'Eligible lending state' for standard docs, or
#     'Eligible lending state (DSCR)' for DSCR scenarios (a broader, distinct
#     list). A scenario in any other state is a hard no for every program.
#   Step 2 (LLM, gpt-4o-mini) — for licensed states, hand the model the scenario
#     context plus every non-eligible_state restriction row for that state and let
#     it decide, per program, whether a row blocks or is just a note. We do NOT
#     re-judge its reading with keyword checks; only generic guards apply
#     (confidence threshold + structural rule scope).
# ---------------------------------------------------------------------------


# restriction_detail markers for the two licensing footprints (Step 1 allowlist).
_ELIGIBLE_STATE_DETAIL_STANDARD = "Eligible lending state"
_ELIGIBLE_STATE_DETAIL_DSCR = "Eligible lending state (DSCR)"


def _eligible_state_set(conn: Any, is_dscr: bool = False) -> set[str]:
    """Overall set of states NewPoint is licensed in (Step 1 allowlist).

    The footprint depends on the scenario doc type: DSCR scenarios use the
    broader ``Eligible lending state (DSCR)`` list; everything else uses the
    standard ``Eligible lending state`` list. (The DSCR list is NOT a superset —
    a few standard-only states are not DSCR-eligible and vice versa.)
    """
    detail = _ELIGIBLE_STATE_DETAIL_DSCR if is_dscr else _ELIGIBLE_STATE_DETAIL_STANDARD
    try:
        rows = conn.execute(
            text(
                "SELECT DISTINCT state FROM map_geographic_restrictions "
                "WHERE restriction_type = 'eligible_state' "
                "AND restriction_detail = :detail"
            ),
            {"detail": detail},
        ).fetchall()
    except Exception:
        return set()
    return {
        (r._mapping.get("state") or "").strip().upper()
        for r in rows
        if (r._mapping.get("state") or "").strip()
    }


def _geo_scenario_context(form: dict[str, Any], state: str) -> dict[str, Any]:
    """Everything the LLM may need to read a restriction row. Locations are
    surfaced across several fields (county / city / borough / zip / flags) because
    the app captures some jurisdictions as a county pick or a boolean follow-up."""

    def _ne(*vals: Any) -> Any:
        for v in vals:
            if isinstance(v, str):
                if v.strip():
                    return v.strip()
            elif v is not None:
                return v
        return ""

    return {
        "state": state,
        "county": _ne(form.get("county"), form.get("state_county"), form.get("stateCounty")),
        "city": _ne(form.get("city"), form.get("state_city"), form.get("stateCity")),
        "borough": _ne(form.get("borough"), form.get("state_borough"), form.get("stateBorough")),
        "zip_code": _ne(form.get("zip_code"), form.get("state_zip_code"), form.get("stateZipCode")),
        "occupancy": _ne(form.get("occupancy"), form.get("occupancy_code")),
        "loan_purpose": _ne(form.get("loan_purpose"), form.get("loan_purpose_code")),
        "property_type": _ne(form.get("property_type_code"), form.get("property_type")),
        "lien_position": _ne(form.get("lien_position"), form.get("lienPosition")),
        "is_second_lien": bool(form.get("is_second_lien")),
        "income_path": _ne(form.get("income_path")),
        "doc_type": _ne(form.get("doc_type")),
        "ltv": form.get("ltv"),
        "cltv": form.get("cltv"),
        "loan_amount": form.get("loan_amount"),
        "fico": form.get("fico"),
        "loan_term_years": form.get("loan_term_years"),
        "citizenship": _ne(form.get("citizenship_code"), form.get("citizenship_bucket")),
        "is_rural_property": bool(form.get("is_rural_property")),
        "acreage": form.get("acreage"),
        "flags": {
            "is_in_baltimore_city": bool(form.get("is_in_baltimore_city")),
            "is_in_philadelphia": bool(form.get("is_in_philadelphia")),
            "is_in_indianapolis": bool(form.get("is_in_indianapolis")),
            "is_in_memphis": bool(form.get("is_in_memphis")),
            "is_in_lubbock": bool(form.get("is_in_lubbock")),
            "is_in_paterson": bool(form.get("is_in_paterson")),
        },
    }


def _geo_scenario_summary(scenario_context: dict[str, Any]) -> str:
    occ = str(scenario_context.get("occupancy") or "").lower()
    if "invest" in occ:
        occ_label = "Investment Property"
    elif "second" in occ:
        occ_label = "Second Home"
    else:
        occ_label = "Primary Residence"

    flags = scenario_context.get("flags") if isinstance(scenario_context.get("flags"), dict) else {}
    loc_parts = [
        scenario_context.get("state"),
        scenario_context.get("county"),
        scenario_context.get("city"),
        scenario_context.get("borough"),
        scenario_context.get("zip_code"),
    ]
    if flags.get("is_in_baltimore_city"):
        loc_parts.append("Baltimore City (flag)")
    if flags.get("is_in_philadelphia"):
        loc_parts.append("Philadelphia (flag)")
    location_summary = ", ".join(str(p) for p in loc_parts if p)
    return (
        f"Occupancy={occ_label}; Purpose={scenario_context.get('loan_purpose') or 'unknown'}; "
        f"Location={location_summary or 'state only'}; "
        f"Property type={scenario_context.get('property_type') or 'unknown'}"
    )


def _llm_geo_adjudicate_program(
    scenario_context: dict[str, Any],
    program: dict[str, Any],
    restriction_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """One gpt-4o-mini call for a single program and its scoped restriction rows."""
    if not restriction_rows:
        return {"blocked": [], "notes": []}

    pid = int(program["program_id"])
    scenario_summary = _geo_scenario_summary(scenario_context)
    system = (
        "You are a strict mortgage geo-restriction reader. Read each restriction_detail "
        "literally against the scenario. Block only when location AND any occupancy/purpose "
        "qualifier both match. When in doubt, note — never block."
    )
    user = (
        f"Program: {program.get('program_name')} (program_id={pid})\n"
        f"SCENARIO: {scenario_summary}\n\n"
        "For EACH restriction row below, decide: does it block this program for this scenario?\n\n"
        "Rules:\n"
        "- 'Investment Properties' / 'Investor occupancy' → blocks ONLY Investment Property\n"
        "- 'all occupancies' or no occupancy qualifier → blocks any occupancy\n"
        "- Primary Residence + 'Investment Properties are ineligible' → do NOT block\n"
        "- Location may be in county, city, borough, zip, or flags (is_in_baltimore_city=true "
        "means property IS in Baltimore City)\n\n"
        "Return STRICT JSON: {\n"
        '  "blocked": [{"rule_id": int, "reason": str, "confidence": number}],\n'
        '  "notes":   [{"rule_id": int, "note": str}]\n'
        "}\n"
        f"Scenario context:\n{json.dumps(scenario_context, ensure_ascii=False)}\n\n"
        f"Restriction rows for this program:\n{json.dumps(restriction_rows, ensure_ascii=False)}"
    )
    try:
        oc = get_openai()
        resp = oc.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        parsed = json.loads((resp.choices[0].message.content or "{}").strip())
        for item in parsed.get("blocked") or []:
            item["program_id"] = pid
        for item in parsed.get("notes") or []:
            item["program_id"] = pid
        return parsed
    except Exception as exc:
        _log.warning("Geo LLM failed for program %s: %s", pid, exc)
        return {"blocked": [], "notes": []}


def _layer5_geo(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str], dict[int, str]]:
    """Pure-LLM geographic restriction layer.

    Returns (remaining_candidates, geo_blocked, overlay_notes).
    geo_blocked: program_id → reason; overlay_notes: program_id → note.
    """
    geo_blocked: dict[int, str] = {}
    overlay_notes: dict[int, str] = {}

    if not candidates:
        return candidates, geo_blocked, overlay_notes

    state = (form.get("state") or "").strip().upper()
    if not state:
        for c in candidates:
            c.setdefault("special_overlay", None)
        return candidates, geo_blocked, overlay_notes

    # ── Step 1 — overall state allowlist (deterministic) ──────────────────
    # DSCR scenarios use a broader licensing footprint than the standard list.
    is_dscr = (form.get("doc_type") or "").strip().lower() == "dscr_rental"
    eligible = _eligible_state_set(conn, is_dscr=is_dscr)
    if eligible and state not in eligible:
        reason = f"NewPoint is not licensed to lend in {state}."
        for c in candidates:
            geo_blocked[int(c["program_id"])] = reason
        return [], geo_blocked, overlay_notes

    # ── Step 2 — LLM adjudication of restriction_detail rows ──────────────
    ids = [int(c["program_id"]) for c in candidates]
    try:
        rows = conn.execute(
            text(
                """
                SELECT id, lender_id, program_id, state, restriction_type, restriction_detail
                FROM map_geographic_restrictions
                WHERE state = :state
                  AND restriction_type <> 'eligible_state'
                  AND (program_id IN :ids OR program_id IS NULL)
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"state": state, "ids": ids or [0]},
        ).fetchall()
    except Exception:
        rows = []

    restriction_rows = [
        {
            "id": int(r._mapping.get("id")),
            "lender_id": (
                int(r._mapping["lender_id"]) if r._mapping.get("lender_id") is not None else None
            ),
            "program_id": (
                int(r._mapping["program_id"]) if r._mapping.get("program_id") is not None else None
            ),
            "state": str(r._mapping.get("state") or ""),
            "restriction_type": str(r._mapping.get("restriction_type") or ""),
            "restriction_detail": str(r._mapping.get("restriction_detail") or ""),
        }
        for r in rows
    ]

    if not restriction_rows:
        for c in candidates:
            c.setdefault("special_overlay", None)
        return candidates, geo_blocked, overlay_notes

    program_catalog = [
        {
            "program_id": int(c["program_id"]),
            "lender_id": int(c.get("lender_id") or 0),
            "program_name": str(c.get("program_name") or ""),
        }
        for c in candidates
    ]
    program_by_id = {int(c["program_id"]): c for c in candidates}
    rules_by_id = {int(r["id"]): r for r in restriction_rows}
    valid_ids = {int(c["program_id"]) for c in candidates}

    def _rule_applies(rule: dict[str, Any], pid: int) -> bool:
        if rule.get("program_id") is not None:
            return int(rule["program_id"]) == pid
        if rule.get("lender_id") is not None:
            cand = program_by_id.get(pid) or {}
            return int(rule["lender_id"]) == int(cand.get("lender_id") or 0)
        return True

    scenario_context = _geo_scenario_context(form, state)

    # One focused LLM call per program (only its scoped rows) — more accurate than
    # one batch call where the model confuses occupancy across programs.
    parsed: dict[str, list] = {"blocked": [], "notes": []}
    for prog in program_catalog:
        pid = int(prog["program_id"])
        prog_rows = [r for r in restriction_rows if _rule_applies(r, pid)]
        result = _llm_geo_adjudicate_program(scenario_context, prog, prog_rows)
        parsed["blocked"].extend(result.get("blocked") or [])
        parsed["notes"].extend(result.get("notes") or [])

    def _add_note(pid: int, note: str) -> None:
        note = (note or "").strip()
        if not note:
            return
        prev = overlay_notes.get(pid)
        overlay_notes[pid] = f"{prev} | {note}" if prev else note

    for item in parsed.get("blocked") or []:
        try:
            pid = int(item.get("program_id"))
            rid = int(item.get("rule_id"))
        except Exception:
            continue
        if pid not in valid_ids:
            continue
        rule = rules_by_id.get(rid)
        if not rule:
            continue
        reason = (
            str(item.get("reason") or "").strip()
            or str(rule.get("restriction_detail") or "").strip()
        )
        try:
            conf = float(item.get("confidence") if item.get("confidence") is not None else 0.0)
        except Exception:
            conf = 0.0
        # Generic, non-domain guards only: confidence + rule scope. We trust the
        # LLM's reading of the rule; we do not re-judge it with keyword logic.
        if conf >= 0.85 and _rule_applies(rule, pid):
            geo_blocked[pid] = reason or "Geographic restriction applies"
        else:
            _add_note(pid, reason or "Potential geo restriction (needs review).")

    for item in parsed.get("notes") or []:
        try:
            pid = int(item.get("program_id"))
        except Exception:
            continue
        if pid not in valid_ids or pid in geo_blocked:
            continue
        rid = item.get("rule_id")
        if rid is not None:
            try:
                rule = rules_by_id.get(int(rid))
            except Exception:
                rule = None
            if rule and not _rule_applies(rule, pid):
                continue
        _add_note(pid, str(item.get("note") or ""))

    remaining: list[dict[str, Any]] = []
    for cand in candidates:
        cpid = int(cand["program_id"])
        if cpid in geo_blocked:
            continue
        # Merge any geo note with the program's EXISTING overlay (e.g. the matrix
        # special_overlays set at candidate-build) instead of overwriting it, so
        # non-blocking special rules still reach the "Additional Considerations"
        # section. Mirrors the append pattern used by the credit/housing layers.
        note = (overlay_notes.get(cpid) or "").strip()
        if note:
            existing = (cand.get("special_overlay") or "").strip()
            cand["special_overlay"] = f"{existing} | {note}" if existing else note
        else:
            cand.setdefault("special_overlay", None)










































































































































































































































































































































        remaining.append(cand)

    return remaining, geo_blocked, overlay_notes


# ---------------------------------------------------------------------------
# Layer 6 — credit history seasoning
# ---------------------------------------------------------------------------

# Mapping from segment keywords to DB event_type codes used in
# map_credit_history_seasoning.event_type.
_CREDIT_EVENT_CODE_MAP: dict[str, str] = {
    # Bankruptcy variants → "bk"
    "bk-ch7-discharged": "bk",
    "bk-ch7-dismissed": "bk",
    "bk-ch13-discharged": "bk",
    "bk-ch13-dismissed": "bk",
    "bk-ch13-active": "bk",
    "chapter 7": "bk",
    "chapter 13": "bk",
    "bankruptcy": "bk",
    "bk": "bk",
    # Foreclosure
    "fc": "fc",
    "foreclosure": "fc",
    # Short sale
    "ss": "ss",
    "short sale": "ss",
    # Deed in lieu
    "dil": "dil",
    "deed in lieu": "dil",
    "dl": "dl",
    # Pre-foreclosure → maps to fc for seasoning purposes
    "pre-fc": "fc",
    "pre-foreclosure": "fc",
    "pre_fc": "fc",
    # Charge-off
    "charge-off": "mc",
    "charge_off": "mc",
    "mc": "mc",
    # Notice of default
    "nod": "nd",
    "notice of default": "nd",
    "nd": "nd",
    # Loan modification
    "mod": "mo",
    "modification": "mo",
    "loan modification": "mo",
    "mo": "mo",
    # Forbearance / deferral
    "forbearance": "fd",
    "deferral": "fd",
    "fd": "fd",
    # Late payments
    "late": "late",
    "mortgage late": "late",
    # Settlement
    "settlement": "settlement",
}

# Bucket string → years_since float (lower bound of range)
_YEARS_BUCKET_MAP: dict[str, float] = {
    "<1 year": 0.5,
    "1-2 years": 1.0,
    "<2 years": 1.0,  # legacy — maps to lower band of old "<2 years" bucket
    "2-3 years": 2.0,
    "3-4 years": 3.0,
    "4-7 years": 4.0,
    "4+ years": 4.0,  # legacy label — same seasoning band as 4-7 years
    "7+ years": 7.0,
}


def _parse_credit_events(raw_event_str: str) -> list[tuple[str, float]]:
    """
    Parse a raw credit event string (possibly semicolon-separated) into a list
    of (event_type_code, years_since) tuples.

    Handles inputs like:
      - "BK-Ch7-Discharged 4-7 years"          → [("bk", 4.0)]
      - "FC; SS 2-3 years"                     → [("fc", ?), ("ss", 2.0)]
      - "BK-Ch7-Discharged 4-7 years; SS 2-3 years" → [("bk", 4.0), ("ss", 2.0)]
      - "bk" (legacy, no years)                → skip (years unknown)
    """
    if not raw_event_str:
        return []

    segments = [s.strip() for s in raw_event_str.split(";") if s.strip()]
    result: list[tuple[str, float]] = []

    for seg in segments:
        seg_lower = seg.lower()

        # --- Resolve event type code ---
        event_code: str | None = None

        # Try exact and prefix matches in order of specificity (longest key first)
        for key in sorted(_CREDIT_EVENT_CODE_MAP, key=len, reverse=True):
            if key in seg_lower:
                event_code = _CREDIT_EVENT_CODE_MAP[key]
                break

        if event_code is None:
            continue  # unrecognised segment — skip

        # --- Resolve years since event ---
        years: float | None = None

        # Check bucket strings first (most reliable)
        for bucket, val in _YEARS_BUCKET_MAP.items():
            if bucket.lower() in seg_lower:
                years = val
                break

        # Fall back to _extract_years (handles "N+ years", "N-M years", plain numbers)
        if years is None:
            years = _extract_years(seg)

        if years is None:
            continue  # years unknown — cannot evaluate seasoning; skip

        result.append((event_code, years))

    return result


def _layer6_credit_seasoning_single(
    conn: Any,
    event_code: str,
    months_since: int,
    ids: list[int],
    ltv: float,
    cltv: float,
    loan_amount: int | None,
    candidates: list[dict[str, Any]],
) -> dict[int, str]:
    """
    Evaluate seasoning for a single (event_code, months_since) against all candidates.
    Returns a dict of program_id → block_reason for programs that fail this event.
    """
    q = text(
        """
        SELECT program_id, event_type, tier, min_months_seasoning,
               max_ltv_overlay, max_cltv_overlay, max_loan_amount_overlay, notes
        FROM map_credit_history_seasoning
        WHERE program_id IN :ids
          AND event_type = :event_type
        ORDER BY program_id, min_months_seasoning DESC
        """
    ).bindparams(bindparam("ids", expanding=True))

    rows = conn.execute(q, {"ids": ids, "event_type": event_code}).fetchall()
    if not rows:
        return {}

    tiers_by_prog: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        r = dict(row._mapping)
        tiers_by_prog.setdefault(int(r["program_id"]), []).append(r)

    blocked: dict[int, str] = {}
    for cand in candidates:
        pid = int(cand["program_id"])
        tiers = tiers_by_prog.get(pid)
        if not tiers:
            # No seasoning data for this event type on this program → pass
            continue

        # Find best-matching tier (sorted DESC by min_months_seasoning)
        matched = None
        for t in tiers:
            min_m = int(t.get("min_months_seasoning") or 0)
            if months_since >= min_m:
                matched = t
                break

        if matched is None:
            blocked[pid] = "Credit event seasoning not met"
            continue

        # Apply overlays
        max_ltv_ov = matched.get("max_ltv_overlay")
        if max_ltv_ov is not None and ltv > float(max_ltv_ov):
            blocked[pid] = matched.get("notes") or f"Credit event tier max LTV {max_ltv_ov}%"
            continue
        max_cltv_ov = matched.get("max_cltv_overlay")
        if max_cltv_ov is not None and cltv > float(max_cltv_ov):
            blocked[pid] = matched.get("notes") or f"Credit event tier max CLTV {max_cltv_ov}%"
            continue
        max_loan_ov = matched.get("max_loan_amount_overlay")
        if max_loan_ov is not None and loan_amount is not None and loan_amount > int(max_loan_ov):
            blocked[pid] = matched.get("notes") or f"Credit event tier max loan {max_loan_ov}"
            continue

        # Passed — record tier note for attaching as overlay later
        # (overlay attachment happens in the caller after all events are evaluated)

    return blocked


def _layer6_credit_seasoning(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """
    Layer 6: credit history seasoning.

    Supports multi-event strings (semicolon-separated). Each distinct
    (event_type_code, years_since) tuple is evaluated independently and the
    UNION of failures across all events is the final blocked set (strictest wins).

    Falls back to the legacy single-event path when the raw credit event string
    contains no semicolons and the existing form fields are already populated.
    """
    raw_credit_event = (form.get("credit_event") or "").strip()

    # Build the list of (event_code, years) tuples to evaluate.
    # Multi-event: parse the raw string which may contain semicolons.
    events: list[tuple[str, float]] = []

    if raw_credit_event:
        events = _parse_credit_events(raw_credit_event)

    # If parsing yielded nothing (e.g. legacy format without a years suffix in the
    # raw string), fall back to the pre-parsed form fields.
    if not events:
        event_code = form.get("credit_event_type_code")
        years = form.get("years_since_event")
        if event_code and event_code != "none" and years is not None:
            events = [(event_code, float(years))]

    if not events:
        return candidates, {}

    ids = [int(r["program_id"]) for r in candidates]
    ltv = form["ltv"]
    cltv = form["cltv"]
    loan_amount = form["loan_amount"]

    # Accumulate blocked programs across all events (union of failures).
    all_blocked: dict[int, str] = {}
    for event_code, years in events:
        months_since = int(round(years * 12))
        per_event_blocked = _layer6_credit_seasoning_single(
            conn, event_code, months_since, ids, ltv, cltv, loan_amount, candidates
        )
        # Merge: first block reason wins per program.
        for pid, reason in per_event_blocked.items():
            all_blocked.setdefault(pid, reason)

    # For programs that pass all events, attach tier notes as overlays.
    # We do a final pass using the first event only to pick up overlay notes
    # (notes are informational and don't affect blocking).
    first_event_code = events[0][0]
    first_months = int(round(events[0][1] * 12))
    q_notes = text(
        """
        SELECT program_id, tier, min_months_seasoning, notes
        FROM map_credit_history_seasoning
        WHERE program_id IN :ids
          AND event_type = :event_type
        ORDER BY program_id, min_months_seasoning DESC
        """
    ).bindparams(bindparam("ids", expanding=True))
    try:
        note_rows = conn.execute(
            q_notes, {"ids": ids, "event_type": first_event_code}
        ).fetchall()
        tiers_for_notes: dict[int, list[dict[str, Any]]] = {}
        for row in note_rows:
            r = dict(row._mapping)
            tiers_for_notes.setdefault(int(r["program_id"]), []).append(r)

        for cand in candidates:
            pid = int(cand["program_id"])
            if pid in all_blocked:
                continue
            tiers = tiers_for_notes.get(pid)
            if not tiers:
                continue
            matched_note = None
            for t in tiers:
                min_m = int(t.get("min_months_seasoning") or 0)
                if first_months >= min_m:
                    matched_note = t
                    break
            if matched_note:
                note = str(matched_note.get("notes") or matched_note.get("tier") or "")
                if note:
                    existing = cand.get("special_overlay") or ""
                    cand["special_overlay"] = (
                        (existing + " | " + note).lstrip(" | ") if existing else note
                    )
    except Exception:
        pass  # Notes are informational — don't fail the layer on DB error

    remaining = [r for r in candidates if int(r["program_id"]) not in all_blocked]
    return remaining, all_blocked


# ---------------------------------------------------------------------------
# Layer 7 — housing history seasoning
# ---------------------------------------------------------------------------


_PAYMENT_HISTORY_MAP: dict[str, str] = {
    "0x30": "0x30",
    "0 x 30": "0x30",
    "1x30": "1x30",
    "1 x 30": "1x30",
    "2x30": "2x30",
    "2 x 30": "2x30",
    "3x30": "3x30",
    "3 x 30": "3x30",
    "1x60": "1x60",
    "1 x 60": "1x60",
    "0x60": "0x60",
    "0 x 60": "0x60",
    "1x90": "1x90",
    "1 x 90": "1x90",
    "1x120": "1x120",
    "1 x 120": "1x120",
}


def _normalise_payment_history(raw: str) -> str | None:
    if not raw:
        return None
    k = raw.strip().lower().replace(" ", "")
    # Try direct match first
    for key, val in _PAYMENT_HISTORY_MAP.items():
        if key.replace(" ", "") == k:
            return val
    return None


def _layer7_housing_history(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Informational overlays only — no hard-blocks."""
    raw_ph = form.get("payment_history", "")
    history_pattern = _normalise_payment_history(raw_ph or "")
    if not history_pattern or not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    q = text(
        """
        SELECT program_id, history_pattern, tier,
               max_ltv_overlay, max_cltv_overlay, max_loan_amount_overlay, notes
        FROM map_housing_history_seasoning
        WHERE program_id IN :ids
          AND history_pattern = :pattern
        ORDER BY program_id
        """
    ).bindparams(bindparam("ids", expanding=True))

    rows = conn.execute(q, {"ids": ids, "pattern": history_pattern}).fetchall()
    if not rows:
        return candidates

    notes_by_prog: dict[int, str] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        note = str(r.get("notes") or r.get("tier") or "")
        ltv_ov = r.get("max_ltv_overlay")
        if ltv_ov is not None:
            note = (note + f" | max_ltv_overlay={ltv_ov}").lstrip(" | ")
        if note:
            notes_by_prog[pid] = note

    for cand in candidates:
        pid = int(cand["program_id"])
        note = notes_by_prog.get(pid)
        if note:
            existing = cand.get("special_overlay") or ""
            cand["special_overlay"] = (existing + " | " + note).lstrip(" | ") if existing else note

    return candidates


# ---------------------------------------------------------------------------
# Scenario considerations — hard filters from wizard Conditions / Basics gates
# ---------------------------------------------------------------------------


def _scenario_block_reason(cand: dict[str, Any], form: dict[str, Any]) -> str | None:
    """Return a block reason when a program fails a scenario-specific gate."""
    if form.get("non_arms_length") is True and cand.get("is_dscr"):
        return "Non-arm's length transactions ineligible on DSCR programs"

    if (
        form.get("first_time_investor")
        and form.get("established_primary_res") is False
        and cand.get("is_dscr")
        and form.get("occupancy") == "investment"
    ):
        return "First-time investor without established primary residence"

    if form.get("power_of_attorney") is True and cand.get("is_foreign_nat"):
        return "Power of Attorney not permitted on Foreign National loans"

    return None


_KNOWN_VISA_CODES = frozenset({
    "h-1b", "h-4 ead", "h-2a", "h-2b", "h-3",
    "e-1", "e-2", "e-3", "eb-5",
    "l-1a", "l-1b", "o-1", "tn",
    "i", "g-1", "g-2", "g-3", "g-4", "g-5", "nato", "r-1",
})


# NPRA allowed only with US credit/FICO — matches full and truncated guideline snippets.
_NPRA_REQUIRES_US_CREDIT_RE = re.compile(
    r"non[- ]permanent resident alien[s]?"
    r".*?(?:\(w/\s*us(?:\s*credit)?|with\s+us\s+credit)",
    re.I,
)


def _parse_npra_max_leverage(content: str) -> float | None:
    """Parse NPRA-specific max LTV/CLTV from Eligible Borrower guideline text."""
    if not re.search(r"non[- ]permanent resident alien", content, re.I):
        return None
    m = re.search(
        r"max\s+(?:ltv/cltv|ltv\s*/\s*cltv)\s*(\d+(?:\.\d+)?)\s*%",
        content,
        re.I,
    )
    if m:
        return float(m.group(1))
    m2 = re.search(r"max\s+(\d+(?:\.\d+)?)\s*%\s*cltv", content, re.I)
    if m2:
        return float(m2.group(1))
    return None


def _qualifying_leverage_for_npra(form: dict[str, Any]) -> float | None:
    """LTV or CLTV to compare against NPRA overlay caps."""
    ltv = _to_float_opt(form.get("ltv"))
    cltv = _to_float_opt(form.get("cltv"))
    if form.get("is_second_lien"):
        return cltv if cltv is not None else ltv
    if ltv is not None and cltv is not None:
        return max(ltv, cltv)
    return ltv if ltv is not None else cltv


def _visa_is_custom(raw: dict[str, Any], visa_type: str) -> bool:
    cat = str(raw.get("visaCategory") or "").strip().lower()
    if "other" in cat and "listed" in cat:
        return True
    vt = visa_type.strip().lower()
    if not vt:
        return False
    # Strip UI description suffix only (em-dash), not hyphens inside visa codes (H-1B, L-1A).
    base = re.split(r"\s*[—–]\s*", vt, maxsplit=1)[0].strip()
    if base in _KNOWN_VISA_CODES:
        return False
    return not any(base == k for k in _KNOWN_VISA_CODES)


def _npra_rules_by_program(conn: Any, program_ids: list[int]) -> dict[int, dict[str, Any]]:
    """
    Parse map_program_rule_guideline for NPRA-specific gates.
    Returns per program_id:
      npra_hard_block          — NPRA explicitly not permitted
      npra_requires_us_credit  — NPRA allowed only with US credit / FICO
      npra_eligible_borrower   — NPRA listed as eligible borrower (no US-credit caveat)
      npra_non_traditional     — NPRA may use non-traditional credit instead of FICO
      npra_max_leverage        — NPRA max LTV/CLTV overlay from Eligible Borrower row
    """
    if not program_ids:
        return {}

    q = text(
        """
        SELECT program_id, category, content
        FROM map_program_rule_guideline
        WHERE program_id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))

    try:
        rows = conn.execute(q, {"ids": program_ids}).fetchall()
    except Exception:
        return {}

    rules: dict[int, dict[str, Any]] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        category = (r.get("category") or "").strip().lower()
        content = (r.get("content") or "").strip()
        blob = f"{category} {content}".lower()
        if "non-permanent" not in blob and "non permanent" not in blob:
            continue

        entry = rules.setdefault(
            pid,
            {
                "npra_hard_block": False,
                "npra_requires_us_credit": False,
                "npra_eligible_borrower": False,
                "npra_non_traditional": False,
                "npra_max_leverage": None,
            },
        )

        if re.search(
            r"not permitted for non[- ]permanent|non[- ]permanent resident aliens?(?:\s+are)?\s+ineligible",
            blob,
        ):
            entry["npra_hard_block"] = True

        if re.search(r"non[- ]traditional credit", blob) and "non-permanent" in blob:
            entry["npra_non_traditional"] = True

        npra_cap = _parse_npra_max_leverage(content)
        if npra_cap is not None:
            prev = entry.get("npra_max_leverage")
            entry["npra_max_leverage"] = (
                min(float(prev), npra_cap) if prev is not None else npra_cap
            )

        if category in {"eligible borrower", "eligible borrowers"} and re.search(
            r"non[- ]permanent resident alien", content, re.I
        ):
            if not _NPRA_REQUIRES_US_CREDIT_RE.search(content):
                entry["npra_eligible_borrower"] = True

        if _NPRA_REQUIRES_US_CREDIT_RE.search(content) or _NPRA_REQUIRES_US_CREDIT_RE.search(
            blob
        ):
            entry["npra_requires_us_credit"] = True

    return rules


def _citizenship_npra_block_reason(
    cand: dict[str, Any],
    form: dict[str, Any],
    npra_rules: dict[int, dict[str, Any]],
) -> str | None:
    if form.get("citizenship_code") != "non_perm_resident":
        return None

    pid = int(cand["program_id"])
    rules = npra_rules.get(pid, {})
    fico = form.get("fico")
    visa_custom = form.get("visa_is_custom", False)

    if rules.get("npra_hard_block"):
        return "Not permitted for Non-Permanent Resident Aliens"

    if rules.get("npra_requires_us_credit") and fico is None:
        return "Non-Permanent Resident Aliens require US credit (FICO score)"

    if fico is None and not rules.get("npra_non_traditional"):
        prog_fico_min = cand.get("min_fico")
        if prog_fico_min is not None and not rules.get("npra_eligible_borrower"):
            return "Credit score required for this program (NPRA with US credit)"

    if visa_custom and not rules.get("npra_eligible_borrower"):
        return f"Visa type not eligible for {cand.get('program_name') or 'this program'}"

    npra_max = rules.get("npra_max_leverage")
    if npra_max is not None:
        leverage = _qualifying_leverage_for_npra(form)
        if leverage is not None and float(leverage) > float(npra_max) + 0.01:
            label = "CLTV" if form.get("is_second_lien") else "LTV/CLTV"
            return (
                f"Non-Permanent Resident Alien max {label} {float(npra_max):g}% "
                f"(scenario: {float(leverage):g}%)"
            )

    return None


def _layer_citizenship_npra_gate(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    if not candidates or form.get("citizenship_code") != "non_perm_resident":
        return candidates, {}

    ids = [int(c["program_id"]) for c in candidates]
    npra_rules = _npra_rules_by_program(conn, ids)
    blocked: dict[int, str] = {}
    remaining: list[dict[str, Any]] = []
    for cand in candidates:
        pid = int(cand["program_id"])
        reason = _citizenship_npra_block_reason(cand, form, npra_rules)
        if reason:
            blocked[pid] = reason
        else:
            remaining.append(cand)
    return remaining, blocked


# ---------------------------------------------------------------------------
# Acreage caps — map_program_rule_guideline
# ---------------------------------------------------------------------------

_ACREAGE_RULE_CATEGORIES = frozenset({
    "acreage",
    "acreage / rural",
    "rural properties",
    "rural property overlays",
})

_ACREAGE_NON_LIMIT_RE = re.compile(
    r"solar|title report|flood|hazard insurance|appraisal|ownership seasoning|"
    r"listed for sale|transferred appraisal|loss payee|ucc filing|pace/hero",
    re.I,
)


def _parse_acreage_caps_from_content(content: str) -> dict[str, float | None]:
    """Extract per-occupancy acreage caps from a guideline snippet."""
    caps: dict[str, float | None] = {
        "primary_max": None,
        "second_max": None,
        "investment_max": None,
        "any_max": None,
    }
    text = (content or "").strip()
    if not text or not re.search(r"\bacres?\b", text, re.I):
        return caps
    if _ACREAGE_NON_LIMIT_RE.search(text):
        return caps

    tl = text.lower()

    m = re.search(
        r"max\s+(\d+(?:\.\d+)?)\s+acres?\s+for\s+primary\s+residence\s*&\s*second\s+home"
        r".*?max\s+(\d+(?:\.\d+)?)\s+acres?\s+investment",
        tl,
    )
    if m:
        caps["primary_max"] = float(m.group(1))
        caps["second_max"] = float(m.group(1))
        caps["investment_max"] = float(m.group(2))
        return caps

    m = re.search(r"max\s+(\d+(?:\.\d+)?)\s+acres?\s+primary\s+residence", tl)
    if m:
        caps["primary_max"] = float(m.group(1))
    m = re.search(r"max\s+(\d+(?:\.\d+)?)\s+acres?\s+second", tl)
    if m:
        caps["second_max"] = float(m.group(1))
    m = re.search(r"max\s+(\d+(?:\.\d+)?)\s+acres?\s+investment", tl)
    if m:
        caps["investment_max"] = float(m.group(1))

    m = re.search(r"property\s+up\s+to\s+(\d+(?:\.\d+)?)\s*-?\s*acres?", tl)
    if m:
        caps["any_max"] = float(m.group(1))

    if caps["any_max"] is None and not any(
        caps[k] is not None for k in ("primary_max", "second_max", "investment_max")
    ):
        m = re.search(r"max\s+(\d+(?:\.\d+)?)\s*-?\s*acres?(?:\s|;|,|\||$)", tl)
        if m:
            caps["any_max"] = float(m.group(1))
        else:
            m = re.search(r"<=\s*(\d+(?:\.\d+)?)\s+acres?", tl)
            if m:
                caps["any_max"] = float(m.group(1))

    return caps


def _merge_acreage_caps(
    base: dict[str, float | None],
    incoming: dict[str, float | None],
) -> dict[str, float | None]:
    """Keep the strictest (lowest) cap when a program has multiple acreage rows."""
    out = dict(base)
    for key in ("primary_max", "second_max", "investment_max", "any_max"):
        inc = incoming.get(key)
        if inc is None:
            continue
        prev = out.get(key)
        out[key] = min(prev, inc) if prev is not None else inc
    return out


def _acreage_cap_for_occupancy(
    caps: dict[str, float | None],
    occupancy: str,
) -> float | None:
    if occupancy == "investment":
        return caps.get("investment_max") or caps.get("any_max")
    if occupancy == "second":
        return (
            caps.get("second_max")
            or caps.get("primary_max")
            or caps.get("any_max")
        )
    if occupancy == "primary":
        return caps.get("primary_max") or caps.get("any_max")
    return caps.get("any_max")


def _acreage_caps_by_program(
    conn: Any,
    program_ids: list[int],
) -> dict[int, dict[str, float | None]]:
    if not program_ids:
        return {}

    q = text(
        """
        SELECT program_id, category, content
        FROM map_program_rule_guideline
        WHERE program_id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))

    try:
        rows = conn.execute(q, {"ids": program_ids}).fetchall()
    except Exception:
        return {}

    caps_by_pid: dict[int, dict[str, float | None]] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        category = (r.get("category") or "").strip().lower()
        content = (r.get("content") or "").strip()
        if category not in _ACREAGE_RULE_CATEGORIES and not re.search(
            r"\b(?:max|up to)\s+\d+(?:\.\d+)?\s*-?\s*acres?\b", content, re.I
        ):
            continue
        parsed = _parse_acreage_caps_from_content(content)
        if not any(parsed.get(k) is not None for k in parsed):
            continue
        caps_by_pid[pid] = _merge_acreage_caps(caps_by_pid.get(pid, {}), parsed)
    return caps_by_pid


def _acreage_block_reason(
    cand: dict[str, Any],
    form: dict[str, Any],
    caps_by_pid: dict[int, dict[str, float | None]],
) -> str | None:
    acres = form.get("acreage")
    if acres is None or float(acres) <= 0:
        return None

    pid = int(cand["program_id"])
    caps = caps_by_pid.get(pid)
    if not caps:
        return None

    cap = _acreage_cap_for_occupancy(caps, form.get("occupancy", ""))
    if cap is None:
        return None

    if float(acres) > float(cap) + 0.01:
        return f"Acreage exceeds program limit ({float(acres):g} ac > max {float(cap):g} ac)"

    return None


def _fetch_rule_snippets_by_program(
    conn: Any,
    program_ids: list[int],
) -> dict[int, list[tuple[str, str]]]:
    """Load map_program_rule_guideline rows grouped by program."""
    if not program_ids:
        return {}

    q = text(
        """
        SELECT program_id, category, content
        FROM map_program_rule_guideline
        WHERE program_id IN :ids
        ORDER BY program_id, category
        """
    ).bindparams(bindparam("ids", expanding=True))

    try:
        rows = conn.execute(q, {"ids": program_ids}).fetchall()
    except Exception:
        return {}

    by_pid: dict[int, list[tuple[str, str]]] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        category = (r.get("category") or "").strip()
        content = (r.get("content") or "").strip()
        if content:
            by_pid.setdefault(pid, []).append((category, content))
    return by_pid


def _snippet_is_absolute_ineligible(content: str) -> bool:
    """Whole-row hard ineligible (not a partial 'X ineligible for Y' caveat)."""
    cl = content.strip().lower()
    if cl in {"ineligible", "not eligible", "not permitted"}:
        return True
    if cl.startswith("ineligible:") or cl.startswith("ineligible "):
        return True
    return False


def _snippet_has_hard_deny_language(content: str) -> bool:
    cl = content.lower()
    if _snippet_is_absolute_ineligible(content):
        return True
    return bool(
        re.search(
            r"\b(not\s+(?:permitted|eligible|allowed)|prohibited|not\s+available)\b",
            cl,
        )
    )


def _property_type_matches_ineligible_list(content: str, form: dict[str, Any]) -> bool:
    """Scenario property type / rural flag appears in an ineligible list or row."""
    cl = content.lower()
    if not _snippet_has_hard_deny_language(content) and "not eligible for" not in cl:
        return False

    pt = form.get("property_type_code", "")
    occ = form.get("occupancy", "")

    if form.get("is_rural_property") and re.search(r"\brural\b", cl):
        if _snippet_is_absolute_ineligible(content) or "ineligible:" in cl:
            return True
        if re.search(r"rural[^.]{0,40}ineligible|ineligible[^.]{0,80}\brural\b", cl):
            return True

    type_checks: list[tuple[bool, tuple[str, ...]]] = [
        (pt == "two_to_four_family", ("2-4 unit", "2-4 units", "2 4 unit", "multifamily", "multi-family")),
        (pt == "condotel", ("condotel", "condo hotel", "condo hotel")),
        (pt == "condo_non_warrantable", ("non-warrantable", "non warrantable")),
        (pt == "five_to_eight_unit", ("5-8 unit", "5-9 unit", "five to eight", "five to nine")),
        (pt == "manufactured", ("manufactured",)),
        (pt == "coop", ("co-op", "coop", "cooperative")),
    ]
    for matches, keywords in type_checks:
        if matches and any(kw in cl for kw in keywords):
            if _snippet_is_absolute_ineligible(content) or "ineligible:" in cl:
                return True
            if re.search(r"not eligible for", cl):
                return True

    if pt == "two_to_four_family" and occ == "second":
        if re.search(r"2-4\s+unit[s]?\s*:\s*not eligible for second", cl):
            return True

    return False


def _parse_property_type_ltv_cap(content: str, property_type_code: str) -> float | None:
    """Max LTV/CLTV for a property type named in a Property Type guideline row."""
    if not property_type_code or not content:
        return None
    cl = content.lower()
    pt = property_type_code.lower()

    type_keywords: dict[str, tuple[str, ...]] = {
        "two_to_four_family": ("2-4 unit", "2-4 units", "2 4 unit"),
        "condo_warrantable": ("condominium", "condo"),
        "condo_non_warrantable": ("non-warrantable", "non warrantable"),
        "condotel": ("condotel", "condo hotel"),
        "single_family": ("single family", "sfr"),
        "pud": ("pud",),
        "townhouse": ("townhome", "townhouse"),
    }
    keywords = type_keywords.get(pt, (pt.replace("_", " "),))
    if not any(kw in cl for kw in keywords):
        return None

    m = re.search(r"max\s+(?:ltv/cltv|ltv\s*/\s*cltv|ltv)\s*(\d+(?:\.\d+)?)\s*%", cl)
    if m:
        return float(m.group(1))
    m = re.search(r"max\s+ltv/cltv\s*(\d+(?:\.\d+)?)\s*%", cl)
    if m:
        return float(m.group(1))
    return None


def _property_type_ltv_block_reason(
    cand: dict[str, Any],
    form: dict[str, Any],
    snippets: list[tuple[str, str]],
) -> str | None:
    pt = form.get("property_type_code", "")
    if not pt:
        return None
    leverage = _qualifying_leverage_for_npra(form)
    if leverage is None:
        return None

    caps: list[float] = []
    for category, content in snippets:
        if (category or "").strip().lower() not in ("property type", "property types"):
            continue
        cap = _parse_property_type_ltv_cap(content, pt)
        if cap is not None:
            caps.append(cap)

    if not caps:
        return None
    cap = min(caps)
    if float(leverage) > cap + 0.01:
        return (
            f"Property type max LTV/CLTV {cap:g}% for this program "
            f"(scenario: {float(leverage):g}%)"
        )
    return None


def _evaluate_rule_snippet_overlay(
    category: str,
    content: str,
    form: dict[str, Any],
    cand: dict[str, Any],
    *,
    stage: RuleOverlayStage,
) -> str | None:
    """
    Return a block reason when a map_program_rule_guideline row applies to this scenario.

    *basics*  — Step 1 fields (citizenship handled separately; property type, FTHB, purpose, lien)
    *extended* — Steps 2–5 (rural, acreage, NOCB, credit history, conditions)
    """
    cat = (category or "").strip().lower()
    text = (content or "").strip()
    if not text:
        return None
    blob = f"{cat} {text}".lower()

    if stage == "basics":
        if form.get("is_fthb"):
            if ("first time home" in cat or "fthb" in cat) and _snippet_is_absolute_ineligible(text):
                return text[:240]
            if cat == "interest only" and "first time home" in blob and "ineligible" in blob:
                return text[:240]

        if form.get("first_time_investor") and form.get("established_primary_res") is False:
            if "first time investor" in blob or "first-time investor" in blob:
                if _snippet_has_hard_deny_language(text):
                    return text[:240]

        if form.get("loan_purpose") == "cash_out":
            if "cash-out ineligible" in blob or "cash out ineligible" in blob:
                return text[:240]
            if cat in ("cash out refinance", "cash out amount", "cash-in-hand", "maximum cash out"):
                if _snippet_is_absolute_ineligible(text):
                    return text[:240]

        if form.get("is_second_lien"):
            if cat in ("subordinate financing", "senior lien eligibility"):
                if _snippet_is_absolute_ineligible(text):
                    return text[:240]

        if cat in ("property type", "property types") or "ineligible:" in blob:
            if _property_type_matches_ineligible_list(text, form):
                return text[:240]

        return None

    # --- extended stage (Steps 2–5) ---
    if form.get("listing_seasoning_yes"):
        if "listed" in cat or "listed for sale" in blob:
            if _snippet_has_hard_deny_language(text) or "prior 6 months" in blob:
                return text[:240]

    if form.get("power_of_attorney") is True and "power of attorney" in cat:
        if _snippet_is_absolute_ineligible(text):
            return text[:240]

    if form.get("non_arms_length") is True:
        if re.search(r"non.?arm['\u2019]?s?\s+length", blob) and _snippet_has_hard_deny_language(text):
            return text[:240]

    if form.get("non_occupant_cob") and form.get("loan_purpose") == "cash_out":
        if "non-occupant" in cat or "non occupant" in cat:
            if "cash out not permitted" in blob or "cash-out not permitted" in blob:
                return text[:240]

    if form.get("is_rural_property"):
        if cat in ("rural properties", "rural property overlays", "acreage / rural"):
            if _snippet_is_absolute_ineligible(text):
                return text[:240]
            if form.get("loan_purpose") == "cash_out" and "cash-out ineligible" in blob:
                return text[:240]
            if form.get("doc_type") == "dscr_rental" and "dscr ineligible" in blob:
                return text[:240]

    if form.get("doc_type") == "dscr_rental" and form.get("is_rural_property"):
        if "dscr ineligible" in blob:
            return text[:240]

    if form.get("io_pref") is True:
        if cat.startswith("interest only") and _snippet_is_absolute_ineligible(text):
            return text[:240]

    ph = (form.get("payment_history") or "").lower().replace(" ", "")
    if "rentfree" in ph or ph == "rent_free":
        if "rent free" in blob and "ineligible" in blob:
            return text[:240]

    return None


def _rule_snippet_overlay_block_reason(
    cand: dict[str, Any],
    form: dict[str, Any],
    snippets: list[tuple[str, str]],
    acreage_caps: dict[int, dict[str, float | None]],
    *,
    stage: RuleOverlayStage,
) -> str | None:
    """Overlay blocks from map_program_rule_guideline for one candidate."""
    if stage == "extended":
        reason = _acreage_block_reason(cand, form, acreage_caps)
        if reason:
            return reason

    if stage == "basics":
        reason = _property_type_ltv_block_reason(cand, form, snippets)
        if reason:
            return reason

    for category, content in snippets:
        reason = _evaluate_rule_snippet_overlay(
            category, content, form, cand, stage=stage
        )
        if reason:
            return reason

    return None


def _layer_rule_snippet_overlays(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    stage: RuleOverlayStage,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """
    Apply map_program_rule_guideline overlay gates.

    basics   — immediately after Layer 1+2 (wizard Step 1: citizenship, property, LTV, purpose)
    extended — after Layers 3–7 (Capacity, Credit, Collateral, Conditions fields)
    """
    if not candidates:
        return candidates, {}

    ids = [int(c["program_id"]) for c in candidates]
    snippets_by_pid = _fetch_rule_snippets_by_program(conn, ids)
    acreage_caps: dict[int, dict[str, float | None]] = {}
    if stage == "extended" and form.get("acreage") is not None and float(form.get("acreage") or 0) > 0:
        acreage_caps = _acreage_caps_by_program(conn, ids)

    blocked: dict[int, str] = {}
    pool = candidates

    if stage == "basics":
        pool, npra_blocked = _layer_citizenship_npra_gate(conn, form, candidates)
        blocked.update(npra_blocked)

    remaining: list[dict[str, Any]] = []
    for cand in pool:
        pid = int(cand["program_id"])
        if pid in blocked:
            continue
        snippets = snippets_by_pid.get(pid, [])
        reason = None
        if stage == "extended":
            reason = _scenario_block_reason(cand, form)
        if not reason:
            reason = _rule_snippet_overlay_block_reason(
                cand, form, snippets, acreage_caps, stage=stage
            )
        if reason:
            blocked[pid] = reason
        else:
            remaining.append(cand)

    return remaining, blocked


def _layer_scenario_considerations(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Backward-compatible alias — extended rule-snippet overlays only."""
    return _layer_rule_snippet_overlays(conn, form, candidates, stage="extended")


def _filter_prepay_stepdown(
    candidates: list[dict[str, Any]],
    form: dict[str, Any],
) -> list[dict[str, Any]]:
    """When borrower specifies stepdown preference, filter prepay option lists."""
    stepdown = (form.get("prepay_stepdown") or "").strip().lower()
    if not stepdown or stepdown in {"no preference", "not applicable"}:
        return candidates

    prep_raw = (form.get("prepayment_terms_raw") or "").strip().lower()
    if not prep_raw or prep_raw in {"none", "no penalty", ""}:
        return candidates

    remaining: list[dict[str, Any]] = []
    for cand in candidates:
        options = [str(o) for o in (cand.get("prepayment_options") or []) if str(o).strip()]
        if not options:
            remaining.append(cand)
            continue
        has_step = any("step" in o.lower() for o in options)
        if stepdown == "yes" and not has_step:
            continue
        if stepdown == "no" and has_step and all("step" in o.lower() for o in options):
            continue
        remaining.append(cand)
    return remaining


# ---------------------------------------------------------------------------
# Layer 8 — rule/guideline notes
# ---------------------------------------------------------------------------

def _rule_note_applies_to_scenario(
    category: str,
    content: str,
    form: dict[str, Any],
) -> bool:
    """Skip guideline rows that clearly apply to a different scenario shape."""
    cat_l = category.lower()
    text_l = content.lower()
    purpose = form.get("loan_purpose", "")
    occ = form.get("occupancy", "")

    cash_out_cats = (
        "cash out",
        "cash-out",
        "cash in hand",
        "cash-in-hand",
        "maximum cash out",
    )
    if any(k in cat_l for k in cash_out_cats) and purpose != "cash_out":
        return False

    if occ == "primary" and re.search(
        r"investment propert(?:y|ies) only|investor occupancy only|non-owner.?occupied only",
        text_l,
    ):
        return False
    if occ == "investment" and re.search(
        r"primary residence only|owner.?occupied only|second home only",
        text_l,
    ):
        return False

    if "dscr" in cat_l and form.get("doc_type") not in ("dscr_rental", "any"):
        return False

    return True


def _layer8_rule_guidelines(
    conn: Any,
    candidates: list[dict[str, Any]],
    form: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach rag_notes from map_program_rule_guideline — filtered for this scenario."""
    if not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    snippets_by_pid = _fetch_rule_snippets_by_program(conn, ids)
    notes_by_prog: dict[int, list[str]] = {}
    seen_by_prog: dict[int, set[str]] = {}
    for pid, snippets in snippets_by_pid.items():
        for category, content in snippets:
            if form and not _rule_note_applies_to_scenario(category, content, form):
                continue
            note = f"{category}: {content}" if category else content
            content_key = re.sub(r"\s+", " ", note.lower())
            if content_key in seen_by_prog.setdefault(pid, set()):
                continue
            seen_by_prog[pid].add(content_key)
            notes_by_prog.setdefault(pid, []).append(note)

    for cand in candidates:
        pid = int(cand["program_id"])
        existing = cand.get("rag_notes") or []
        new_notes = filter_notes_for_summarize(notes_by_prog.get(pid, []))
        cand["rag_notes"] = existing + new_notes
        cand["rule_notes"] = []

    return candidates


# ---------------------------------------------------------------------------
# Layer 9 — prepayment options (investment only)
# ---------------------------------------------------------------------------


def _layer9_prepayment(
    conn: Any,
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach prepayment_options list to each candidate."""
    for cand in candidates:
        cand.setdefault("prepayment_options", [])

    if form.get("occupancy") != "investment" or not form.get("state"):
        return candidates
    if not candidates:
        return candidates

    ids = [int(r["program_id"]) for r in candidates]
    state = form["state"]

    q = text(
        """
        SELECT pp.program_id, pt.name AS term_name, pp.occupancy_scope, pp.ineligible_states
        FROM map_program_prepayment_options pp
        INNER JOIN dim_prepayment_terms pt ON pt.id = pp.prepayment_term_id
        WHERE pp.program_id IN :ids
          AND (pp.occupancy_scope = 'investment' OR pp.occupancy_scope = 'all')
        ORDER BY pp.program_id, pt.name
        """
    ).bindparams(bindparam("ids", expanding=True))

    try:
        rows = conn.execute(q, {"ids": ids}).fetchall()
    except Exception:
        # Table may not exist yet
        return candidates

    options_by_prog: dict[int, list[str]] = {}
    for row in rows:
        r = dict(row._mapping)
        pid = int(r["program_id"])
        ineligible = (r.get("ineligible_states") or "")
        ineligible_list = [s.strip().upper() for s in re.split(r"[,;|]", ineligible) if s.strip()]
        if state in ineligible_list:
            continue
        options_by_prog.setdefault(pid, []).append(str(r.get("term_name") or ""))

    for cand in candidates:
        pid = int(cand["program_id"])
        cand["prepayment_options"] = options_by_prog.get(pid, [])

    return candidates


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layer 10 — Qdrant RAG cross-verification
# ---------------------------------------------------------------------------

_STATE_NAMES: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming",
}

# Categories from map_program_rule_guideline worth surfacing as notes
_NOTE_CATEGORIES = frozenset({
    "reserves", "gift_funds", "non_occupant_coborrower", "acreage_limits",
    "first_time_investor", "entity_vesting", "non_warrantable_condo",
    "declining_market", "loan_amount_overlays", "residual_income",
    "io_overlays", "cash_out_limits", "cross_collateral", "aus_findings",
})


def _qdrant_scroll_chunks(client: Any, program_id: int) -> list[str]:
    """Return all text chunks in mortgage_matrices for this program (MySQL id).

    Ingest uses ``program_mysql_id`` (Everest/Summit) or ``program_id`` (Denali/NQM).
    """
    from qdrant_client import models as qmodels  # local import — optional dep
    pid = int(program_id)
    scroll_filter = qmodels.Filter(
        should=[
            qmodels.FieldCondition(
                key="program_mysql_id",
                match=qmodels.MatchValue(value=pid),
            ),
            qmodels.FieldCondition(
                key="program_id",
                match=qmodels.MatchValue(value=pid),
            ),
        ]
    )
    chunks: list[str] = []
    offset: Any = None
    while True:
        points, offset = client.scroll(
            collection_name=config.MATRIX_COLLECTION_NAME,
            scroll_filter=scroll_filter,
            limit=50,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for pt in points:
            body = ((pt.payload or {}).get("text") or "").strip()
            if body:
                chunks.append(body)
        if offset is None:
            break
    return chunks


def _detect_hard_block(text_lower: str, form: dict[str, Any]) -> str | None:
    """
    Return a human-readable block reason if the chunk text clearly contradicts
    the scenario. Return None if no contradiction found.
    Only fires on very high-confidence patterns to avoid false positives.
    """
    state = form.get("state", "").upper()
    fico = form.get("fico")
    occupancy = form.get("occupancy", "")
    loan_purpose = form.get("loan_purpose", "")
    property_type = form.get("property_type_code", "")

    # ── State ineligibility ───────────────────────────────────────────────────
    if state:
        state_name = _STATE_NAMES.get(state, "").lower()
        state_code_lower = state.lower()
        # Pattern: "not available in XX" / "ineligible in Texas" / "excluded: XX"
        not_avail_patterns = [
            rf"\bnot\s+(?:available|offered|eligible|permitted)\s+in\s+{re.escape(state_code_lower)}\b",
            rf"\bnot\s+(?:available|offered|eligible|permitted)\s+in\s+{re.escape(state_name)}\b" if state_name else None,
            rf"\b{re.escape(state_code_lower)}\s+(?:is\s+)?(?:not\s+)?ineligible\b",
        ]
        for pat in not_avail_patterns:
            if pat and re.search(pat, text_lower):
                return f"Program text indicates not available in {state}"

        # Pattern: explicit ineligible state list containing this state code
        # e.g. "Ineligible States: AK, CT, HI, TX"
        ineligible_list_m = re.search(
            r"ineligible\s+states?\s*[:\-]\s*([A-Z, \n]+)", text_lower.upper()
        )
        if ineligible_list_m:
            listed = re.findall(r"\b([A-Z]{2})\b", ineligible_list_m.group(1))
            if state in listed:
                return f"State {state} listed in program ineligible states"

    # ── FICO floor in text clearly above borrower score ───────────────────────
    if fico is not None:
        # "minimum fico: 720" / "min credit score 700"
        for m in re.finditer(
            r"min(?:imum)?\s+(?:fico|credit\s+score)[^\d]{0,20}(\d{3})", text_lower
        ):
            floor = int(m.group(1))
            # Only fire if floor > borrower + 20 (safety margin for context ambiguity)
            if floor > fico + 20:
                return f"Program text states minimum FICO {floor}; borrower score {fico}"

    # ── Occupancy hard blocks ─────────────────────────────────────────────────
    if occupancy == "primary":
        allows_primary = bool(
            re.search(
                r"\bprimary(?:\s*,|\s+and|\s+or|\s+&|\s+home|\s+residence|\s+occup)"
                r"|\bowner.?occupied\b"
                r"|\ball\s+occupanc",
                text_lower,
            )
        )
        if not allows_primary:
            if re.search(r"\bnon.?owner.?occupied\s+only\b", text_lower):
                return "Program text indicates non-owner-occupied only"
            if re.search(r"\binvestment\s+properties?\s+only\b", text_lower):
                return "Program text indicates investment properties only"
            for m in re.finditer(r"\binvestment\s+only\b", text_lower):
                # Prepayment sections often say "Investment Only" for penalty scope — not occupancy.
                window = text_lower[max(0, m.start() - 80) : m.start()]
                if re.search(r"prepay(?:ment)?(?:\s+penalt)?", window):
                    continue
                return "Program text indicates investment properties only"
    if occupancy == "investment":
        if re.search(r"\bowner.?occupied\s+only\b", text_lower):
            return "Program text indicates owner-occupied only"
        if re.search(r"\bno\s+investment\s+propert", text_lower):
            return "Program text indicates no investment properties"

    # ── Loan purpose hard blocks ──────────────────────────────────────────────
    if loan_purpose == "cash_out":
        if re.search(r"\bcash.?out\s+(?:refinance\s+)?not\s+(?:permitted|allowed|eligible)\b", text_lower):
            return "Program text indicates cash-out not permitted"
        if re.search(r"\bpurchase\s+(?:transactions?\s+)?only\b", text_lower):
            return "Program text indicates purchase transactions only"

    # ── Property type hard blocks ─────────────────────────────────────────────
    if property_type == "condo_non_warrantable":
        if re.search(r"\bnon.?warrantable\s+condos?\s+not\s+(?:permitted|allowed|eligible)\b", text_lower):
            return "Program text indicates non-warrantable condos not permitted"
    if property_type in ("two_to_four_family", "five_to_eight_unit"):
        if re.search(r"\b(?:2|two).?4\s+unit\s+not\s+(?:permitted|allowed|eligible)\b", text_lower):
            return "Program text indicates 2-4 unit not permitted"

    return None


_NOTE_PATTERNS: list[tuple[str, str]] = [
    # (regex, note label)
    (r"\breserves?\s*[:\-]\s*[\d]+\s*month", "Reserves requirement applies — review details"),
    (r"\bminimum\s+reserves?\b", "Minimum reserves required — review guidelines"),
    (r"\bdeclining\s+market", "Declining market overlay may apply"),
    (r"\bgift\s+funds?\s+not\s+(?:permitted|allowed)", "Gift funds not permitted for this scenario"),
    (r"\bgift\s+funds?\s+limited", "Gift funds may be limited — review guidelines"),
    (r"\bfirst.?time\s+investor\b", "First-time investor restriction may apply"),
    (r"\bentit(?:y|ies)\s+(?:vesting|borrower)", "Entity/LLC vesting — additional review may be needed"),
    (r"\bnon.?traditional\s+credit\b", "Non-traditional credit documentation may be required"),
    (r"\bappraisal\s+(?:review|waiver|required)\b", "Appraisal review requirement may apply"),
    (r"\baus\s+findings?\b", "AUS findings may be required — review guidelines"),
    (r"\bcross.?collateral", "Cross-collateral restriction noted"),
    (r"\bresidual\s+income", "Residual income requirement may apply"),
    (r"\bio\s+(?:overlay|eligible|not\s+(?:eligible|permitted))", "Interest-only overlay applies"),
    (r"\bcash.?in.?hand\s+limit", "Cash-in-hand cap may apply — verify limit"),
    (r"\bacreage\s+limit", "Acreage limit restriction may apply"),
    (r"\brural\s+propert", "Rural property requirements may apply"),
    (r"\bnon.?occupant\s+co.?borrower", "Non-occupant co-borrower guidelines apply — review requirements"),
    (r"\bprepayment\s+penalt(?:y|ies)\s+(?:not\s+available|prohibited)", "Prepayment penalty may not be available in this state"),
]


def _extract_rag_notes(text_lower: str, rule_notes: list) -> list[str]:
    """
    Collect informational notes from Qdrant chunk text pattern matches.
    rule_notes is kept for signature compatibility but is now always empty
    (Layer 8 writes directly to rag_notes).
    """
    notes: list[str] = []
    seen: set[str] = set()

    def _add(note: str) -> None:
        if note not in seen:
            seen.add(note)
            notes.append(note)

    for pattern, label in _NOTE_PATTERNS:
        if re.search(pattern, text_lower):
            _add(label)

    return notes


def _layer10_qdrant_verify(
    form: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Layer 10: Qdrant cross-verification.

    For each candidate, fetches all chunks from mortgage_matrices where
    program_mysql_id = program_id, then:
      - Runs hard-block heuristics → removes program, adds to rag_ineligible
      - Extracts informational notes → attaches as rag_notes on the candidate
      - If no chunks found → passes through unchanged (graceful degradation)

    Qdrant errors are silently swallowed so eligibility never fails due to
    vector-DB unavailability.
    """
    if not candidates:
        return candidates, []

    try:
        client = get_qdrant("verify")
    except Exception:
        return candidates, []

    verified: list[dict[str, Any]] = []
    rag_ineligible: list[dict[str, Any]] = []

    for cand in candidates:
        pid = cand.get("program_id")
        if pid is None:
            verified.append(cand)
            continue

        try:
            chunks = _qdrant_scroll_chunks(client, int(pid))
        except Exception:
            # Qdrant unreachable or collection missing — pass through
            verified.append(cand)
            continue

        if not chunks:
            # No chunks indexed for this program — pass through unchanged
            verified.append(cand)
            continue

        combined = "\n".join(chunks).lower()

        block_reason = _detect_hard_block(combined, form)
        if block_reason:
            rag_ineligible.append({
                "program_id": int(pid),
                "program_name": cand.get("program_name", ""),
                "reason": block_reason,
            })
            continue

        # Qdrant text-pattern notes (merge with Layer 8 guideline notes already on candidate)
        qdrant_notes = _extract_rag_notes(combined, [])
        if qdrant_notes:
            existing = cand.get("rag_notes") or []
            seen = set(existing)
            cand["rag_notes"] = existing + [n for n in qdrant_notes if n not in seen]

        verified.append(cand)

    return verified, rag_ineligible


def _candidate_pids(candidates: list[dict[str, Any]]) -> set[int]:
    return {int(c["program_id"]) for c in candidates}


def find_eligible_programs(
    raw_form: dict[str, Any],
    quick: bool = False,
    form_id: str | None = None,
    collect_trace: bool = False,
) -> dict[str, Any]:
    """
    Entry point used by the FastAPI backend.

    When *quick* is True, Layer 8 (rule/guideline notes) and Layer 10 (Qdrant
    RAG cross-verification) are skipped.  The result is returned immediately
    from pure SQL — suitable for real-time sidebar counts while the form is
    being filled.

    Returns:
        eligible        — list of candidate dicts matching the API contract
        geo_blocked     — dict[program_id (int) → reason (str)]
        overlay_blocked — dict[program_id (int) → reason (str)]
        rag_ineligible  — list (always empty; kept for API compat)
        total_screened  — int (programs from Layer 1+2 before later filters)
        form            — normalized form dict
    """
    form = _normalise_form(raw_form)
    # In quick mode, treat unset fields as "no constraint" (dynamic sidebar narrowing).
    if quick:
        def _filled(key: str) -> bool:
            return bool(str(raw_form.get(key) or "").strip())

        if not _filled("occupancy"):
            form = {**form, "occupancy": "any"}
        if not _filled("loanPurpose"):
            form = {**form, "loan_purpose": "any"}
        if not _filled("citizenship"):
            form = {**form, "quick_skip_citizenship": True}
        if not _filled("propertyType"):
            form = {**form, "property_type_code": ""}
        if not _filled("loanAmount"):
            form = {**form, "loan_amount": None}
        if not _filled("ltv") and not _filled("cltv"):
            form = {**form, "quick_skip_ltv": True}
        if not _filled("estimatedDti"):
            form = {**form, "dti": None}
        if not _filled("dscr"):
            form = {**form, "dscr": None}
        # isSecondLien not explicitly set → skip the lien-type filter entirely
        raw_lien = (raw_form.get("isSecondLien") or "").strip().lower()
        if raw_lien not in {"yes", "y", "true", "1", "no", "n", "false", "0"}:
            form = {**form, "quick_skip_lien": True}
        # Income path (Investment) — lock DSCR vs income-qualified when user chose
        income_path = form.get("income_path")
        if income_path == "dscr":
            form = {**form, "doc_type": "dscr_rental", "quick_skip_dscr": False}
        elif income_path == "income":
            form = {**form, "income_path_only": True, "quick_skip_dscr": False}
        elif (
            not _filled("documentationType")
            and not _filled("dscr")
            and not _filled("investmentIncomePath")
            and not _filled("qualificationPath")
        ):
            form = {**form, "quick_skip_dscr": True, "doc_type": "any"}
        # Foreign National without US credit — no FICO gate until score provided
        if (
            form.get("citizenship_code") == "foreign_national"
            and form.get("has_us_credit") is False
        ):
            form = {**form, "fico": None}
        elif not _filled("decisionCreditScore"):
            form = {**form, "fico": None}
    # Hard blocks — universal ineligible scenarios
    if (
        form.get("ofac_sanctioned")
        or form.get("hi_lava_blocked")
        or form.get("listing_seasoning_yes")
        or (
            form.get("power_of_attorney") is True
            and form.get("citizenship_code") == "foreign_national"
        )
    ):
        return _empty_eligibility_result(form)

    engine = _get_engine()

    geo_blocked: dict[int, str] = {}
    credit_blocked: dict[int, str] = {}
    basics_overlay_blocked: dict[int, str] = {}
    extended_overlay_blocked: dict[int, str] = {}
    name_by_pid: dict[int, str] = {}
    prog_by_id: dict[int, dict[str, Any]] = {}
    total_screened = 0
    candidates: list[dict[str, Any]] = []
    trace: EligibilityTraceCollector | None = None
    all_active_count = 0
    want_trace = bool((form_id and not quick) or collect_trace)

    with engine.connect() as conn:
        if want_trace:
            all_programs = load_all_active_programs(conn)
            all_active_count = len(all_programs)
            trace = EligibilityTraceCollector(all_programs)

        # ── Layer 1: dim_programs ─────────────────────────────────────────
        program_rows = _layer1_programs(conn, form, quick=quick)
        if trace is not None:
            layer1_ids = {int(r["program_id"]) for r in program_rows}
            trace.mark_layer1_failures(layer1_ids, form)

        if not program_rows:
            if trace is not None and form_id and not quick:
                write_eligibility_trace_log(
                    trace,
                    form_id=form_id,
                    raw_form=raw_form,
                    form=form,
                    matched_count=0,
                    total_active=all_active_count,
                    quick=quick,
                )
            return {
                "eligible": [],
                "near_misses": [],
                "geo_blocked": {},
                "overlay_blocked": {},
                "geo_exclusions": [],
                "overlay_exclusions": [],
                "rag_ineligible": [],
                "total_screened": 0,
                "form": form,
                "program_trace": trace.to_dict() if trace is not None else None,
            }

        prog_by_id: dict[int, dict[str, Any]] = {
            int(r["program_id"]): r for r in program_rows
        }
        program_ids = list(prog_by_id.keys())

        # ── Layer 2: map_ltv_matrix ───────────────────────────────────────
        ltv_rows_by_prog = _layer2_ltv_matrix(
            conn, form, program_ids, quick=quick, prog_by_id=prog_by_id
        )

        is_second_lien = form["is_second_lien"]
        ltv = form["ltv"]
        cltv = form["cltv"]
        loan_purpose = form["loan_purpose"]
        doc_type = form["doc_type"]
        occupancy = form["occupancy"]
        loan_amount = form["loan_amount"]
        rule_dti_by_pid = _rule_snippet_max_dti_by_program(conn, program_ids)

        candidates: list[dict[str, Any]] = []
        for pid, matrix_rows in ltv_rows_by_prog.items():
            prog = prog_by_id.get(pid)
            if prog is None:
                continue

            # LTV gate — quick scan skips until user enters LTV/CLTV
            if quick and form.get("quick_skip_ltv"):
                valid_rows = matrix_rows
            else:
                valid_rows = _rows_passing_ltv(matrix_rows, ltv, cltv, is_second_lien)
            if not valid_rows:
                if trace is not None:
                    trace.mark_layer2_failure(pid, matrix_rows, form)
                continue

            valid_rows = _rows_matching_loan_tier(valid_rows, loan_amount)
            if not valid_rows:
                if trace is not None:
                    trace.mark_layer2_failure(pid, matrix_rows, form)
                continue

            # Max LTV caps across all matrix rows that pass scenario filters (Program Limit column)
            filtered_ltv_caps = _ltv_caps_by_purpose(valid_rows, loan_purpose, is_second_lien)

            # Scenario tier row from map_ltv_matrix for Best Match column
            best = _pick_scenario_matrix_row(
                valid_rows, doc_type, occupancy, loan_amount, form.get("fico")
            )
            if best is None:
                if trace is not None:
                    trace.mark_layer2_failure(pid, matrix_rows, form)
                continue

            scenario_tier_max = _row_max_loan_cap(best, prog)
            if (
                scenario_tier_max is not None
                and loan_amount is not None
                and exceeds_loan(loan_amount, scenario_tier_max)
            ):
                if trace is not None:
                    trace.mark_layer2_failure(pid, matrix_rows, form)
                continue

            # Determine is_itin / is_foreign_nat from citizenship_code — handled in builder
            candidates.append(
                _build_layer2_candidate(
                    prog=prog,
                    pid=pid,
                    best=best,
                    valid_rows=valid_rows,
                    form=form,
                    rule_dti_by_pid=rule_dti_by_pid,
                    filtered_ltv_caps=filtered_ltv_caps,
                )
            )

        if trace is not None:
            layer2_pass_ids = _candidate_pids(candidates)
            for pid in program_ids:
                if pid in layer2_pass_ids:
                    continue
                if not trace.is_pending(pid):
                    continue
                rows = ltv_rows_by_prog.get(pid, [])
                trace.mark_layer2_failure(pid, rows, form)

        layer2_pass_pids = _candidate_pids(candidates)
        total_screened = len(candidates)
        name_by_pid = {int(c["program_id"]): str(c.get("program_name") or "") for c in candidates}

        # ── Layer 2b: rule-snippet overlays (Basics / Step 1) ─────────────
        before = _candidate_pids(candidates)
        candidates, basics_overlay_blocked = _layer_rule_snippet_overlays(
            conn, form, candidates, stage="basics"
        )
        if trace is not None:
            trace.mark_layer_removals(
                before,
                _candidate_pids(candidates),
                "rule_overlays_basics",
                basics_overlay_blocked,
                default_reason="Basics rule-snippet overlay (citizenship, property type, FTHB, …)",
            )

        # ── Layer 3: FTHB ─────────────────────────────────────────────────
        if form.get("is_fthb"):
            before = _candidate_pids(candidates)
            candidates = _layer3_fthb(conn, form, candidates)
            if trace is not None:
                trace.mark_layer_removals(
                    before,
                    _candidate_pids(candidates),
                    LAYER3,
                    {},
                    default_reason="First-time homebuyer not eligible or loan exceeds FTHB cap",
                )

        # ── Layer 4: products ─────────────────────────────────────────────
        candidates = _layer4_products(conn, form, candidates)

        # ── Layer 4b: product preference filter (term, IO, rate type) ────
        before = _candidate_pids(candidates)
        candidates = _layer4b_product_prefs(conn, form, candidates)
        if trace is not None:
            trace.mark_layer_removals(
                before,
                _candidate_pids(candidates),
                LAYER4B,
                {},
                default_reason="No product matching preferences (term / IO / rate type / FTHB)",
            )

        # ── Layer 5: geographic restrictions ──────────────────────────────
        before = _candidate_pids(candidates)
        candidates, geo_blocked, _geo_overlays = _layer5_geo(conn, form, candidates)
        if trace is not None:
            trace.mark_layer_removals(
                before,
                _candidate_pids(candidates),
                LAYER5,
                geo_blocked,
                default_reason="Geographic restriction applies",
            )

        # ── Layer 6: credit history seasoning ─────────────────────────────
        before = _candidate_pids(candidates)
        candidates, credit_blocked = _layer6_credit_seasoning(conn, form, candidates)
        if trace is not None:
            trace.mark_layer_removals(
                before,
                _candidate_pids(candidates),
                LAYER6,
                credit_blocked,
                default_reason="Credit event seasoning not met",
            )

        # ── Layer 7: housing history ───────────────────────────────────────
        candidates = _layer7_housing_history(conn, form, candidates)

        # ── Layer 7b: rule-snippet overlays (Capacity / Credit / Collateral / Conditions) ──
        before = _candidate_pids(candidates)
        candidates, extended_overlay_blocked = _layer_rule_snippet_overlays(
            conn, form, candidates, stage="extended"
        )
        if trace is not None:
            trace.mark_layer_removals(
                before,
                _candidate_pids(candidates),
                "rule_overlays_extended",
                extended_overlay_blocked,
                default_reason="Extended rule-snippet overlay (rural, acreage, NOCB, conditions, …)",
            )

        # ── Layer 8: rule/guideline notes (skipped in quick mode) ────────
        if not quick:
            candidates = _layer8_rule_guidelines(conn, candidates, form)

        # ── Layer 9: prepayment options (+ stepdown filter) ───────────────
        candidates = _layer9_prepayment(conn, form, candidates)
        candidates = _filter_prepay_stepdown(candidates, form)

    # ── Layer 10: Qdrant RAG cross-verification (skipped in quick mode) ──
    qdrant_ineligible: list[dict[str, Any]] = []
    if not quick:
        before_l10 = _candidate_pids(candidates)
        candidates, qdrant_ineligible = _layer10_qdrant_verify(form, candidates)
        if trace is not None:
            qdrant_reasons = {
                int(r["program_id"]): str(r.get("reason") or "Qdrant guideline cross-verify block")
                for r in qdrant_ineligible
            }
            trace.mark_layer_removals(
                before_l10,
                _candidate_pids(candidates),
                LAYER10,
                qdrant_reasons,
                default_reason="Qdrant guideline cross-verify block",
            )

    matched_pids = _candidate_pids(candidates)
    near_misses: list[dict[str, Any]] = []
    # Surface "Just Missed" programs whenever any exist — not only when fewer than
    # 3 programs matched. They render under "Understand Exclusions"; the cap keeps
    # the list short even when there are plenty of eligible programs.
    if not quick and prog_by_id:
        with engine.connect() as nm_conn:
            near_misses = _find_near_miss_programs(
                nm_conn,
                form,
                quick=quick,
                matched_pids=matched_pids,
                layer2_pass_pids=layer2_pass_pids,
                prog_by_id=prog_by_id,
                ltv_rows_by_prog=ltv_rows_by_prog,
                rule_dti_by_pid=rule_dti_by_pid,
                limit=3,
            )

    if trace is not None:
        trace.finalize_matches(candidates, form)
        if form_id and not quick:
            write_eligibility_trace_log(
                trace,
                form_id=form_id,
                raw_form=raw_form,
                form=form,
                matched_count=len(candidates),
                total_active=all_active_count,
                quick=quick,
            )

    # Strip internal keys
    for cand in candidates:
        cand.pop("_prog", None)

    overlay_blocked: dict[int, str] = {**credit_blocked}

    if basics_overlay_blocked:
        overlay_blocked.update(basics_overlay_blocked)
    if extended_overlay_blocked:
        overlay_blocked.update(extended_overlay_blocked)

    geo_exclusions = _exclusions_from_blocked(geo_blocked, name_by_pid, prog_by_id)
    overlay_exclusions = _exclusions_from_blocked(overlay_blocked, name_by_pid, prog_by_id)
    rag_for_api = [
        {
            "program_name": str(r.get("program_name") or ""),
            "program": str(r.get("program_name") or ""),
            "reason": str(r.get("reason") or ""),
        }
        for r in qdrant_ineligible
    ]

    return {
        "eligible": candidates,
        "near_misses": near_misses,
        "geo_blocked": geo_blocked,
        "overlay_blocked": overlay_blocked,
        "geo_exclusions": geo_exclusions,
        "overlay_exclusions": overlay_exclusions,
        "rag_ineligible": rag_for_api,
        "total_screened": total_screened,
        "form": form,
        "program_trace": trace.to_dict() if trace is not None else None,
    }


# ===== service =============================================================

"""Shared eligibility engine wrapper for form mode, chat mode, and intake."""


import logging
import uuid
from typing import Callable

from fastapi import HTTPException


_log = logging.getLogger(__name__)

_find_eligible = find_eligible_programs  # defined above (engine, same module)
ELIGIBILITY_AVAILABLE = True


def _f(v: object) -> float | None:
    try:
        return float(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ltv_pct_display(v: object) -> float | None:
    """Whole-number LTV/CLTV % for API + UI (e.g. 89.99 → 90)."""
    n = _f(v)
    if n is None:
        return None
    return float(round(n))


def _int(v: object) -> int | None:
    try:
        return int(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _best_match_from_row(raw: object) -> BestMatchMetrics | None:
    if not isinstance(raw, dict):
        return None
    return BestMatchMetrics(
        min_fico=_int(raw.get("min_fico")),
        min_loan=_int(raw.get("min_loan")),
        max_loan=_int(raw.get("max_loan")),
        max_ltv_purchase=_ltv_pct_display(raw.get("max_ltv_purchase")),
        max_ltv_rate_term=_ltv_pct_display(raw.get("max_ltv_rate_term")),
        max_ltv_cashout=_ltv_pct_display(raw.get("max_ltv_cashout")),
        max_dti=_f(raw.get("max_dti")),
        min_dscr=_f(raw.get("min_dscr")),
    )


def _derive_program_type(r: dict) -> str:
    if r.get("is_dscr"):
        return "DSCR"
    if r.get("is_itin"):
        return "ITIN"
    if r.get("is_foreign_nat"):
        return "Foreign National"
    return "Non-QM"


def eligible_program_from_row(r: dict) -> EligibleProgram:
    bm_raw = r.get("best_match") if isinstance(r.get("best_match"), dict) else {}
    min_fico = _int(r.get("min_fico")) or _int(bm_raw.get("min_fico"))
    min_loan = _int(r.get("min_loan")) or _int(bm_raw.get("min_loan"))
    max_loan = _int(r.get("max_loan")) or _int(bm_raw.get("max_loan"))
    return EligibleProgram(
        investor=r.get("lender") or r.get("investor", ""),
        investor_name=r.get("lender_name") or r.get("investor_name", ""),
        program_name=r.get("program_name", ""),
        program_name_np=r.get("program_name_np") or None,
        program_type=_derive_program_type(r),
        is_dscr=bool(r.get("is_dscr")),
        is_itin=bool(r.get("is_itin")),
        is_foreign_nat=bool(r.get("is_foreign_nat")),
        min_fico=min_fico,
        min_loan=min_loan,
        max_loan=max_loan,
        max_ltv_purchase=_ltv_pct_display(r.get("max_ltv_purchase")),
        max_ltv_rate_term=_ltv_pct_display(r.get("max_ltv_refi")),
        max_ltv_cashout=_ltv_pct_display(r.get("max_ltv_cashout")),
        max_dti=_f(r.get("max_dti")),
        min_dscr=_f(r.get("min_dscr")),
        best_match=_best_match_from_row(r.get("best_match")),
        doc_type=r.get("doc_type") or None,
        occupancy=r.get("occupancy_code") or None,
        occupancy_types=r.get("occupancy_types") or None,
        property_types=r.get("property_types") or None,
        loan_purposes_allowed=r.get("loan_purposes_allowed") or None,
        doc_types_allowed=r.get("doc_types_allowed") or None,
        program_notes=r.get("program_notes") or None,
        is_active=bool(r.get("is_active", True)),
        products_available=r.get("products_available_label") or None,
        products=r.get("products_all") or r.get("products_available") or None,
        products_matching=r.get("products_matching") or None,
        special_overlay=str(r["special_overlay"]) if r.get("special_overlay") else None,
        rag_notes=r.get("rag_notes") or None,
        program_id=_int(r.get("program_id")),
    )


def run_eligibility_engine(
    payload: dict,
    *,
    quick: bool = False,
    form_id: str | None = None,
) -> dict:
    if not ELIGIBILITY_AVAILABLE or _find_eligible is None:
        raise HTTPException(status_code=503, detail="Eligibility engine unavailable")
    try:
        return _find_eligible(payload, quick=quick, form_id=form_id)
    except HTTPException:
        raise
    except Exception as exc:
        label = "Quick eligibility error" if quick else "Eligibility engine error"
        raise HTTPException(status_code=502, detail=f"{label}: {exc}") from exc


def _exclusions(result: dict, key: str) -> list[ProgramExclusion]:
    raw = result.get(key) or []
    return [
        ProgramExclusion(
            program_name=str(r.get("program_name") or ""),
            reason=str(r.get("reason") or ""),
        )
        for r in raw
        if isinstance(r, dict)
    ]


def build_full_response(
    body: EligibilityRequest,
    *,
    session_id: str | None = None,
    log_session: Callable[[EligibilityRequest, str, dict, list[EligibleProgram]], None] | None = None,
) -> EligibilityResponse:
    if not ELIGIBILITY_AVAILABLE:
        return EligibilityResponse(
            session_id="",
            eligible=[],
            near_misses=[],
            geo_blocked_count=0,
            overlay_blocked_count=0,
            rag_ineligible=[],
            total_screened=0,
            available=False,
        )

    sid = session_id or str(uuid.uuid4())
    result = run_eligibility_engine(body.model_dump(), quick=False, form_id=sid)

    try:
        eligible_programs = [
            eligible_program_from_row(r)
            for r in (result.get("eligible") or [])
            if isinstance(r, dict)
        ]
        near_miss_programs = [
            NearMissProgram(
                **eligible_program_from_row(r).model_dump(),
                near_miss_hint=str(r.get("near_miss_hint") or ""),
                near_miss_type=str(r.get("near_miss_type") or "") or None,
                near_miss_suggestion=str(r.get("near_miss_suggestion") or "") or None,
                suggested_ltv=_f(r.get("suggested_ltv")),
                suggested_loan=_int(r.get("suggested_loan")),
            )
            for r in (result.get("near_misses") or [])
            if isinstance(r, dict) and r.get("near_miss_hint")
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Response mapping error: {exc}") from exc

    if log_session is not None:
        log_session(body, sid, result, eligible_programs)

    return EligibilityResponse(
        session_id=sid,
        eligible=eligible_programs,
        near_misses=near_miss_programs,
        geo_blocked_count=len(result.get("geo_blocked") or {}),
        overlay_blocked_count=len(result.get("overlay_blocked") or {}),
        geo_exclusions=_exclusions(result, "geo_exclusions"),
        overlay_exclusions=_exclusions(result, "overlay_exclusions"),
        rag_ineligible=result.get("rag_ineligible") or [],
        total_screened=result.get("total_screened") or 0,
        available=True,
    )


def build_quick_response(body: QuickEligibilityRequest) -> QuickEligibilityResponse:
    if not ELIGIBILITY_AVAILABLE:
        return QuickEligibilityResponse(count=0, program_names=[], available=False)

    payload = {k: v for k, v in body.model_dump().items() if k != "include_programs" and v not in (None, "", 0, 0.0)}
    _log.info("[quick] REQUEST  %s", payload)

    result = run_eligibility_engine(body.model_dump(), quick=True)
    eligible_rows = [r for r in (result.get("eligible") or []) if isinstance(r, dict)]
    names = [str(r.get("program_name") or "") for r in eligible_rows]
    _log.info("[quick] RESPONSE count=%d programs=%s", len(names), names)

    programs: list[EligibleProgram] | None = None
    if body.include_programs:
        programs = [eligible_program_from_row(r) for r in eligible_rows]

    return QuickEligibilityResponse(
        count=len(names),
        program_names=names,
        available=True,
        eligible=programs,
        total_screened=result.get("total_screened") or 0,
        geo_blocked_count=len(result.get("geo_blocked") or {}),
        overlay_blocked_count=len(result.get("overlay_blocked") or {}),
    )


# ===== routes ==============================================================

"""POST /api/eligibility/full and POST /api/eligibility/quick — shared by form and chat."""


from typing import Callable

from fastapi import APIRouter


router = APIRouter(prefix="/api/eligibility", tags=["eligibility"])

_log_session_cb: Callable[[EligibilityRequest, str, dict, list[EligibleProgram]], None] | None = None


def configure(
    log_session: Callable[[EligibilityRequest, str, dict, list[EligibleProgram]], None] | None = None,
) -> None:
    global _log_session_cb
    _log_session_cb = log_session


@router.post("/full", response_model=EligibilityResponse)
def eligibility_full(body: EligibilityRequest) -> EligibilityResponse:
    return build_full_response(body, log_session=_log_session_cb)


@router.post("", response_model=EligibilityResponse, include_in_schema=False)
def eligibility_legacy(body: EligibilityRequest) -> EligibilityResponse:
    """Backward-compatible alias for /api/eligibility/full."""
    return build_full_response(body, log_session=_log_session_cb)


@router.post("/quick", response_model=QuickEligibilityResponse)
def eligibility_quick(body: QuickEligibilityRequest) -> QuickEligibilityResponse:
    """SQL-only eligibility scan — skips Qdrant/RAG for real-time counts and previews."""
    return build_quick_response(body)


# ===== Geo restriction evaluation (folded from geo/evaluator.py) =====

"""
Live intake geo helpers: follow-up completeness + a deterministic state-licensing
gate. The per-program geo restriction filtering is the pure-LLM ``_layer5_geo``
above — the single source of truth for both form and chat. No parallel rules here.
"""

from typing import Any

from backend.metrics import (
    FORM_KEY_TO_GEO_DATA_KEY,
    GEO_STATE_FIELDS,
    get_state_fields,
    get_state_fields_for_county,
    state_needs_geo_followup,
)


def wizard_to_geo_data(raw: dict[str, Any]) -> dict[str, Any]:
    """Map wizard / API camelCase fields → geo evaluator keys."""
    return {
        "state": (raw.get("state") or "").strip().upper(),
        "occupancy": (raw.get("occupancy") or "").strip(),
        "rentalType": (raw.get("rentalType") or "").strip(),
        "investmentIncomePath": (raw.get("investmentIncomePath") or "").strip(),
        "county": (raw.get("stateCounty") or raw.get("county") or "").strip(),
        "city": (raw.get("stateCity") or raw.get("city") or "").strip(),
        "borough": (raw.get("stateBorough") or raw.get("borough") or "").strip(),
        "zipCode": (raw.get("stateZipCode") or raw.get("zipCode") or "").strip(),
        "isInBaltimoreCity": (raw.get("isInBaltimoreCity") or "").strip(),
        "isInIndianapolis": (raw.get("isInIndianapolis") or "").strip(),
        "isInPhiladelphia": (raw.get("isInPhiladelphia") or "").strip(),
        "isInMemphis": (raw.get("isInMemphis") or "").strip(),
        "isInLubbock": (raw.get("isInLubbock") or "").strip(),
    }


def _field_value(data: dict[str, Any], field: str) -> str:
    return str(data.get(field) or "").strip()


def is_geo_location_complete(data: dict[str, Any]) -> bool:
    state = (data.get("state") or "").strip().upper()
    if not state:
        return False
    county = (data.get("county") or "").strip()
    if not county:
        return False
    # County-gated: only the follow-ups for THIS county are required.
    fields = get_state_fields_for_county(state, county)
    for field in fields:
        if not field.get("required"):
            continue
        form_key = field.get("form_key") or ""
        geo_key = FORM_KEY_TO_GEO_DATA_KEY.get(form_key, form_key)
        val = _field_value(data, geo_key if geo_key in data else form_key)
        if field.get("widget") == "zip":
            digits = "".join(c for c in val if c.isdigit())
            if len(digits) != 5:
                return False
        elif not val:
            return False
    return True


def evaluate_geo(data: dict[str, Any]) -> dict[str, Any]:
    """Live intake geo check shown while a scenario is being filled.

    Deterministic only. The detailed, per-program geo filtering is the LLM layer
    in ``_layer5_geo`` (the single source of truth for both form and chat). Here
    we only:
      1. enforce the NewPoint state-licensing allowlist (Step 1) as a hard block, and
      2. report whether the state carries any location restrictions, so the UI
         never runs a second, parallel rules engine.

    Returns: complete, warnings[{message,severity}], hard_block|None, has_restrictions.
    """
    state = (data.get("state") or "").strip().upper()
    complete = is_geo_location_complete(data)

    if not state:
        return {"complete": False, "warnings": [], "hard_block": None, "has_restrictions": False}

    is_dscr = (data.get("investmentIncomePath") or "").strip().lower() == "dscr"
    eligible: set[str] = set()
    has_rows = False
    try:
        eng = _get_db_engine()
        with eng.connect() as conn:
            eligible = _eligible_state_set(conn, is_dscr=is_dscr)
            if (not eligible) or state in eligible:
                has_rows = bool(
                    conn.execute(
                        text(
                            "SELECT 1 FROM map_geographic_restrictions "
                            "WHERE state = :state AND restriction_type <> 'eligible_state' LIMIT 1"
                        ),
                        {"state": state},
                    ).first()
                )
    except Exception:
        eligible, has_rows = set(), False

    # Step 1 — NewPoint licensing footprint (same allowlist the engine uses).
    if eligible and state not in eligible:
        msg = f"NewPoint is not licensed to lend in {state}."
        return {
            "complete": complete,
            "warnings": [{"message": msg, "severity": "error"}],
            "hard_block": msg,
            "has_restrictions": True,
        }

    if not complete and state_needs_geo_followup(state):
        return {"complete": False, "warnings": [], "hard_block": None, "has_restrictions": False}

    warnings: list[dict[str, str]] = []
    if has_rows:
        warnings.append(
            {
                "message": (
                    f"{state} has location-specific lender restrictions; matching programs "
                    "are checked against your county/city when you run eligibility."
                ),
                "severity": "info",
            }
        )
    elif complete and not state_needs_geo_followup(state):
        warnings.append(
            {"message": f"No location-specific restrictions for {state}.", "severity": "success"}
        )

    return {
        "complete": complete,
        "warnings": warnings,
        "hard_block": None,
        "has_restrictions": bool(has_rows),
    }


def next_missing_geo_field(state: str, values: dict[str, str]) -> dict[str, Any] | None:
    """First unfilled required geo field for chat intake (ordered).
    County-gated: only the follow-ups for the chosen county apply."""
    st = (state or "").strip().upper()
    county = (values.get("stateCounty") or values.get("county") or "").strip()
    for field in get_state_fields_for_county(st, county):
        if not field.get("required"):
            continue
        form_key = field["form_key"]
        geo_key = FORM_KEY_TO_GEO_DATA_KEY.get(form_key, form_key)
        val = (values.get(form_key) or values.get(geo_key) or "").strip()
        if field.get("widget") == "zip":
            digits = "".join(c for c in val if c.isdigit())
            if len(digits) != 5:
                return field
        elif not val:
            return field
    return None


# ===== Geo routes /api/geo/* (folded from geo/routes.py) =====

"""Geographic follow-up config and evaluation API — no frontend hardcoding."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.metrics import get_geo_config

geo_router = APIRouter(prefix="/api/geo", tags=["geo"])


class GeoEvaluateRequest(BaseModel):
    state: str = ""
    occupancy: str = ""
    rentalType: str = ""
    investmentIncomePath: str = ""
    stateCounty: str = ""
    stateCity: str = ""
    stateBorough: str = ""
    stateZipCode: str = ""
    isInBaltimoreCity: str = ""
    isInIndianapolis: str = ""
    isInPhiladelphia: str = ""
    isInMemphis: str = ""
    isInLubbock: str = ""


@geo_router.get("/config")
async def geo_config(state: str | None = Query(default=None)) -> dict[str, Any]:
    """Follow-up field definitions and options per state."""
    if state:
        st = state.strip().upper()
        cfg = get_geo_config(st)
        if not cfg.get("fields"):
            raise HTTPException(status_code=404, detail=f"No geo follow-up for state {st}")
        return cfg
    return get_geo_config()


@geo_router.post("/evaluate")
async def geo_evaluate(req: GeoEvaluateRequest) -> dict[str, Any]:
    """Warnings, hard blocks, and completion for a location scenario."""
    data = wizard_to_geo_data(req.model_dump())
    result = evaluate_geo(data)
    return {
        **result,
        "state": data.get("state") or "",
    }


@geo_router.post("/complete")
async def geo_complete(req: GeoEvaluateRequest) -> dict[str, bool]:
    """Lightweight completion check for form step gates."""
    data = wizard_to_geo_data(req.model_dump())
    return {"complete": is_geo_location_complete(data)}


@geo_router.get("/counties")
async def geo_counties(
    state: str = Query(..., min_length=2, max_length=2),
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Search dim_county rows for a state (typeahead county picker)."""
    from sqlalchemy import text

    from backend.connections.db import get_engine

    st = state.strip().upper()
    needle = q.strip()
    pattern = f"%{needle}%" if needle else "%"
    sql = text(
        """
        SELECT id, county_name, state_code
        FROM dim_county
        WHERE state_code = :state AND county_name LIKE :pattern
        ORDER BY county_name
        LIMIT :lim
        """
    )
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sql, {"state": st, "pattern": pattern, "lim": limit}).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"County lookup unavailable: {exc}") from exc
    counties = [
        {"id": int(r["id"]), "county_name": r["county_name"], "state_code": r["state_code"]}
        for r in rows
    ]
    return {"state": st, "query": needle, "counties": counties}
