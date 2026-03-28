# AI Prompt Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve AI classification and enrichment accuracy via system prompt separation, deterministic temperature, chain-of-thought reasoning, multilingual batch context, and Levenshtein fuzzy category matching.

**Architecture:** Extend the `BaseLLMProvider.complete()` interface with optional `system`, `temperature` params (backward-compatible). Route these through `LLMRouter`. Update `ai_enricher.py` prompts to use system/user separation, reasoning-first JSON, and multilingual hints. Add `difflib` fuzzy fallback in `loader.py`.

**Tech Stack:** Python 3.11+, requests, difflib (stdlib), existing provider SDKs (anthropic, openai-compat REST)

---

## File Map

| File | Change |
|------|--------|
| `core/providers/base.py` | Add `system`, `temperature` to `complete()` signature |
| `core/providers/anthropic_provider.py` | Pass `system=`, `temperature=` to API |
| `core/providers/ollama_provider.py` | Switch to `/api/chat`, support system + temperature |
| `core/providers/groq_provider.py` | Add system message, temperature, json_mode |
| `core/providers/gemini_provider.py` | Add systemInstruction, temperature |
| `core/providers/mistral_provider.py` | Add system message, temperature, json_mode |
| `core/llm_router.py` | Pass `system`, `temperature`, `json_mode` through to provider |
| `core/ai_enricher.py` | System prompts, reasoning field, multilingual hint, temperature=0.2 |
| `core/loader.py` | `difflib.get_close_matches` fallback in `category_id()` |
| `tests/test_ai_prompt_quality.py` | Unit tests for all new behavior |

---

## Task 1: Extend provider interface

**Files:**
- Modify: `core/providers/base.py`
- Modify: `core/providers/anthropic_provider.py`
- Modify: `core/providers/ollama_provider.py`
- Modify: `core/providers/groq_provider.py`
- Modify: `core/providers/gemini_provider.py`
- Modify: `core/providers/mistral_provider.py`
- Modify: `core/llm_router.py`
- Test: `tests/test_ai_prompt_quality.py`

- [ ] **Step 1.1: Write failing test for base interface**

```python
# tests/test_ai_prompt_quality.py
"""Tests for AI prompt quality improvements."""
import pytest
from unittest.mock import patch, MagicMock


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
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
cd C:\Users\manue\Desktop\marketplace_tool
python -m pytest tests/test_ai_prompt_quality.py::test_llm_router_passes_system_and_temperature -v
```

Expected: FAIL — `complete()` does not accept `system` or `temperature` kwargs.

- [ ] **Step 1.3: Update `core/providers/base.py`**

```python
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """Interface comun pentru toți providerii LLM."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Numele providerului (ex: 'anthropic', 'ollama')."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        """
        Trimite prompt-ul și returnează răspunsul text.
        system: mesaj de sistem static (instrucțiuni, context permanent).
        temperature: 0.0–1.0; None = folosește default-ul providerului.
        Ridică excepție dacă apelul eșuează.
        """

    def is_available(self) -> bool:
        return True
```

- [ ] **Step 1.4: Update `core/llm_router.py` — `complete()` method only**

Change only the `complete` method in `LLMRouter`:

```python
def complete(self, prompt: str, max_tokens: int = 300, *,
             system: str | None = None,
             temperature: float | None = None) -> str:
    return self._provider.complete(prompt, max_tokens,
                                   system=system, temperature=temperature)
```

- [ ] **Step 1.5: Update `core/providers/anthropic_provider.py`**

```python
import os
from .base import BaseLLMProvider

_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self):
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key or key.startswith("sk-ant-your"):
            raise ValueError(
                "ANTHROPIC_API_KEY lipsește sau nu este configurată în .env"
            )
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=key)
        except ImportError:
            raise ImportError(
                "Pachetul 'anthropic' nu este instalat. Rulează: pip install anthropic"
            )

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        kwargs = dict(
            model=_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = self._client.messages.create(**kwargs)
        return msg.content[0].text
```

- [ ] **Step 1.6: Update `core/providers/ollama_provider.py`**

