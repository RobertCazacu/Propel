"""
Tests for mapping bug fixes — RC-1 through RC-9.
"""
import pandas as pd
import pytest
from core.loader import _normalize_char_name, MarketplaceData
from core.processor import detect_culoare_baza, detect_material, detect_sport
from core.processor import detect_tip_produs, detect_imprimeu, detect_sezon
from core.processor import detect_croiala, detect_lungime_maneca, validate_existing


def _make_data(cat_id: str, char_name: str, values: list) -> MarketplaceData:
    """Helper: construieste MarketplaceData minimal cu un set de valori permise."""
    cats = pd.DataFrame({
        "id": [cat_id], "name": ["TestCat"],
        "emag_id": [None], "parent_id": [None]
    })
    chars = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": [cat_id],
        "name": [char_name], "mandatory": ["0"], "restrictive": ["1"]
    })
    vals = pd.DataFrame([
        {"category_id": cat_id, "characteristic_id": "1",
         "characteristic_name": char_name, "value": v}
        for v in values
    ])
    d = MarketplaceData("test")
    d.load_from_dataframes(cats, chars, vals)
    return d


# ── RC-5: _normalize_char_name diacritics ──────────────────────────────────────

def test_normalize_char_name_strips_diacritics():
    assert _normalize_char_name("Mărime:") == _normalize_char_name("Marime:")
    assert _normalize_char_name("Culoare de bază") == _normalize_char_name("Culoare de baza")
    assert _normalize_char_name("Tip:") == "tip"
    assert _normalize_char_name("  Marime:  ") == "marime"


# ── RC-3: detectori case-insensitive ──────────────────────────────────────────

def test_detect_culoare_baza_case_insensitive():
    """Valid values cu lowercase trebuie detectate corect."""
    data = _make_data("1", "Culoare de baza", ["negru", "alb"])
    result = detect_culoare_baza("Tricou negru", "", data, "1")
    assert result == "negru"


def test_detect_culoare_baza_standard_case():
    """Valid values cu casing standard continuă să funcționeze."""
    data = _make_data("1", "Culoare de baza", ["Negru", "Alb"])
    result = detect_culoare_baza("Tricou Negru", "", data, "1")
    assert result == "Negru"


def test_detect_material_case_insensitive():
    data = _make_data("1", "Material:", ["bumbac", "poliester"])
    result = detect_material("Tricou din bumbac", "", data, "1")
    assert result == "bumbac"


def test_detect_sport_case_insensitive():
    data = _make_data("1", "Sport:", ["alergare", "fitness"])
    result = detect_sport("Pantofi de alergare", "", data, "1")
    assert result == "alergare"


# ── RC-4: detect_tip_produs "short" false positive ───────────────────────────

def test_detect_tip_produs_no_false_positive_short_sleeve():
    data = _make_data("1", "Tip produs:", ["Pantaloni", "Tricou"])
    result = detect_tip_produs("Nike Short Sleeve Running Top - L", data, "1")
    assert result != "Pantaloni", (
        f"'short sleeve' clasificat greșit ca Pantaloni, got {result!r}"
    )


def test_detect_tip_produs_sort_still_works():
    data = _make_data("1", "Tip produs:", ["Pantaloni"])
    result = detect_tip_produs("Pantaloni Scurti Nike - L", data, "1")
    assert result == "Pantaloni"


def test_detect_tip_produs_tricou():
    data = _make_data("1", "Tip produs:", ["Tricou", "Pantaloni"])
    result = detect_tip_produs("Tricou Nike Dri-FIT - S", data, "1")
    assert result == "Tricou"


# ── RC-7: detect_imprimeu "model" false positive ──────────────────────────────

def test_detect_imprimeu_no_false_positive_model():
    data = _make_data("1", "Imprimeu:", ["Cu model", "Uni", "Logo"])
    result = detect_imprimeu("Nike Air Force 1 Model 2024 - Alb", "", data, "1")
    assert result != "Cu model", (
        f"'model' din titlu clasificat greșit ca imprimeu 'Cu model', got {result!r}"
    )


def test_detect_imprimeu_graphic_works():
    data = _make_data("1", "Imprimeu:", ["Cu model", "Uni"])
    result = detect_imprimeu("Tricou Graphic Nike", "", data, "1")
    assert result == "Cu model"


# ── RC-9: detect_sezon fleece lightweight ────────────────────────────────────

def test_detect_sezon_fleece_lightweight_not_winter():
    data = _make_data("1", "Sezon:", ["Toamna-Iarna", "Primavara-Vara"])
    result = detect_sezon("Bluza Fleece Lightweight", "", data, "1")
    assert result != "Toamna-Iarna", (
        f"fleece lightweight clasificat greșit ca iarnă, got {result!r}"
    )


def test_detect_sezon_fleece_warm_is_winter():
    data = _make_data("1", "Sezon:", ["Toamna-Iarna"])
    result = detect_sezon("Hanorac Fleece Thermal Warm", "", data, "1")
    assert result == "Toamna-Iarna"


