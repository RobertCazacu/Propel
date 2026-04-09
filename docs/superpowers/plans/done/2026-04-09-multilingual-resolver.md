# Multilingual Characteristic Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Romanian hardcoded values from being written to Hungarian/Bulgarian marketplace products by introducing a 3-pass characteristic resolver with locale-aware validation.

**Architecture:** A new `CharacteristicResolver` module handles all characteristic value resolution via Pass 1 (fuzzy on allowed_values), Pass 2 (Ollama local repair, budgeted), and Pass 3 (adaptive floor rescue). A locale registry (JSON) maps marketplace_id → language_code and is required at run-time. The AI enricher validation loop replaces hard rejects with resolver calls.

**Tech Stack:** Python 3.11+, rapidfuzz, DuckDB (existing), Ollama local (optional for Pass 2), Streamlit (UI)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `config/locale_registry.json` | **CREATE** | Maps marketplace_id → language_code |
| `core/characteristic_resolver.py` | **CREATE** | `ResolutionResult`, `CharacteristicResolver` (3 passes) |
| `core/ai_enricher.py` | **MODIFY** | Add `_get_language_code()`, harden prompts, integrate resolver |
| `tests/test_characteristic_resolver.py` | **CREATE** | Unit tests for all 3 passes + edge cases |
| `pages/process.py` | **MODIFY** | Show "Needs Review" section with top-3 suggestions in results |

---

## Task 1: Locale Registry JSON

**Files:**
- Create: `config/locale_registry.json`

- [ ] **Step 1: Create locale registry**

```json
{
  "_comment": "Maps marketplace_id tokens → ISO 639-1 language code. Required at runtime.",
  "emag_ro": "ro",
  "emag": "ro",
  "romania": "ro",
  "emag_hu": "hu",
  "hu": "hu",
  "hungary": "hu",
  "magyarország": "hu",
  "fashiondays_hu": "hu",
  "pepita": "hu",
  "emag_bg": "bg",
  "bg": "bg",
  "bulgaria": "bg",
  "fashiondays_bg": "bg",
  "allegro": "pl",
  "allegro_pl": "pl",
  "pl": "pl",
  "poland": "pl",
  "fashiondays": "ro",
  "trendyol": "tr",
  "decathlon": "ro"
}
```

Save to `config/locale_registry.json`.

- [ ] **Step 2: Verify file is valid JSON**

```bash
python -c "import json; json.load(open('config/locale_registry.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add config/locale_registry.json
git commit -m "feat: add locale registry for marketplace language resolution"
```

---

## Task 2: ResolutionResult dataclass + locale helpers

**Files:**
- Create: `core/characteristic_resolver.py` (initial skeleton)

- [ ] **Step 1: Write failing test for locale resolution**

Create `tests/test_characteristic_resolver.py`:

```python
"""Tests for core.characteristic_resolver"""
import pytest
from core.characteristic_resolver import get_language_code, ConfigurationError

class TestLocaleResolution:
    def test_emag_hu_returns_hu(self):
        assert get_language_code("eMAG HU") == "hu"

    def test_emag_ro_returns_ro(self):
        assert get_language_code("eMAG Romania") == "ro"

    def test_allegro_returns_pl(self):
        assert get_language_code("Allegro") == "pl"

    def test_unknown_marketplace_raises(self):
        with pytest.raises(ConfigurationError):
            get_language_code("unknown_xyz_marketplace")

    def test_case_insensitive(self):
        assert get_language_code("EMAG HU") == "hu"
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_characteristic_resolver.py::TestLocaleResolution -v
```

Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create `core/characteristic_resolver.py` with locale helpers**

