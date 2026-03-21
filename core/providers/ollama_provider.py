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

    def complete(self, prompt: str, max_tokens: int = 300) -> str:
        try:
            resp = requests.post(
                f"{self._base_url}/api/generate",
                json={
                    "model":  self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.1},
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["response"]
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
