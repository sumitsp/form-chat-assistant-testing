"""LoanPASS iframe embed configuration (secrets from environment only)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_loanpass_embed_config() -> dict[str, Any] | None:
    """
    Credentials for the iframe ``log-in`` postMessage (client-side per LoanPASS docs).
    Returns None when required env vars are missing.
    """
    origin = (os.getenv("LOANPASS_ORIGIN") or "https://app.loanpass.io").strip().rstrip("/")
    # Default tenant: https://app.loanpass.io/login/newpoint (email + password only on that page)
    client_access_id = (os.getenv("LOANPASS_CLIENT_ACCESS_ID") or "newpoint").strip()
    email = (os.getenv("LOANPASS_EMAIL") or "").strip()
    password = os.getenv("LOANPASS_PASSWORD") or ""
    if not email or not password:
        return None
    return {
        "origin": origin,
        "clientAccessId": client_access_id,
        "email": email,
        "password": password,
    }


def loanpass_configured() -> bool:
    return get_loanpass_embed_config() is not None