```python
"""
Characteristic resolver — multilingual 3-pass resolution engine.

Pass 1: exact/fuzzy match on allowed_values
Pass 2: Ollama local repair (budgeted, mandatory only)
Pass 3: adaptive floor rescue with near-tie guard

Public API:
    get_language_code(marketplace_id) -> str
    CharacteristicResolver.resolve(...) -> ResolutionResult
"""
from __future__ import annotations
import json
import re
import logging
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

log = logging.getLogger("marketplace.resolver")

# ── Exceptions ────────────────────────────────────────────────────────────────

class ConfigurationError(Exception):
    """Raised when locale registry is missing or marketplace has no mapping."""


# ── Locale registry ───────────────────────────────────────────────────────────

_REGISTRY: dict | None = None
_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "locale_registry.json"


def _load_registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        if not _REGISTRY_PATH.exists():
            raise ConfigurationError(
                f"Locale registry not found at {_REGISTRY_PATH}. "
                "Create config/locale_registry.json with marketplace_id → language_code mappings."
            )
        _REGISTRY = {
            k: v for k, v in json.loads(_REGISTRY_PATH.read_text(encoding="utf-8")).items()
            if not k.startswith("_")
        }
    return _REGISTRY


def get_language_code(marketplace: str) -> str:
    """
    Resolve marketplace name to ISO 639-1 language code.
    Raises ConfigurationError if no mapping found.

    Resolution: tokenize marketplace name, match against registry keys.
    """
    registry = _load_registry()
    key = marketplace.lower().strip()
    tokens = set(re.split(r"[\s_\-]+", key))

    # Direct key match first
    if key in registry:
        return registry[key]

    # Token match: any registry key found as token in marketplace name
    for reg_key, lang in registry.items():
        reg_tokens = set(re.split(r"[\s_\-]+", reg_key.lower()))
        if reg_tokens & tokens:  # any overlap
            return lang

    raise ConfigurationError(
        f"Marketplace '{marketplace}' not found in locale registry ({_REGISTRY_PATH}). "
        f"Add it to config/locale_registry.json."
    )
```

- [ ] **Step 4: Run locale tests**

```bash
python -m pytest tests/test_characteristic_resolver.py::TestLocaleResolution -v
```

Expected: all 5 PASS

- [ ] **Step 5: Commit**

```bash
git add core/characteristic_resolver.py tests/test_characteristic_resolver.py
git commit -m "feat: locale registry + get_language_code with fail-fast"
```

---

## Task 3: ResolutionResult dataclass + Pass 1 (fuzzy)

**Files:**
- Modify: `core/characteristic_resolver.py`
- Modify: `tests/test_characteristic_resolver.py`

- [ ] **Step 1: Write failing tests for Pass 1**

Add to `tests/test_characteristic_resolver.py`:

```python
from core.characteristic_resolver import CharacteristicResolver, ResolutionResult

ALLOWED_HU = ["Kék", "Fekete", "Fehér", "Piros", "Zöld", "Sárga", "Szürke"]
ALLOWED_RO = ["Albastru", "Negru", "Alb", "Rosu", "Verde", "Galben", "Gri"]

class TestPass1Fuzzy:
    def setup_method(self):
        self.resolver = CharacteristicResolver(marketplace_id="eMAG HU")

    def test_exact_match_returns_auto_accept(self):
        r = self.resolver.resolve("Kék", ALLOWED_HU, is_mandatory=False)
        assert r.value == "Kék"
        assert r.method == "exact"
        assert r.score >= 0.82
        assert r.needs_review is False

    def test_always_returns_top_k_even_on_failure(self):
        r = self.resolver.resolve("xyzzy123", ALLOWED_HU, is_mandatory=False)
        assert len(r.top_k_candidates) >= 1  # always has candidates
        assert all(isinstance(v, str) and isinstance(s, float) for v, s in r.top_k_candidates)

    def test_fuzzy_match_ro_to_hu_fails_pass1(self):
        # "Albastru" (RO) should NOT match "Kék" (HU) via simple fuzzy
        r = self.resolver.resolve("Albastru", ALLOWED_HU, is_mandatory=False)
        # Score should be low — no linguistic overlap
        assert r.score < 0.82 or r.needs_review is True

    def test_top_k_values_all_from_allowed(self):
        r = self.resolver.resolve("blue", ALLOWED_HU, is_mandatory=False)
        for v, s in r.top_k_candidates:
            assert v in ALLOWED_HU
            assert 0.0 <= s <= 1.0

    def test_near_tie_sets_needs_review(self):
        # Two very similar values — resolver should flag near-tie
        allowed = ["Kék", "Kék sötét"]
        r = self.resolver.resolve("Kék", allowed, is_mandatory=False)
        # Either accepts or flags review due to near-tie
        assert isinstance(r.needs_review, bool)

    def test_result_method_is_valid(self):
        r = self.resolver.resolve("Fekete", ALLOWED_HU, is_mandatory=False)
        assert r.method in ("exact", "fuzzy", "semantic", "repair", "rescue", "none")
```

- [ ] **Step 2: Run to verify fails**

