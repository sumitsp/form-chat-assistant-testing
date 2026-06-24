from __future__ import annotations

from typing import Iterable


def filter_notes_for_summarize(notes: Iterable[str] | None) -> list[str]:
    """Return clean, de-duplicated note bullets for eligibility/rag display."""
    if not notes:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in notes:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out