Switch from `/api/generate` to `/api/chat` (supports system messages):

```python
import os
import requests
from .base import BaseLLMProvider

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL    = "qwen2.5:14b"


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(self):
        self._base_url = os.getenv("OLLAMA_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        self._model    = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        timeout = int(os.getenv("OLLAMA_TIMEOUT", "300"))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = requests.post(
                f"{self._base_url}/api/chat",
                json={
                    "model":   self._model,
                    "messages": messages,
                    "stream":  False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature if temperature is not None else 0.2,
                    },
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"Ollama timeout ({timeout}s) pentru modelul {self._model}. "
                "Mareste OLLAMA_TIMEOUT in .env sau foloseste un model mai rapid."
            )
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Ollama nu este pornit la {self._base_url}. "
                "Rulează 'ollama serve' în terminal."
            )

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self._base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False
```

- [ ] **Step 1.7: Update `core/providers/groq_provider.py`**

```python
import os
import requests
from .base import BaseLLMProvider

_DEFAULT_MODEL = "llama-3.3-70b-versatile"
_API_URL       = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(BaseLLMProvider):
    name = "groq"

    def __init__(self):
        self._key = os.getenv("GROQ_API_KEY", "").strip()
        if not self._key:
            raise ValueError(
                "GROQ_API_KEY lipsește. Creează un cont gratuit pe console.groq.com"
            )
        self._model = os.getenv("GROQ_MODEL", _DEFAULT_MODEL)

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model":       self._model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature if temperature is not None else 0.2,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=30,
        )
        if resp.status_code == 401:
            raise PermissionError("GROQ_API_KEY invalidă.")
        if resp.status_code == 429:
            raise RuntimeError("Groq rate limit atins. Așteaptă sau treci la alt provider.")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 1.8: Update `core/providers/mistral_provider.py`**

```python
import os
import requests
from .base import BaseLLMProvider

_DEFAULT_MODEL = "mistral-small-latest"
_API_URL       = "https://api.mistral.ai/v1/chat/completions"


class MistralProvider(BaseLLMProvider):
    name = "mistral"

    def __init__(self):
        self._key = os.getenv("MISTRAL_API_KEY", "").strip()
        if not self._key:
            raise ValueError(
                "MISTRAL_API_KEY lipsește. Creează un cont pe console.mistral.ai"
            )
        self._model = os.getenv("MISTRAL_MODEL", _DEFAULT_MODEL)

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model":      self._model,
            "messages":   messages,
            "max_tokens": max_tokens,
            "temperature": temperature if temperature is not None else 0.2,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=30,
        )
        if resp.status_code == 401:
            raise PermissionError("MISTRAL_API_KEY invalidă.")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 1.9: Update `core/providers/gemini_provider.py`**

```python
import os
import requests
from .base import BaseLLMProvider

_DEFAULT_MODEL  = "gemini-2.0-flash"
_API_BASE       = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self):
        self._key = os.getenv("GEMINI_API_KEY", "").strip()
        if not self._key:
            raise ValueError(
                "GEMINI_API_KEY lipsește. Adaugă cheia în .env"
            )
        self._model = os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        url  = f"{_API_BASE}/{self._model}:generateContent?key={self._key}"
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature if temperature is not None else 0.2,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 400:
            raise ValueError(f"Gemini API error 400: {resp.text[:200]}")
        if resp.status_code == 403:
            raise PermissionError("GEMINI_API_KEY invalidă sau fără acces.")
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
```

- [ ] **Step 1.10: Run tests**

```bash
python -m pytest tests/test_ai_prompt_quality.py -v
```

Expected: PASS for both router tests.

- [ ] **Step 1.11: Commit**

```bash
git add core/providers/base.py core/providers/anthropic_provider.py \
        core/providers/ollama_provider.py core/providers/groq_provider.py \
        core/providers/gemini_provider.py core/providers/mistral_provider.py \
        core/llm_router.py tests/test_ai_prompt_quality.py
git commit -m "feat: extend LLM provider interface with system prompt and temperature"
```

---

## Task 2: System prompt + temperature in ai_enricher

