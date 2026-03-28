"""Tests for per-attribute fusion engine (fusion_attrs.py)."""
import pytest
from unittest.mock import MagicMock


def _make_data(valid_values: dict = None, restrictive: bool = True):
    """Mock MarketplaceData."""
    data = MagicMock()
    vv = valid_values or {}

    def find_valid(val, cat_id, char_name):
        allowed = vv.get(char_name, set())
        if not allowed:
            return val if not restrictive else None
        for v in allowed:
            if v.casefold() == val.strip().casefold():
                return v
        return None

    data.find_valid.side_effect = find_valid
    data.valid_values.side_effect = lambda cat_id, char: vv.get(char, set())
    data.is_restrictive.return_value = restrictive
    return data


class TestFuseAttribute:

    def test_vision_ineligible_attr_returns_text(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": False}
        data = _make_data({"Material:": {"Bumbac", "Poliester"}})
        result = fuse_attribute("Material:", ("Bumbac", 0.95, "rule"),
                                ("Poliester", 0.99, "vision"), policy, data, "cat1")
        assert result.action == "keep_text"
        assert result.final_value == "Bumbac"

    def test_vision_fills_empty_field(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru", "Alb"}})
        result = fuse_attribute("Culoare de baza", None,
                                ("Negru", 0.85, "color_algorithm"), policy, data, "cat1")
        assert result.action == "use_vision"
        assert result.final_value == "Negru"
        assert result.needs_review is False

    def test_text_wins_when_field_filled(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru", "Alb"}})
        result = fuse_attribute("Culoare de baza", ("Negru", 0.95, "rule"),
                                ("Alb", 0.90, "color_algorithm"), policy, data, "cat1")
        assert result.action == "keep_text"
        assert result.final_value == "Negru"
        assert result.needs_review is False

    def test_conflict_both_strong_review_flag(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "review"}
        data = _make_data({"Imprimeu:": {"Logo", "Uni", "Grafic"}})
        result = fuse_attribute("Imprimeu:", ("Uni", 0.80, "rule"),
                                ("Logo", 0.85, "vision_llm_cloud"), policy, data, "cat1")
        assert result.action == "conflict_review"
        assert result.final_value == "Uni"   # text wins conservator
        assert result.needs_review is True

    def test_vision_below_threshold_skipped(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.70, "conflict_action": "prefer_text"}
        data = _make_data({"Imprimeu:": {"Logo", "Uni"}})
        result = fuse_attribute("Imprimeu:", None,
                                ("Logo", 0.50, "vision_llm_cloud"), policy, data, "cat1")
        assert result.action == "skip"
        assert result.final_value is None

    def test_invalid_vision_value_rejected(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Stil:": {"Profil inalt", "Profil jos"}}, restrictive=True)
        result = fuse_attribute("Stil:", None,
                                ("X-tra High", 0.92, "vision_llm_cloud"), policy, data, "cat1")
        assert result.action == "skip"
        assert result.final_value is None

    def test_freeform_vision_accepted(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({}, restrictive=False)
        result = fuse_attribute("Culoare de baza", None,
                                ("Albastru royal", 0.75, "color_algorithm"), policy, data, "cat1")
        assert result.action == "use_vision"
        assert result.final_value == "Albastru royal"

    def test_no_signals_returns_skip(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = fuse_attribute("Culoare de baza", None, None, policy, data, "cat1")
        assert result.action == "skip"
        assert result.final_value is None


class TestFuseAllAttributes:

    def test_fuse_all_respects_policy(self):
        from core.vision.fusion_attrs import fuse_all_attributes
        rules = {
            "default": {
                "attribute_fusion_policy": {
                    "Culoare de baza": {
                        "vision_eligible": True,
                        "override_text_if_filled": False,
                        "min_vision_confidence": 0.60,
                        "conflict_action": "prefer_text",
                    },
                    "Material:": {"vision_eligible": False},
                }
            }
        }
        text_chars = {"Material:": "Bumbac"}
        vision_attrs = {
            "Culoare de baza": ("Negru", 0.85, "color_algorithm"),
            "Material:": ("Poliester", 0.90, "vision_llm_cloud"),
        }
        data = _make_data({
            "Culoare de baza": {"Negru", "Alb"},
            "Material:": {"Bumbac", "Poliester"},
        })
        result = fuse_all_attributes(text_chars, vision_attrs, rules, data, "cat1")
        assert result["Culoare de baza"].action == "use_vision"
        assert result["Culoare de baza"].final_value == "Negru"
        assert "Material:" not in result
