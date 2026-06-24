"""Master metrics list — the canonical input-field catalog (single source of truth).

This is the contract shared by the eligibility engine (#2) and chat extraction
(#3): every input field/metric, its allowed values, format, whether it is
mandatory (essential) vs optional — a slot's `trigger` lambda holds the
condition for when an essential slot applies — plus the helpers that normalize
and validate a raw value
against the list. Lifted out of retrieval/slot_engine.py, which now imports
these names back (the slot *engine* keeps the portfolio-state / planning logic).
"""
from __future__ import annotations

import re
from typing import Any  # noqa: F401  (kept for annotation compatibility)


# ---------------------------------------------------------------------------
# Slot definitions
# ---------------------------------------------------------------------------
# 'trigger' is a lambda (portfolio: dict) -> bool.
# None trigger = always shown (no condition).
# Options use snake_case codes that the extractor LLM returns verbatim.
# ---------------------------------------------------------------------------

def _is_dscr_path(p: dict) -> bool:
    # Full Documentation always means personal income / DTI — never DSCR.
    if p.get("doc_type") == "full_doc":
        return False
    occ = p.get("occupancy", "")
    pt = (p.get("property_type") or "").lower()
    ip = p.get("investment_income_path", "")
    return occ == "investment_property" and ("5-9" in pt or "5-8" in pt or ip == "dscr")


_DOC_TIMEFRAME_DOC_TYPES = frozenset({
    "full_doc",
    "bank_stmt_12_or_24",
    "bank_stmt_business",
    "pl_only",
    "pl_2mo_bs",
    "1099",
})


def _doc_timeframe_applies(p: dict) -> bool:
    return not _is_dscr_path(p) and p.get("doc_type") in _DOC_TIMEFRAME_DOC_TYPES


def _needs_fico(p: dict) -> bool:
    return p.get("citizenship") != "foreign_national"


# U.S. state normalization (2-letter codes for geo triggers)
_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "d.c.": "DC",
}


_CITY_TO_STATE: dict[str, str] = {
    # Florida
    "miami": "FL", "orlando": "FL", "tampa": "FL", "jacksonville": "FL",
    "fort lauderdale": "FL", "ft lauderdale": "FL", "fort myers": "FL", "ft myers": "FL",
    "boca raton": "FL", "west palm beach": "FL", "palm beach": "FL",
    "sarasota": "FL", "naples": "FL", "gainesville": "FL", "tallahassee": "FL",
    "pensacola": "FL", "clearwater": "FL", "st. petersburg": "FL", "saint petersburg": "FL",
    # California
    "los angeles": "CA", "la": "CA", "san francisco": "CA", "sf": "CA",
    "san diego": "CA", "sacramento": "CA", "san jose": "CA",
    "fresno": "CA", "long beach": "CA", "bakersfield": "CA", "anaheim": "CA",
    "riverside": "CA", "stockton": "CA", "irvine": "CA", "oakland": "CA",
    # Texas
    "houston": "TX", "dallas": "TX", "austin": "TX", "san antonio": "TX",
    "fort worth": "TX", "el paso": "TX", "arlington": "TX", "corpus christi": "TX",
    "plano": "TX", "lubbock": "TX", "garland": "TX", "irving": "TX",
    # New York
    "new york": "NY", "nyc": "NY", "new york city": "NY",
    "brooklyn": "NY", "manhattan": "NY", "queens": "NY", "bronx": "NY", "staten island": "NY",
    "buffalo": "NY", "rochester": "NY", "albany": "NY", "yonkers": "NY",
    # Illinois
    "chicago": "IL", "aurora": "IL", "joliet": "IL", "naperville": "IL",
    "rockford": "IL", "springfield": "IL", "peoria": "IL",
    # Arizona
    "phoenix": "AZ", "tucson": "AZ", "mesa": "AZ", "chandler": "AZ",
    "gilbert": "AZ", "glendale": "AZ", "scottsdale": "AZ", "tempe": "AZ",
    # Pennsylvania
    "philadelphia": "PA", "pittsburgh": "PA", "allentown": "PA",
    "erie": "PA", "reading": "PA", "scranton": "PA",
    # Georgia
    "atlanta": "GA", "columbus": "GA", "savannah": "GA", "augusta": "GA",
    "macon": "GA", "athens": "GA", "sandy springs": "GA",
    # North Carolina
    "charlotte": "NC", "raleigh": "NC", "greensboro": "NC", "durham": "NC",
    "winston-salem": "NC", "winston salem": "NC", "fayetteville": "NC",
    # Ohio
    "columbus": "OH", "cleveland": "OH", "cincinnati": "OH", "toledo": "OH",
    "akron": "OH", "dayton": "OH",
    # Michigan
    "detroit": "MI", "grand rapids": "MI", "warren": "MI", "sterling heights": "MI",
    "ann arbor": "MI", "lansing": "MI",
    # Tennessee
    "nashville": "TN", "memphis": "TN", "knoxville": "TN", "chattanooga": "TN",
    "clarksville": "TN", "murfreesboro": "TN",
    # Washington
    "seattle": "WA", "spokane": "WA", "tacoma": "WA", "bellevue": "WA",
    "kent": "WA", "renton": "WA",
    # Colorado
    "denver": "CO", "colorado springs": "CO", "aurora": "CO",
    "fort collins": "CO", "lakewood": "CO", "thornton": "CO",
    # Nevada
    "las vegas": "NV", "henderson": "NV", "reno": "NV", "north las vegas": "NV",
    # Maryland
    "baltimore": "MD", "frederick": "MD", "rockville": "MD", "gaithersburg": "MD",
    # Massachusetts
    "boston": "MA", "worcester": "MA", "springfield": "MA", "lowell": "MA",
    "cambridge": "MA", "brockton": "MA",
    # New Jersey
    "newark": "NJ", "jersey city": "NJ", "paterson": "NJ", "elizabeth": "NJ",
    "trenton": "NJ", "camden": "NJ", "atlantic city": "NJ",
    # Virginia
    "virginia beach": "VA", "norfolk": "VA", "chesapeake": "VA", "richmond": "VA",
    "newport news": "VA", "alexandria": "VA", "hampton": "VA",
    # Indiana
    "indianapolis": "IN", "fort wayne": "IN", "evansville": "IN", "south bend": "IN",
    # Minnesota
    "minneapolis": "MN", "saint paul": "MN", "st. paul": "MN", "rochester": "MN",
    # Oregon
    "portland": "OR", "eugene": "OR", "salem": "OR", "gresham": "OR",
    # Kentucky
    "louisville": "KY", "lexington": "KY", "bowling green": "KY",
    # Louisiana
    "new orleans": "LA", "baton rouge": "LA", "shreveport": "LA",
    # Alabama
    "birmingham": "AL", "montgomery": "AL", "huntsville": "AL", "mobile": "AL",
    # Utah
    "salt lake city": "UT", "west valley city": "UT", "provo": "UT", "west jordan": "UT",
    # Wisconsin
    "milwaukee": "WI", "madison": "WI", "green bay": "WI",
    # Missouri
    "kansas city": "MO", "st. louis": "MO", "saint louis": "MO", "springfield": "MO",
    # South Carolina
    "columbia": "SC", "charleston": "SC", "north charleston": "SC",
    # Oklahoma
    "oklahoma city": "OK", "tulsa": "OK", "norman": "OK",
    # Connecticut
    "bridgeport": "CT", "new haven": "CT", "hartford": "CT", "stamford": "CT",
    # Iowa
    "des moines": "IA", "cedar rapids": "IA", "davenport": "IA",
    # Mississippi
    "jackson": "MS", "gulfport": "MS", "southaven": "MS",
    # Kansas
    "wichita": "KS", "overland park": "KS", "kansas city": "KS",
    # Hawaii
    "honolulu": "HI", "hilo": "HI", "kailua": "HI", "pearl city": "HI",
    # New Mexico
    "albuquerque": "NM", "las cruces": "NM", "santa fe": "NM",
    # Nebraska
    "omaha": "NE", "lincoln": "NE",
    # Idaho
    "boise": "ID", "meridian": "ID", "nampa": "ID",
    # Arkansas
    "little rock": "AR", "fort smith": "AR",
    # West Virginia
    "charleston": "WV", "huntington": "WV",
    # Delaware
    "wilmington": "DE", "dover": "DE",
    # Rhode Island
    "providence": "RI", "cranston": "RI",
    # Montana
    "billings": "MT", "missoula": "MT", "great falls": "MT",
    # Alaska
    "anchorage": "AK", "fairbanks": "AK", "juneau": "AK",
}


