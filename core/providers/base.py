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
