"""Tests for AI prompt quality improvements."""
import pytest
import pandas as pd
from unittest.mock import MagicMock


# ── Task 1: LLM Router interface ───────────────────────────────────────────────

def test_llm_router_passes_system_and_temperature():
    """LLMRouter.complete() must forward system and temperature to provider."""
    from core.llm_router import LLMRouter
    mock_provider = MagicMock()
    mock_provider.name = "mock"
    mock_provider.complete.return_value = "{}"

    router = LLMRouter.__new__(LLMRouter)
    router._provider = mock_provider

    router.complete("user msg", 100, system="sys msg", temperature=0.2)

    mock_provider.complete.assert_called_once_with(
        "user msg", 100, system="sys msg", temperature=0.2
    )


def test_llm_router_defaults_are_none():
    """system and temperature default to None (backward-compatible)."""
    from core.llm_router import LLMRouter
    mock_provider = MagicMock()
    mock_provider.name = "mock"
    mock_provider.complete.return_value = "{}"

    router = LLMRouter.__new__(LLMRouter)
    router._provider = mock_provider

    router.complete("user msg", 100)

    mock_provider.complete.assert_called_once_with("user msg", 100, system=None, temperature=None)


# ── Task 2: System prompts in ai_enricher ─────────────────────────────────────

def test_build_char_system_prompt_contains_rules():
    """System prompt must contain classification rules, not product data."""
    from core.ai_enricher import _build_char_system_prompt
    sys_prompt = _build_char_system_prompt("eMAG Romania")
    assert "JSON" in sys_prompt
    assert "OBLIGATORIU" in sys_prompt
    assert "Tricou" not in sys_prompt  # no product data in system prompt


def test_build_batch_system_prompt_contains_categories():
    """Batch system prompt must contain category list and JSON rule."""
    from core.ai_enricher import _build_batch_system_prompt
    sys_prompt = _build_batch_system_prompt("eMAG Romania", ["Cat A", "Cat B"])
    assert "JSON" in sys_prompt
    assert "Cat A" in sys_prompt
    assert "Cat B" in sys_prompt


def test_build_batch_system_prompt_multilingual():
    """Batch system prompt must mention that product titles can be in any language."""
    from core.ai_enricher import _build_batch_system_prompt
    sys_prompt = _build_batch_system_prompt("eMAG Romania", ["Cat A"])
    # Must mention language-agnostic classification
    assert any(word in sys_prompt.lower() for word in ["limbă", "limba", "language", "orice limbă", "orice limba"])


def test_enrich_with_ai_uses_temperature(monkeypatch):
    """enrich_with_ai must call router.complete with temperature=0.2 and a system prompt."""
    import core.ai_enricher as enricher

    calls = []

    class FakeRouter:
        provider_name = "mock"

        class _provider:
            _model = "mock"

        def complete(self, prompt, max_tokens, *, system=None, temperature=None):
            calls.append({"prompt": prompt, "system": system, "temperature": temperature})
            return '{"_reasoning": "test", "Culoare": "Negru"}'

    monkeypatch.setattr(enricher, "get_router", lambda: FakeRouter())
    monkeypatch.setattr(enricher, "_load_cache",
                        lambda: {"category_map": {}, "char_map": {}, "learned_title_rules": []})
    monkeypatch.setattr(enricher, "_save_cache", lambda c: None)
    monkeypatch.setattr(enricher, "log_char_enrichment", lambda **kw: None)

    result = enricher.enrich_with_ai(
        title="Tricou sport Nike",
        description="",
        category="Tricouri",
        existing={},
        char_options={"Culoare": {"Negru", "Alb"}},
        valid_values_for_cat={"Culoare": {"Negru", "Alb"}},
        marketplace="eMAG Romania",
    )

    assert calls, "complete() was not called"
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["system"] is not None
    validated, _ = result
    assert validated.get("Culoare") == "Negru"


# ── Task 3: Reasoning field ────────────────────────────────────────────────────

def test_parse_json_strips_reasoning():
    """_parse_json must return dict without _reasoning key."""
    from core.ai_enricher import _parse_json
    raw = '{"_reasoning": "Produsul este negru", "Culoare": "Negru", "Marime": "M"}'
    result = _parse_json(raw)
    assert "_reasoning" not in result
    assert result == {"Culoare": "Negru", "Marime": "M"}


def test_parse_json_handles_missing_reasoning():
    """_parse_json works normally when _reasoning is absent."""
    from core.ai_enricher import _parse_json
    raw = '{"Culoare": "Negru"}'
    result = _parse_json(raw)
    assert result == {"Culoare": "Negru"}


def test_parse_json_strips_reasoning_with_markdown():
    """_parse_json strips _reasoning even when wrapped in markdown code fences."""
    from core.ai_enricher import _parse_json
    raw = '```json\n{"_reasoning": "some logic", "Marime": "XL"}\n```'
    result = _parse_json(raw)
    assert "_reasoning" not in result
    assert result == {"Marime": "XL"}


# ── Task 4: Fuzzy category matching ───────────────────────────────────────────

def test_category_id_exact_match():
    """category_id() must find exact match."""
    from core.loader import MarketplaceData
    mp = _make_mp("Tricouri pentru copii")
    assert mp.category_id("Tricouri pentru copii") == 1


def test_category_id_normalized_match():
    """category_id() must find match ignoring diacritics/case."""
    from core.loader import MarketplaceData
    mp = _make_mp("Tricouri pentru copii")
    assert mp.category_id("tricouri pentru copii") == 1


def test_category_id_fuzzy_match():
    """category_id() must find close match via difflib as last resort."""
    from core.loader import MarketplaceData
    mp = _make_mp("Tricouri pentru copii")
    # One-letter typo: "pentr" instead of "pentru"
    assert mp.category_id("Tricouri pentr copii") == 1


def test_category_id_no_false_positive():
    """Fuzzy match must NOT fire on completely different strings."""
    from core.loader import MarketplaceData
    mp = _make_mp("Pantofi sport")
    assert mp.category_id("Tricouri copii") is None


def test_category_id_too_different_returns_none():
    """Fuzzy match must NOT match when similarity is too low."""
    from core.loader import MarketplaceData
    mp = _make_mp("Tricouri pentru copii")
    # Abbreviation — too different
    assert mp.category_id("Tricouri pt copii") is None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mp(category_name: str):
    from core.loader import MarketplaceData
    mp = MarketplaceData("test")
    mp.load_from_dataframes(
        cats=pd.DataFrame({
            "id": [1], "emag_id": [1],
            "name": [category_name], "parent_id": [None],
        }),
        chars=pd.DataFrame(columns=["id", "category_id", "name", "mandatory"]),
        vals=pd.DataFrame(columns=["category_id", "characteristic_id",
                                   "characteristic_name", "value"]),
    )
    return mp
