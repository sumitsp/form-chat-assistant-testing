from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.connections.db import get_engine


def ensure_testing_history_table() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS testing_history (
                    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL,
                    source_file_name VARCHAR(255) NOT NULL,
                    chunk_label VARCHAR(255) NOT NULL,
                    chunk_index INT NOT NULL,
                    chunk_total INT NOT NULL,
                    scenario_count INT NOT NULL,
                    details_json LONGTEXT NOT NULL,
                    pdf_blob LONGBLOB NOT NULL,
                    json_blob LONGBLOB NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_testing_history_created_at (created_at),
                    INDEX idx_testing_history_source_file (source_file_name)
                )
                """
            )
        )


def insert_testing_history_row(
    *,
    job_id: str,
    source_file_name: str,
    chunk_label: str,
    chunk_index: int,
    chunk_total: int,
    scenario_count: int,
    details_json: str,
    pdf_blob: bytes,
    json_blob: bytes,
) -> int:
    ensure_testing_history_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO testing_history (
                    job_id,
                    source_file_name,
                    chunk_label,
                    chunk_index,
                    chunk_total,
                    scenario_count,
                    details_json,
                    pdf_blob,
                    json_blob
                ) VALUES (
                    :job_id,
                    :source_file_name,
                    :chunk_label,
                    :chunk_index,
                    :chunk_total,
                    :scenario_count,
                    :details_json,
                    :pdf_blob,
                    :json_blob
                )
                """
            ),
            {
                "job_id": job_id,
                "source_file_name": source_file_name[:255],
                "chunk_label": chunk_label[:255],
                "chunk_index": int(chunk_index),
                "chunk_total": int(chunk_total),
                "scenario_count": int(scenario_count),
                "details_json": details_json,
                "pdf_blob": pdf_blob,
                "json_blob": json_blob,
            },
        )
        return int(result.lastrowid)


def list_testing_history(*, page: int, page_size: int) -> dict[str, Any]:
    ensure_testing_history_table()
    page = max(1, int(page))
    page_size = max(1, min(50, int(page_size)))
    offset = (page - 1) * page_size

    engine = get_engine()
    with engine.begin() as conn:
        total = int(conn.execute(text("SELECT COUNT(*) FROM testing_history")).scalar() or 0)
        rows = conn.execute(
            text(
                """
                SELECT
                    id,
                    source_file_name,
                    chunk_label,
                    scenario_count,
                    chunk_index,
                    chunk_total,
                    created_at
                FROM testing_history
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset},
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        m = dict(row._mapping)
        created_at = m.get("created_at")
        created_iso = (
            created_at.isoformat(sep=" ", timespec="seconds")
            if isinstance(created_at, datetime)
            else str(created_at or "")
        )
        items.append(
            {
                "id": int(m["id"]),
                "source_file_name": str(m["source_file_name"] or ""),
                "chunk_label": str(m["chunk_label"] or ""),
                "scenario_count": int(m["scenario_count"] or 0),
                "chunk_index": int(m["chunk_index"] or 0),
                "chunk_total": int(m["chunk_total"] or 0),
                "created_at": created_iso,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


def get_testing_history_artifact(history_id: int, kind: str) -> tuple[bytes, str]:
    ensure_testing_history_table()
    if kind not in {"pdf", "json"}:
        raise ValueError("kind must be 'pdf' or 'json'")
    col = "pdf_blob" if kind == "pdf" else "json_blob"
    ext = "pdf" if kind == "pdf" else "json"
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT source_file_name, chunk_label, {col} AS file_blob
                FROM testing_history
                WHERE id = :id
                """
            ),
            {"id": int(history_id)},
        ).fetchone()
    if not row:
        raise KeyError("history id not found")
    m = dict(row._mapping)
    file_name = str(m.get("source_file_name") or "batch")
    base = file_name.rsplit(".", 1)[0].strip() or "batch"
    chunk_label = str(m.get("chunk_label") or "").strip()
    safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in chunk_label).strip("-")
    if safe_label:
        out_name = f"{base}-{safe_label}.{ext}"
    else:
        out_name = f"{base}.{ext}"
    return bytes(m.get("file_blob") or b""), out_name


def delete_testing_history_row(history_id: int) -> bool:
    ensure_testing_history_table()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM testing_history WHERE id = :id"),
            {"id": int(history_id)},
        )
    return int(result.rowcount or 0) > 0
