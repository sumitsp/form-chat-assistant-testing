"""
LoanPASS Public API client — https://docs.loanpass.io/public-api/

Uses REST (not iframe): login → execute-summary / execute-product → price scenarios.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from backend import config
from backend.loanpass_config import get_loanpass_embed_config
from backend.loanpass_fields import map_form_to_loanpass_fields

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing service health — read by /api/loanpass/health, which the Docker
# pricing-watchdog polls. The HTTP server staying up is NOT enough: when
# LoanPASS token churn / rate-limits wedge pricing, every request fails while
# /health would otherwise still answer "ok" and the watchdog would never
# restart. So we record real LoanPASS call outcomes here and surface a
# "degraded" status once pricing has been consistently failing.
# ---------------------------------------------------------------------------
_health_lock = threading.Lock()
_pricing_health: dict[str, Any] = {
    "last_success_ts": None,
    "last_failure_ts": None,
    "consecutive_failures": 0,
    "total_success": 0,
    "total_failure": 0,
    "last_error": None,
}

# Consecutive LoanPASS failures before /health reports "degraded" (so the
# watchdog restarts pricing, clearing the stale cached session token).
PRICING_DEGRADED_AFTER = int(os.environ.get("PRICING_DEGRADED_AFTER", "3"))
# Only treat failures as "current" within this window — if pricing has been
# idle (no recent traffic) we don't want stale failures to force a restart.
PRICING_FAILURE_WINDOW_S = float(os.environ.get("PRICING_FAILURE_WINDOW_S", "300"))


def record_pricing_success() -> None:
    """A LoanPASS pricing call succeeded — clears the failure streak."""
    with _health_lock:
        _pricing_health["last_success_ts"] = time.time()
        _pricing_health["consecutive_failures"] = 0
        _pricing_health["total_success"] += 1
        _pricing_health["last_error"] = None


def record_pricing_failure(detail: str) -> None:
    """A LoanPASS pricing call failed (auth / network / 5xx)."""
    with _health_lock:
        _pricing_health["last_failure_ts"] = time.time()
        _pricing_health["consecutive_failures"] += 1
        _pricing_health["total_failure"] += 1
        _pricing_health["last_error"] = (detail or "")[:300]


def pricing_health_snapshot() -> dict[str, Any]:
    """Snapshot + derived status ('ok' | 'degraded') for the health endpoint."""
    with _health_lock:
        snap = dict(_pricing_health)
    now = time.time()
    recent_failure = (
        snap["last_failure_ts"] is not None
        and (now - snap["last_failure_ts"]) <= PRICING_FAILURE_WINDOW_S
    )
    degraded = snap["consecutive_failures"] >= PRICING_DEGRADED_AFTER and recent_failure
    snap["status"] = "degraded" if degraded else "ok"
    return snap

_FOCUS_LOCK_DAYS = config.LOANPASS_FOCUS_LOCK_DAYS

_PUBLISHED_VERSION = {"type": "current"}
_OUTPUT_FILTER = {"type": "all"}


class LoanpassError(Exception):
    pass


class LoanpassNotConfiguredError(LoanpassError):
    pass


class LoanpassPricingUnavailableError(LoanpassError):
    """No LoanPASS product mapping, or dual-mapping gate could not pick a variant."""


def _pricing_unavailable_message(display_name: str) -> str:
    label = display_name.strip() or "this program"
    return (
        f"No pricing found for **{label}**. "
        "We will notify you once we get it."
    )


def get_dim_program_loanpass(program_id: int) -> dict[str, Any] | None:
    """Load program_name_loanpass (+ labels) from dim_programs."""
    from sqlalchemy import text

    from backend.connections.db import get_engine

    with get_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT program_id, program_code, program_name, program_name_np,
                           program_name_loanpass
                    FROM dim_programs
                    WHERE program_id = :pid
                    LIMIT 1
                    """
                ),
                {"pid": int(program_id)},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def get_dim_product_type(product_type_id: int) -> dict[str, Any] | None:
    """Load a row from dim_product_types."""
    from sqlalchemy import text

    from backend.connections.db import get_engine

    with get_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT id, name, io_period_years, amort_period_years, total_term_years
                    FROM dim_product_types
                    WHERE id = :pid
                    LIMIT 1
                    """
                ),
                {"pid": int(product_type_id)},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def get_dim_product_type_by_name(name: str) -> dict[str, Any] | None:
    target = (name or "").strip()
    if not target:
        return None
    from sqlalchemy import text

    from backend.connections.db import get_engine

    with get_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT id, name, io_period_years, amort_period_years, total_term_years
                    FROM dim_product_types
                    WHERE name = :name
                    LIMIT 1
                    """
                ),
                {"name": target},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def _parse_loanpass_json_list(raw: Any) -> list[str]:
    """map_program_products loanpass_* columns — plain string or JSON string array."""
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            return []
    return [text]