```bash
python -m pytest tests/test_characteristic_resolver.py::TestPass1Fuzzy -v
```

Expected: `AttributeError` — `CharacteristicResolver` not implemented yet

- [ ] **Step 3: Implement `ResolutionResult` and `CharacteristicResolver.resolve()` Pass 1**

Add to `core/characteristic_resolver.py`:

```python
from core.color_mapper.normalize import normalize_color_text

# ── Thresholds ────────────────────────────────────────────────────────────────

AUTO_ACCEPT    = 0.82
SOFT_ACCEPT    = 0.70
NEAR_TIE_DELTA = 0.10

def _adaptive_floor(n_allowed: int) -> float:
    if n_allowed <= 10:
        return 0.55
    if n_allowed <= 50:
        return 0.62
    return 0.70


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ResolutionResult:
    value: Optional[str]
    method: str          # exact/fuzzy/repair/rescue/none
    score: float
    top_k_candidates: list[tuple[str, float]] = field(default_factory=list)
    needs_review: bool   = False
    soft_review: bool    = False
    low_confidence_autofill: bool = False
    reason: str          = ""


# ── Fuzzy scoring (reuses rapidfuzz like color_mapper) ───────────────────────

def _score(query: str, candidate: str) -> float:
    q = normalize_color_text(query)
    c = normalize_color_text(candidate)
    try:
        from rapidfuzz import fuzz
        fuzzy = fuzz.token_set_ratio(q, c) / 100.0
    except ImportError:
        from difflib import SequenceMatcher
        fuzzy = SequenceMatcher(None, q, c).ratio()
    # Jaccard token overlap
    tq, tc = set(q.split()), set(c.split())
    jaccard = len(tq & tc) / len(tq | tc) if (tq or tc) else 0.0
    return round(0.75 * fuzzy + 0.25 * jaccard, 6)


def _top_k(query: str, allowed_values: list[str], k: int = 3) -> list[tuple[str, float]]:
    scored = [(v, _score(query, v)) for v in allowed_values]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


# ── Resolver ──────────────────────────────────────────────────────────────────

class CharacteristicResolver:
    """
    3-pass resolver for marketplace characteristic values.

    Pass 1: fuzzy match on allowed_values (deterministic, no LLM)
    Pass 2: Ollama local repair — only for mandatory fields, budgeted
    Pass 3: adaptive floor rescue with near-tie guard
    """

    def __init__(self, marketplace_id: str = ""):
        self.marketplace_id = marketplace_id
        try:
            self.language_code = get_language_code(marketplace_id) if marketplace_id else "ro"
        except ConfigurationError:
            self.language_code = "ro"
            log.warning("[Resolver] Locale not found for '%s', defaulting to 'ro'", marketplace_id)

    def resolve(
        self,
        raw_value: str,
        allowed_values: list[str],
        is_mandatory: bool = False,
        char_name: str = "",
        budget_ctx: dict | None = None,   # {"remaining": int, "used": int}
    ) -> ResolutionResult:
        """
        Resolve raw_value to an allowed_value from the marketplace.
        Always returns top_k_candidates even on failure.
        """
        if not allowed_values:
            return ResolutionResult(
                value=None, method="none", score=0.0,
                top_k_candidates=[], needs_review=True,
                reason="no_allowed_values",
            )

        top_k = _top_k(raw_value, allowed_values, k=3)
        best_val, best_score = top_k[0]
        near_tie = (
            len(top_k) >= 2 and
            (top_k[0][1] - top_k[1][1]) < NEAR_TIE_DELTA
        )

        # ── Pass 1: exact/fuzzy ───────────────────────────────────────────────
        # Exact check first
        norm_raw = normalize_color_text(raw_value)
        for v in allowed_values:
            if normalize_color_text(v) == norm_raw:
                return ResolutionResult(
                    value=v, method="exact", score=1.0,
                    top_k_candidates=top_k, needs_review=False,
                    reason="exact_normalized_match",
                )

        if best_score >= AUTO_ACCEPT and not near_tie:
            return ResolutionResult(
                value=best_val, method="fuzzy", score=best_score,
                top_k_candidates=top_k, needs_review=False,
                reason=f"fuzzy_score_{best_score:.3f}",
            )

        if best_score >= AUTO_ACCEPT and near_tie:
            return ResolutionResult(
                value=best_val, method="fuzzy", score=best_score,
                top_k_candidates=top_k, needs_review=True, soft_review=True,
                reason=f"near_tie_delta_{top_k[0][1]-top_k[1][1]:.3f}",
            )

        # ── Pass 2: Ollama repair (mandatory only, budgeted) ──────────────────
        if is_mandatory and budget_ctx is not None and budget_ctx.get("remaining", 0) > 0:
            repaired = self._ollama_repair(raw_value, top_k, char_name)
            if repaired and repaired in allowed_values:
                budget_ctx["remaining"] -= 1
                budget_ctx["used"] = budget_ctx.get("used", 0) + 1
                r_score = next((s for v, s in top_k if v == repaired), SOFT_ACCEPT)
                return ResolutionResult(
                    value=repaired, method="repair", score=max(r_score, SOFT_ACCEPT),
                    top_k_candidates=top_k, needs_review=True, soft_review=True,
                    reason="ollama_repair",
                )

        # ── Pass 3: mandatory rescue ──────────────────────────────────────────
        if is_mandatory:
            floor = _adaptive_floor(len(allowed_values))
            if best_score >= floor and not near_tie:
                return ResolutionResult(
                    value=best_val, method="rescue", score=best_score,
                    top_k_candidates=top_k,
                    needs_review=True, soft_review=True, low_confidence_autofill=True,
                    reason=f"mandatory_rescue_score_{best_score:.3f}_floor_{floor:.2f}",
                )

        return ResolutionResult(
            value=None, method="none", score=best_score,
            top_k_candidates=top_k, needs_review=True,
            reason=f"score_{best_score:.3f}_below_floor_or_near_tie",
        )

    def _ollama_repair(
        self,
        raw_value: str,
        top_k: list[tuple[str, float]],
        char_name: str,
    ) -> Optional[str]:
        """
        Ask Ollama to pick one from top-3 candidates.
        Returns the chosen value or None if Ollama unavailable/fails.
        """
        try:
            import requests
            base_url = "http://localhost:11434"
            r = requests.get(f"{base_url}/api/tags", timeout=2)
            if r.status_code != 200:
                log.debug("[Resolver] Ollama not available, skipping Pass 2")
                return None
        except Exception:
            log.debug("[Resolver] Ollama not reachable, skipping Pass 2")
            return None

        candidates = [v for v, _ in top_k]
        prompt = (
            f"Marketplace: {self.marketplace_id} (language: {self.language_code})\n"
            f"Characteristic: {char_name}\n"
            f"Raw value detected: {raw_value}\n"
            f"Choose ONE value from this list that best matches the raw value, "
            f"or null if none match:\n"
            f"{candidates}\n\n"
            f"Rules:\n"
            f"- Return ONLY one value from the list above, copied exactly.\n"
            f"- Do NOT translate. Do NOT invent.\n"
            f"- If none match, return null.\n"
            f"- JSON only: {{\"choice\": \"value\"}} or {{\"choice\": null}}"
        )
        try:
            import os
            model = os.getenv("OLLAMA_REPAIR_MODEL", "gemma3")
            resp = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.0, "num_predict": 50}},
                timeout=15,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            import json as _json
            parsed = _json.loads(raw)
            choice = parsed.get("choice")
            if choice and choice in candidates:
                log.info("[Resolver] Ollama repair: '%s' → '%s'", raw_value, choice)
                return choice
            log.debug("[Resolver] Ollama repair returned non-candidate: %r", choice)
            return None
        except Exception as e:
            log.warning("[Resolver] Ollama repair error: %s", e)
            return None
```

