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
