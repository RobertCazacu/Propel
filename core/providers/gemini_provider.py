import os
import requests
from .base import BaseLLMProvider

_DEFAULT_MODEL  = "gemini-2.5-flash"
_API_BASE       = "https://generativelanguage.googleapis.com/v1beta/models"

# Modele cu thinking integrat — necesită max_tokens mai mare și thinkingConfig
_THINKING_MODELS = {"gemini-2.5-flash", "gemini-2.5-pro"}
_DEFAULT_MAX_TOKENS_THINKING = 8192
_DEFAULT_THINKING_BUDGET     = 1024  # tokeni rezervați pentru thinking (0 = dezactivat)


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
        is_thinking = self._model in _THINKING_MODELS
        effective_max = max(max_tokens, _DEFAULT_MAX_TOKENS_THINKING) if is_thinking else max_tokens
        gen_config: dict = {
            "maxOutputTokens": effective_max,
            "temperature": temperature if temperature is not None else 0.2,
        }
        if is_thinking:
            gen_config["thinkingConfig"] = {"thinkingBudget": _DEFAULT_THINKING_BUDGET}
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        resp = requests.post(url, json=body, timeout=60)
        if resp.status_code == 400:
            raise ValueError(f"Gemini API error 400: {resp.text[:300]}")
        if resp.status_code == 403:
            raise PermissionError("GEMINI_API_KEY invalidă sau fără acces.")
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError(f"Gemini: niciun candidat în răspuns. Raw: {str(data)[:300]}")
        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "")
        if finish_reason in ("SAFETY", "RECITATION", "BLOCKED"):
            raise ValueError(f"Gemini: răspuns blocat (finishReason={finish_reason})")
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        if not parts:
            raise ValueError(f"Gemini: parts lipsă în răspuns. Raw: {str(data)[:300]}")
        return parts[0]["text"]
