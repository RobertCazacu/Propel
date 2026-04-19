"""Tests for Task 1A: warning when mandatory char has no valid_values."""
import pytest
from unittest.mock import MagicMock


def _make_data_mock(valid_values_map: dict):
    """Create a MarketplaceData mock where valid_values returns per-char sets."""
    data = MagicMock()
    data.valid_values.side_effect = lambda cat_id, ch: valid_values_map.get(ch, set())
    return data


def test_warning_when_valid_values_empty(caplog):
    """ATENȚIE warning fires when valid_values is empty for a mandatory missing char."""
    import logging
    from core.processor import _warn_missing_mandatory_no_values

    data = _make_data_mock({"Marime:": set()})

    with caplog.at_level(logging.WARNING, logger="marketplace.processor"):
        _warn_missing_mandatory_no_values(data, cat_id=123, still_missing=["Marime:"])

    assert any("[ATENȚIE]" in r.message for r in caplog.records)
    assert any("Marime:" in r.message for r in caplog.records)


def test_no_warning_when_valid_values_populated(caplog):
    """ATENȚIE warning does NOT fire when valid_values is non-empty."""
    import logging
    from core.processor import _warn_missing_mandatory_no_values

    data = _make_data_mock({"Marime:": {"S", "M", "L"}})

    with caplog.at_level(logging.WARNING, logger="marketplace.processor"):
        _warn_missing_mandatory_no_values(data, cat_id=123, still_missing=["Marime:"])

    assert not any("[ATENȚIE]" in r.message for r in caplog.records)