_VALID_STATE_CODES: frozenset[str] = frozenset(_STATE_NAME_TO_CODE.values())


def city_to_state_code(city_name: str) -> str | None:
    """Return 2-letter state code for a well-known city, or None if not found.

    Unknown cities return None (the caller then relies on the Extractor LLM, or
    asks the user) — we must NOT guess. The suffix-strip only fires when an
    explicit separator precedes a VALID state code (e.g. "Tinytown, FL"), so we
    never misread the trailing two letters of an arbitrary word as a state.
    """
    if not city_name:
        return None
    key = city_name.strip().lower()
    # Direct lookup
    if key in _CITY_TO_STATE:
        return _CITY_TO_STATE[key]
    # Strip an explicit state suffix like "City, FL" / "City (FL)" / "City FL" —
    # only when a real separator precedes a recognized 2-letter state code.
    m = re.match(r'^.+?[\s,(]+\(?([A-Za-z]{2})\)?$', city_name.strip())
    if m:
        code = m.group(1).upper()
        if code in _VALID_STATE_CODES:
            return code
    # Partial prefix match against known cities (e.g. "Fort Laud" → Fort Lauderdale)
    for city_key, code in _CITY_TO_STATE.items():
        if city_key.startswith(key) and len(key) >= 4:
            return code
    return None


def property_state_code(portfolio: dict) -> str:
    """Normalize property_state to a 2-letter code when possible."""
    raw = str(portfolio.get("property_state") or "").strip()
    if not raw:
        return ""
    if len(raw) == 2:
        return raw.upper()
    low = raw.lower().replace(".", "")
    if low in _STATE_NAME_TO_CODE:
        return _STATE_NAME_TO_CODE[low]
    for name, code in _STATE_NAME_TO_CODE.items():
        if name in low or low in name:
            return code
    return raw.upper()


def normalize_property_state_value(value: str) -> str:
    code = property_state_code({"property_state": value})
    return code or value.strip()


def normalize_geo_slot_value(slot_id: str, value: str, portfolio: dict) -> str:
    """Normalize a free-text geo sub-field value to the nearest chip code."""
    state = property_state_code(portfolio)
    return normalize_geo_value(slot_id, value, state)