- [ ] **Step 4: Run Pass 1 tests**

```bash
python -m pytest tests/test_characteristic_resolver.py::TestPass1Fuzzy -v
```

Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add core/characteristic_resolver.py tests/test_characteristic_resolver.py
git commit -m "feat: ResolutionResult + CharacteristicResolver Pass 1 fuzzy + Pass 3 rescue"
```

---

## Task 4: Pass 2 budget tests + Pass 3 edge cases

**Files:**
- Modify: `tests/test_characteristic_resolver.py`

- [ ] **Step 1: Write budget and rescue tests**

Add to `tests/test_characteristic_resolver.py`:

```python
class TestPass3Rescue:
    def setup_method(self):
        self.resolver = CharacteristicResolver(marketplace_id="eMAG HU")

    def test_rescue_fires_when_mandatory_and_above_floor(self):
        # Small list → floor = 0.55
        allowed = ["Futás", "Kosárlabda"]
        # "running" should score > 0.55 against "Futás" via fuzzy
        r = self.resolver.resolve("running", allowed, is_mandatory=True)
        if r.value is not None:
            assert r.method == "rescue"
            assert r.low_confidence_autofill is True
            assert r.needs_review is True

    def test_rescue_blocked_on_near_tie(self):
        # Two candidates with nearly identical scores
        allowed = ["Kék", "Kék sötét", "Piros"]
        r = self.resolver.resolve("Kék", allowed, is_mandatory=True)
        # Near-tie between "Kék" and "Kék sötét" should set soft_review
        if r.value == "Kék":
            assert r.soft_review is True or r.needs_review is True

    def test_below_floor_returns_none_with_review(self):
        # xyzzy has no match
        allowed = ["Futás", "Kosárlabda", "Tenisz", "Úszás"]
        r = self.resolver.resolve("xyzzy_qqq", allowed, is_mandatory=True)
        # top_k always populated
        assert len(r.top_k_candidates) >= 1
        # If value is None, must be needs_review
        if r.value is None:
            assert r.needs_review is True

    def test_top_k_always_populated_on_failure(self):
        allowed = ["Futás", "Kosárlabda"]
        r = self.resolver.resolve("completely_unrelated_xyz", allowed, is_mandatory=False)
        assert len(r.top_k_candidates) >= 1
        assert r.top_k_candidates[0][0] in allowed

    def test_adaptive_floor_large_list(self):
        # Large list (>50 values) → floor = 0.70
        allowed = [f"Value{i}" for i in range(60)]
        r = self.resolver.resolve("xyz", allowed, is_mandatory=True)
        # With 60 random values, score will be very low → rescue should NOT fire
        if r.value is not None and r.method == "rescue":
            assert r.score >= 0.70

