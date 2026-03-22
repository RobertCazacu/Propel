"""
Vision providers for product-type detection from images.

BaseVisionProvider — abstract interface
OllamaVisionProvider — uses Ollama REST API (llava-phi3 or similar)
MockVisionProvider — always returns "" (used as fallback / in tests)

Only used when enable_product_hint=True. Color analysis does NOT need a provider.
"""
from __future__ import annotations
import io
import base64
from abc import ABC, abstractmethod
from typing import Optional
from PIL import Image
from core.app_logger import get_logger

log = get_logger("marketplace.vision.provider")


class BaseVisionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def analyze(self, img: Image.Image, prompt: str) -> str:
        """Send image to vision model with prompt, return text response."""
        ...

    def is_available(self) -> bool:
        return True


class MockVisionProvider(BaseVisionProvider):
    """Always returns empty string. Used as safe fallback."""
    name = "mock"

    def analyze(self, img: Image.Image, prompt: str) -> str:
        return ""

    def is_available(self) -> bool:
        return True


class OllamaVisionProvider(BaseVisionProvider):
    """
    Calls Ollama's /api/generate endpoint with a base64-encoded image.
    Requires a vision-capable model: llava-phi3, llava, moondream, gemma3, etc.
    """
    name = "ollama"

    def __init__(
        self,
        model: str = "llava-phi3",
        base_url: str = "http://localhost:11434",
        timeout: int = 30,
    ):
        import os
        self._model    = os.getenv("OLLAMA_VISION_MODEL", model)
        self._base_url = os.getenv("OLLAMA_BASE_URL", base_url).rstrip("/")
        self._timeout  = timeout

    def is_available(self) -> bool:
        try:
            import requests
            r = requests.get(f"{self._base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def analyze(self, img: Image.Image, prompt: str) -> str:
        import requests
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        payload = {
            "model":  self._model,
            "prompt": prompt,
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.05, "num_predict": 80},
        }
        try:
            resp = requests.post(
                f"{self._base_url}/api/generate",
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            log.warning("OllamaVisionProvider.analyze error: %s", e)
            return ""


def build_vision_provider(
    provider_name: str = "ollama",
    model: str = "llava-phi3",
    base_url: str = "http://localhost:11434",
) -> BaseVisionProvider:
    """
    Factory. Falls back to MockVisionProvider if the requested provider
    is unavailable (Ollama not running, model not pulled, etc.).
    """
    if provider_name == "ollama":
        p = OllamaVisionProvider(model=model, base_url=base_url)
        if p.is_available():
            log.info("Vision provider: Ollama (%s)", p._model)
            return p
        log.warning(
            "Ollama vision provider not reachable at %s — falling back to Mock. "
            "Run 'ollama serve' and pull a vision model (e.g. 'ollama pull llava-phi3').",
            base_url,
        )
    return MockVisionProvider()