**Files:**
- Modify: `core/ai_enricher.py`
- Test: `tests/test_ai_prompt_quality.py`

This task splits the static "how to answer" rules into a `system` role message, keeping only product-specific data in the `user` message. Also sets `temperature=0.2` on all AI calls.

- [ ] **Step 2.1: Add failing tests**

```python
def test_build_char_system_prompt_contains_rules():
    """System prompt must contain classification rules, not product data."""
    from core.ai_enricher import _build_char_system_prompt
    sys_prompt = _build_char_system_prompt("eMAG Romania")
    assert "JSON" in sys_prompt
    assert "OBLIGATORIU" in sys_prompt
    assert "Tricou" not in sys_prompt  # no product data in system prompt


def test_build_batch_system_prompt_contains_multilingual():
    """Batch system prompt must mention that titles can be in any language."""
    from core.ai_enricher import _build_batch_system_prompt
    sys_prompt = _build_batch_system_prompt("eMAG Romania", ["Cat A", "Cat B"])
    assert "JSON" in sys_prompt
    assert "Cat A" in sys_prompt  # category list in system


def test_enrich_with_ai_uses_temperature(monkeypatch):
    """enrich_with_ai must call router.complete with temperature=0.2."""
    import core.ai_enricher as enricher
    calls = []

    class FakeRouter:
        provider_name = "mock"
        class _provider:
            _model = "mock"

        def complete(self, prompt, max_tokens, *, system=None, temperature=None):
            calls.append({"system": system, "temperature": temperature})
            return '{"_reasoning": "test", "Culoare": "Negru"}'

    monkeypatch.setattr(enricher, "get_router", lambda: FakeRouter())
    monkeypatch.setattr(enricher, "_load_cache", lambda: {"category_map": {}, "char_map": {}, "learned_title_rules": []})
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
    assert result.get("Culoare") == "Negru"
```

- [ ] **Step 2.2: Run to verify failure**

```bash
python -m pytest tests/test_ai_prompt_quality.py::test_build_char_system_prompt_contains_rules -v
```

Expected: FAIL — `_build_char_system_prompt` does not exist yet.

- [ ] **Step 2.3: Add system prompt builders and update calls in `core/ai_enricher.py`**

Add two new functions after `_mp_ctx()`:

```python
def _build_char_system_prompt(marketplace: str) -> str:
    """Static system prompt for characteristic enrichment — never contains product data."""
    return (
        f"Ești un expert în clasificarea produselor pe marketplace-uri.\n"
        f"Marketplace activ: {_mp_ctx(marketplace)}\n\n"
        "REGULI STRICTE:\n"
        "1. Răspunde EXCLUSIV cu JSON valid: {\"Nume caracteristica\": \"valoare\", ...}\n"
        "2. Primul câmp TREBUIE să fie \"_reasoning\": o propoziție scurtă care explică alegerile.\n"
        "3. Pentru câmpuri cu listă de valori: folosești EXACT o valoare din lista permisă.\n"
        "4. Pentru câmpuri libere: folosești valorile în limba locală a marketplace-ului.\n"
        "5. Câmpurile marcate [OBLIGATORIU] se completează cu prioritate maximă.\n"
        "6. Dacă nu poți determina o valoare, omite acea caracteristică.\n"
        "7. Zero text în afara JSON-ului. Fără markdown, fără explicații extra."
    )


def _build_batch_system_prompt(marketplace: str, category_list: list[str]) -> str:
    """Static system prompt for batch category classification."""
    cats_list = "\n".join(category_list)
    return (
        f"Ești un expert în clasificarea produselor pe marketplace-uri.\n"
        f"Marketplace activ: {_mp_ctx(marketplace)}\n\n"
        "CATEGORII DISPONIBILE (copiază EXACT, fără modificări):\n"
        f"{cats_list}\n\n"
        "REGULI STRICTE:\n"
        "1. Răspunde EXCLUSIV cu JSON: {\"1\":\"Categorie\",\"2\":\"Categorie\",...}\n"
        "2. Copiezi EXACT numele categoriei din lista de mai sus.\n"
        "3. Titlurile produselor pot fi în orice limbă — clasifici după tipul produsului, nu după limbă.\n"
        "4. Dacă nicio categorie nu se potrivește, pui null pentru acel produs.\n"
        "5. Zero text în afara JSON-ului."
    )
```