def test_detect_sezon_winter_keywords():
    data = _make_data("1", "Sezon:", ["Toamna-Iarna", "Primavara-Vara"])
    result = detect_sezon("Geaca Winter Warm", "", data, "1")
    assert result == "Toamna-Iarna"


# ── RC-8: validate_existing case-insensitive ──────────────────────────────────

def test_validate_existing_case_insensitive():
    data = _make_data("1", "Culoare de baza", ["Negru", "Alb"])
    result = validate_existing({"Culoare de baza": "NEGRU"}, "TestCat", data)
    assert "Culoare de baza" not in result, (
        f"'NEGRU' raportat invalid deși 'Negru' e valid"
    )


def test_validate_existing_truly_invalid():
    data = _make_data("1", "Culoare de baza", ["Negru", "Alb"])
    result = validate_existing({"Culoare de baza": "Verde"}, "TestCat", data)
    assert "Culoare de baza" in result


# ── RC-1: cache key include data identity ─────────────────────────────────────

def test_ai_fixes_wrong_rule_detection():
    """P02: Un câmp cu valoare invalidă trebuie inclus în mandatory_missing pentru AI."""
    cats = pd.DataFrame({
        "id": ["1"], "name": ["TestCat"],
        "emag_id": [None], "parent_id": [None]
    })
    chars = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": ["1"],
        "name": ["Marime:"], "mandatory": ["1"], "restrictive": ["1"]
    })
    vals = pd.DataFrame([
        {"category_id": "1", "characteristic_id": "1",
         "characteristic_name": "Marime:", "value": v}
        for v in ["S", "M", "L", "XL"]
    ])
    data = MarketplaceData("test")
    data.load_from_dataframes(cats, chars, vals)

    from core.processor import _get_mandatory_missing_for_ai
    missing = _get_mandatory_missing_for_ai(
        cat_id="1",
        combined_existing={"Marime:": "42"},  # valoare invalidă — nu e în valid_values
        data=data,
    )
    assert "Marime:" in missing, "Câmpul cu valoare invalidă trebuie re-evaluat de AI"


def test_cache_key_stable_after_gc():
    """P05: Cache nu trebuie reutilizat după GC al instanței data."""
    import gc
    from core.processor import process_product, _applicable_detectors_cache

    data1 = _make_data("cat_gc", "Marime:", ["S", "M", "L"])
    # Populează cache-ul pentru data1
    process_product("Tricou - S", "", "TestCat", {}, data1)
    assert data1 in _applicable_detectors_cache

    # Șterge data1 și forțează GC
    del data1
    gc.collect()

    # Cache-ul nu mai trebuie să conțină intrarea pentru data1 (WeakKeyDictionary auto-expirat)
    # Verificăm că un obiect nou cu același cat_id este tratat independent
    data2 = _make_data("cat_gc", "Culoare de baza", ["Negru", "Alb"])
    process_product("Tricou Negru - S", "", "TestCat", {}, data2)
    assert data2 in _applicable_detectors_cache
    # data2 cache trebuie să conțină detectorul culoare, nu marime
    cat_cache = _applicable_detectors_cache[data2]
    detector_names = {cn for cn, _ in cat_cache.get("cat_gc", ())}
    assert "Marime:" not in detector_names, "Stale cache din data1 reutilizat pentru data2"


def test_cache_key_includes_data_identity():
    """Detectori diferiți pentru instanțe diferite de MarketplaceData cu același cat_id."""
    from core.processor import process_product, _applicable_detectors_cache

    cats = pd.DataFrame({
        "id": ["cat1"], "name": ["Tricouri"],
        "emag_id": [None], "parent_id": [None]
    })
    chars_marime = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": ["cat1"],
        "name": ["Marime:"], "mandatory": ["0"], "restrictive": ["1"]
    })
    chars_culoare = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": ["cat1"],
        "name": ["Culoare de baza"], "mandatory": ["0"], "restrictive": ["1"]
    })
    vals_marime = pd.DataFrame([{
        "category_id": "cat1", "characteristic_id": "1",
        "characteristic_name": "Marime:", "value": "42 EU"
    }])
    vals_culoare = pd.DataFrame([{
        "category_id": "cat1", "characteristic_id": "1",
        "characteristic_name": "Culoare de baza", "value": "Negru"
    }])

    data1 = MarketplaceData("mp1")
    data1.load_from_dataframes(cats.copy(), chars_marime, vals_marime)

    data2 = MarketplaceData("mp2")
    data2.load_from_dataframes(cats.copy(), chars_culoare, vals_culoare)

    # data2 nu are Marime — cache-ul vechi din data1 nu trebuie să fie reutilizat
    r2 = process_product("Tricou Negru - M", "", "Tricouri", {}, data2)
    assert "Marime:" not in r2, (
        "Cache stale: detectorul Marime: din data1 aplicat pe data2"
    )
