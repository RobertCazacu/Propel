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

    # Token match: ALL registry key tokens must be present in marketplace tokens
    # Sort by specificity (more tokens = more specific = tried first)
    sorted_items = sorted(
        registry.items(),
        key=lambda kv: -len(re.split(r"[\s_\-]+", kv[0]))
    )
    for reg_key, lang in sorted_items:
        reg_tokens = set(re.split(r"[\s_\-]+", reg_key.lower()))
        if reg_tokens <= tokens:  # all registry tokens found in marketplace tokens
            return lang

    raise ConfigurationError(
        f"Marketplace '{marketplace}' not found in locale registry ({_REGISTRY_PATH}). "
        f"Add it to config/locale_registry.json."
    )


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
    top_k_candidates: list = field(default_factory=list)  # list[tuple[str, float]]
    needs_review: bool   = False
    soft_review: bool    = False
    low_confidence_autofill: bool = False
    reason: str          = ""


# ── Fuzzy scoring ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lightweight normalization for scoring."""
    try:
        from core.color_mapper.normalize import normalize_color_text
        return normalize_color_text(text)
    except Exception:
        return text.lower().strip()


def _score(query: str, candidate: str) -> float:
    q = _normalize(query)
    c = _normalize(candidate)
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


def _top_k(query: str, allowed_values: list, k: int = 3) -> list:
    """Return top-k (value, score) tuples sorted by score descending."""
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
        allowed_values: list,
        is_mandatory: bool = False,
        char_name: str = "",
        budget_ctx: dict | None = None,   # {"remaining": int, "used": int}
    ) -> ResolutionResult:
        """
        Resolve raw_value to an allowed_value from the marketplace.
        Always returns top_k_candidates even on failure.
        """
        log.debug(
            "[Resolver] resolve() called: char=%r raw=%r mandatory=%s allowed_cnt=%d",
            char_name, raw_value, is_mandatory, len(allowed_values),
        )

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
        norm_raw = _normalize(raw_value)
        for v in allowed_values:
            if _normalize(v) == norm_raw:
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
        top_k: list,
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
            import json as _json
            model = os.getenv("OLLAMA_REPAIR_MODEL", "gemma3")
            log.info(
                "[Pass2/Ollama] Sending repair request for [%s].\n"
                "  Raw input  : %r\n"
                "  Candidates : %s\n"
                "  Model      : %s",
                char_name, raw_value, candidates, model,
            )
            log.debug("[Pass2/Ollama] Full prompt:\n%s", prompt)
            resp = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.0, "num_predict": 50}},
                timeout=15,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            log.debug("[Pass2/Ollama] Raw response: %r", raw[:200])
            parsed = _json.loads(raw)
            choice = parsed.get("choice")
            if choice and choice in candidates:
                log.info(
                    "[Pass2/Ollama] Repair accepted: '%s' → '%s'  (char=%s)",
                    raw_value, choice, char_name,
                )
                return choice
            if choice is None:
                log.info(
                    "[Pass2/Ollama] Ollama chose null — no match found "
                    "(char=%s raw=%r candidates=%s)",
                    char_name, raw_value, candidates,
                )
            else:
                log.debug(
                    "[Pass2/Ollama] Repair rejected — non-candidate returned: %r "
                    "(expected one of %s, char=%s)",
                    choice, candidates, char_name,
                )
            return None
        except Exception as e:
            log.warning("[Resolver] Ollama repair error: %s", e)
            return None
