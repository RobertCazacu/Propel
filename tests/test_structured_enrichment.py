"""
Tests for structured AI enrichment: off/shadow/on modes, sampling, provider mismatch, fallback.
"""
import sys
import types
import random
import pytest
from unittest.mock import MagicMock, patch, call


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_mock_streamlit(cfg=None):
    """Returns a mock streamlit module with session_state containing structured config."""
    st = MagicMock()
    st.session_state = {"structured_output_config": cfg or {}}
    return st


# ── 1. Router: complete_structured delegă corect ──────────────────────────────

def test_router_complete_structured_delegates():
    """LLMRouter.complete_structured delegă la provider și returnează dict."""
    from core.llm_router import LLMRouter
    router = LLMRouter.__new__(LLMRouter)
    mock_provider = MagicMock()
    mock_provider.name = "anthropic"
    mock_provider.complete_structured.return_value = {"Culoare": "Alb"}
    router._provider = mock_provider

    result = router.complete_structured("prompt", {"type": "object"})
    assert result == {"Culoare": "Alb"}
    mock_provider.complete_structured.assert_called_once_with(
        "prompt", {"type": "object"}, system=None
    )


def test_router_complete_structured_returns_none_on_exc():
    """LLMRouter.complete_structured returnează None dacă providerul aruncă excepție."""
    from core.llm_router import LLMRouter
    router = LLMRouter.__new__(LLMRouter)
    mock_provider = MagicMock()
    mock_provider.name = "anthropic"
    mock_provider.complete_structured.side_effect = RuntimeError("API down")
    router._provider = mock_provider

    result = router.complete_structured("prompt", {})
    assert result is None  # fără crash


# ── 2. Config helpers ─────────────────────────────────────────────────────────

def test_get_structured_config_defaults(monkeypatch):
    """get_structured_config returnează default-uri când nu e setat nimic."""
    mock_st = _make_mock_streamlit(cfg={})
    monkeypatch.setitem(sys.modules, "streamlit", mock_st)

    from core import ai_enricher
    cfg = ai_enricher.get_structured_config()

    assert cfg["mode"] == "off"
    assert cfg["sample"] == pytest.approx(0.10)
    assert cfg["provider_only"] is True


def test_get_structured_config_from_session(monkeypatch):
    """get_structured_config citește corect din session_state."""
    mock_st = _make_mock_streamlit(cfg={"mode": "shadow", "sample": 0.5, "provider_only": False})
    monkeypatch.setitem(sys.modules, "streamlit", mock_st)

    from core import ai_enricher
    cfg = ai_enricher.get_structured_config()

    assert cfg["mode"] == "shadow"
    assert cfg["sample"] == pytest.approx(0.5)
    assert cfg["provider_only"] is False


# ── 3. _should_run_structured ─────────────────────────────────────────────────

def test_should_run_structured_mode_off():
    """mode=off => niciodată structured."""
    from core.ai_enricher import _should_run_structured
    cfg = {"mode": "off", "sample": 1.0, "provider_only": False}
    assert _should_run_structured(cfg, "anthropic") is False


def test_should_run_structured_provider_mismatch():
    """mode=on, provider_only=True, provider=groq => skip fără crash."""
    from core.ai_enricher import _should_run_structured
    cfg = {"mode": "on", "sample": 1.0, "provider_only": True}
    assert _should_run_structured(cfg, "groq") is False


def test_should_run_structured_sample_zero():
    """sample=0 => structured rulează 0%."""
    from core.ai_enricher import _should_run_structured
    cfg = {"mode": "on", "sample": 0.0, "provider_only": False}
    results = [_should_run_structured(cfg, "anthropic") for _ in range(100)]
    assert not any(results)


def test_should_run_structured_sample_one():
    """sample=1 => structured rulează 100%."""
    from core.ai_enricher import _should_run_structured
    cfg = {"mode": "on", "sample": 1.0, "provider_only": False}
    results = [_should_run_structured(cfg, "anthropic") for _ in range(20)]
    assert all(results)


def test_should_run_structured_sampling_probabilistic():
    """sample=0.5 => ~50% cu toleranță largă."""
    from core.ai_enricher import _should_run_structured
    cfg = {"mode": "shadow", "sample": 0.5, "provider_only": False}
    random.seed(42)
    results = [_should_run_structured(cfg, "anthropic") for _ in range(200)]
    rate = sum(results) / len(results)
    assert 0.3 < rate < 0.7  # între 30% și 70%


# ── 4. enrich_with_ai cu mode=off ─────────────────────────────────────────────

