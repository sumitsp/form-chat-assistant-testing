"""Query/index embedding helpers — must match Qdrant collection vector size.

Canonical home for embeddings (moved from backend/retrieval/embeddings.py,
which now re-exports from here). The OpenAI client comes from
backend.connections.openai so there is a single shared instance.
"""
from __future__ import annotations

from backend import config
from backend.connections.openai import get_openai

_st_model = None


def _sentence_transformer_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _st_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed one or more strings; output dimension must match config.VECTOR_SIZE."""
    if not texts:
        return []
    cleaned = [(t or " ").strip() or " " for t in texts]

    if config.EMBEDDING_PROVIDER == "openai":
        client = get_openai()
        resp = client.embeddings.create(
            model=config.OPENAI_EMBEDDING_MODEL,
            input=cleaned,
        )
        ordered = sorted(resp.data, key=lambda row: row.index)
        vectors = [row.embedding for row in ordered]
    else:
        model = _sentence_transformer_model()
        vectors = model.encode(cleaned, normalize_embeddings=True).tolist()

    for vec in vectors:
        if len(vec) != config.VECTOR_SIZE:
            raise RuntimeError(
                f"Embedding dimension {len(vec)} does not match VECTOR_SIZE={config.VECTOR_SIZE}. "
                f"Check EMBEDDING_PROVIDER / OPENAI_EMBEDDING_MODEL and Qdrant collection config."
            )
    return vectors


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]


def warmup_embeddings() -> None:
    """Load embedding backend once at API startup."""
    if config.EMBEDDING_PROVIDER == "openai":
        get_openai()
    else:
        _sentence_transformer_model()