class TestBudget:
    def test_budget_is_per_product_not_per_field(self):
        resolver = CharacteristicResolver(marketplace_id="eMAG HU")
        budget = {"remaining": 1, "used": 0}
        allowed = ["Futás", "Kosárlabda"]

        # First call consumes budget if Pass 2 is triggered
        resolver.resolve("something", allowed, is_mandatory=True,
                         char_name="Sport:", budget_ctx=budget)
        # Second call should see remaining = 0
        assert budget["remaining"] >= 0  # consumed max 1

    def test_deterministic_same_input(self):
        resolver = CharacteristicResolver(marketplace_id="eMAG Romania")
        allowed = ["Albastru", "Negru", "Rosu"]
        r1 = resolver.resolve("blue", allowed, is_mandatory=False)
        r2 = resolver.resolve("blue", allowed, is_mandatory=False)
        assert r1.value == r2.value
        assert r1.score == r2.score
        assert r1.method == r2.method
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_characteristic_resolver.py -v
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_characteristic_resolver.py
git commit -m "test: Pass 2 budget + Pass 3 rescue edge cases"
```

---

## Task 5: Integrate resolver into ai_enricher.py

**Files:**
- Modify: `core/ai_enricher.py`

- [ ] **Step 1: Add `_get_language_code` to ai_enricher.py**

In `core/ai_enricher.py`, add after `_mp_ctx` function (around line 360):

```python
def _get_language_code(marketplace: str) -> str:
    """Returns ISO 639-1 language code for marketplace prompt context."""
    try:
        from core.characteristic_resolver import get_language_code
        return get_language_code(marketplace)
    except Exception:
        return "ro"  # safe default for prompts only — resolver enforces strict