SLOT_DEFS: list[dict] = [
    # ── Section 1: Borrower & Property ────────────────────────────────────
    {
        "id": "citizenship", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Citizenship",
        "options": [
            {"code": "us_citizen",          "label": "US Citizen"},
            {"code": "foreign_national",     "label": "Foreign National"},
            {"code": "non_perm_resident",    "label": "Non-Permanent Resident Alien"},
            {"code": "perm_resident",        "label": "Permanent Resident Alien"},
            {"code": "daca",                 "label": "DACA"},
            {"code": "itin",                 "label": "ITIN"},
        ],
        "prompt": "What's the borrower's citizenship status?",
        "hint": None, "trigger": None,
    },
    {
        "id": "visa_category", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Visa Category",
        "options": [
            {"code": "employment",           "label": "Employment Visa"},
            {"code": "treaty_investor",      "label": "Investor / Treaty Visa"},
            {"code": "intracompany",         "label": "Intracompany Transfer"},
            {"code": "extraordinary",        "label": "Extraordinary Ability / Professional"},
            {"code": "religious_diplomatic", "label": "Religious / Diplomatic / Special"},
            {"code": "other",                "label": "Other / Not Listed"},
        ],
        "prompt": "Which category of visa?",
        "hint": None,
        "trigger": lambda p: p.get("citizenship") == "non_perm_resident",
    },
    {
        "id": "visa_type", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Visa Type",
        "options": [
            {"code": "E-1", "label": "E-1"}, {"code": "E-2", "label": "E-2"},
            {"code": "E-3", "label": "E-3"}, {"code": "H1-B", "label": "H-1B"},
            {"code": "L-1", "label": "L-1"}, {"code": "O-1", "label": "O-1"},
            {"code": "TN-NAFTA", "label": "TN/NAFTA"}, {"code": "EB-5", "label": "EB-5"},
            {"code": "other", "label": "Other / Not in list"},
        ],
        "prompt": "Which work visa? Eligibility varies by visa type.",
        "hint": None,
        "trigger": lambda p: (
            p.get("citizenship") == "non_perm_resident"
            and p.get("visa_category") not in (None, "", "other")
        ),
    },
    {
        "id": "occupancy", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Occupancy",
        "options": [
            {"code": "primary_residence",   "label": "Primary Residence"},
            {"code": "second_home",         "label": "Second Home"},
            {"code": "investment_property", "label": "Investment Property"},
        ],
        "prompt": "What's the intended occupancy?",
        "hint": None, "trigger": None,
    },
    {
        "id": "loan_purpose", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Loan Purpose",
        "options": [
            {"code": "purchase",   "label": "Purchase"},
            {"code": "rate_term",  "label": "Rate & Term Refinance"},
            {"code": "cash_out",   "label": "Cash-Out Refinance"},
        ],
        "prompt": "What's the loan purpose?",
        "hint": None, "trigger": None,
    },
    {
        "id": "property_type", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Property Type",
        "options": [
            {"code": "single_family",         "label": "Single Family"},
            {"code": "pud",                   "label": "PUD"},
            {"code": "townhouse",             "label": "Townhouse"},
            {"code": "condo_warrantable",     "label": "Condominium (Warrantable)"},
            {"code": "condo_non_warrantable", "label": "Condominium (Non-Warrantable)"},
            {"code": "condotel",              "label": "Condotel"},
            {"code": "two_to_four_family",    "label": "2-4 Unit"},
            {"code": "five_to_eight_unit",    "label": "5-8 Unit"},
            {"code": "mixed_use",             "label": "Mixed-Use"},
            {"code": "manufactured_home",     "label": "Manufactured Home"},
            {"code": "cooperative",           "label": "Cooperative"},
        ],
        "prompt": "What type of property?",
        "hint": None, "trigger": None,
    },
    {
        "id": "property_value", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "Property Value",
        "options": [], "prompt": "What's the property value or purchase price?",
        "hint": "e.g. 850000", "trigger": None,
    },
    {
        "id": "loan_amount", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "Loan Amount",
        "options": [], "prompt": "What's the loan amount, down payment, or LTV?",
        "hint": "Enter a loan amount (e.g. 680000), a down payment (e.g. 20% down), or an LTV (e.g. 75%)",
        "trigger": None,
    },
    {
        "id": "cash_in_hand", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "Cash-Out Target",
        "options": [], "prompt": "Target cash-in-hand amount from this refinance?",
        "hint": "e.g. 75000",
        "trigger": lambda p: p.get("loan_purpose") == "cash_out",
    },
    {
        "id": "ltv", "section": 1, "priority": "essential",
        "kind": "number", "sidebar_label": "LTV",
        "options": [], "prompt": "What's the LTV?",
        "hint": "1–100", "trigger": None,
    },
    {
        "id": "cltv", "section": 1, "priority": "essential",
        "kind": "number", "sidebar_label": "CLTV",
        "options": [], "prompt": "What's the combined CLTV?",
        "hint": "1–100",
        "trigger": lambda p: uses_cltv_leverage(p),
    },
    {
        "id": "fico", "section": 1, "priority": "essential",
        "kind": "number", "sidebar_label": "Decision Credit Score",
        "options": [], "prompt": "What's the decision FICO / credit score?",
        "hint": "300–850", "trigger": _needs_fico,
    },
    {
        "id": "lien_position", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Lien Position",
        "options": [
            {"code": "first_lien_only",       "label": "First Lien Only"},
            {"code": "second_lien",           "label": "Second Lien (Standalone HELOC / HELOAN)"},
            {"code": "second_lien_piggyback", "label": "Second Lien (Piggyback — closes with first)"},
        ],
        "prompt": "Is this a first lien, a standalone second lien, or a piggyback second lien?",
        "hint": None, "trigger": None,
    },
    {
        "id": "second_lien_product", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "2nd Lien Product",
        "options": [
            {"code": "heloc",  "label": "HELOC"},
            {"code": "heloan", "label": "HELOAN / Closed-End Second"},
        ],
        "prompt": "Is this a HELOC or a HELOAN (closed-end second)?",
        "hint": None,
        "trigger": lambda p: p.get("lien_position") == "second_lien",
    },
    {
        "id": "heloc_draw_years", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Draw Period",
        "options": [
            {"code": "2", "label": "2 years"},
            {"code": "3", "label": "3 years"},
            {"code": "5", "label": "5 years"},
        ],
        "prompt": "What draw period for the HELOC — 2, 3, or 5 years?",
        "hint": None,
        "trigger": lambda p: (
            p.get("lien_position") == "second_lien"
            and p.get("second_lien_product") == "heloc"
        ),
    },
    {
        "id": "heloc_initial_draw", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "Initial Draw",
        "options": [],
        "prompt": "How much is the initial draw on the HELOC?",
        "hint": "Dollar amount drawn at closing",
        "trigger": lambda p: (
            p.get("lien_position") == "second_lien"
            and p.get("second_lien_product") == "heloc"
        ),
    },
    {
        "id": "existing_first_lien", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "First Lien Balance",
        "options": [], "prompt": "What's the balance on the existing first lien?",
        "hint": "Dollar amount",
        "trigger": lambda p: (
            p.get("lien_position") in ("second_lien", "second_lien_piggyback")
            # Labels spec: the payoff balance is also REQUIRED on first-lien refis.
            or (
                p.get("lien_position") == "first_lien_only"
                and p.get("loan_purpose") in ("rate_term", "cash_out")
            )
        ),
    },
    {
        "id": "existing_second_lien", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Existing 2nd Lien",
        "options": [
            {"code": "None",                                    "label": "None"},
            {"code": "Yes — needs subordination",               "label": "Yes – needs subordination"},
            {"code": "Yes — being paid off in this transaction","label": "Yes – being paid off"},
        ],
        "prompt": "Is there an existing second lien on this property?",
        "hint": None,
        "trigger": lambda p: (
            p.get("lien_position") == "first_lien_only"
            and p.get("loan_purpose") in ("rate_term", "cash_out")
        ),
    },
    {
        "id": "existing_second_lien_balance", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "2nd Lien Balance",
        "options": [], "prompt": "What's the balance on the existing second lien?",
        "hint": "Dollar amount",
        "trigger": lambda p: p.get("existing_second_lien") == "Yes — needs subordination",
    },
    {
        "id": "existing_mortgage_upb", "section": 1, "priority": "essential",
        "kind": "currency", "sidebar_label": "Existing Mortgage Balance",
        "options": [], "prompt": "What's the existing mortgage balance being refinanced?",
        "hint": "Dollar amount",
        "trigger": lambda p: (
            p.get("lien_position") == "first_lien_only"
            and p.get("loan_purpose") in ("rate_term", "cash_out")
        ),
    },
    {
        "id": "first_time_homebuyer", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "First-Time Homebuyer",
        "options": [
            {"code": "Yes", "label": "Yes"},
            {"code": "No",  "label": "No"},
        ],
        "prompt": "Is the borrower a first-time homebuyer?",
        "hint": None,
        "trigger": lambda p: (
            p.get("occupancy") in ("primary_residence", "second_home")
            and p.get("loan_purpose") == "purchase"
        ),
    },
    {
        "id": "first_time_investor", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "First-Time Investor",
        "options": [
            {"code": "Yes", "label": "Yes"},
            {"code": "No",  "label": "No"},
        ],
        "prompt": "Is this the borrower's first investment property?",
        "hint": None,
        "trigger": lambda p: (
            p.get("occupancy") == "investment_property"
            and p.get("loan_purpose") == "purchase"
        ),
    },
    {
        "id": "established_primary_res", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Established Primary Residence",
        "options": [
            {"code": "Yes", "label": "Yes"},
            {"code": "No",  "label": "No"},
        ],
        "prompt": "Does the borrower have an established primary residence of their own?",
        "hint": None,
        "trigger": lambda p: (
            p.get("occupancy") == "investment_property"
            and (p.get("first_time_homebuyer") == "Yes" or p.get("first_time_investor") == "Yes")
        ),
    },
    {
        "id": "investment_income_path", "section": 1, "priority": "essential",
        "kind": "enum", "sidebar_label": "Qualification Path",
        "options": [
            {"code": "income", "label": "Personal income (documentation)"},
            {"code": "dscr",   "label": "DSCR / rental income"},
        ],
        "prompt": "How will this investment property be qualified — personal income or DSCR/rental cash flow?",
        "hint": None,
        "trigger": lambda p: (
            p.get("occupancy") == "investment_property"
            # Full Documentation is always income path — no need to ask
            and p.get("doc_type") != "full_doc"
            and not (("5-9" in (p.get("property_type") or "").lower())
                     or ("5-8" in (p.get("property_type") or "").lower()))
        ),
    },

    # ── Section 2: Docs & Financials ─────────────────────────────────────
    {
        "id": "doc_type", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Doc Type",
        "options": [
            {"code": "full_doc",           "label": "Full Documentation"},
            {"code": "bank_stmt_12_or_24", "label": "Bank Statements (12 or 24 Months)"},
            {"code": "bank_stmt_business", "label": "Bank Statements (Business)"},
            {"code": "pl_only",            "label": "P&L Only"},
            {"code": "pl_2mo_bs",          "label": "P&L with 2 Month Bank Statement"},
            {"code": "asset_util",         "label": "Asset Utilization"},
            {"code": "asset_qualifier",    "label": "Asset Qualifier"},
            {"code": "1099",               "label": "1099"},
            {"code": "wvoe",               "label": "WVOE Only"},
        ],
        "prompt": "How will income be documented?",
        "hint": None,
        "trigger": lambda p: not _is_dscr_path(p),
    },
    {
        "id": "doc_timeframe", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Doc Timeframe",
        "options": [
            # Labels match the frontend display (formatDocumentationTimeframeDisplay)
            # so chat capture pills and the sidebar read identically.
            {"code": "12", "label": "12-month"},
            {"code": "24", "label": "24-month"},
        ],
        "prompt": "What documentation timeframe applies?",
        "hint": "12 or 24 (1-year or 2-year for tax returns / 1099s)",
        "trigger": _doc_timeframe_applies,
    },
    {
        "id": "bank_stmt_source", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Bank Stmt Source",
        "options": [
            {"code": "personal", "label": "Personal"},
            {"code": "business", "label": "Business"},
            {"code": "both", "label": "Both"},
        ],
        "prompt": "Personal or business bank statements?",
        "hint": None,
        "trigger": lambda p: p.get("doc_type") in ("bank_stmt_12_or_24", "bank_stmt_business"),
    },
    {
        "id": "self_employment_years", "section": 2, "priority": "essential",
        "kind": "number", "sidebar_label": "Self-Employment Years",
        "options": [], "prompt": "How many years self-employed? (2+ is standard)",
        "hint": "e.g. 3",
        "trigger": lambda p: p.get("doc_type") in (
            "bank_stmt_12_or_24", "bank_stmt_business", "pl_only", "pl_2mo_bs", "1099"
        ),
    },
    {
        "id": "estimated_dti", "section": 2, "priority": "essential",
        "kind": "number", "sidebar_label": "DTI",
        "options": [], "prompt": "What's the estimated DTI?",
        "hint": "0–75, e.g. 43",
        "trigger": lambda p: not _is_dscr_path(p),
    },
    {
        "id": "reserves_months", "section": 2, "priority": "essential",
        "kind": "number", "sidebar_label": "Months of Reserves",
        "options": [], "prompt": "How many months of reserves are available (PITIA)?",
        "hint": "e.g. 6",
        "trigger": None,
    },
    {
        "id": "assets", "section": 2, "priority": "optional",
        "kind": "currency", "sidebar_label": "Liquid Assets",
        "options": [], "prompt": "Roughly how much in liquid assets does the borrower have?",
        "hint": "e.g. 150000", "trigger": None,
    },
    {
        "id": "dscr", "section": 2, "priority": "essential",
        "kind": "number", "sidebar_label": "DSCR",
        "options": [], "prompt": "What's the DSCR — gross rent divided by PITIA?",
        "hint": "e.g. 1.15",
        "trigger": _is_dscr_path,
    },
    {
        "id": "rental_type", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Rental Type",
        "options": [
            {"code": "Long-term rental",  "label": "Long-term rental"},
            {"code": "Short-term rental", "label": "Short-term rental"},
        ],
        "prompt": "Is the rental income long-term or short-term?",
        "hint": None,
        "trigger": _is_dscr_path,
    },
    {
        "id": "prepayment_terms", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Prepayment",
        "options": [
            {"code": "5 Year",     "label": "5 Year"},
            {"code": "4 Year",     "label": "4 Year"},
            {"code": "3 Year",     "label": "3 Year"},
            {"code": "2 Year",     "label": "2 Year"},
            {"code": "1 Year",     "label": "1 Year"},
            {"code": "No Penalty", "label": "No Penalty"},
        ],
        "prompt": "Any maximum prepayment penalty term?",
        "hint": None,
        "trigger": lambda p: p.get("occupancy") == "investment_property",
    },
    {
        "id": "prepay_stepdown", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Stepdown Prepay",
        "options": [
            {"code": "Yes",          "label": "Yes — step-down structure"},
            {"code": "No",           "label": "No"},
            {"code": "No Preference","label": "No preference"},
        ],
        "prompt": "Step-down prepay? (Penalty reduces each year over the lock period.)",
        "hint": None,
        "trigger": lambda p: (
            p.get("occupancy") == "investment_property"
            and p.get("prepayment_terms") in ("3 Year", "4 Year", "5 Year")
        ),
    },
    {
        "id": "vacant_property", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Vacant Property",
        "options": [
            {"code": "Yes", "label": "Yes — currently vacant"},
            {"code": "No",  "label": "No — tenant-occupied or owner-occupied"},
        ],
        "prompt": "Is the property currently vacant?",
        "hint": "Vacant DSCR refinances are generally ineligible — exceptions for recent rehab.",
        "trigger": lambda p: (
            _is_dscr_path(p) and p.get("loan_purpose") in ("rate_term", "cash_out")
        ),
    },
    {
        "id": "recently_rehabbed", "section": 2, "priority": "essential",
        "kind": "enum", "sidebar_label": "Recently Rehabbed",
        "options": [
            {"code": "Yes", "label": "Yes — within 6 months"},
            {"code": "No",  "label": "No"},
        ],
        "prompt": "Was the property recently constructed or rehabbed (within 6 months)?",
        "hint": None,
        "trigger": lambda p: (
            _is_dscr_path(p)
            and p.get("loan_purpose") in ("rate_term", "cash_out")
            and p.get("vacant_property") == "Yes"
        ),
    },
    {
        "id": "interest_only", "section": 2, "priority": "optional",
        "kind": "enum", "sidebar_label": "Interest-Only",
        "options": [
            {"code": "yes", "label": "Yes — IO"},
            {"code": "no", "label": "No"},
        ],
        "prompt": "Interested in interest-only? Lowers monthly payment but caps LTV.",
        "hint": None, "trigger": None,
    },
    {
        "id": "gift_funds_pct", "section": 2, "priority": "optional",
        "kind": "number", "sidebar_label": "Gift Funds %",
        "options": [], "prompt": "Any down payment coming from a gift? What percentage?",
        "hint": "0–100",
        "trigger": lambda p: p.get("occupancy") in ("primary_residence", "second_home"),
    },

    # ── Section 3: Location ───────────────────────────────────────────────
    # property_state is always asked; the sub-geo slots are essential-but-triggered
    # (their trigger fires on the chosen state) so the Asker asks for them
    # immediately after the state is filled.
    {
        "id": "property_state", "section": 3, "priority": "essential",
        "kind": "text", "sidebar_label": "State",
        "options": [],
        "prompt": "Give us some details about the property location — state, city or county if known.",
        "hint": "e.g. Miami-Dade County, FL or Dallas, TX", "trigger": None,
    },
    {
        "id": "state_county", "section": 3, "priority": "essential",
        "kind": "text", "sidebar_label": "County",
        "options": [],
        "prompt": "Which county is the property in?",
        "hint": "Search or type the county name",
        "trigger": lambda p: bool(property_state_code(p)),
    },
    {
        "id": "state_city", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Paterson?",
        "options": [
            {"code": "Paterson", "label": "Paterson"},
            {"code": "Other Passaic County", "label": "Other Passaic County"},
        ],
        "prompt": "Is the property in Paterson, or elsewhere in Passaic County?",
        "hint": None,
        "trigger": lambda p: property_state_code(p) == "NJ"
        and _norm_county_name(p.get("state_county") or "") == "passaic",
    },
    {
        "id": "state_borough", "section": 3, "priority": "essential",
        "kind": "text", "sidebar_label": "Borough",
        "options": [],
        "prompt": "Which borough is the property in?",
        "hint": "NYC borough, Orange County, or Other upstate / Long Island.",
        "trigger": lambda p: False,
    },
    {
        "id": "state_zip", "section": 3, "priority": "essential",
        "kind": "number", "sidebar_label": "ZIP Code",
        "options": [],
        "prompt": "What is the property's ZIP code?",
        "hint": "e.g. 19103",
        "trigger": lambda p: property_state_code(p) == "PA"
        and _norm_county_name(p.get("state_county") or "") == "philadelphia",
    },
    {
        "id": "is_in_indianapolis", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Indianapolis?",
        "options": [
            {"code": "Indianapolis", "label": "Indianapolis"},
            {"code": "Other Marion County", "label": "Other Marion County"},
        ],
        "prompt": "Is the property in Indianapolis, or elsewhere in Marion County?",
        "hint": None,
        "trigger": lambda p: property_state_code(p) == "IN"
        and _norm_county_name(p.get("state_county") or "") == "marion",
    },
    {
        "id": "is_in_baltimore", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Baltimore City?",
        "options": [
            {"code": "Baltimore City", "label": "Baltimore City"},
            {"code": "Other Baltimore County", "label": "Other Baltimore County"},
        ],
        "prompt": "Is the property in Baltimore City, or elsewhere in Baltimore County?",
        "hint": None,
        "trigger": lambda p: property_state_code(p) == "MD"
        and _norm_county_name(p.get("state_county") or "") == "baltimore",
    },
    {
        "id": "is_in_philadelphia", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "In Philadelphia?",
        "options": [{"code": "Yes", "label": "Yes"}, {"code": "No", "label": "No"}],
        "prompt": "Is the property located within Philadelphia city limits?",
        "hint": None,
        "trigger": lambda p: False,
    },
    {
        "id": "is_in_memphis", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Memphis?",
        "options": [
            {"code": "Memphis", "label": "Memphis"},
            {"code": "Other Shelby County", "label": "Other Shelby County"},
        ],
        "prompt": "Is the property in Memphis, or elsewhere in Shelby County?",
        "hint": None,
        "trigger": lambda p: property_state_code(p) == "TN"
        and _norm_county_name(p.get("state_county") or "") == "shelby",
    },
    {
        "id": "is_in_lubbock", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Lubbock?",
        "options": [
            {"code": "Lubbock", "label": "Lubbock"},
            {"code": "Other Lubbock County", "label": "Other Lubbock County"},
        ],
        "prompt": "Is the property in Lubbock, or elsewhere in Lubbock County?",
        "hint": None,
        "trigger": lambda p: property_state_code(p) == "TX"
        and _norm_county_name(p.get("state_county") or "") == "lubbock",
    },
    {
        "id": "hi_lava_zone", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "HI Lava Zone",
        "options": [
            {"code": "Zone 1",   "label": "Zone 1"},
            {"code": "Zone 2",   "label": "Zone 2"},
            {"code": "Zone 3-9", "label": "Zone 3-9 (lower risk)"},
        ],
        "prompt": "Which Hawaii lava zone is the property in?",
        "hint": "Zones 1 and 2 are generally ineligible for financing.",
        "trigger": lambda p: property_state_code(p) == "HI",
    },
    {
        "id": "acreage", "section": 3, "priority": "essential",
        "kind": "number", "sidebar_label": "Acreage",
        "options": [], "prompt": "Acreage? Most programs cap at 2–20 acres.",
        "hint": "e.g. 1.5",
        "trigger": lambda p: p.get("property_type") in ("single_family", "pud", "townhouse"),
    },
    {
        "id": "rural_property", "section": 3, "priority": "essential",
        "kind": "enum", "sidebar_label": "Rural Property",
        "options": [
            {"code": "yes", "label": "Yes"},
            {"code": "no", "label": "No"},
            {"code": "unsure", "label": "Not sure"},
        ],
        "prompt": "Is this a rural property (CFPB rural/underserved designation)?",
        "hint": None,
        # Ask once we know the property type (rural/underserved is property-type-dependent)
        "trigger": lambda p: _is_filled(p, "property_type"),
    },
    {
        "id": "declining_market", "section": 3, "priority": "optional",
        "kind": "enum", "sidebar_label": "Declining Market",
        "options": [
            {"code": "no_unknown", "label": "No / Unknown"},
            {"code": "yes", "label": "Yes — declining"},
        ],
        "prompt": "Is the property in a declining market? (Pre-applies a ~5% LTV reduction.)",
        "hint": None, "trigger": None,
    },
    {
        "id": "property_condition", "section": 3, "priority": "optional",
        "kind": "enum", "sidebar_label": "Property Condition",
        "options": [
            {"code": "good", "label": "Good"},
            {"code": "fair", "label": "Fair"},
            {"code": "c5", "label": "C5 — needs review"},
            {"code": "c6", "label": "C6 — ineligible"},
        ],
        "prompt": "Anything to flag about property condition?",
        "hint": None, "trigger": None,
    },

    # ── Section 4: Credit & Housing History ───────────────────────────────
    {
        "id": "credit_event_category", "section": 4, "priority": "essential",
        "kind": "enum", "sidebar_label": "Credit Events",
        "options": [
            {"code": "None",       "label": "None"},
            {"code": "BK",         "label": "Bankruptcy (Ch 7 / 11 / 13)"},
            {"code": "FC",         "label": "Foreclosure"},
            {"code": "SS",         "label": "Short Sale"},
            {"code": "DIL",        "label": "Deed-in-Lieu"},
            {"code": "Pre-FC",     "label": "Pre-Foreclosure"},
            {"code": "Charge-Off", "label": "Mortgage Charge-Off"},
            {"code": "NOD",        "label": "Notice of Default"},
            {"code": "Mod",        "label": "Loan Modification"},
            {"code": "Forbearance","label": "Forbearance"},
            {"code": "Deferral",   "label": "Deferral"},
        ],
        "prompt": "Any credit events? If yes — what was the event and roughly how long ago did it happen?",
        "hint": "e.g. \"Bankruptcy Ch 7 discharged 4 years ago\" or \"Short sale 3 years ago\" — or say None",
        "trigger": None,
    },
    {
        "id": "credit_event_type", "section": 4, "priority": "essential",
        "kind": "enum", "sidebar_label": "Credit Event Type",
        "options": [
            {"code": "Ch. 7 discharged",   "label": "Ch. 7 Discharged"},
            {"code": "Ch. 13 discharged",  "label": "Ch. 13 Discharged"},
            {"code": "Ch. 13 dismissed",   "label": "Ch. 13 Dismissed"},
            {"code": "Foreclosure",        "label": "Foreclosure"},
            {"code": "Short sale",         "label": "Short Sale"},
            {"code": "Deed-in-lieu",       "label": "Deed-in-Lieu"},
            {"code": "Loan modification",  "label": "Loan Modification"},
            {"code": "Pre-foreclosure",    "label": "Pre-Foreclosure"},
            {"code": "Mortgage charge-off","label": "Mortgage Charge-Off"},
            {"code": "Notice of default",  "label": "Notice of Default"},
            {"code": "Forbearance",        "label": "Forbearance"},
            {"code": "Deferral",           "label": "Deferral"},
        ],
        "prompt": "What specific type of credit event?",
        "hint": None,
        "trigger": lambda p: p.get("credit_event_category") not in (None, "", "None"),
    },
    {
        "id": "years_since_event", "section": 4, "priority": "essential",
        "kind": "enum", "sidebar_label": "Years Since Event",
        "options": [
            {"code": "<1 year", "label": "<1 year"},
            {"code": "1-2 years", "label": "1–2 years"},
            {"code": "2-3 years", "label": "2–3 years"},
            {"code": "3-4 years", "label": "3–4 years"},
            {"code": "4-7 years", "label": "4–7 years"},
            {"code": "7+ years", "label": "7+ years"},
        ],
        "prompt": "How long ago was the credit event?",
        "hint": None,
        "trigger": lambda p: p.get("credit_event_category") not in (None, "", "None"),
    },
    {
        "id": "payment_history", "section": 4, "priority": "essential",
        "kind": "enum", "sidebar_label": "Housing History",
        "options": [
            {"code": "0x30", "label": "0×30×12"},
            {"code": "1x30", "label": "1×30×12"},
            {"code": "1x60", "label": "1×60×12"},
            {"code": "1x120","label": "1×120×12"},
        ],
        "prompt": "How has the housing payment history looked over the past 12 months?",
        "hint": None, "trigger": None,
    },
    {
        "id": "tradelines", "section": 4, "priority": "optional",
        "kind": "enum", "sidebar_label": "Tradelines",
        "options": [
            {"code": "three_twelve",   "label": "3+ active, 12+ mo"},
            {"code": "two_twentyfour", "label": "2+ active, 24+ mo"},
            {"code": "mortgage",       "label": "Mortgage 36+ mo"},
            {"code": "unsure",         "label": "Unsure"},
            {"code": "none",           "label": "None — non-traditional"},
        ],
        "prompt": "Any tradeline info? (Auto-waived for standard 3-bureau FICO borrowers.)",
        "hint": None, "trigger": None,
    },

    # ── Section 5: Transaction Conditions ─────────────────────────────────────
    {
        "id": "power_of_attorney", "section": 5, "priority": "essential",
        "kind": "enum", "sidebar_label": "Power of Attorney",
        "options": [
            {"code": "yes", "label": "Yes"},
            {"code": "no",  "label": "No"},
        ],
        "prompt": "Is a Power of Attorney (POA) being used for this transaction?",
        "hint": None,
        # Ask for all purchase and refinance scenarios once we have a loan purpose
        "trigger": lambda p: _is_filled(p, "loan_purpose"),
    },
    {
        "id": "non_arms_length", "section": 5, "priority": "essential",
        "kind": "enum", "sidebar_label": "Non-Arm's Length",
        "options": [
            {"code": "yes", "label": "Yes — related party"},
            {"code": "no",  "label": "No"},
        ],
        "prompt": "Is this a non-arm's length transaction (buyer and seller are related parties)?",
        "hint": None,
        "trigger": lambda p: _is_filled(p, "loan_purpose"),
    },
]

