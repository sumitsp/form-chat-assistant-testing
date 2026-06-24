"""Outbound connections — one home for every external client.

DB engine, OpenAI clients, Qdrant clients, embedding helpers and logging
setup all live here so the rest of the backend imports a single, shared,
lazily-initialized instance instead of building its own.
"""