def _patch_enricher_common(monkeypatch, mock_router, mock_st, cfg_mode):
    """Patch comun pentru toate testele enrich_with_ai: cache gol + deps mock."""
    monkeypatch.setitem(sys.modules, "streamlit", mock_st)
    import core.ai_enricher as enricher
    monkeypatch.setattr(enricher, "get_router", lambda: mock_router)
    monkeypatch.setattr(enricher, "write_run_to_duckdb", lambda **kw: None)
    monkeypatch.setattr(enricher, "log_char_enrichment", lambda **kw: None)
    monkeypatch.setattr(enricher, "get_product_knowledge", lambda **kw: None)
    monkeypatch.setattr(enricher, "upsert_product_knowledge", lambda **kw: None)
    # Cache gol — evită early return din cache hit pe disk
    monkeypatch.setattr(enricher, "_load_cache",
                        lambda: {"category_map": {}, "char_map": {}, "learned_title_rules": [], "done_map": {}})
    monkeypatch.setattr(enricher, "_save_cache", lambda c: None)
    return enricher


def _make_router(complete_resp='{"Culoare": "Alb"}', structured_resp=None):
    mock_router = MagicMock()
    mock_router.provider_name = "anthropic"
    mock_router.complete.return_value = complete_resp
    mock_router.complete_structured.return_value = structured_resp
    return mock_router


_CALL_KWARGS = dict(
    title="Produs test XY9",
    description="",
    category="TestCatXY",
    existing={},
    char_options={"Culoare": {"Alb", "Negru"}},
    valid_values_for_cat={"Culoare": {"Alb", "Negru"}},
    marketplace="test_mp",
)


def test_enrich_mode_off_uses_only_text_path(monkeypatch):
    """mode=off => complete_structured niciodată apelat."""
    mock_st = _make_mock_streamlit(cfg={"mode": "off", "sample": 1.0, "provider_only": False})
    mock_router = _make_router(structured_resp={"Culoare": "Negru"})
    enricher = _patch_enricher_common(monkeypatch, mock_router, mock_st, "off")

    enricher.enrich_with_ai(**_CALL_KWARGS)

    mock_router.complete_structured.assert_not_called()


# ── 5. mode=shadow — output din text, structured logat ────────────────────────

def test_enrich_mode_shadow_output_from_text(monkeypatch):
    """mode=shadow => complete_structured apelat, output final din text flow."""
    mock_st = _make_mock_streamlit(cfg={"mode": "shadow", "sample": 1.0, "provider_only": False})
    mock_router = _make_router(structured_resp={"Culoare": "Negru"})
    enricher = _patch_enricher_common(monkeypatch, mock_router, mock_st, "shadow")

    validated, _ = enricher.enrich_with_ai(**_CALL_KWARGS)

    # Shadow: structured apelat dar outputul din text ("Alb" nu "Negru")
    mock_router.complete_structured.assert_called_once()
    assert validated.get("Culoare") == "Alb"


# ── 6. mode=on + structured success ──────────────────────────────────────────

def test_enrich_mode_on_uses_structured_result(monkeypatch):
    """mode=on + structured ok => validated vine din structured output."""
    mock_st = _make_mock_streamlit(cfg={"mode": "on", "sample": 1.0, "provider_only": False})
    mock_router = _make_router(structured_resp={"Culoare": "Negru"})
    enricher = _patch_enricher_common(monkeypatch, mock_router, mock_st, "on")

    validated, _ = enricher.enrich_with_ai(**_CALL_KWARGS)

    # On: structured folosit, "Negru" e valid → validated = "Negru"
    assert validated.get("Culoare") == "Negru"


# ── 7. mode=on + structured None => fallback text ─────────────────────────────

def test_enrich_mode_on_fallback_when_structured_none(monkeypatch):
    """mode=on + structured returnează None => fallback text fără crash."""
    mock_st = _make_mock_streamlit(cfg={"mode": "on", "sample": 1.0, "provider_only": False})
    mock_router = _make_router(structured_resp=None)  # eșec structured
    enricher = _patch_enricher_common(monkeypatch, mock_router, mock_st, "on")

    validated, _ = enricher.enrich_with_ai(**_CALL_KWARGS)

    # Fallback la text — "Alb"
    assert validated.get("Culoare") == "Alb"


# ── 8. Telemetry: câmpurile structured ajung în write_run_to_duckdb ───────────

def test_telemetry_includes_structured_fields(monkeypatch):
    """write_run_to_duckdb este apelat cu câmpurile structured corecte."""
    mock_st = _make_mock_streamlit(cfg={"mode": "shadow", "sample": 1.0, "provider_only": False})
    mock_router = _make_router(structured_resp={"Culoare": "Negru"})
    captured = {}

    def fake_write(**kw):
        captured.update(kw)

    enricher = _patch_enricher_common(monkeypatch, mock_router, mock_st, "shadow")
    monkeypatch.setattr(enricher, "write_run_to_duckdb", fake_write)

    enricher.enrich_with_ai(**_CALL_KWARGS)

    assert captured.get("structured_mode") == "shadow"
    assert captured.get("structured_attempted") is True
    assert captured.get("structured_success") is True
    assert captured.get("schema_fields_count", 0) >= 1
