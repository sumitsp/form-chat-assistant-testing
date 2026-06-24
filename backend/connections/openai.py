"""Shared OpenAI client singletons (sync + async).

Replaces the inline ``OpenAI(...)`` / ``AsyncOpenAI(...)`` builds that were
scattered across rag.py, chat_asker.py, chat_extractor.py, embeddings.py and
apis/main.py.
"""
from __future__ import annotations

from backend import config

_sync = None
_async = None


def get_openai():
    """Return the shared synchronous OpenAI client (lazily created)."""
    global _sync
    if _sync is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is required to call OpenAI.")
        from openai import OpenAI

        _sync = OpenAI(api_key=config.OPENAI_API_KEY)
    return _sync


def get_async_openai():
    """Return the shared asynchronous OpenAI client (lazily created)."""
    global _async
    if _async is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is required to call OpenAI.")
        from openai import AsyncOpenAI

        _async = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return _async
