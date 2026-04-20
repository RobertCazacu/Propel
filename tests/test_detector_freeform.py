"""Tests for Task 2: detect_* returns hardcoded value when vs is empty (freeform)."""
import pytest
from unittest.mock import MagicMock


def _make_data(valid_values=None, find_valid_return=None):
    data = MagicMock()
    data.valid_values.return_value = valid_values if valid_values is not None else set()
    data.find_valid.return_value = find_valid_return
    return data


# ─── detect_material ─────────────────────────────────────────────────────────

def test_detect_material_freeform_returns_hardcoded():
    """detect_material returns 'Bumbac' when vs empty and keyword matches."""
    from core.processor import detect_material
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_material("Nike Cotton T-Shirt", "", data, cat_id=99)
    assert result == "Bumbac"
    data.find_valid.assert_called_with("Bumbac", 99, "Material:")


def test_detect_material_freeform_no_keyword_returns_none():
    """detect_material returns None when no keyword matches (even if vs empty)."""
    from core.processor import detect_material
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_material("Generic Shoe", "", data, cat_id=99)
    assert result is None


def test_detect_material_with_vs_populated_find_valid_none_returns_none():
    """detect_material returns None when vs is populated but find_valid returns None.

    Note: 'cotton' keyword matches 'Bumbac', find_valid returns None, and vs is
    non-empty — so the freeform path does NOT fire, and the function returns None.
    """
    from core.processor import detect_material
    data = _make_data(valid_values={"Bumbac", "Poliester"}, find_valid_return=None)
    result = detect_material("Cotton T-Shirt", "", data, cat_id=99)
    assert result is None


def test_detect_material_with_vs_populated_find_valid_hit():
    """detect_material returns mapped value when vs is populated and find_valid succeeds."""
    from core.processor import detect_material
    data = _make_data(valid_values={"Bumbac", "Poliester"}, find_valid_return="Bumbac")
    result = detect_material("Cotton T-Shirt", "", data, cat_id=99)
    assert result == "Bumbac"


# ─── detect_sport ────────────────────────────────────────────────────────────

def test_detect_sport_freeform_returns_hardcoded():
    """detect_sport returns 'Fotbal' when vs empty and keyword matches."""
    from core.processor import detect_sport
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_sport("Nike Football Boots", "", data, cat_id=99)
    assert result == "Fotbal"
    data.find_valid.assert_called_with("Fotbal", 99, "Sport:")


def test_detect_sport_freeform_no_keyword_returns_none():
    """detect_sport returns None when no keyword matches (even if vs empty)."""
    from core.processor import detect_sport
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_sport("Generic Shirt", "", data, cat_id=99)
    assert result is None


def test_detect_sport_with_vs_populated_find_valid_none_returns_none():
    """detect_sport returns None when vs populated but find_valid returns None."""
    from core.processor import detect_sport
    data = _make_data(valid_values={"Fotbal", "Baschet"}, find_valid_return=None)
    result = detect_sport("Football Boots", "", data, cat_id=99)
    assert result is None


def test_detect_sport_with_vs_populated_find_valid_hit():
    """detect_sport returns mapped value when vs is populated and find_valid succeeds."""
    from core.processor import detect_sport
    data = _make_data(valid_values={"Fotbal", "Baschet"}, find_valid_return="Fotbal")
    result = detect_sport("Football Boots", "", data, cat_id=99)
    assert result == "Fotbal"


# ─── detect_sistem_inchidere ─────────────────────────────────────────────────

def test_detect_sistem_inchidere_freeform_returns_hardcoded():
    """detect_sistem_inchidere returns 'Siret' when vs empty and keyword matches."""
    from core.processor import detect_sistem_inchidere
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_sistem_inchidere("Adidas Running Shoe with Laces", "", data, cat_id=99)
    assert result == "Siret"
    data.find_valid.assert_called_with("Siret", 99, "Sistem inchidere:")


def test_detect_sistem_inchidere_freeform_no_keyword_returns_none():
    """detect_sistem_inchidere returns None when no keyword matches (even if vs empty)."""
    from core.processor import detect_sistem_inchidere
    data = _make_data(valid_values=set(), find_valid_return=None)
    result = detect_sistem_inchidere("Generic Shirt", "", data, cat_id=99)
    assert result is None


def test_detect_sistem_inchidere_with_vs_populated_find_valid_none_returns_none():
    """detect_sistem_inchidere returns None when vs populated but find_valid returns None."""
    from core.processor import detect_sistem_inchidere
    data = _make_data(valid_values={"Siret", "Velcro"}, find_valid_return=None)
    result = detect_sistem_inchidere("Shoe with laces", "", data, cat_id=99)
    assert result is None
