import json
import re
from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """Interface comun pentru toți providerii LLM."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Numele providerului (ex: 'anthropic', 'ollama')."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        """
        Trimite prompt-ul și returnează răspunsul text.
        system: mesaj de sistem static (instrucțiuni, context permanent).
        temperature: 0.0–1.0; None = folosește default-ul providerului.
        Ridică excepție dacă apelul eșuează.
        """

    def is_available(self) -> bool:
        """
        Verifică dacă providerul e configurat și accesibil.
        Override în providerii care necesită verificare activă.
        """
        return True

    def complete_structured(
        self,
        prompt: str,
        schema: dict,
        system: str | None = None,
    ) -> dict | None:
        """Completare cu structured output conform JSON Schema.

        Returnează dict conform schemei sau None dacă modelul nu poate genera.
        Implementarea implicită face fallback la complete() + json.loads.
        Override în provideri care suportă tool_use nativ.
        """
        system_msg = system or "Returnează DOAR JSON valid, fără text suplimentar."
        raw = self.complete(prompt, max_tokens=500, system=system_msg)
        try:
            text = raw.strip()
            # P16: regex robust pentru extragere din ```json...``` sau ```...```
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if match:
                text = match.group(1).strip()
            if not text:
                return None
            return json.loads(text)
        except Exception:
            return None
