"""Marginal tolerance for numeric eligibility gates (rounding / display precision)."""

from __future__ import annotations

# Half a percent — LTV, CLTV, DTI
ELIG_PCT_TOLERANCE = 0.5

# Dollars — loan amount min/max caps
ELIG_LOAN_TOLERANCE = 2

# FICO points
ELIG_FICO_TOLERANCE = 2

# Acres
ELIG_ACREAGE_TOLERANCE = 0.05

# DSCR ratio (e.g. 1.24 vs 1.25 min)
ELIG_DSCR_TOLERANCE = 0.05


def exceeds_pct(actual: float | int, cap: float | int) -> bool:
    return float(actual) > float(cap) + ELIG_PCT_TOLERANCE


def below_pct(actual: float | int, minimum: float | int) -> bool:
    return float(actual) < float(minimum) - ELIG_PCT_TOLERANCE


def exceeds_loan(actual: int | float, cap: int | float) -> bool:
    return float(actual) > float(cap) + ELIG_LOAN_TOLERANCE


def below_loan(actual: int | float, minimum: int | float) -> bool:
    return float(actual) < float(minimum) - ELIG_LOAN_TOLERANCE


def below_fico(actual: int, minimum: int) -> bool:
    return int(actual) < int(minimum) - ELIG_FICO_TOLERANCE


def above_fico(actual: int, maximum: int) -> bool:
    return int(actual) > int(maximum) + ELIG_FICO_TOLERANCE


def below_dscr(actual: float, minimum: float) -> bool:
    return float(actual) < float(minimum) - ELIG_DSCR_TOLERANCE


def exceeds_acreage(actual: float, cap: float) -> bool:
    return float(actual) > float(cap) + ELIG_ACREAGE_TOLERANCE


def loan_within_tier(
    amt: int | float,
    row_min: int | float | None,
    row_max: int | float | None,
) -> bool:
    if row_min is not None and below_loan(amt, row_min):
        return False
    if row_max is not None and exceeds_loan(amt, row_max):
        return False
    return True


def fico_meets_min(fico: int, fico_min: int) -> bool:
    return not below_fico(fico, fico_min)