_SLOT_BY_ID: dict[str, dict] = {s["id"]: s for s in SLOT_DEFS}


# ---------------------------------------------------------------------------
# Catalog predicates (used by SLOT_DEFS triggers and the slot engine)
# ---------------------------------------------------------------------------

def _is_filled(portfolio: dict, slot_id: str) -> bool:
    val = portfolio.get(slot_id)
    if val is None or val == "" or val == []:
        return False
    return portfolio.get(f"{slot_id}_status", "pending") in ("filled", "inferred")


def _is_triggered(slot: dict, portfolio: dict) -> bool:
    trigger = slot.get("trigger")
    return trigger is None or trigger(portfolio)


def uses_cltv_leverage(portfolio: dict) -> bool:
    """Piggyback 2nd or 1st with a subordinating retained 2nd — separate LTV + CLTV fields."""
    if portfolio.get("lien_position") in ("second_lien", "second_lien_piggyback"):
        return True
    if portfolio.get("existing_second_lien") == "Yes — needs subordination":
        return True
    return False


# ===== Geo field catalog (folded from geo/config.py) =====

"""
Unified geographic follow-up configuration — single source of truth.

Drives:
  - Form-mode location sub-questions (frontend via /api/geo/config)
  - Chat intake geo slots (slot_engine)
  - Geo warnings / hard blocks (geo_evaluator → /api/geo/evaluate)
"""

