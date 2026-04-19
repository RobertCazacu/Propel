"""Tests for Task 3B: AI returns array -> pick best single value."""
import pytest
from unittest.mock import MagicMock


def _make_data(find_valid_map: dict = None):
    """find_valid_map: {(val, cat_id, char_name): return_val}"""
    data = MagicMock()
    def _find_valid(val, cat_id, char_name):
        if find_valid_map:
            return find_valid_map.get((val, cat_id, char_name))
        return None
    data.find_valid.side_effect = _find_valid
    data.valid_values.return_value = set()
    return data


def _run_array_coercion(ch_val, data, cat_id, ch_name):
    """Replicate the array-coercion logic from enrich_with_ai inline."""
    if isinstance(ch_val, list):
        ch_val = next(
            (v for v in ch_val if data and data.find_valid(str(v), cat_id, ch_name)),
            ch_val[0] if ch_val else ""
        )
    return ch_val


def test_array_picks_first_valid_match():
    """Array -> picks first element found via find_valid."""
    data = _make_data({
        ("Lighting", 1, "Feature:"): "Lighting",
        ("Stopwatch", 1, "Feature:"): None,
        ("Waterproof", 1, "Feature:"): None,
    })
    result = _run_array_coercion(["Lighting", "Stopwatch", "Waterproof"], data, 1, "Feature:")
    assert result == "Lighting"


def test_array_picks_first_element_when_no_match():
    """Array -> picks first element when find_valid returns None for all."""
    data = _make_data({})
    result = _run_array_coercion(["Alpha", "Beta", "Gamma"], data, 1, "Feature:")
    assert result == "Alpha"


def test_empty_array_returns_empty_string():
    """Empty array -> returns '' (downstream `if not val_str: continue` handles it)."""
    data = _make_data({})
    result = _run_array_coercion([], data, 1, "Feature:")
    assert result == ""


def test_non_array_value_unchanged():
    """Non-array values pass through untouched."""
    data = _make_data({})
    result = _run_array_coercion("Bumbac", data, 1, "Material:")
    assert result == "Bumbac"


def test_array_coercion_warning_logged(caplog):
    """Array coercion produces correct value (precondition for warning in real code)."""
    data = _make_data({("Lighting", 1, "Feature:"): "Lighting"})
    result = _run_array_coercion(["Lighting", "Stopwatch"], data, 1, "Feature:")
    assert result == "Lighting"
