"""
Denali NQM — Seller guideline PDF → Qdrant (hierarchical TOC chunks).

Reads the underwriting guidelines PDF, walks the embedded PDF bookmarks (TOC)
in order, and builds one vector per substantive section:
  heading_level_1 / heading_level_2 [/ heading_level_3] + body text for that TOC entry only.

Collection: Denali_NQM_mortgage_guideline

Usage:
  python ingest/lenders/denali_nqm_guidelines_qdrant.py           # preview only (no Qdrant)
  python ingest/lenders/denali_nqm_guidelines_qdrant.py --dry-run  # same
  python ingest/lenders/denali_nqm_guidelines_qdrant.py --apply # embed + upsert to Qdrant
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import fitz
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


PDF_DEFAULT = (
    ROOT / "input" / "Denali (NQM)" / "Guidelines" / "Flex NonQM and DSCR Underwriting Guidelines_2 13 2026.pdf"
)
COLLECTION_NAME = "Denali_NQM_mortgage_guideline"
LENDER_CODE = "NQM"
EFFECTIVE_DATE = date(2026, 2, 13)
# Skip boilerplate pages before guideline body / TOC duplication (bookmark page is 1-based).
MIN_BOOKMARK_PAGE = 11


@dataclass
class BookmarkEntry:
    level: int
    title: str
    page: int  # 1-based, from PyMuPDF TOC
    y: float


def _normalize_title(raw: str) -> str:
    t = raw.replace("\ufffd", " ").replace("\uf6d4", "")
    t = " ".join(t.split())
    return t.strip()


def _effective_date_from_path(pdf_path: Path) -> str:
    m = re.search(r"[_\-\s]+(\d{1,2})[_\-\s]+(\d{1,2})[_\-\s]+(\d{4})\s*\.pdf", pdf_path.name, re.I)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            pass
    return EFFECTIVE_DATE.isoformat()


def load_bookmarks(doc: fitz.Document) -> list[BookmarkEntry]:
    raw = doc.get_toc(simple=False)
    out: list[BookmarkEntry] = []
    for row in raw:
        if len(row) < 3:
            continue
        level, title, page = row[0], row[1], row[2]
        title_clean = _normalize_title(str(title))
        if not title_clean:
            continue
        y = 0.0
        meta = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
        to = meta.get("to")
        if to is not None and hasattr(to, "y"):
            y = float(to.y)
        out.append(BookmarkEntry(level=int(level), title=title_clean, page=int(page), y=y))
    return out


def _extract_between(doc: fitz.Document, a: BookmarkEntry, b: BookmarkEntry | None) -> str:
    """
    Text from just below bookmark ``a`` until just before bookmark ``b`` (or end of PDF if ``b`` is None).
    PyMuPDF page numbers are 1-based in the TOC; ``page`` indices here match that convention.
    """
    sp, sy = a.page, float(a.y)
    margin_top = sy + 12.0
    margin_bottom = 6.0
    texts: list[str] = []

    if b is None:
        ep = doc.page_count
        end_y = doc[ep - 1].rect.height
    else:
        ep = max(int(b.page), sp)
        end_y = float(b.y)

    pn = sp
    while pn <= ep:
        page = doc[pn - 1]
        pw, ph = page.rect.width, page.rect.height

        if pn == sp and pn == ep:
            top = margin_top
            if b is None:
                bot = ph
            else:
                bot = max(top + 24.0, end_y - margin_bottom)
            r = fitz.Rect(0, top, pw, min(bot, ph))
            texts.append(page.get_text("text", clip=r).strip())
        elif pn == sp:
            r = fitz.Rect(0, margin_top, pw, ph)
            texts.append(page.get_text("text", clip=r).strip())
        elif pn == ep:
            if b is None:
                bot = ph
            else:
                bot = max(0.0, end_y - margin_bottom)
            r = fitz.Rect(0, 0, pw, min(bot, ph))
            texts.append(page.get_text("text", clip=r).strip())
        else:
            texts.append(page.get_text("text").strip())

        pn += 1

    merged = "\n".join(t for t in texts if t)
    return _clean_extracted_body(a.title, merged)


_LEADER_RE = re.compile(r"\.{4,}|…{3,}|_{4,}|─{4,}")
_PAGE_HEADER_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+", re.I)


def _clean_extracted_body(section_title: str, body: str) -> str:
    lines = [ln.strip() for ln in body.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln:
            continue
        if _LEADER_RE.search(ln):
            continue
        if _PAGE_HEADER_RE.match(ln):
            continue
        out.append(ln)

    tt = section_title.casefold().strip()
    while out and tt and out[0].casefold().replace(" ", "") == tt.replace(" ", ""):
        out.pop(0)

    return "\n".join(out).strip()


def build_section_chunks(doc: fitz.Document, bookmarks: list[BookmarkEntry]) -> list[dict[str, Any]]:
    # Outline order must match PDF bookmarks so parent headings line up with children.
    stack: dict[int, str] = {}
    chunks: list[dict[str, Any]] = []

    for i, entry in enumerate(bookmarks):
        if entry.page < MIN_BOOKMARK_PAGE:
            continue
        lvl = entry.level
        if lvl < 1:
            continue
        stack[lvl] = entry.title
        for k in list(stack.keys()):
            if k > lvl:
                del stack[k]

        if lvl == 1:
            continue

        segments = [stack[j] for j in range(1, lvl + 1) if j in stack]
        if len(segments) < 2:
            continue

        section_path = " / ".join(segments)
        hl1, hl2, hl3 = segments[0], segments[1] if len(segments) > 1 else None, None
        if len(segments) == 3:
            hl3 = segments[2]
        elif len(segments) > 3:
            hl3 = " / ".join(segments[2:])

        nxt = bookmarks[i + 1] if i + 1 < len(bookmarks) else None
        body = _extract_between(doc, entry, nxt)
        if len(body) < 40:
            continue

        chunks.append(
            {
                "heading_level_1": hl1,
                "heading_level_2": hl2,
                "heading_level_3": hl3,
                "section_path": section_path,
                "text": body,
                "page_start": entry.page,
            }
        )

    return chunks


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(size=config.VECTOR_SIZE, distance=models.Distance.COSINE, on_disk=True),
        )
    idx_fields = [
        ("chunk_id", models.PayloadSchemaType.KEYWORD),
        ("lender_id", models.PayloadSchemaType.KEYWORD),
        ("section_path", models.PayloadSchemaType.KEYWORD),
        ("heading_level_1", models.PayloadSchemaType.KEYWORD),
        ("heading_level_2", models.PayloadSchemaType.KEYWORD),
        ("heading_level_3", models.PayloadSchemaType.KEYWORD),
        ("effective_date", models.PayloadSchemaType.KEYWORD),
        ("source_file", models.PayloadSchemaType.KEYWORD),
        ("page_start", models.PayloadSchemaType.INTEGER),
    ]
    for field, schema in idx_fields:
        try:
            client.create_payload_index(collection_name=COLLECTION_NAME, field_name=field, field_schema=schema)
        except Exception:
            pass


def upsert_chunks(
    chunks: list[dict[str, Any]],
    *,
    pdf_name: str,
    effective_date: str,
    model: SentenceTransformer | None,
    client: QdrantClient | None,
    batch_size: int = 24,
) -> int:
    if not chunks:
        return 0
    assert model is not None and client is not None

    _ensure_collection(client)
    texts = [f"{c['section_path']}\n\n{c['text']}" for c in chunks]
    points: list[models.PointStruct] = []
    for i, chunk in enumerate(chunks):
        stext = texts[i]
        vec = model.encode(stext, normalize_embeddings=True).tolist()
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"nqm-guideline::{pdf_name}::{chunk['section_path']}"))

        hl3 = chunk.get("heading_level_3")
        payload: dict[str, Any] = {
            "chunk_id": cid,
            "lender_id": LENDER_CODE,
            "section_path": chunk["section_path"],
            "heading_level_1": chunk.get("heading_level_1"),
            "heading_level_2": chunk.get("heading_level_2"),
            "heading_level_3": hl3,
            "text": chunk["text"],
            "source_file": pdf_name,
            "effective_date": effective_date,
            "page_start": chunk.get("page_start"),
            "doc_kind": "denali_underwriting_guideline",
        }
        points.append(models.PointStruct(id=cid, vector=vec, payload=payload))

    for j in range(0, len(points), batch_size):
        client.upsert(collection_name=COLLECTION_NAME, points=points[j : j + batch_size], wait=True)

    return len(points)


def run(pdf_path: Path, do_apply: bool) -> None:
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    eff = _effective_date_from_path(pdf_path)
    doc = fitz.open(pdf_path)
    try:
        bms = load_bookmarks(doc)
        chunks = build_section_chunks(doc, bms)
    finally:
        doc.close()

    if not chunks:
        print("No substantive sections extracted (TOC empty or skipped).")
        return

    print(f"Extracted {len(chunks)} sections from {pdf_path.name}.")

    if not do_apply:
        for sample in chunks[:5]:
            print("---")
            print(sample["section_path"])
            print(sample["text"][:400], "...\n")

        remaining = len(chunks) - 5
        if remaining > 0:
            print(f"... and {remaining} more (use --apply to upsert)")
        return

    model = SentenceTransformer(config.EMBEDDING_MODEL)
    client = QdrantClient(url=config.QDRANT_URL)
    n = upsert_chunks(
        chunks,
        pdf_name=pdf_path.name,
        effective_date=eff,
        model=model,
        client=client,
    )
    print(f"Upserted {n} vectors into '{COLLECTION_NAME}' ({config.QDRANT_URL}).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Denali NQM underwriting guidelines PDF → Qdrant (TOC-aware chunks)")
    ap.add_argument("--pdf", type=Path, default=PDF_DEFAULT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("Use either --apply or --dry-run, not both.")
    run(args.pdf, do_apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    main()