def _form_is_standard_doc(form: dict[str, Any]) -> bool:
    doc = (form.get("documentationType") or "").strip().lower()
    if doc in ("full documentation", "full_doc", "full doc"):
        return True
    return "full documentation" in doc or doc.startswith("full_doc")


def _form_is_dscr_path(form: dict[str, Any]) -> bool:
    if (form.get("investmentIncomePath") or "").strip().lower() == "dscr":
        return True
    doc = (form.get("documentationType") or "").strip().lower()
    return doc in ("dscr", "dscr_rental", "rental income")


def _pick_dual_loanpass_product_id(
    names: list[str],
    ids: list[str],
    *,
    form: dict[str, Any],
    product_label: str,
) -> str:
    """Choose one of two parallel LoanPASS products (standard/alt doc or FN vs FN DSCR)."""
    if len(ids) != 2 or len(names) != 2:
        raise LoanpassError("Invalid dual LoanPASS product mapping.")

    lowers = [n.lower() for n in names]
    has_standard = any("standard" in n for n in lowers)
    has_alt = any("alt" in n for n in lowers)
    if has_standard and has_alt:
        want_standard = _form_is_standard_doc(form)
        for i, low in enumerate(lowers):
            if want_standard and "standard" in low:
                return ids[i]
            if not want_standard and "alt" in low:
                return ids[i]
        raise LoanpassPricingUnavailableError(
            f'No pricing found for "{product_label}" with the selected documentation type.'
        )

    dscr_flags = ["dscr" in n for n in lowers]
    if sum(dscr_flags) == 1:
        want_dscr = _form_is_dscr_path(form)
        for i, is_dscr in enumerate(dscr_flags):
            if want_dscr == is_dscr:
                return ids[i]
        raise LoanpassPricingUnavailableError(
            f'No pricing found for "{product_label}" with the selected income path.'
        )

    raise LoanpassPricingUnavailableError(
        f'No pricing found for "{product_label}" — could not resolve the mapped product variant.'
    )


def get_map_program_product_mapping(
    program_id: int, product_type_id: int
) -> dict[str, Any] | None:
    from sqlalchemy import text

    from backend.connections.db import get_engine

    with get_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT loanpass_product_id, loanpass_product_name
                    FROM map_program_products
                    WHERE program_id = :pid AND product_type_id = :ptid
                    LIMIT 1
                    """
                ),
                {"pid": int(program_id), "ptid": int(product_type_id)},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def resolve_map_loanpass_product_id(
    program_id: int,
    product_type_id: int,
    form: dict[str, Any],
    *,
    product_label: str = "",
) -> str:
    """
    LoanPASS productId from map_program_products.

    Single mapped id → return as-is.
    Two mapped ids → gate on standard vs alt doc, or FN vs FN DSCR.
    No mapping → LoanpassPricingUnavailableError.
    """
    row = get_map_program_product_mapping(program_id, product_type_id)
    label = product_label.strip() or f"product type {product_type_id}"
    if not row:
        raise LoanpassPricingUnavailableError(_pricing_unavailable_message(label))

    ids = _parse_loanpass_json_list(row.get("loanpass_product_id"))
    if not ids:
        raise LoanpassPricingUnavailableError(_pricing_unavailable_message(label))

    if len(ids) == 1:
        return ids[0]

    names = _parse_loanpass_json_list(row.get("loanpass_product_name"))
    if len(names) != len(ids):
        names = [""] * len(ids)
    return _pick_dual_loanpass_product_id(names, ids, form=form, product_label=label)


def list_db_program_product_types(program_id: int) -> list[dict[str, Any]]:
    """Product types for a program from map_program_products + dim_product_types."""
    from sqlalchemy import text

    from backend.connections.db import get_engine

    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT pt.id, pt.name, pt.io_period_years,
                           pt.amort_period_years, pt.total_term_years,
                           pp.loanpass_product_id
                    FROM map_program_products pp
                    INNER JOIN dim_product_types pt ON pt.id = pp.product_type_id
                    WHERE pp.program_id = :pid
                    ORDER BY pt.name
                    """
                ),
                {"pid": int(program_id)},
            )
            .mappings()
            .all()
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        raw_ids = row.get("loanpass_product_id")
        ids = _parse_loanpass_json_list(raw_ids)
        if not ids:
            continue
        row["loanpass_product_id"] = ids[0] if len(ids) == 1 else None
        row["loanpass_product_variants"] = len(ids)
        out.append(row)
    return out


def _parse_loanpass_program_names(raw: str) -> list[str]:
    """dim_programs.program_name_loanpass — plain string or JSON name array."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [text]


def resolve_loanpass_program_name(
    *,
    program_id: int | None,
    program_name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Resolve the LoanPASS match string from dim_programs.program_name_loanpass.
    Raises LoanpassPricingUnavailableError when the column is null/blank.
    """
    if program_id is not None:
        row = get_dim_program_loanpass(int(program_id))
        if not row:
            raise LoanpassError(f"Program id {program_id} was not found in dim_programs.")
        lp_names = _parse_loanpass_program_names(row.get("program_name_loanpass") or "")
        if not lp_names:
            display = (row.get("program_name_np") or row.get("program_name") or "").strip()
            label = display or f"program {program_id}"
            raise LoanpassPricingUnavailableError(_pricing_unavailable_message(label))
        row = {**row, "_loanpass_names": lp_names}
        return lp_names[0], row

    fallback = (program_name or "").strip()
    if not fallback:
        raise LoanpassError("program_id or program_name is required for pricing.")
    return fallback, {}