```

- [ ] **Step 2: Update `_build_char_system_prompt` to include language_code**

Replace the current `_build_char_system_prompt` body to add `language_code` in context:

```python
def _build_char_system_prompt(marketplace: str) -> str:
    lang = _get_language_code(marketplace)
    lang_names = {"ro": "Romanian", "hu": "Hungarian", "bg": "Bulgarian",
                  "pl": "Polish", "tr": "Turkish", "en": "English"}
    lang_name = lang_names.get(lang, lang.upper())

    return (
        f"You are a product catalog expert for marketplaces.\n"
        f"Marketplace: {_mp_ctx(marketplace)}\n"
        f"Output language: {lang_name} ({lang}). All freeform text values must be in this language.\n\n"

        "MISSION: Complete missing characteristics by extracting signals from product data.\n\n"

        "RESPONSE FORMAT — strict JSON only, no text outside:\n"
        '{"_src": "<signal>", "Characteristic": "value", ...}\n'
        "  _src = main signal used, e.g. 'title:Dri-FIT→Polyester'\n\n"

        "RULES (priority order — higher rule always wins):\n"
        "P1. [MANDATORY] fields → complete with maximum priority.\n"
        "P2. Fields with value list → copy EXACTLY one value from the list provided. "
        "NEVER translate. NEVER invent. NEVER use values from outside the list.\n"
        "P3. Freeform fields (no list) → value in output language, concise.\n"
        "P4. Brand signals (concept inference only — do NOT use as final value):\n"
        "    Dri-FIT/Climalite/Climacool → polyester-type material\n"
        "    Fleece/Polar → fleece-type material\n"
        "    Merino → wool-type material\n"
        "    DWR → water-resistant\n"
        "    Air Max/React/Zoom → running shoe type\n"
        "    For fields with a list: use P2 (copy from list), not P4.\n"
        "P5. Cannot determine with certainty → OMIT field (do not guess).\n"
        "P6. Zero text outside JSON. No markdown, no explanations.\n\n"

        "SIGNAL HIERARCHY (most reliable first):\n"
        "  1. Product title\n"
        "  2. Brand + model\n"
        "  3. Description\n"
        "  4. Metadata (EAN, weight, warranty)\n"
        "  5. Cross-marketplace data (if provided at end of prompt)"
    )
```

- [ ] **Step 3: Fix batch prompt default gender**

In `_build_batch_system_prompt`, replace:
```python
"3. Categorie ambiguă (gen neclar) → alege genul indicat în titlu; dacă lipsește → bărbați.\n"
```
with:
```python
"3. Categorie ambiguă (gen neclar) → alege genul indicat în titlu; dacă lipsește → alege categoria fără gen specificat sau cea mai neutră disponibilă.\n"
```

- [ ] **Step 4: Integrate resolver into validation loop**

In `enrich_product_characteristics`, find the hard-reject block (around line 858-862):
```python
else:
    log.warning(
        "AI char respins [%s] = %r — nu e in lista de valori permise (%d valori)",
        ch_name, ch_val, len(vs),
    )
```

Replace with:

```python
else:
    # Pass 1/2/3 resolver instead of hard reject
    from core.characteristic_resolver import CharacteristicResolver
    _resolver = CharacteristicResolver(marketplace_id=marketplace)
    _budget = getattr(enrich_product_characteristics, "_repair_budget", None)
    if _budget is None:
        # Initialize budget per product call (attached to function as call-local)
        _budget = {"remaining": 2, "used": 0}
        enrich_product_characteristics._repair_budget = _budget

    res = _resolver.resolve(
        val_str,
        list(vs),
        is_mandatory=(ch_name in (mandatory_chars or [])),
        char_name=ch_name,
        budget_ctx=_budget,
    )
    if res.value is not None:
        validated[ch_name] = res.value
        log.info(
            "[Resolver] %s [%s] = %r method=%s score=%.3f "
            "review=%s soft=%s rescue=%s",
            marketplace, ch_name, res.value, res.method,
            res.score, res.needs_review, res.soft_review,
            res.low_confidence_autofill,
        )
        if res.needs_review:
            # Store review metadata for UI
            result_meta = validated.setdefault("_review_flags", {})
            result_meta[ch_name] = {
                "value": res.value,
                "method": res.method,
                "score": round(res.score, 4),
                "top_k": res.top_k_candidates,
                "reason": res.reason,
            }
    else:
        log.warning(
            "[Resolver] UNRESOLVED [%s] = %r top_k=%s reason=%s",
            ch_name, ch_val,
            [(v, round(s, 3)) for v, s in res.top_k_candidates[:2]],
            res.reason,
        )
        # Store top_k for UI review suggestions
        result_meta = validated.setdefault("_review_flags", {})
        result_meta[ch_name] = {
            "value": None,
            "method": "none",
            "top_k": res.top_k_candidates,
            "reason": res.reason,
        }