from typing import Any

GEO_YES_NO_OPTIONS: list[dict[str, str]] = [
    {"code": "Yes", "label": "Yes"},
    {"code": "No", "label": "No"},
]

# slot_id → form field key (wizard camelCase)
SLOT_TO_FORM_KEY: dict[str, str] = {
    "state_county": "stateCounty",
    "state_city": "stateCity",
    "state_borough": "stateBorough",
    "state_zip": "stateZipCode",
    "is_in_indianapolis": "isInIndianapolis",
    "is_in_baltimore": "isInBaltimoreCity",
    "is_in_philadelphia": "isInPhiladelphia",
    "is_in_memphis": "isInMemphis",
    "is_in_lubbock": "isInLubbock",
}

FORM_KEY_TO_GEO_DATA_KEY: dict[str, str] = {
    "stateCounty": "county",
    "stateCity": "city",
    "stateBorough": "borough",
    "stateZipCode": "zipCode",
    "isInIndianapolis": "isInIndianapolis",
    "isInBaltimoreCity": "isInBaltimoreCity",
    "isInPhiladelphia": "isInPhiladelphia",
    "isInMemphis": "isInMemphis",
    "isInLubbock": "isInLubbock",
}

# City-vs-rest-of-county enums. Codes carry the LITERAL city name so the existing
# eligibility normaliser (`_normalise_form`) recognises them as the in-city signal
# (e.g. "Baltimore City" → is_in_baltimore_city). No geo-evaluation logic changes.
_GEO_MD_BALTIMORE = [
    {"code": "Baltimore City", "label": "Baltimore City"},
    {"code": "Other Baltimore County", "label": "Other Baltimore County"},
]
_GEO_IN_MARION = [
    {"code": "Indianapolis", "label": "Indianapolis"},
    {"code": "Other Marion County", "label": "Other Marion County"},
]
_GEO_NJ_PASSAIC = [
    {"code": "Paterson", "label": "Paterson"},
    {"code": "Other Passaic County", "label": "Other Passaic County"},
]
_GEO_TX_LUBBOCK = [
    {"code": "Lubbock", "label": "Lubbock"},
    {"code": "Other Lubbock County", "label": "Other Lubbock County"},
]
_GEO_TN_SHELBY = [
    {"code": "Memphis", "label": "Memphis"},
    {"code": "Other Shelby County", "label": "Other Shelby County"},
]

