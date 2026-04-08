"""Tests for state.py backend flag.

get_backend() reads os.environ at CALL time, not at import time,
so no importlib.reload() is needed.
"""
import os


def test_get_backend_default(monkeypatch):
    monkeypatch.delenv("REFERENCE_BACKEND", raising=False)
    from core.state import get_backend
    assert get_backend() == "duckdb"


def test_get_backend_parquet(monkeypatch):
    monkeypatch.setenv("REFERENCE_BACKEND", "parquet")
    from core.state import get_backend
    assert get_backend() == "parquet"


def test_get_backend_dual(monkeypatch):
    monkeypatch.setenv("REFERENCE_BACKEND", "dual")
    from core.state import get_backend
    assert get_backend() == "dual"
