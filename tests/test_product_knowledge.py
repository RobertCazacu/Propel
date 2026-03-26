"""Tests for product_knowledge table CRUD."""
import json
import pytest
import duckdb
from unittest.mock import patch
from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path):
    """DuckDB in-memory via temp path pentru izolare teste."""
    db_path = tmp_path / "test.duckdb"
    with patch("core.reference_store_duckdb.DB_PATH", db_path):
        from core.reference_store_duckdb import ensure_schema, upsert_product_knowledge, get_product_knowledge
        ensure_schema()
        yield db_path, upsert_product_knowledge, get_product_knowledge


def test_upsert_and_retrieve_by_ean(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean="5901234123457",
        brand="Samsung",
        normalized_title="samsung galaxy s24 128gb negru",
        marketplace="emag_hu",
        offer_id="OFF-001",
        category="Telefoane",
        final_attributes={"Culoare": "Negru", "Capacitate": "128GB"},
        confidence=0.95,
        run_id="run-test-001",
    )
    result = get(ean="5901234123457")
    assert result is not None
    assert result["brand"] == "Samsung"
    assert result["final_attributes"]["Culoare"] == "Negru"
    assert result["confidence"] == 0.95


def test_retrieve_by_brand_title_fallback(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean=None,
        brand="Nike",
        normalized_title="nike air max 90 alb",
        marketplace="allegro",
        offer_id="OFF-002",
        category="Încălțăminte sport",
        final_attributes={"Culoare": "Alb", "Mărime": "42"},
        confidence=0.80,
        run_id="run-test-002",
    )
    result = get(ean=None, brand="Nike", normalized_title="nike air max 90 alb")
    assert result is not None
    assert result["final_attributes"]["Mărime"] == "42"


def test_upsert_updates_existing(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace="emag_hu",
        offer_id="OFF-003",
        category="Căști",
        final_attributes={"Culoare": "Negru"},
        confidence=0.70,
        run_id="run-v1",
    )
    # Upsert cu date mai noi
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace="allegro",
        offer_id="OFF-004",
        category="Căști",
        final_attributes={"Culoare": "Negru", "Conectivitate": "Bluetooth 5.2"},
        confidence=0.92,
        run_id="run-v2",
    )
    result = get(ean="1111111111111")
    assert result["confidence"] == 0.92
    assert "Conectivitate" in result["final_attributes"]


def test_no_result_for_unknown_ean(tmp_db):
    db_path, upsert, get = tmp_db
    result = get(ean="0000000000000")
    assert result is None