# Per-state follow-up field definitions. Each fires ONLY after State + County, and
# only when the chosen county matches `county_match` (the city is ambiguous inside
# that county). NY dropped — the county pick now carries the borough information.
GEO_STATE_FIELDS: dict[str, list[dict[str, Any]]] = {
    "IN": [
        {
            "slot_id": "is_in_indianapolis",
            "form_key": "isInIndianapolis",
            "county_match": "Marion County",
            "label": "Indianapolis?",
            "widget": "select",
            "required": True,
            "options": _GEO_IN_MARION,
            "prompt": "Is the property in Indianapolis, or elsewhere in Marion County?",
        },
    ],
    "MD": [
        {
            "slot_id": "is_in_baltimore",
            "form_key": "isInBaltimoreCity",
            "county_match": "Baltimore County",
            "label": "Baltimore City?",
            "widget": "select",
            "required": True,
            "options": _GEO_MD_BALTIMORE,
            "prompt": "Is the property in Baltimore City, or elsewhere in Baltimore County?",
        },
    ],
    "NJ": [
        {
            "slot_id": "state_city",
            "form_key": "stateCity",
            "county_match": "Passaic County",
            "label": "Paterson?",
            "widget": "select",
            "required": True,
            "options": _GEO_NJ_PASSAIC,
            "prompt": "Is the property in Paterson, or elsewhere in Passaic County?",
        },
    ],
    "PA": [
        {
            "slot_id": "state_zip",
            "form_key": "stateZipCode",
            "county_match": "Philadelphia County",
            "label": "Zip Code",
            "widget": "zip",
            "required": True,
            "options": [],
            "prompt": "What is the property's ZIP code?",
            "hint": "5-digit ZIP, e.g. 19103",
        },
    ],
    "TN": [
        {
            "slot_id": "is_in_memphis",
            "form_key": "isInMemphis",
            "county_match": "Shelby County",
            "label": "Memphis?",
            "widget": "select",
            "required": True,
            "options": _GEO_TN_SHELBY,
            "prompt": "Is the property in Memphis, or elsewhere in Shelby County?",
        },
    ],
    "TX": [
        {
            "slot_id": "is_in_lubbock",
            "form_key": "isInLubbock",
            "county_match": "Lubbock County",
            "label": "Lubbock?",
            "widget": "select",
            "required": True,
            "options": _GEO_TX_LUBBOCK,
            "prompt": "Is the property in Lubbock, or elsewhere in Lubbock County?",
        },
    ],
}


