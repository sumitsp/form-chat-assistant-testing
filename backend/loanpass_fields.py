"""Map wizard / eligibility form dict → LoanPASS creditApplicationFields."""

from __future__ import annotations

import re
from typing import Any

from backend import config

_OCCUPANCY = {
    "Primary Residence": "primary-residence",
    "Second Home": "second-home",
    "Investment Property": "investment-property",
}
_PURPOSE = {
    "Purchase": "purchase",
    "Refinance": "refinance",
    "Cash-Out Refinance": "cash-out",
}
_PROPERTY = {
    "single_family": "single-family",
    "pud": "pud",
    "townhouse": "townhouse",
    "condo_warrantable": "condo-warrantable",
    "condo_non_warrantable": "condo-non-warrantable",
    "condotel": "condotel",
    "two_to_four_family": "two-to-four-family",
    "five_to_eight_unit": "multi-family",
    "mixed_use": "mixed-use",
    "manufactured_home": "manufactured-home",
    "cooperative": "cooperative",
}


def _parse_money(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    n = float(re.sub(r"[,$\s]", "", s))
    if n <= 0:
        return None
    return f"{n:.2f}"


def _unit_count(property_type: str) -> str:
    if property_type == "two_to_four_family":
        return "3"
    if property_type == "five_to_eight_unit":
        return "6"
    return "1"


def _parse_percent(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("%", "")
    if not s:
        return None
    try:
        n = float(re.sub(r"[^\d.]", "", s))
    except ValueError:
        return None
    if n <= 0:
        return None
    return f"{n:g}"


# Wizard label or matrix code → LoanPASS documentation-type variantId
_DOC_TYPE_VARIANT: dict[str, str] = {
    "full documentation": "full-documentation",
    "full_doc": "full-documentation",
    "1099": "1099",
    "asset utilization": "asset-utilization",
    "asset_util": "asset-utilization",
    "asset qualifier": "asset-utilization",
    "asset_qualifier": "asset-utilization",
    "profit and loss": "profit-and-loss",
    "pl_only": "profit-and-loss",
    "p&l with 2-month bank statements": "profit-and-loss",
    "pl_2mo_bs": "profit-and-loss",
    "wvoe only": "wvoe",
    "wvoe": "wvoe",
    "rental income": "dscr-rental",
    "dscr_rental": "dscr-rental",
    "dscr": "dscr-rental",
    "itin": "itin",
    "alternative documentation": "non-traditional",
    "non_traditional": "non-traditional",
}

_FTHB_VARIANT = {
    "yes": "yes",
    "y": "yes",
    "true": "yes",
    "1": "yes",
    "no": "no",
    "n": "no",
    "false": "no",
    "0": "no",
}


def _documentation_variant(raw: str) -> str | None:
    key = raw.strip().lower()
    if not key or key == "dscr":
        return "dscr-rental"
    if key in _DOC_TYPE_VARIANT:
        return _DOC_TYPE_VARIANT[key]
    norm = key.replace("-", "_").replace(" ", "_")
    if norm in _DOC_TYPE_VARIANT:
        return _DOC_TYPE_VARIANT[norm]
    if "bank statement" in key:
        return None
    return None


# Doc types whose 12-/24-month timeframe the borrower picks (asked in form chat).
# Asset Utilization, Asset Qualifier, and WVOE default to 24 month.
def _doc_uses_selected_timeframe(doc_raw: str) -> bool:
    low = doc_raw.strip().lower()
    if "full documentation" in low or low == "full_doc":
        return True
    if "bank statement" in low or low.startswith("bank_stmt"):
        return True
    if "p&l" in low or low.startswith("pl_"):
        return True
    if low == "1099":
        return True
    return False


def _selected_timeframe_variant(form: dict[str, Any]) -> str | None:
    """User-selected documentationTimeframe ('12' | '24') → variantId."""
    sel = (form.get("documentationTimeframe") or "").strip().lower()
    if sel in ("12", "24"):
        return f"{sel}-month"
    if "12" in sel:
        return "12-month"
    if "24" in sel:
        return "24-month"
    return None


def map_form_to_loanpass_fields(form: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []

    def push(field_id: str, value: dict[str, Any]) -> None:
        fields.append({"fieldId": field_id, "value": value})

    occ = _OCCUPANCY.get((form.get("occupancy") or "").strip())
    if occ:
        push(
            "field@occupancy-type",
            {"type": "enum", "enumTypeId": "occupancy-type", "variantId": occ},
        )

    purpose_raw = (
        (form.get("primaryLoanPurpose") or form.get("loanPurpose") or "").strip()
    )
    purpose = _PURPOSE.get(purpose_raw)
    if purpose:
        push(
            "field@loan-purpose",
            {"type": "enum", "enumTypeId": "loan-purpose", "variantId": purpose},
        )

    prop = _PROPERTY.get((form.get("propertyType") or "").strip())
    if prop:
        push(
            "field@property-type",
            {"type": "enum", "enumTypeId": "property-type", "variantId": prop},
        )

    push(
        "field@number-of-units",
        {"type": "number", "value": _unit_count((form.get("propertyType") or "").strip())},
    )

    state = (form.get("state") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", state):
        push("field@state", {"type": "string", "format": "us-state-code", "value": state})

    fico = re.sub(r"\D", "", str(form.get("decisionCreditScore") or ""))
    if fico:
        push("field@decision-credit-score", {"type": "number", "value": fico})

    loan_amt = _parse_money(form.get("loanAmount"))
    if loan_amt:
        push("field@base-loan-amount", {"type": "number", "value": loan_amt})

    value = _parse_money(form.get("valueSalesPrice"))
    if value:
        push("field@purchase-price", {"type": "number", "value": value})
        push("field@appraised-value", {"type": "number", "value": value})

    reserves = re.sub(r"\D", "", str(form.get("reservesAvailable") or form.get("reservesMonths") or ""))
    if reserves:
        push("field@months-of-reserves", {"type": "number", "value": reserves})

    # Focus lock sent to LoanPASS (see LOANPASS_FOCUS_LOCK_DAYS in config).
    push(
        "rate-lock-period",
        {
            "type": "duration",
            "unit": "days",
            "count": str(config.LOANPASS_FOCUS_LOCK_DAYS),
        },
    )

    loan_term = (form.get("loanTerm") or "").strip()
    if loan_term and "no preference" not in loan_term.lower():
        m = re.search(r"(\d+)", loan_term)
        if m:
            months = int(m.group(1)) * 12
            push(
                "field@desired-loan-term",
                {"type": "duration", "unit": "months", "count": str(months)},
            )

    dti = _parse_percent(form.get("estimatedDti"))
    if dti:
        push("field@estimated-dti", {"type": "number", "value": dti})

    dscr = _parse_percent(form.get("dscr") or form.get("loanLevelDscr"))
    if dscr:
        push("field@estimated-dscr", {"type": "number", "value": dscr})

    doc_raw = (form.get("documentationType") or "").strip()
    is_dscr_path = (form.get("investmentIncomePath") or "").strip().lower() == "dscr"
    if is_dscr_path:
        doc_raw = doc_raw or "DSCR"
    doc_variant = _documentation_variant(doc_raw) if doc_raw else None
    if doc_variant:
        push(
            "field@documentation-type",
            {
                "type": "enum",
                "enumTypeId": "documentation-type",
                "variantId": doc_variant,
            },
        )

    # Documentation timeframe (income path only; DSCR / rental has none).
    # Full Doc, bank statements, P&L, and 1099 send the borrower's selection;
    # asset / WVOE types are hardcoded to 24 month.
    if doc_raw and not is_dscr_path and doc_variant != "dscr-rental":
        if _doc_uses_selected_timeframe(doc_raw):
            timeframe = _selected_timeframe_variant(form) or "24-month"
        else:
            timeframe = "24-month"
        push(
            "field@documentation-type-timeframe",
            {
                "type": "enum",
                "enumTypeId": "documentation-type-timeframe",
                "variantId": timeframe,
            },
        )

    fthb_raw = (form.get("firstTimeHomebuyer") or "").strip().lower()
    fthb_variant = _FTHB_VARIANT.get(fthb_raw)
    if fthb_variant:
        push(
            "field@first-time-homebuyer",
            {
                "type": "enum",
                "enumTypeId": "first-time-homebuyer",
                "variantId": fthb_variant,
            },
        )

    return fields
