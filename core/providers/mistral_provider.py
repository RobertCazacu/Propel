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

    def complete(self, prompt: str, max_tokens: int = 300) -> str:
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      self._model,
                "messages":   [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            },
            timeout=30,
        )
        if resp.status_code == 401:
            raise PermissionError("MISTRAL_API_KEY invalidă.")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