def states_with_geo_followup() -> list[str]:
    return sorted(GEO_STATE_FIELDS.keys())


def _geo_county_key(val: str) -> str:
    """Normalise a county label for `county_match` comparison ("Baltimore County"
    → "baltimore", "Baltimore City" → "baltimore city" — kept distinct)."""
    v = (val or "").strip().lower().replace("-", " ")
    for suffix in (" county", " counties", " parish", " borough"):
        if v.endswith(suffix):
            v = v[: -len(suffix)].strip()
    return v


def geo_field_applies_to_county(field: dict[str, Any], county: str) -> bool:
    """A field with no `county_match` applies state-wide; otherwise only when the
    selected county matches."""
    cm = str(field.get("county_match") or "").strip()
    if not cm:
        return True
    return _geo_county_key(cm) == _geo_county_key(county)


def get_state_fields(state: str) -> list[dict[str, Any]]:
    return list(GEO_STATE_FIELDS.get((state or "").strip().upper(), []))


def get_state_fields_for_county(state: str, county: str) -> list[dict[str, Any]]:
    """State follow-up fields filtered to the selected county (the new conditional
    trigger). Used by completion checks and the next-question walk."""
    return [f for f in get_state_fields(state) if geo_field_applies_to_county(f, county)]


def state_needs_geo_followup(state: str) -> bool:
    return (state or "").strip().upper() in GEO_STATE_FIELDS


def get_geo_config(state: str | None = None) -> dict[str, Any]:
    """Full or per-state config for API responses."""
    if state:
        st = state.strip().upper()
        return {"state": st, "fields": get_state_fields(st)}
    return {
        "followup_states": states_with_geo_followup(),
        "states": {st: get_state_fields(st) for st in GEO_STATE_FIELDS},
    }


def get_chips_for_slot(slot_id: str, state: str) -> list[dict[str, str]]:
    """Chip options for a geo slot in a given state."""
    for field in get_state_fields(state):
        if field.get("slot_id") == slot_id:
            opts = field.get("options") or []
            return [{"code": o["code"], "label": o["label"]} for o in opts]
    return []


def normalize_geo_value(slot_id: str, value: str, state: str) -> str:
    """Map free-text geo input to nearest chip code, or 'other'."""
    if not value:
        return value
    if slot_id == "state_county":
        return str(value).strip()
    chips = get_chips_for_slot(slot_id, state)
    if not chips:
        return value
    low = value.strip().lower()
    for chip in chips:
        if chip["code"].lower() == low or chip["label"].lower() == low:
            return chip["code"]
    for chip in chips:
        chip_key = chip["code"].lower().replace("_", " ")
        chip_label = chip["label"].lower().replace(" county", "").replace(" city", "").strip()
        if chip_key == low or chip_label == low:
            return chip["code"]
        if chip_key.startswith(low) and len(low) >= 3:
            return chip["code"]
    return "other"


def _norm_county_name(val: str) -> str:
    """Normalize county display names for geo inference."""
    v = (val or "").strip().lower().replace("-", " ")
    for suffix in (" county", " counties", " parish", " borough"):
        if v.endswith(suffix):
            v = v[: -len(suffix)].strip()
    return v


