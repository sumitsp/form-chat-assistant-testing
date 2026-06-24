"""
LoanPASS pricing endpoints (`/api/loanpass/*`).

Extracted from ``backend/api.py`` so the pricing surface can be hosted as its
own process (see ``backend/pricing_app.py``) — LoanPASS occasionally needs an
independent restart (rate-limit / token churn) without bouncing the main API.
The same router is also mountable inline for single-process dev.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class LoanpassPriceRequest(BaseModel):
    """Wizard scenario + program to price via LoanPASS Public API."""

    form: dict[str, Any]
    program_id: int | None = None
    program_name: str | None = None
    investor_name: str | None = None
    product_id: str | None = None
    product_label: str | None = None
    product_type_id: int | None = None


class LoanpassPriceScenarioRow(BaseModel):
    option: int
    rate: str | None = None
    price: str | None = None
    lock_period: str | None = None
    lock_days: int | None = None
    adjusted_price: str | None = None
    rate_credit: str | None = None


class LoanpassPricingGridCell(BaseModel):
    adjusted_price: str | None = None
    rate_credit: str | None = None
    final_est_payment: str | None = None
    final_est_dti: str | None = None
    final_est_dscr: str | None = None
    available: bool = False


class LoanpassPricingGridRate(BaseModel):
    rate: str
    rate_display: str
    locks: dict[str, LoanpassPricingGridCell] = Field(default_factory=dict)


class LoanpassPricingGrid(BaseModel):
    lock_periods: list[int] = Field(default_factory=list)
    rates: list[LoanpassPricingGridRate] = Field(default_factory=list)


class LoanpassPriceResponse(BaseModel):
    reply: str
    program_name: str
    program_code: str | None = None
    breadcrumbs: str | None = None
    product_type_id: int | None = None
    product_label: str | None = None
    loanpass_product_id: str | None = None
    loanpass_product_name: str | None = None
    loanpass_investor: str | None = None
    status: str | None = None
    effective_date: str | None = None
    info_notes: list[str] = Field(default_factory=list)
    scenario_count: int = 0
    price_scenarios: list[LoanpassPriceScenarioRow] = Field(default_factory=list)
    pricing_grid: LoanpassPricingGrid | None = None


class LoanpassProductsRequest(BaseModel):
    form: dict[str, Any]
    program_id: int | None = None
    program_name: str | None = None
    investor_name: str | None = None


class LoanpassProgramMetaResponse(BaseModel):
    program_id: int
    program_code: str | None = None
    program_name_np: str | None = None
    program_name_loanpass: str | None = None
    pricing_available: bool = False


class LoanpassProductItem(BaseModel):
    product_type_id: int | None = None
    product_name: str | None = None
    io_period_years: int | None = None
    amort_period_years: int | None = None
    total_term_years: int | None = None
    loanpass_product_id: str | None = None


class LoanpassProductsResponse(BaseModel):
    program_name: str
    products: list[LoanpassProductItem] = Field(default_factory=list)


@router.get("/api/loanpass/program/{program_id}", response_model=LoanpassProgramMetaResponse)
def loanpass_program_meta(program_id: int):
    """dim_programs lookup — is LoanPASS pricing configured for this program?"""
    from backend.loanpass_client import get_dim_program_loanpass  # noqa: PLC0415

    row = get_dim_program_loanpass(program_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Program id {program_id} not found.")
    lp = (row.get("program_name_loanpass") or "").strip() or None
    return LoanpassProgramMetaResponse(
        program_id=int(row["program_id"]),
        program_code=(row.get("program_code") or None),
        program_name_np=(row.get("program_name_np") or None),
        program_name_loanpass=lp,
        pricing_available=bool(lp),
    )


@router.post("/api/loanpass/products", response_model=LoanpassProductsResponse)
def loanpass_products(body: LoanpassProductsRequest):
    """dim_product_types for a program (map_program_products)."""
    from backend.loanpass_client import (  # noqa: PLC0415
        LoanpassError,
        LoanpassPricingUnavailableError,
        list_program_products,
    )

    if body.program_id is None:
        raise HTTPException(status_code=400, detail="program_id is required.")

    try:
        out = list_program_products(
            body.form,
            program_id=body.program_id,
            program_name=(body.program_name or "").strip() or None,
            investor_name=(body.investor_name or "").strip() or None,
        )
    except LoanpassPricingUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except LoanpassError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return LoanpassProductsResponse(
        program_name=out["program_name"],
        products=out.get("products") or [],
    )


@router.post("/api/loanpass/price", response_model=LoanpassPriceResponse)
def loanpass_price(body: LoanpassPriceRequest):
    """
    LoanPASS Public API pricing — execute-summary + execute-product.
    See https://docs.loanpass.io/public-api/
    """
    from backend.loanpass_client import (  # noqa: PLC0415
        LoanpassError,
        LoanpassNotConfiguredError,
        LoanpassPricingUnavailableError,
        fetch_product_pricing,
    )

    if body.program_id is None and not (body.program_name or "").strip():
        raise HTTPException(status_code=400, detail="program_id or program_name is required.")

    try:
        out = fetch_product_pricing(
            body.form,
            program_id=body.program_id,
            program_name=(body.program_name or "").strip() or None,
            investor_name=(body.investor_name or "").strip() or None,
            product_id=(body.product_id or "").strip() or None,
            product_label=(body.product_label or "").strip() or None,
            product_type_id=body.product_type_id,
        )
    except LoanpassNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except LoanpassPricingUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except LoanpassError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    grid_raw = out.get("pricing_grid") or {}
    pricing_grid = LoanpassPricingGrid(
        lock_periods=grid_raw.get("lock_periods") or [],
        rates=grid_raw.get("rates") or [],
    )
    return LoanpassPriceResponse(
        reply=out["reply"],
        program_name=out["program_name"],
        program_code=out.get("program_code"),
        breadcrumbs=out.get("breadcrumbs"),
        product_type_id=out.get("product_type_id"),
        product_label=out.get("product_label"),
        loanpass_product_id=out.get("loanpass_product_id"),
        loanpass_product_name=out.get("loanpass_product_name"),
        loanpass_investor=out.get("loanpass_investor"),
        status=out.get("status"),
        effective_date=out.get("effective_date"),
        info_notes=out.get("info_notes") or [],
        scenario_count=int(out.get("scenario_count") or 0),
        price_scenarios=out.get("price_scenarios") or [],
        pricing_grid=pricing_grid,
    )
