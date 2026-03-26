"""
Tests for core/reference_store_duckdb.py
Run with: pytest tests/test_reference_store_duckdb.py -v
"""
import tempfile
from pathlib import Path

import pytest
import pandas as pd


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Returns a Path for a temp DuckDB file."""
    return tmp_path / "test_reference.duckdb"


@pytest.fixture
def sample_data():
    cats = pd.DataFrame({
        "id":        ["cat1", "cat2"],
        "emag_id":   ["cat1", "cat2"],
        "name":      ["Tricouri", "Pantaloni"],
        "parent_id": [None, None],
    })
    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat1"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [True, False],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1", "cat1", "cat1"],
        "characteristic_id":  ["10",   "10",   "20"],
        "characteristic_name":["Culoare", "Culoare", "Marime"],
        "value":              ["Rosu", "Albastru", "M"],
    })
    return cats, chars, vals


# ── Task 2: init_db + is_available ─────────────────────────────────────────────

def test_init_db_creates_file(tmp_db):
    from core.reference_store_duckdb import init_db
    assert not tmp_db.exists()
    init_db(tmp_db)
    assert tmp_db.exists()


def test_init_db_creates_all_tables(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db
    init_db(tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    expected = {"marketplaces", "categories", "characteristics",
                "characteristic_values", "import_runs", "import_issues"}
    assert expected.issubset(tables)


def test_init_db_upserts_emag_hu_marketplace(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db, EMAG_HU_ID
    init_db(tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        row = con.execute(
            "SELECT marketplace_id, storage_backend FROM marketplaces WHERE marketplace_id=?",
            [EMAG_HU_ID],
        ).fetchone()
    assert row is not None
    assert row[0] == EMAG_HU_ID
    assert row[1] == "duckdb"


def test_init_db_idempotent(tmp_db):
    from core.reference_store_duckdb import init_db
    init_db(tmp_db)
    init_db(tmp_db)  # should not raise


def test_is_available_false_when_no_db(tmp_db):
    from core.reference_store_duckdb import is_available
    assert not is_available("emag_hu", tmp_db)


def test_is_available_false_after_init_only(tmp_db):
    from core.reference_store_duckdb import init_db, is_available
    init_db(tmp_db)
    assert not is_available("emag_hu", tmp_db)


# ── Task 3: _enrich_values_robust ─────────────────────────────────────────────

def test_enrich_values_fills_missing_category_id():
    from core.reference_store_duckdb import _enrich_values_robust

    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat2"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [True, False],
    })
    vals = pd.DataFrame({
        "category_id":        [None, "cat2", None],
        "characteristic_id":  ["10",  "20",  "10"],
        "characteristic_name": [None,  "Marime", None],
        "value":              ["Rosu", "M",   "Albastru"],
    })

    result = _enrich_values_robust(vals, chars)

    assert result.iloc[0]["category_id"] == "cat1"
    assert result.iloc[0]["characteristic_name"] == "Culoare"
    assert result.iloc[1]["category_id"] == "cat2"
    assert result.iloc[2]["category_id"] == "cat1"


def test_enrich_values_leaves_existing_category_id_intact():
    from core.reference_store_duckdb import _enrich_values_robust

    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["catX"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame({
        "category_id":        ["catY"],
        "characteristic_id":  ["10"],
        "characteristic_name": ["Culoare"],
        "value":              ["Rosu"],
    })

    result = _enrich_values_robust(vals, chars)
    assert result.iloc[0]["category_id"] == "catY"


# ── Task 4: _validate_and_create_issues ───────────────────────────────────────

def test_validate_orphan_characteristic():
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat_MISSING"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [False, False],
    })
    vals = pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"])

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    types = [i["issue_type"] for i in issues]
    assert "orphan_characteristic" in types


def test_validate_mandatory_no_values():
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"])

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    types = [i["issue_type"] for i in issues]
    assert "mandatory_no_values" in types


def test_validate_empty_value():
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [False],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1"],
        "characteristic_id":  ["10"],
        "characteristic_name":["Culoare"],
        "value":              ["   "],
    })

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    types = [i["issue_type"] for i in issues]
    assert "empty_value" in types
    assert any(i["severity"] == "error" for i in issues)


def test_validate_no_issues_on_clean_data():
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1"],
        "characteristic_id":  ["10"],
        "characteristic_name":["Culoare"],
        "value":              ["Rosu"],
    })

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    blocking = [i for i in issues if i["issue_type"] in ("empty_value", "orphan_characteristic")]
    assert len(blocking) == 0


# ── Task 5: import_emag_hu + summary + issues ─────────────────────────────────

def test_import_emag_hu_returns_run_id(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    assert isinstance(run_id, str) and len(run_id) > 0


def test_import_emag_hu_sets_is_available(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, is_available
    cats, chars, vals = sample_data
    init_db(tmp_db)
    assert not is_available("emag_hu", tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    assert is_available("emag_hu", tmp_db)


def test_import_emag_hu_stores_correct_counts(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, get_import_summary
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    summary = get_import_summary(run_id, db_path=tmp_db)
    assert summary["categories"] == 2
    assert summary["characteristics"] == 2
    assert summary["values"] == 3


def test_import_emag_hu_is_idempotent(tmp_db, sample_data):
    import duckdb
    from core.reference_store_duckdb import init_db, import_emag_hu
    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM categories WHERE marketplace_id='emag_hu'"
        ).fetchone()[0]
    assert count == 2


def test_get_issues_returns_list(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, get_issues
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    issues = get_issues(run_id, db_path=tmp_db)
    assert isinstance(issues, list)


# ── Task 6: load_marketplace_data (coloane critice) ───────────────────────────

def test_load_marketplace_data_column_names(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, load_marketplace_data
    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)

    cats_r, chars_r, vals_r = load_marketplace_data("emag_hu", tmp_db)

    assert set(cats_r.columns) >= {"id", "emag_id", "name"}
    assert set(chars_r.columns) >= {"id", "category_id", "name", "mandatory"}
    assert set(vals_r.columns) >= {"category_id", "characteristic_name", "value"}


def test_load_marketplace_data_integration_with_marketplace_data(tmp_db, sample_data):
    """End-to-end: după import + load, metodele publice ale MarketplaceData funcționează."""
    from core.reference_store_duckdb import init_db, import_emag_hu, load_marketplace_data
    from core.loader import MarketplaceData

    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)

    cats_r, chars_r, vals_r = load_marketplace_data("emag_hu", tmp_db)
    mp = MarketplaceData("eMAG HU")
    mp.load_from_dataframes(cats_r, chars_r, vals_r)

    assert mp.is_loaded()
    cat_id = mp.category_id("Tricouri")
    assert cat_id is not None
    assert mp.category_name(cat_id) == "Tricouri"
    assert "Culoare" in mp.mandatory_chars(cat_id)
    assert "Rosu" in mp.valid_values(cat_id, "Culoare")
    assert mp.has_char(cat_id, "Culoare")
    assert "Tricouri" in mp.category_list()
    stats = mp.stats()
    assert stats["categories"] == 2
    assert stats["values"] == 3


# ── Task 2: marketplace_id_slug ───────────────────────────────────────────────

def test_slug_known_marketplaces():
    from core.reference_store_duckdb import marketplace_id_slug
    assert marketplace_id_slug("eMAG HU")      == "emag_hu"
    assert marketplace_id_slug("Allegro")       == "allegro"
    assert marketplace_id_slug("eMAG Romania")  == "emag_romania"
    assert marketplace_id_slug("Trendyol")      == "trendyol"
    assert marketplace_id_slug("FashionDays")   == "fashiondays"


def test_slug_custom_marketplaces():
    from core.reference_store_duckdb import marketplace_id_slug
    assert marketplace_id_slug("My Custom MP") == "my_custom_mp"
    assert marketplace_id_slug("cat-001 Store") == "cat_001_store"
    assert marketplace_id_slug("  Spaces  ")   == "spaces"


def test_ensure_marketplace_registers_new(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db, ensure_marketplace
    init_db(tmp_db)
    mp_id = ensure_marketplace(tmp_db, "my_custom_mp", "My Custom MP")
    assert mp_id == "my_custom_mp"
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        row = con.execute(
            "SELECT marketplace_name FROM marketplaces WHERE marketplace_id=?",
            ["my_custom_mp"],
        ).fetchone()
    assert row is not None
    assert row[0] == "My Custom MP"


def test_ensure_marketplace_idempotent(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db, ensure_marketplace
    init_db(tmp_db)
    ensure_marketplace(tmp_db, "test_mp", "Test MP")
    ensure_marketplace(tmp_db, "test_mp", "Test MP")  # must not raise or duplicate
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM marketplaces WHERE marketplace_id=?", ["test_mp"]
        ).fetchone()[0]
    assert count == 1
