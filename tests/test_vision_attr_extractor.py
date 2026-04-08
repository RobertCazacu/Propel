"""Tests for cloud vision structured attribute extractor."""
import pytest
from unittest.mock import MagicMock


def _make_data(valid_values: dict = None, restrictive: bool = True):
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


class TestParseVisionResponse:

    def test_parses_valid_json(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Negru", "Imprimeu:": "Logo"}'
        data = _make_data({"Culoare de baza": {"Negru"}, "Imprimeu:": {"Logo"}})
        result = _parse_vision_response(raw, {"Culoare de baza", "Imprimeu:"}, data, "cat1")
        assert result["Culoare de baza"] == "Negru"
        assert result["Imprimeu:"] == "Logo"

    def test_rejects_hallucinated_attr(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Negru", "Inventat:": "ceva"}'
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert "Inventat:" not in result

    def test_rejects_value_not_in_list(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Mov electric"}'
        data = _make_data({"Culoare de baza": {"Negru", "Alb", "Rosu"}}, restrictive=True)
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert "Culoare de baza" not in result

    def test_fallback_returns_empty_on_invalid_json(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = "The product is a black t-shirt with logo print"
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result == {}  # NEVER parse free text

    def test_strips_markdown_fence(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '```json\n{"Culoare de baza": "Negru"}\n```'
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result["Culoare de baza"] == "Negru"

    def test_accepts_freeform_when_no_valid_values(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Albastru royal"}'
        data = _make_data({}, restrictive=False)
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result["Culoare de baza"] == "Albastru royal"

    def test_empty_response_returns_empty(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        data = _make_data({"Culoare de baza": {"Negru"}})
        assert _parse_vision_response("", {"Culoare de baza"}, data, "cat1") == {}
        assert _parse_vision_response("   ", {"Culoare de baza"}, data, "cat1") == {}


class TestBuildVisionPrompt:

    def test_prompt_excludes_already_filled(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        eligible = {"Culoare de baza": {"Negru", "Alb"}, "Imprimeu:": {"Logo", "Uni"}}
        existing = {"Culoare de baza": "Negru"}
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, existing)
        assert "Culoare de baza" not in prompt
        assert "Imprimeu:" in prompt

    def test_prompt_has_no_confidence_request(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        eligible = {"Culoare de baza": {"Negru"}}
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, {})
        assert "confidence" not in prompt.lower()

    def test_returns_empty_when_all_filled(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        eligible = {"Culoare de baza": {"Negru"}}
        existing = {"Culoare de baza": "Negru"}
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, existing)
        assert prompt == ""