def _normalize_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1}


def _score_product_match(
    product: dict[str, Any],
    *,
    program_name: str,
    investor_name: str | None,
    program_names: list[str] | None = None,
) -> float:
    names = program_names or [program_name]
    return max(
        _score_product_match_one(product, program_name=n, investor_name=investor_name)
        for n in names
    )


def _score_product_match_one(
    product: dict[str, Any],
    *,
    program_name: str,
    investor_name: str | None,
) -> float:
    name = (product.get("productName") or "").lower()
    investor = (product.get("investorName") or "").lower()
    code = (product.get("productCode") or "").lower()
    target = program_name.lower()
    inv = (investor_name or "").lower()

    score = 0.0
    if target in name:
        score += 10.0
    if inv and inv in investor:
        score += 4.0
    if inv and inv in name:
        score += 2.0

    target_tokens = _normalize_tokens(program_name)
    name_tokens = _normalize_tokens(name)
    code_tokens = _normalize_tokens(code)
    overlap = len(target_tokens & (name_tokens | code_tokens))
    score += overlap * 1.5

    status = (product.get("status") or "").lower()
    if status in ("available", "approved"):
        score += 3.0
    elif status == "reviewrequired":
        score += 1.0
    else:
        score -= 2.0

    return score


def _product_name_is_io(product_name: str) -> bool:
    lower = product_name.lower()
    return " io" in lower or "interest-only" in lower or lower.endswith(" io")


def _score_product_for_dim_type(
    product: dict[str, Any],
    *,
    program_name: str,
    investor_name: str | None,
    dim_product: dict[str, Any],
    program_names: list[str] | None = None,
) -> float:
    pname = (product.get("productName") or "").lower()
    score = _score_product_match(
        product,
        program_name=program_name,
        investor_name=investor_name,
        program_names=program_names,
    )

    io_years = int(dim_product.get("io_period_years") or 0)
    is_io = _product_name_is_io(pname)
    if io_years > 0 and is_io:
        score += 10.0
    elif io_years == 0 and not is_io:
        score += 8.0
    elif io_years > 0 and not is_io:
        score -= 6.0
    elif io_years == 0 and is_io:
        score -= 8.0

    total_term = dim_product.get("total_term_years")
    if total_term:
        term = int(total_term)
        if f"{term} year" in pname or f"{term} yr" in pname or f"{term}-year" in pname:
            score += 5.0
        elif term == 30 and "40 year" in pname:
            score -= 4.0

    label = (dim_product.get("name") or "").strip()
    label_tokens = _normalize_tokens(label)
    name_tokens = _normalize_tokens(pname)
    overlap = len(label_tokens & name_tokens)
    score += overlap * 2.5
    if label.lower() in pname:
        score += 12.0

    if "sofr" in label.lower() and "arm" in label.lower():
        if "arm" in pname:
            score += 4.0
        if "full am" in pname:
            score += 6.0

    return score


def _pick_product_for_dim_type(
    products: list[dict[str, Any]],
    *,
    program_name: str,
    investor_name: str | None,
    dim_product: dict[str, Any],
    program_names: list[str] | None = None,
) -> dict[str, Any] | None:
    if not products:
        return None
    ranked = sorted(
        products,
        key=lambda p: _score_product_for_dim_type(
            p,
            program_name=program_name,
            investor_name=investor_name,
            dim_product=dim_product,
            program_names=program_names,
        ),
        reverse=True,
    )
    best = ranked[0]
    if (
        _score_product_for_dim_type(
            best,
            program_name=program_name,
            investor_name=investor_name,
            dim_product=dim_product,
            program_names=program_names,
        )
        < 2
    ):
        return None
    return best


def _pick_product(
    products: list[dict[str, Any]],
    *,
    program_name: str,
    investor_name: str | None,
    product_label: str | None = None,
    program_names: list[str] | None = None,
) -> dict[str, Any] | None:
    if not products:
        return None
    ranked = sorted(
        products,
        key=lambda p: _score_product_match(
            p,
            program_name=program_name,
            investor_name=investor_name,
            program_names=program_names,
        ),
        reverse=True,
    )
    label = (product_label or "").strip().lower()
    if label:
        label_tokens = _normalize_tokens(label)

        def label_score(p: dict[str, Any]) -> float:
            name = (p.get("productName") or "").lower()
            code = (p.get("productCode") or "").lower()
            base = _score_product_match(
                p,
                program_name=program_name,
                investor_name=investor_name,
                program_names=program_names,
            )
            name_tokens = _normalize_tokens(name)
            code_tokens = _normalize_tokens(code)
            overlap = len(label_tokens & (name_tokens | code_tokens))
            if label in name or label in code:
                return base + 20.0
            return base + overlap * 2.5

        ranked = sorted(ranked, key=label_score, reverse=True)
        best = ranked[0]
        if label_score(best) < 2:
            return None
        return best

    best = ranked[0]
    if (
        _score_product_match(
            best,
            program_name=program_name,
            investor_name=investor_name,
            program_names=program_names,
        )
        < 2
    ):
        return None
    return best


