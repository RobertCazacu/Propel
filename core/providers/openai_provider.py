import os
import requests
from .base import BaseLLMProvider

_DEFAULT_MODEL = "gpt-4o-mini"
_API_URL       = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self):
        self._key = os.getenv("OPENAI_API_KEY", "").strip()
        if not self._key or self._key.startswith("sk-your"):
            raise ValueError(
                "OPENAI_API_KEY lipsește sau nu este configurată în .env"
            )
        self._model = os.getenv("OPENAI_MODEL", _DEFAULT_MODEL)

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
            raise PermissionError("OPENAI_API_KEY invalidă.")
        if resp.status_code == 429:
            raise RuntimeError("OpenAI rate limit atins. Încearcă mai târziu.")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
