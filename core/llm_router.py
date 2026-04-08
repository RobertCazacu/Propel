"""
LLM Router — punct unic de acces pentru toate apelurile AI.

Providerul activ se configurează în .env:
    ACTIVE_PROVIDER=anthropic   # anthropic | ollama | gemini | groq | mistral

Schimbarea providerului:
    - .env + restart aplicație (metoda standard)
    - switch_provider("groq")   (runtime, fără restart)
"""
import os
import threading
from pathlib import Path
from core.app_logger import get_logger
from core.providers.base import BaseLLMProvider

log = get_logger("marketplace.llm_router")

VALID_PROVIDERS = ["anthropic", "openai", "ollama", "gemini", "groq", "mistral"]

# Singleton
_instance: "LLMRouter | None" = None
_instance_lock = threading.Lock()   # P04: double-checked locking


# ── Factory ────────────────────────────────────────────────────────────────────

def _build_provider(name: str) -> BaseLLMProvider:
    name = name.lower().strip()

    if name not in VALID_PROVIDERS:
        raise ValueError(
            f"Provider '{name}' necunoscut.\n"
            f"Provideri valizi: {', '.join(VALID_PROVIDERS)}\n"
            f"Verifică variabila ACTIVE_PROVIDER din .env"
        )

    if name == "anthropic":
        from core.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if name == "openai":
        from core.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "ollama":
        from core.providers.ollama_provider import OllamaProvider
        return OllamaProvider()
    if name == "gemini":
        from core.providers.gemini_provider import GeminiProvider
        return GeminiProvider()
    if name == "groq":
        from core.providers.groq_provider import GroqProvider
        return GroqProvider()
    if name == "mistral":
        from core.providers.mistral_provider import MistralProvider
        return MistralProvider()


# ── Router ─────────────────────────────────────────────────────────────────────

class LLMRouter:
    def __init__(self, provider_name: str | None = None):
        name = provider_name or os.getenv("ACTIVE_PROVIDER", "anthropic")
        self._provider = _build_provider(name)
        log.info("LLM Router pornit — provider activ: %s", self._provider.name)
        print(f"[LLM] Provider activ: {self._provider.name.upper()}")

    # ── Interfața publică ──────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        """
        Trimite prompt-ul la providerul activ și returnează răspunsul text.
        Propagă excepțiile — gestionarea erorilor rămâne la apelant.
        """
        return self._provider.complete(prompt, max_tokens,
                                       system=system, temperature=temperature)

    def is_available(self) -> bool:
        """Verifică dacă providerul activ e configururat și accesibil."""
        return self._provider.is_available()

    def complete_structured(
        self,
        prompt: str,
        schema: dict,
        system: str | None = None,
    ) -> dict | None:
        """Delegă complete_structured la providerul activ.

        Returnează dict conform schemei sau None dacă providerul nu suportă
        structured output sau apelul eșuează — fără crash garantat.
        """
        try:
            return self._provider.complete_structured(prompt, schema, system=system)
        except Exception as exc:
            log.warning("complete_structured failed (provider=%s): %s", self._provider.name, exc)
            return None

    def supports_structured(self) -> bool:
        """Returnează True dacă providerul activ are complete_structured nativ (nu fallback)."""
        return hasattr(self._provider, "complete_structured") and \
               type(self._provider).complete_structured is not \
               __import__("core.providers.base", fromlist=["BaseLLMProvider"]).BaseLLMProvider.complete_structured

    def switch_provider(self, name: str) -> None:
        """Schimbă providerul la runtime fără restart."""
        old = self._provider.name
        self._provider = _build_provider(name)
        log.info("LLM Router: switch %s → %s", old, name)
        print(f"[LLM] Switch provider: {old} → {name.upper()}")


# ── Singleton helpers ──────────────────────────────────────────────────────────

def get_router() -> LLMRouter:
    """Returnează instanța singleton a router-ului (thread-safe via double-checked locking)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:   # P04: a doua verificare sub lock
                _instance = LLMRouter()
    return _instance


def switch_provider(name: str) -> None:
    """Schimbă providerul activ la runtime (shortcut global)."""
    get_router().switch_provider(name)


def reset_router() -> None:
    """Forțează re-inițializarea router-ului (util după schimbare .env)."""
    global _instance
    _instance = None