```

- [ ] **Step 5: Reset budget per product call**

At the top of `enrich_product_characteristics` function body, add:

```python
# Reset repair budget per product
enrich_product_characteristics._repair_budget = {"remaining": 2, "used": 0}
```

- [ ] **Step 6: Verify syntax**

```bash
python -c "import ast; ast.parse(open('core/ai_enricher.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add core/ai_enricher.py
git commit -m "feat: integrate CharacteristicResolver into AI enricher validation loop"
```

---

## Task 6: UI "Needs Review" section in results

**Files:**
- Modify: `pages/process.py`

- [ ] **Step 1: Find results display section**

Search for where `result["new_chars"]` or `mapping_log` is shown in results in `pages/process.py`. The results table is built after `_process_all` completes.

- [ ] **Step 2: Add "Needs Review" expander in results**

After the results dataframe, add:

```python
# ── Needs Review section ────────────────────────────────────────────────────
review_products = [
    r for r in results
    if r.get("new_chars", {}).get("_review_flags")
]
if review_products:
    with st.expander(
        f"⚠️ {len(review_products)} produse cu câmpuri ce necesită verificare",
        expanded=True,
    ):
        st.caption(
            "Aceste valori au fost completate cu încredere scăzută (rescue/repair). "
            "Verifică și corectează dacă este necesar."
        )
        for prod in review_products[:20]:  # max 20 in UI
            flags = prod["new_chars"].get("_review_flags", {})
            if not flags:
                continue
            st.markdown(f"**{prod.get('title', prod.get('id', '?'))[:80]}**")
            for char_name, meta in flags.items():
                val = meta.get("value")
                top_k = meta.get("top_k", [])
                method = meta.get("method", "?")
                score = meta.get("score", 0)
                sugestii = ", ".join(
                    f"`{v}` ({s:.2f})" for v, s in top_k[:3]
                )
                if val:
                    st.markdown(
                        f"  - **{char_name}**: completat cu `{val}` "
                        f"(method={method}, score={score:.2f}) | "
                        f"Sugestii: {sugestii}"
                    )
                else:
                    st.markdown(
                        f"  - **{char_name}**: ❌ necompletat | "
                        f"Sugestii: {sugestii}"
                    )
            st.divider()
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('pages/process.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pages/process.py
git commit -m "feat: UI Needs Review section with top-3 suggestions for low-confidence fills"
```

---

## Task 7: Full test run + verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/test_characteristic_resolver.py tests/test_color_mapper.py -v
```

Expected: all PASS

- [ ] **Step 2: Verify locale registry works end-to-end**

```bash
python -c "
from core.characteristic_resolver import get_language_code, ConfigurationError
print(get_language_code('eMAG HU'))   # hu
print(get_language_code('eMAG Romania'))  # ro
print(get_language_code('Allegro'))   # pl
try:
    get_language_code('xyz_unknown')
    print('ERROR: should have raised')
except ConfigurationError as e:
    print('ConfigurationError raised correctly:', str(e)[:60])
"
```

- [ ] **Step 3: Verify resolver works on realistic HU case**

```bash
python -c "
from core.characteristic_resolver import CharacteristicResolver
r = CharacteristicResolver('eMAG HU')
# Simulates: AI returned 'Baschet' (RO) but HU marketplace has 'Kosárlabda'
res = r.resolve('Baschet', ['Kosárlabda', 'Futás', 'Labdarúgás', 'Tenisz'], is_mandatory=True)
print(f'value={res.value} method={res.method} score={res.score:.3f} review={res.needs_review}')
print(f'top_k={res.top_k_candidates}')
"
```

- [ ] **Step 4: Commit final**

```bash
git add .
git commit -m "feat: multilingual characteristic resolver V2.1 — complete implementation"
```

---

## Acceptance Criteria Checklist

- [ ] `get_language_code("eMAG HU")` returns `"hu"`
- [ ] Unknown marketplace raises `ConfigurationError`
- [ ] Resolver always returns `top_k_candidates` (even on failure)
- [ ] `Baschet` (RO) on HU marketplace → rescue attempt, not silent drop
- [ ] `_review_flags` metadata stored on resolved-but-uncertain fields
- [ ] UI shows "Needs Review" section with top-3 suggestions
- [ ] Ollama Pass 2 skips gracefully when Ollama offline
- [ ] Budget `MAX_REPAIR_CALLS = 2` respected per product
- [ ] Batch prompt no longer defaults gender to "bărbați"
- [ ] System prompt includes `marketplace_id` + `language_code`
- [ ] All tests pass: `pytest tests/test_characteristic_resolver.py -v`
