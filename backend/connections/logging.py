"""One logging home for the backend.

Today this owns the central logging *config* and the eligibility trace-file
gate + retention helpers. (The MySQL session/chat log writers in apis/main.py
move here when api.py is built in a later phase.)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> None:
    """Configure backend logging once, at app startup.

    Adds a root stream handler only if nothing else configured one (so it
    does not fight uvicorn's handlers), then sets the backend.* level.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logging.getLogger("backend").setLevel(level)


def trace_file_enabled() -> bool:
    """Whether eligibility trace .txt files should be written at all."""
    return bool(config.ELIGIBILITY_TRACE_TO_FILE)


def prune_trace_logs(logs_dir: Path, keep: int | None = None) -> None:
    """Keep only the most recent `keep` *.txt trace files in logs_dir."""
    keep = config.ELIGIBILITY_TRACE_KEEP if keep is None else keep
    try:
        files = sorted(
            logs_dir.glob("*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[max(keep, 0):]:
            stale.unlink(missing_ok=True)
    except Exception as exc:  # best-effort; never break the request
        logging.getLogger(__name__).warning("Trace log prune failed: %s", exc)


def _parse_io_body(raw: bytes | None) -> Any:
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", "replace").strip()
    except Exception:
        return {"_binary_bytes": len(raw)}
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def prune_api_io_logs(logs_dir: Path, keep: int | None = None) -> None:
    """Keep only the most recent `keep` api_io_*.json files in logs_dir."""
    keep = config.LOG_API_IO_FILE_KEEP if keep is None else keep
    try:
        files = sorted(
            logs_dir.glob("api_io_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[max(keep, 0):]:
            stale.unlink(missing_ok=True)
    except Exception as exc:
        logging.getLogger(__name__).warning("API IO log prune failed: %s", exc)


def write_api_io_log(
    *,
    method: str,
    path: str,
    query: str,
    status_code: int,
    duration_ms: float,
    request_body: bytes | None = None,
    response_body: bytes | None = None,
    content_type: str | None = None,
) -> None:
    """Persist one /api/* exchange to logs/api_io_<timestamp>.json."""
    if not config.LOG_API_IO_TO_FILE:
        return
    try:
        logs_dir = config.REPO_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        record: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "query": query or None,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 1),
            "content_type": content_type,
            "request": _parse_io_body(request_body),
            "response": _parse_io_body(response_body),
        }
        out = logs_dir / f"api_io_{ts}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        prune_api_io_logs(logs_dir)
    except Exception as exc:
        logging.getLogger(__name__).warning("API IO log write failed: %s", exc)