def infer_geo_followups_from_county(state: str, county: str) -> dict[str, str]:
    """Pre-fill ONLY the geo signals the county pick already determines, so the
    ambiguous city question still gets asked.

      - MD "Baltimore City" county → in city (separate selectable jurisdiction)
      - PA "Philadelphia County"   → consolidated city-county = in Philadelphia
        (keeps the is_in_philadelphia signal; the ZIP is still asked)

    All other (state, county) pairs return {} — either the city question fires
    (ambiguous county) or there's no follow-up at all (county carries the answer)."""
    st = (state or "").strip().upper()
    county_norm = _norm_county_name(county)
    if not st or not county_norm:
        return {}
    if st == "MD" and county_norm == "baltimore city":
        return {"is_in_baltimore": "Baltimore City"}
    if st == "PA" and county_norm == "philadelphia":
        return {"is_in_philadelphia": "Yes"}
    return {}


# ===== Portfolio → EligibilityRequest contract (the snake→camel translation) =====

def _parse_money(val: Any) -> float:
    try:
        return float(str(val or 0).replace(",", "").replace("$", "").replace("%", ""))
    except (ValueError, TypeError):
        return 0.0


_OCC_MAP = {
    "primary_residence":   "Primary Residence",
    "second_home":         "Second Home",
    "investment_property": "Investment Property",
}
_PURPOSE_MAP = {
    "purchase":  "Purchase",
    "rate_term": "Refinance",
    "cash_out":  "Cash-Out Refinance",
}
_CIT_MAP = {
    "us_citizen":        "US Citizen",
    "perm_resident":     "Permanent Resident Alien",
    "non_perm_resident": "Non-Permanent Resident Alien",
    "foreign_national":  "Foreign National",
    "daca":              "DACA",
    "itin":              "ITIN",
}
_PROP_TYPE_MAP = {
    "single_family":         "single_family",
    "pud":                   "pud",
    "townhouse":             "townhouse",
    "condo_warrantable":     "condo_warrantable",
    "condo_non_warrantable": "condo_non_warrantable",
    "condotel":              "condotel",
    "two_to_four_family":    "two_to_four_family",
    "five_to_eight_unit":    "five_to_eight_unit",
    "five_to_eight_family":  "five_to_eight_unit",
    "mixed_use":             "mixed_use",
    "manufactured_home":     "manufactured_home",
    "manufactured":          "manufactured_home",
    "cooperative":           "cooperative",
}


def portfolio_to_eligibility_request(
    portfolio: dict,
    scenario_notes: list | None = None,
) -> dict:
    """Convert an intake portfolio to an EligibilityRequest-compatible dict."""
    def _g(key: str) -> str:
        return str(portfolio.get(key) or "")

    # CLTV — explicit slot or computed from balances
    cltv = _g("cltv")
    lien_pos = _g("lien_position")
    existing_second = _g("existing_second_lien")
    if not cltv:
        try:
            pv = _parse_money(_g("property_value"))
            la = _parse_money(_g("loan_amount"))
            if pv > 0 and la > 0:
                if lien_pos == "second_lien_piggyback":
                    first = _parse_money(_g("existing_first_lien"))
                    cltv = str(round((first + la) / pv * 100, 2))
                elif existing_second == "Yes — needs subordination":
                    elb = _parse_money(_g("existing_second_lien_balance"))
                    cltv = str(round((la + elb) / pv * 100, 2))
        except (ValueError, ZeroDivisionError, TypeError):
            pass

    return {
        "citizenship": _CIT_MAP.get(_g("citizenship"), _g("citizenship")),
        "occupancy":   _OCC_MAP.get(_g("occupancy"),   _g("occupancy")),
        "loanPurpose": _PURPOSE_MAP.get(_g("loan_purpose"), _g("loan_purpose")),
        "propertyType": _PROP_TYPE_MAP.get(_g("property_type"), _g("property_type")),
        "valueSalesPrice": _g("property_value"),
        "loanAmount":      _g("loan_amount"),
        "ltv":             _g("ltv"),
        "cltv":            cltv,
        "decisionCreditScore": _g("fico"),
        "lienPosition":        _g("lien_position"),
        "isSecondLien":        "yes" if _g("lien_position") in {"second_lien", "second_lien_piggyback"} else "no",
        "primaryLoanPurpose":  _PURPOSE_MAP.get(_g("loan_purpose"), _g("loan_purpose")),
        "secondLienProduct":   _g("second_lien_product"),
        "helocDrawYears":      _g("heloc_draw_years"),
        "helocInitialDraw":    _g("heloc_initial_draw"),
        "cashInHandRequest":   _g("cash_in_hand"),
        # existing_mortgage_upb (refi payoff) is the same number under an older slot id.
        "existingFirstLien":   _g("existing_first_lien") or _g("existing_mortgage_upb"),
        "documentationType":   _g("doc_type"),
        "documentationTimeframe": _g("doc_timeframe"),
        "estimatedDti":        _g("estimated_dti"),
        "dscr":                _g("dscr"),
        "interestOnlyPref":    _g("interest_only"),
        "rentalType":          _g("rental_type"),
        "prepaymentTerms":     _g("prepayment_terms"),
        "state":               _g("property_state"),
        "stateCounty":         _g("state_county"),
        "stateCity":           _g("state_city"),
        "stateBorough":        _g("state_borough"),
        "stateZipCode":        _g("state_zip"),
        "isInBaltimoreCity":   _g("is_in_baltimore"),
        "isInIndianapolis":    _g("is_in_indianapolis"),
        "isInPhiladelphia":    _g("is_in_philadelphia"),
        "isInMemphis":         _g("is_in_memphis"),
        "isInLubbock":         _g("is_in_lubbock"),
        "firstTimeHomebuyer":  _g("first_time_homebuyer"),
        "firstTimeInvestor":   _g("first_time_investor"),
        "establishedPrimaryRes": _g("established_primary_res"),
        "qualificationPath":   _g("investment_income_path"),
        "creditEvent":         _g("credit_event_category"),
        "creditEventType":     _g("credit_event_type"),
        "yearsSinceEvent":     _g("years_since_event"),
        "paymentHistory":      _g("payment_history"),
        "hiLavaZone":        _g("hi_lava_zone"),
        "vacantProperty":    _g("vacant_property"),
        "recentlyRehabbed":  _g("recently_rehabbed"),
        "prepayStepdown":    _g("prepay_stepdown"),
        "visaCategory":      _g("visa_category"),
        "isRuralProperty":   _g("rural_property"),
        "powerOfAttorney":   "Yes" if _g("power_of_attorney") == "yes" else ("No" if _g("power_of_attorney") == "no" else ""),
        "nonArmsLength":     "Yes" if _g("non_arms_length") == "yes" else ("No" if _g("non_arms_length") == "no" else ""),
    }


# ---------------------------------------------------------------------------
# Planner utility: force combined / definitive overrides
# ---------------------------------------------------------------------------

