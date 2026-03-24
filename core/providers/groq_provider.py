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
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       self._model,
                "messages":    messages,
                "max_tokens":  max_tokens,
                "temperature": temperature if temperature is not None else 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        if resp.status_code == 401:
            raise PermissionError("GROQ_API_KEY invalidă.")
        if resp.status_code == 429:
            raise RuntimeError("Groq rate limit atins. Așteaptă sau treci la alt provider.")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
