"""Shared Qdrant clients keyed by timeout profile.

Consolidates the three previously-scattered timeout configs:
  - "default" (120s) — heavy retrieval (was rag.get_qdrant_client)
  - "meta"    (10s)  — fast metadata calls, keeps /api/health responsive
  - "verify"  (15s)  — eligibility Layer 10 verification
"""
from __future__ import annotations

from backend import config

_PROFILE_TIMEOUTS = {"default": 120, "meta": 10, "verify": 15}
_clients: dict[str, object] = {}


def get_qdrant(profile: str = "default"):
    """Return the shared QdrantClient for the given timeout profile."""
    client = _clients.get(profile)
    if client is None:
        from qdrant_client import QdrantClient

        timeout = _PROFILE_TIMEOUTS.get(profile, 120)
        client = QdrantClient(url=config.QDRANT_URL, prefer_grpc=False, timeout=timeout)
        _clients[profile] = client
    return client
