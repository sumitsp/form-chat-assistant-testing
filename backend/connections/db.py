"""Single shared SQLAlchemy engine for MySQL.

Replaces the four ad-hoc engines that used to live in eligibility.py, rag.py,
session_store.py and apis/main.py. All of them now delegate here.
"""
from __future__ import annotations

from backend import config

_engine = None


def get_engine():
    """Return the process-wide SQLAlchemy engine (lazily created)."""
    global _engine
    if _engine is None:
        from sqlalchemy import create_engine

        _engine = create_engine(config.mysql_url(), echo=False, pool_pre_ping=True)
    return _engine
