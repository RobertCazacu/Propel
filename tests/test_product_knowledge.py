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
        marketplace_id="emag_hu",
        offer_id="OFF-001",
        category="Telefoane",
        final_attributes={"Culoare": "Negru", "Capacitate": "128GB"},
        confidence=0.95,
        run_id="run-test-001",
    )
    result = get(ean="5901234123457", marketplace_id="emag_hu")
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
        marketplace_id="allegro",
        offer_id="OFF-002",
        category="Încălțăminte sport",
        final_attributes={"Culoare": "Alb", "Mărime": "42"},
        confidence=0.80,
        run_id="run-test-002",
    )
    result = get(ean=None, brand="Nike", normalized_title="nike air max 90 alb", marketplace_id="allegro")
    assert result is not None
    assert result["final_attributes"]["Mărime"] == "42"


def test_upsert_updates_existing_same_marketplace(tmp_db):
    """Acelasi EAN + acelasi marketplace_id → update (nu duplicate)."""
    db_path, upsert, get = tmp_db
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace_id="emag_hu",
        offer_id="OFF-003",
        category="Căști",
        final_attributes={"Culoare": "Negru"},
        confidence=0.70,
        run_id="run-v1",
    )
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace_id="emag_hu",
        offer_id="OFF-003b",
        category="Căști",
        final_attributes={"Culoare": "Negru", "Conectivitate": "Bluetooth 5.2"},
        confidence=0.92,
        run_id="run-v2",
    )
    result = get(ean="1111111111111", marketplace_id="emag_hu")
    assert result["confidence"] == 0.92
    assert "Conectivitate" in result["final_attributes"]


def test_same_ean_different_marketplace_isolated(tmp_db):
    """Acelasi EAN pe marketplace-uri diferite → înregistrări separate, izolate."""
    db_path, upsert, get = tmp_db
    upsert(
        ean="2222222222222",
        brand="Nike",
        normalized_title="nike air max 90",
        marketplace_id="emag_hu",
        offer_id="OFF-HU",
        category="Încălțăminte",
        final_attributes={"Szín": "Fekete", "Méret": "42"},
        confidence=0.85,
        run_id="run-hu",
    )
    upsert(
        ean="2222222222222",
        brand="Nike",
        normalized_title="nike air max 90",
        marketplace_id="allegro",
        offer_id="OFF-AL",
        category="Obuwie",
        final_attributes={"Kolor": "Czarny", "Rozmiar": "42"},
        confidence=0.88,
        run_id="run-al",
    )
    result_hu = get(ean="2222222222222", marketplace_id="emag_hu")
    result_al = get(ean="2222222222222", marketplace_id="allegro")
    assert result_hu is not None and result_al is not None
    assert "Szín" in result_hu["final_attributes"]
    assert "Kolor" in result_al["final_attributes"]
    # Nu se amestecă
    assert "Kolor" not in result_hu["final_attributes"]
    assert "Szín" not in result_al["final_attributes"]


def test_no_result_for_unknown_ean(tmp_db):
    db_path, upsert, get = tmp_db
    result = get(ean="0000000000000", marketplace_id="emag_hu")
    assert result is None