def _rank_program_products(
    products: list[dict[str, Any]],
    *,
    program_name: str,
    investor_name: str | None,
    min_score: float = 2.0,
    program_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    scored = [
        (
            _score_product_match(
                p,
                program_name=program_name,
                investor_name=investor_name,
                program_names=program_names,
            ),
            p,
        )
        for p in products
    ]
    return [
        p
        for score, p in sorted(scored, key=lambda x: x[0], reverse=True)
        if score >= min_score
    ]


def _field_map(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows or []:
        fid = row.get("fieldId")
        val = row.get("value")
        if fid and val:
            out[str(fid)] = val
    return out


def _format_duration(val: dict[str, Any] | None) -> str:
    if not val or val.get("type") != "duration":
        return ""
    count = val.get("count", "")
    unit = val.get("unit", "days")
    return f"{count} {unit}".strip()


def _scenario_lock_days(scenario: dict[str, Any]) -> int | None:
    fields = _field_map(scenario.get("priceScenarioFields"))
    lock_val = fields.get("rate-lock-period")
    return _parse_lock_days(lock_val, _format_duration(lock_val))


def _prune_pricing_logs(logs_dir: Path, keep: int) -> None:
    try:
        files = sorted(
            logs_dir.glob("loanpass_pricing_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[max(keep, 0):]:
            stale.unlink(missing_ok=True)
    except Exception as exc:
        _log.warning("LoanPASS pricing log prune failed: %s", exc)


def _write_pricing_log(record: dict[str, Any]) -> None:
    if not config.LOANPASS_PRICING_LOG_TO_FILE:
        return
    try:
        logs_dir = config.REPO_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = logs_dir / f"loanpass_pricing_{ts}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune_pricing_logs(logs_dir, config.LOANPASS_PRICING_LOG_KEEP)
    except Exception as exc:
        _log.warning("LoanPASS pricing log write failed: %s", exc)


def _parse_lock_days(lock_val: dict[str, Any] | None, lock_str: str | None = None) -> int | None:
    if lock_val and lock_val.get("type") == "duration":
        unit = (lock_val.get("unit") or "days").lower()
        if unit.startswith("day"):
            try:
                return int(lock_val.get("count") or 0)
            except (TypeError, ValueError):
                pass
    if lock_str:
        m = re.search(r"(\d+)", lock_str)
        if m:
            return int(m.group(1))
    return None


def _fmt_price(raw: Any) -> str | None:
    if raw in ("", None):
        return None
    try:
        return f"{float(raw):.3f}"
    except (TypeError, ValueError):
        return str(raw)


def _fmt_rate_value(raw: Any) -> str | None:
    """Interest rate numeric string with exactly 3 decimal places (no % suffix)."""
    if raw in ("", None):
        return None
    try:
        return f"{float(raw):.3f}"
    except (TypeError, ValueError):
        return str(raw).strip() or None


def _fmt_rate_pct(raw: Any) -> str | None:
    val = _fmt_rate_value(raw)
    return f"{val}%" if val else None


def _fmt_rate_credit(price_raw: Any) -> str:
    if price_raw in ("", None):
        return "0.000"
    try:
        delta = float(price_raw) - 100.0
        return f"{max(0.0, delta):.3f}"
    except (TypeError, ValueError):
        return "0.000"


def _parse_money_float(raw: Any) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        n = float(re.sub(r"[,$\s]", "", s))
    except ValueError:
        return None
    return n if n > 0 else None


def _estimate_payment(rate_str: Any, loan_amount: float | None, amort_years: int | None) -> str | None:
    if not rate_str or not loan_amount or not amort_years:
        return None
    try:
        annual = float(str(rate_str).replace("%", ""))
        monthly_rate = annual / 100.0 / 12.0
        n = int(amort_years) * 12
        if monthly_rate <= 0:
            return f"${loan_amount / n:,.2f}"
        payment = loan_amount * monthly_rate * (1 + monthly_rate) ** n / (
            (1 + monthly_rate) ** n - 1
        )
        return f"${payment:,.2f}"
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _form_metric(form: dict[str, Any], *keys: str, decimals: int = 3) -> str | None:
    for key in keys:
        raw = form.get(key)
        if raw in (None, ""):
            continue
        s = str(raw).strip().replace("%", "")
        if not s:
            continue
        try:
            cleaned = re.sub(r"[^\d.]", "", s)
            return f"{float(cleaned):.{decimals}f}"
        except ValueError:
            return s
    return None


def _calc_field_map(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows or []:
        fid = row.get("fieldId")
        if fid:
            out[str(fid)] = row.get("value")
    return out


def _calc_number(calc: dict[str, Any], *field_ids: str) -> float | None:
    for fid in field_ids:
        val = calc.get(fid)
        if not val or not isinstance(val, dict):
            continue
        raw = val.get("value")
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _scenario_price_raw(scenario: dict[str, Any]) -> Any:
    price = scenario.get("adjustedPrice")
    if price not in (None, ""):
        return price
    fields = _field_map(scenario.get("priceScenarioFields"))
    return (fields.get("base-price") or {}).get("value", "")


def _scenario_rate_raw(scenario: dict[str, Any]) -> Any:
    rate = scenario.get("adjustedRate")
    if rate not in (None, ""):
        return rate
    fields = _field_map(scenario.get("priceScenarioFields"))
    return (fields.get("base-interest-rate") or {}).get("value", "")


def _parse_min_rate_from_scenarios(scenarios: list[dict[str, Any]]) -> float | None:
    """Min rate floor surfaced in LoanPASS rejection messages (e.g. 6.125%)."""
    for scenario in scenarios:
        for rej in scenario.get("rejections") or []:
            msg = (rej.get("message") or "").lower()
            m = re.search(r"min rate after adjustments is ([\d.]+)", msg)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
    return None


def _scenario_is_priced(
    scenario: dict[str, Any],
    *,
    min_rate: float | None = None,
) -> bool:
    """
    Match LoanPASS pricing table cells.

    LoanPASS returns many scenarios as ``rejected`` yet still shows prices for
    valid rate/lock combos. Hide a cell when:
    - status is ``error``
    - rate is at/below the min-rate floor
    - rejections include floor / invalid-lock messages
    """
    status = (scenario.get("status") or "").lower()
    if status == "error":
        return False

    price = _scenario_price_raw(scenario)
    if price in ("", None):
        return False

    rate_raw = _scenario_rate_raw(scenario)
    try:
        rate = float(rate_raw)
    except (TypeError, ValueError):
        return False

    if min_rate is not None and rate <= min_rate:
        return False

    for rej in scenario.get("rejections") or []:
        msg = (rej.get("message") or "").lower()
        if "price falls below floor" in msg:
            return False
        if "rate lock period must be greater than zero" in msg:
            return False

    return True


def _scenario_metrics(
    scenario: dict[str, Any],
    *,
    form: dict[str, Any],
    amort_years: int | None,
) -> dict[str, str | None]:
    calc = _calc_field_map(scenario.get("calculatedFields"))
    rate_raw = _scenario_rate_raw(scenario)
    loan_amount = _parse_money_float(form.get("loanAmount") or form.get("loan_amount"))

    dti_val = _calc_number(calc, "calc@final-est-dti", "calc@est-dti")
    dscr_val = _calc_number(calc, "calc@final-est-dscr", "calc@est-dscr")
    pay_val = _calc_number(calc, "calc@final-est-payment", "calc@est-payment")

    dti = f"{dti_val:.3f}" if dti_val is not None else _form_metric(form, "estimatedDti", "dti", "DTI")
    dscr = (
        f"{dscr_val:.3f}"
        if dscr_val is not None
        else _form_metric(form, "dscr", "loanLevelDscr", "DSCR") or "0.000"
    )
    payment = (
        f"${pay_val:,.2f}"
        if pay_val is not None
        else _estimate_payment(rate_raw, loan_amount, amort_years)
    )
    credit_val = _calc_number(calc, "calc@rate-credit", "calc@final-rate-credit")
    rate_credit = f"{credit_val:.3f}" if credit_val is not None else None
    return {
        "final_est_dti": dti,
        "final_est_dscr": dscr,
        "final_est_payment": payment,
        "rate_credit": rate_credit,
    }


def _build_breadcrumbs(dim_row: dict[str, Any] | None, form: dict[str, Any]) -> str:
    parts: list[str] = []
    if dim_row:
        name = (dim_row.get("program_name_np") or dim_row.get("program_name") or "").strip()
        if name:
            parts.append(name.upper())
        code = (dim_row.get("program_code") or "").strip()
        if code:
            parts.append(code.replace("_", " "))
    lien = (form.get("lienPosition") or form.get("lien_position") or "").strip().lower()
    if "second" in lien:
        parts.append("SECOND LIEN")
    elif lien:
        parts.append("FIRST LIEN")
    return " · ".join(parts) if parts else ""


def _build_pricing_grid(
    scenarios: list[dict[str, Any]],
    *,
    form: dict[str, Any],
    amort_years: int | None,
    focus_lock_days: int = _FOCUS_LOCK_DAYS,
) -> dict[str, Any]:
    """Build rate grid for a single focus lock period (default 30 days)."""
    min_rate = _parse_min_rate_from_scenarios(scenarios)
    by_rate: dict[str, dict[int, dict[str, Any]]] = {}

    for scenario in scenarios:
        lock_days = _scenario_lock_days(scenario)
        if lock_days != focus_lock_days:
            continue

        rate_raw = _scenario_rate_raw(scenario)
        price_raw = _scenario_price_raw(scenario)
        if not rate_raw:
            continue
        rate_key = _fmt_rate_value(rate_raw) or str(rate_raw)

        available = _scenario_is_priced(scenario, min_rate=min_rate)
        metrics = _scenario_metrics(scenario, form=form, amort_years=amort_years)
        cell = {
            "adjusted_price": _fmt_price(price_raw) if available else None,
            "rate_credit": (
                metrics["rate_credit"]
                if available and metrics.get("rate_credit") is not None
                else (_fmt_rate_credit(price_raw) if available else None)
            ),
            "final_est_payment": metrics["final_est_payment"] if available else None,
            "final_est_dti": metrics["final_est_dti"] if available else None,
            "final_est_dscr": metrics["final_est_dscr"] if available else None,
            "available": available,
        }
        by_rate.setdefault(rate_key, {})[focus_lock_days] = cell

    rates: list[dict[str, Any]] = []
    for rate_key in sorted(by_rate.keys(), key=float):
        locks = by_rate[rate_key]
        if focus_lock_days not in locks:
            continue
        rates.append(
            {
                "rate": rate_key,
                "rate_display": f"{rate_key}%",
                "locks": {str(focus_lock_days): locks[focus_lock_days]},
            }
        )

    lock_periods = [focus_lock_days] if rates else []
    return {"lock_periods": lock_periods, "rates": rates}


def _scenario_row(scenario: dict[str, Any], index: int) -> dict[str, Any]:
    rate = _scenario_rate_raw(scenario)
    price = _scenario_price_raw(scenario)
    fields = _field_map(scenario.get("priceScenarioFields"))
    lock_val = fields.get("rate-lock-period")
    lock = _format_duration(lock_val)
    lock_days = _parse_lock_days(lock_val, lock)
    rate_s = _fmt_rate_pct(rate)
    return {
        "option": index,
        "rate": rate_s,
        "price": str(price) if price not in ("", None) else None,
        "lock_period": lock or None,
        "lock_days": lock_days,
        "adjusted_price": _fmt_price(price),
        "rate_credit": _fmt_rate_credit(price),
    }


def _scenario_line(scenario: dict[str, Any], index: int) -> str:
    fields = _field_map(scenario.get("priceScenarioFields"))
    rate = (fields.get("base-interest-rate") or {}).get("value", "")
    price = (fields.get("base-price") or {}).get("value", "")
    lock = _format_duration(fields.get("rate-lock-period"))
    parts = []
    if rate:
        parts.append(f"Rate: {rate}%")
    if price:
        parts.append(f"Price: {price}")
    if lock:
        parts.append(f"Lock: {lock}")
    detail = " · ".join(parts) if parts else "See pricing details"
    return f"- Option {index}: {detail}"


def format_pricing_reply(
    *,
    program_name: str,
    product: dict[str, Any],
    price_scenarios: list[dict[str, Any]],
    summary_totals: dict[str, Any] | None,
) -> str:
    investor = product.get("investorName") or ""
    lp_name = product.get("productName") or program_name
    status = product.get("status") or ""

    intro = (
        f"Indicative pricing for **{program_name}** "
        f"(matched product: {lp_name}"
        + (f", {investor}" if investor else "")
        + ")."
    )
    if status and status.lower() not in ("available", "approved"):
        intro += f" Product status: {status}."

    if not price_scenarios:
        return (
            f"{intro}\n\n"
            "No price scenarios were returned for this product with your current scenario. "
            "Adjust inputs or verify the program is pricing-enabled."
        )

    # Prefer lowest rate scenarios
    def rate_key(s: dict[str, Any]) -> float:
        fields = _field_map(s.get("priceScenarioFields"))
        raw = (fields.get("base-interest-rate") or {}).get("value", "999")
        try:
            return float(raw)
        except ValueError:
            return 999.0

    sorted_scenarios = sorted(price_scenarios, key=rate_key)[:6]
    lines = [_scenario_line(s, i + 1) for i, s in enumerate(sorted_scenarios)]

    footer = ""
    if summary_totals:
        avail = summary_totals.get("available")
        if avail is not None:
            footer = f"\n\n({avail} products are available for this scenario overall.)"

    return (
        f"{intro}\n\n"
        "Here are the top price scenarios:\n\n"
        + "\n".join(lines)
        + "\n\n"
        "Rates are indicative and subject to lock desk confirmation."
        + footer
    )


@lru_cache(maxsize=1)
def _session_token() -> str:
    cfg = get_loanpass_embed_config()
    if not cfg:
        raise LoanpassNotConfiguredError(
            "Pricing is not configured on the server."
        )
    origin = cfg["origin"]
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{origin}/api/login",
                json={
                    "clientAccessId": cfg["clientAccessId"],
                    "emailAddress": cfg["email"],
                    "password": cfg["password"],
                },
            )
    except httpx.HTTPError as exc:  # connection / timeout
        record_pricing_failure(f"login {type(exc).__name__}: {exc}")
        raise LoanpassError(f"Pricing login error: {exc}") from exc
    if r.status_code != 200:
        record_pricing_failure(f"login HTTP {r.status_code}")
        raise LoanpassError(f"Pricing login failed (HTTP {r.status_code}).")
    data = r.json()
    token = data.get("sessionToken")
    if not token:
        record_pricing_failure("login returned no session token")
        raise LoanpassError("Pricing login returned no session token.")
    return str(token)


def reset_session_token() -> None:
    """Drop the cached LoanPASS session token so the next call re-logs in.

    Called when a request comes back 401/403 (the cached token expired / was
    invalidated by token churn) so pricing self-heals WITHOUT a process restart.
    """
    _session_token.cache_clear()


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_session_token()}",
        "Content-Type": "application/json",
    }


def _pricing_request_body(credit_fields: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "creditApplicationFields": credit_fields,
        "currentTime": now,
        "outputFieldsFilter": _OUTPUT_FILTER,
        "publishedVersionRequest": _PUBLISHED_VERSION,
    }


def list_program_products(
    form: dict[str, Any],
    *,
    program_name: str | None = None,
    program_id: int | None = None,
    investor_name: str | None = None,
) -> dict[str, Any]:
    """dim_product_types for this program (via map_program_products)."""
    del form, investor_name  # reserved for future preference filtering
    if program_id is None:
        raise LoanpassError("program_id is required to list DB product types.")

    loanpass_name, dim_row = resolve_loanpass_program_name(
        program_id=program_id, program_name=program_name
    )
    product_types = list_db_program_product_types(int(program_id))
    display_name = (
        (dim_row.get("program_name_np") or dim_row.get("program_name") or "").strip()
        if dim_row
        else (program_name or loanpass_name)
    )
    return {
        "program_id": program_id,
        "program_name": display_name or loanpass_name,
        "program_name_loanpass": loanpass_name,
        "products": [
            {
                "product_type_id": int(pt["id"]),
                "product_name": pt.get("name"),
                "io_period_years": pt.get("io_period_years"),
                "amort_period_years": pt.get("amort_period_years"),
                "total_term_years": pt.get("total_term_years"),
                "loanpass_product_id": pt.get("loanpass_product_id"),
            }
            for pt in product_types
        ],
    }


def fetch_product_pricing(
    form: dict[str, Any],
    *,
    program_name: str | None = None,
    program_id: int | None = None,
    investor_name: str | None = None,
    product_id: str | None = None,
    product_label: str | None = None,
    product_type_id: int | None = None,
) -> dict[str, Any]:
    """
    Price via LoanPASS execute-product only (no execute-summary).

    Resolves productId from map_program_products; dual mappings are gated on
    standard vs alt doc or FN vs FN DSCR. Unmapped rows raise pricing unavailable.
    """
    del product_id, investor_name  # API compat; resolution is DB + form gates only.
    loanpass_name, dim_row = resolve_loanpass_program_name(
        program_id=program_id, program_name=program_name
    )
    display_name = (
        (dim_row.get("program_name_np") or dim_row.get("program_name") or "").strip()
        if dim_row
        else (program_name or loanpass_name)
    ) or loanpass_name

    cfg = get_loanpass_embed_config()
    if not cfg:
        raise LoanpassNotConfiguredError(
            "Pricing is not configured on the server."
        )

    credit_fields = map_form_to_loanpass_fields(form)
    if not credit_fields:
        raise LoanpassError("Not enough scenario data to request pricing.")

    body = _pricing_request_body(credit_fields)
    origin = cfg["origin"]
    headers = _auth_headers()
    pricing_log: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "program_id": program_id,
        "program_name": display_name,
        "program_name_loanpass": loanpass_name,
        "product_type_id": product_type_id,
        "product_label": product_label,
        "focus_lock_days": _FOCUS_LOCK_DAYS,
        "calls": [],
    }

    dim_product: dict[str, Any] | None = None
    if product_type_id is not None:
        dim_product = get_dim_product_type(int(product_type_id))
    elif (product_label or "").strip():
        dim_product = get_dim_product_type_by_name(product_label.strip())
        if dim_product and product_type_id is None:
            product_type_id = int(dim_product["id"])

    if program_id is None:
        raise LoanpassError("program_id is required for pricing.")
    if product_type_id is None:
        raise LoanpassError("product_type_id is required for pricing.")

    resolved_label = (
        (dim_product or {}).get("name")
        or (product_label or "").strip()
        or f"product type {product_type_id}"
    )
    product_id = resolve_map_loanpass_product_id(
        int(program_id),
        int(product_type_id),
        form,
        product_label=str(resolved_label),
    )
    pricing_log["direct_product_id"] = product_id
    _log.info(
        "LoanPASS execute-product direct product_id=%s program_id=%s product_type_id=%s",
        product_id,
        program_id,
        product_type_id,
    )

    with httpx.Client(timeout=90) as client:
        prod_body = {**body, "productId": str(product_id)}
        prod_url = f"{origin}/api/execute-product"
        try:
            prod_resp = client.post(prod_url, headers=headers, json=prod_body)
            if prod_resp.status_code in (401, 403):
                # Cached session token expired / invalidated — refresh and retry
                # once so pricing self-heals without a process restart.
                _log.warning(
                    "LoanPASS auth HTTP %s — refreshing session token and retrying once",
                    prod_resp.status_code,
                )
                reset_session_token()
                headers = _auth_headers()
                prod_resp = client.post(prod_url, headers=headers, json=prod_body)
        except httpx.HTTPError as exc:  # connection / timeout / read error
            record_pricing_failure(f"execute-product {type(exc).__name__}: {exc}")
            raise LoanpassError(f"Pricing request error: {exc}") from exc
        detail: dict[str, Any] = {}
        try:
            detail = prod_resp.json()
        except Exception:
            detail = {"_raw": prod_resp.text[:2000]}
        scenarios_all = detail.get("priceScenarios") or []
        scenarios_30 = [
            s for s in scenarios_all if _scenario_lock_days(s) == _FOCUS_LOCK_DAYS
        ]
        _log.info(
            "LoanPASS execute-product product_id=%s → HTTP %s scenarios=%s (lock_%sd=%s)",
            product_id,
            prod_resp.status_code,
            len(scenarios_all),
            _FOCUS_LOCK_DAYS,
            len(scenarios_30),
        )
        pricing_log["calls"].append(
            {
                "step": "execute-product",
                "url": prod_url,
                "request": prod_body,
                "status_code": prod_resp.status_code,
                "response_summary": {
                    "product_id": product_id,
                    "product_name": detail.get("productName"),
                    "scenario_count_all": len(scenarios_all),
                    f"scenario_count_lock_{_FOCUS_LOCK_DAYS}d": len(scenarios_30),
                },
                "response": detail if prod_resp.status_code == 200 else {"error": prod_resp.text[:2000]},
            }
        )
        _write_pricing_log(pricing_log)
        if prod_resp.status_code != 200:
            record_pricing_failure(
                f"execute-product HTTP {prod_resp.status_code}: {prod_resp.text[:200]}"
            )
            raise LoanpassError(
                f"Pricing request failed (HTTP {prod_resp.status_code}): "
                f"{prod_resp.text[:300]}"
            )
        record_pricing_success()

    scenarios = scenarios_30

    def rate_key(s: dict[str, Any]) -> float:
        fields = _field_map(s.get("priceScenarioFields"))
        raw = (fields.get("base-interest-rate") or {}).get("value", "999")
        try:
            return float(raw)
        except ValueError:
            return 999.0

    sorted_scenarios = sorted(scenarios, key=rate_key)
    table_rows = [_scenario_row(s, i + 1) for i, s in enumerate(sorted_scenarios[:12])]

    resolved_label = (
        (dim_product or {}).get("name")
        or (product_label or "").strip()
        or detail.get("productName")
    )
    amort_years = None
    if dim_product:
        amort_years = dim_product.get("amort_period_years") or dim_product.get("total_term_years")
        try:
            amort_years = int(amort_years) if amort_years is not None else None
        except (TypeError, ValueError):
            amort_years = None

    pricing_grid = _build_pricing_grid(
        sorted_scenarios,
        form=form,
        amort_years=amort_years,
    )
    effective_raw = detail.get("rateSheetEffectiveTimestamp")
    effective_date = None
    if effective_raw:
        try:
            dt = datetime.fromisoformat(str(effective_raw).replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
            effective_date = f"{dt.day} {dt.strftime('%b %Y')}"
        except ValueError:
            effective_date = str(effective_raw)[:10]

    reply = format_pricing_reply(
        program_name=display_name,
        product=detail,
        price_scenarios=sorted_scenarios[:6],
        summary_totals=None,
    )

    return {
        "reply": reply,
        "program_id": program_id,
        "program_name": display_name,
        "program_name_loanpass": loanpass_name,
        "program_code": (dim_row or {}).get("program_code"),
        "breadcrumbs": _build_breadcrumbs(dim_row, form),
        "product_type_id": (dim_product or {}).get("id"),
        "product_label": resolved_label,
        "loanpass_product_id": product_id,
        "loanpass_product_name": detail.get("productName"),
        "loanpass_investor": detail.get("investorName"),
        "status": detail.get("status"),
        "effective_date": effective_date,
        "scenario_count": len(scenarios),
        "price_scenarios": table_rows,
        "pricing_grid": pricing_grid,
    }