In `_build_prompt()`, remove the static rule text at the end (those rules move to system prompt). The function becomes user-prompt-only — it keeps product data but drops the final instructions paragraph:

Replace the last `prompt +=` block (the one with "Campurile marcate [OBLIGATORIU]...") with:

```python
    prompt += (
        'Completează caracteristicile lipsă. Câmpurile marcate [OBLIGATORIU] au prioritate maximă.'
    )
```

In `_process_batch()`, update the prompt and the `router.complete()` call:

Replace the prompt construction and `router.complete()` call with:

```python
    lines = []
    for i, prod in enumerate(batch, 1):
        desc = re.sub(r"<[^>]+>", " ", prod.get("description") or "").strip()[:80]
        line = f'{i}. "{prod["title"]}"'
        if desc:
            line += f" | {desc}"
        lines.append(line)

    user_prompt = "PRODUSE DE CLASIFICAT:\n" + "\n".join(lines) + "\n"
    system_prompt = _build_batch_system_prompt(marketplace, category_list)

    # ... (rest stays same, but replace router.complete call:)
    raw = router.complete(user_prompt, max_tok, system=system_prompt, temperature=0.2)
```

Also update the logging variable `prompt` → log both system + user (log `system_prompt + "\n---\n" + user_prompt`).

In `enrich_with_ai()`, update the `router.complete()` call:

```python
    system_prompt = _build_char_system_prompt(marketplace)
    raw = get_router().complete(prompt, max_tok, system=system_prompt, temperature=0.2)
```

- [ ] **Step 2.4: Run tests**

```bash
python -m pytest tests/test_ai_prompt_quality.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add core/ai_enricher.py tests/test_ai_prompt_quality.py
git commit -m "feat: add system prompts and temperature=0.2 to AI enrichment calls"
```

---

## Task 3: Reasoning field (_reasoning) in AI responses

**Files:**
- Modify: `core/ai_enricher.py` (`_parse_json` + processing loops)
- Test: `tests/test_ai_prompt_quality.py`

The system prompt already asks for `_reasoning` as the first field. This task ensures `_reasoning` is stripped from results so it doesn't pollute validated chars/categories.

- [ ] **Step 3.1: Add failing test**

```python
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
```

- [ ] **Step 3.2: Run to verify failure**

```bash
python -m pytest tests/test_ai_prompt_quality.py::test_parse_json_strips_reasoning -v
```

Expected: FAIL — `_parse_json` does not strip `_reasoning`.

- [ ] **Step 3.3: Update `_parse_json` in `core/ai_enricher.py`**

Add one line at the end of `_parse_json`, before the final `return {}`:

After the `try/except` block that returns the parsed result, strip `_reasoning` from any successfully parsed dict. Replace the function body:

```python
def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    result = None
    try:
        result = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                result = json.loads(m.group())
            except Exception:
                pass
    if isinstance(result, dict):
        result.pop("_reasoning", None)
        return result
    return {}
```

- [ ] **Step 3.4: Run tests**

```bash
python -m pytest tests/test_ai_prompt_quality.py -v
```

Expected: all tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/ai_enricher.py tests/test_ai_prompt_quality.py
git commit -m "feat: strip _reasoning field from AI JSON responses"
```

---

## Task 4: Levenshtein fuzzy category matching in loader

**Files:**
- Modify: `core/loader.py`
- Test: `tests/test_ai_prompt_quality.py`

Uses `difflib.get_close_matches` (stdlib, no new deps) as the last fallback in `category_id()`. Threshold: 0.82 similarity (tight enough to avoid false positives on short names).

- [ ] **Step 4.1: Add failing test**

```python
def test_category_id_levenshtein_fallback():
    """category_id() must find a category via fuzzy matching as last resort."""
    import pandas as pd
    from core.loader import MarketplaceData

    mp = MarketplaceData("test")
    mp.load_from_dataframes(
        cats=pd.DataFrame({
            "id": [1], "emag_id": [1], "name": ["Tricouri pentru copii"], "parent_id": [None]
        }),
        chars=pd.DataFrame(columns=["id", "category_id", "name", "mandatory"]),
        vals=pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"]),
    )

    # Exact match
    assert mp.category_id("Tricouri pentru copii") == 1

    # Normalized match (diacritics)
    assert mp.category_id("tricouri pentru copii") == 1

    # Fuzzy match (typo / slight variation)
    assert mp.category_id("Tricouri pt copii") is None  # too different
    assert mp.category_id("Tricouri pentr copii") == 1  # close enough


