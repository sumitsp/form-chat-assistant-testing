import os
import re
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Repo root (parent of backend/)
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

# Load .env deterministically so API startup cwd does not matter.
load_dotenv(ENV_PATH)

QDRANT_URL = os.getenv("QDRANT_URL", "http://187.77.186.41:6333").rstrip("/")
# Shared matrices collection
COLLECTION_NAME = "mortgage_matrices"
MATRIX_COLLECTION_NAME = "mortgage_matrices"

# Lender guideline collections (explicit, fixed names)
GUIDELINE_COLLECTIONS = {
    "nqm": "Denali_NQM_mortgage_guideline",
    "everest": "Everest_Deephaven_mortgage_guideline",
    "versus": "Summit_Versus_mortgage_guideline",
}

# Display labels for the UI/API
PROGRAM_DISPLAY_NAMES = {
    "nqm": "Denali (NQM)",
    "everest": "Everest (Deephaven)",
    "versus": "Summit (Verus)",
}

# Match keys to lender codes used in SQL + Qdrant matrix payload.
LENDER_CODE_BY_KEY = {
    "nqm": "NQM",
    "everest": "DHM",
    "versus": "VMC",
}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

NEWPOINT_DIR = REPO_ROOT / "Newpoint"
INPUT_DATA_DIR = REPO_ROOT / "input"

# MySQL connection
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "newpoint_mortgage")


def mysql_url() -> str:
    user = quote_plus(MYSQL_USER)
    password = quote_plus(MYSQL_PASSWORD)
    db = quote_plus(MYSQL_DATABASE)
    return (
        f"mysql+pymysql://{user}:{password}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{db}?charset=utf8mb4"
    )


# Lender/Investor slug map: folder name → canonical lender code (matches lenders.code)
INVESTOR_SLUG_MAP = {
    "denali (nqm)": "denali",
    "everest (deephaven)": "everest",
    "summit (verus)": "summit",
}

# Backward-compat alias
LENDER_SLUG_MAP = INVESTOR_SLUG_MAP

INVESTOR_FULL_NAME_MAP = {
    "denali": "NQM Funding, LLC",
    "everest": "Deephaven Mortgage LLC",
    "summit": "Verus Mortgage Capital",
}

# Backward-compat alias
LENDER_FULL_NAME_MAP = INVESTOR_FULL_NAME_MAP

# Embeddings for Qdrant (must match collection vector size at ingest time).
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    OPENAI_EMBEDDING_MODEL
    if EMBEDDING_PROVIDER == "openai"
    else "sentence-transformers/all-MiniLM-L6-v2",
)
VECTOR_SIZE = int(
    os.getenv(
        "VECTOR_SIZE",
        "1536" if EMBEDDING_PROVIDER == "openai" else "384",
    )
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# Logging level for backend.* loggers (see backend/connections/logging.py).
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()

# Log every /api/* request + response body to the terminal (dev visibility).
# ⚠️ Logs request/response bodies which include borrower data (PII) — set
# LOG_API_IO=0 in any shared/production environment.
LOG_API_IO = os.getenv("LOG_API_IO", "1").strip().lower() in {"1", "true", "yes", "on"}

# Write /api/* request + response payloads to logs/api_io_*.json (like LoanPASS pricing logs).
# OFF by default — enable with LOG_API_IO_FILE=1. ⚠️ Contains PII.
LOG_API_IO_TO_FILE = os.getenv("LOG_API_IO_FILE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOG_API_IO_FILE_KEEP = int(os.getenv("LOG_API_IO_FILE_KEEP", "50"))

# Eligibility trace FILE logging is a debug artifact written to logs/.
# OFF by default to prevent unbounded growth (the in-memory trace used by
# form-history / PDF is unaffected — only the .txt side-file is gated).
# Enable with ELIGIBILITY_TRACE=1.
ELIGIBILITY_TRACE_TO_FILE = os.getenv("ELIGIBILITY_TRACE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# When file logging is on, keep only the most recent N trace files in logs/.
ELIGIBILITY_TRACE_KEEP = int(os.getenv("ELIGIBILITY_TRACE_KEEP", "50"))

# LoanPASS pricing payload/response FILE logging (logs/loanpass_pricing_*.json).
# OFF by default — enable with LOANPASS_PRICING_LOG=1 to verify API hits.
LOANPASS_PRICING_LOG_TO_FILE = os.getenv("LOANPASS_PRICING_LOG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOANPASS_PRICING_LOG_KEEP = int(os.getenv("LOANPASS_PRICING_LOG_KEEP", "50"))

# Focus lock period sent to LoanPASS and shown in the pricing grid (days).
LOANPASS_FOCUS_LOCK_DAYS = int(os.getenv("LOANPASS_FOCUS_LOCK_DAYS", "30"))

# Pricing now lives in its OWN service (backend/pricing_app.py) so LoanPASS can be
# restarted independently. Set MOUNT_PRICING_INLINE=1 to also serve /api/loanpass/*
# from the main API (single-process dev convenience). Default OFF.
MOUNT_PRICING_INLINE = os.getenv("MOUNT_PRICING_INLINE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def program_slug(name: str) -> str:
    """
    Turn a program folder name into a stable slug.
    Example: "Denali (NQM)" -> "denali-nqm"
    """
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]+", " ", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "program"


def list_program_folders() -> list[Path]:
    if not NEWPOINT_DIR.is_dir():
        return []
    return sorted([p for p in NEWPOINT_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")])


def program_collection_name(program_folder_name: str) -> str:
    # One consolidated collection + one per program
    return f"{COLLECTION_NAME}__{program_slug(program_folder_name)}"


def program_collection_name_from_key(program_key: str) -> str:
    """
    Return the guideline collection for a known lender key.
    """
    key = program_slug(program_key)
    return GUIDELINE_COLLECTIONS.get(key, MATRIX_COLLECTION_NAME)


def matrix_collection_name() -> str:
    return MATRIX_COLLECTION_NAME


def lender_code_from_key(program_key: str) -> str | None:
    key = program_slug(program_key)
    return LENDER_CODE_BY_KEY.get(key)
