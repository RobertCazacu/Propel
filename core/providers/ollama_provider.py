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
                    "model":    self._model,
                    "messages": messages,
                    "stream":   False,
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