def test_category_id_fuzzy_no_false_positive():
    """Fuzzy match must NOT fire on completely different strings."""
    import pandas as pd
    from core.loader import MarketplaceData

    mp = MarketplaceData("test")
    mp.load_from_dataframes(
        cats=pd.DataFrame({
            "id": [1], "emag_id": [1], "name": ["Pantofi sport"], "parent_id": [None]
        }),
        chars=pd.DataFrame(columns=["id", "category_id", "name", "mandatory"]),
        vals=pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"]),
    )
    assert mp.category_id("Tricouri copii") is None
```

- [ ] **Step 4.2: Run to verify failure**

```bash
python -m pytest tests/test_ai_prompt_quality.py::test_category_id_levenshtein_fallback -v
```

Expected: FAIL — `category_id("Tricouri pentr copii")` returns None, not 1.

- [ ] **Step 4.3: Update `category_id()` in `core/loader.py`**

Add `import difflib` at the top of the file (after existing imports).

Replace the `category_id` method:

```python
def category_id(self, name: str) -> Optional[int]:
    result = self._cat_name_to_id.get(name)
    if result is None:
        result = self._cat_name_normalized.get(_normalize_str(name))
    if result is None and self._cat_name_to_id:
        # Fuzzy fallback: last resort for minor typos / abbreviations
        matches = difflib.get_close_matches(
            name, self._cat_name_to_id.keys(), n=1, cutoff=0.82
        )
        if matches:
            result = self._cat_name_to_id[matches[0]]
    return result
```

- [ ] **Step 4.4: Run tests**

```bash
python -m pytest tests/test_ai_prompt_quality.py -v
```

Expected: all tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add core/loader.py tests/test_ai_prompt_quality.py
git commit -m "feat: add difflib fuzzy fallback in category_id() for minor typo tolerance"
```

---

## Task 5: Final verification

- [ ] **Step 5.1: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS including existing `tests/test_reference_store_duckdb.py`.

- [ ] **Step 5.2: Quick smoke test**

```bash
python -c "
from core.loader import MarketplaceData
from core.ai_enricher import _parse_json, _build_char_system_prompt, _build_batch_system_prompt

# loader fuzzy
import pandas as pd
mp = MarketplaceData('test')
mp.load_from_dataframes(
    pd.DataFrame({'id':[1],'emag_id':[1],'name':['Tricouri copii'],'parent_id':[None]}),
    pd.DataFrame(columns=['id','category_id','name','mandatory']),
    pd.DataFrame(columns=['category_id','characteristic_id','characteristic_name','value']),
)
assert mp.category_id('tricouri copii') == 1, 'normalized fail'
assert mp.category_id('Tricouri copiii') == 1, 'fuzzy fail'

# _parse_json strips reasoning
r = _parse_json('{\"_reasoning\":\"test\",\"Culoare\":\"Negru\"}')
assert '_reasoning' not in r
assert r['Culoare'] == 'Negru'

# system prompts
sp = _build_char_system_prompt('eMAG Romania')
assert 'JSON' in sp and 'OBLIGATORIU' in sp

bp = _build_batch_system_prompt('eMAG Romania', ['Cat A'])
assert 'Cat A' in bp

print('Toate verificarile OK')
"
```

- [ ] **Step 5.3: Final commit**

```bash
git add -A
git commit -m "feat: ai prompt quality — system prompts, temperature, reasoning, multilingual, fuzzy category"
```
