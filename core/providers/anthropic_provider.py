import os
from .base import BaseLLMProvider

_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self):
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key or key.startswith("sk-ant-your"):
            raise ValueError(
                "ANTHROPIC_API_KEY lipsește sau nu este configurată în .env"
            )
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=key)
        except ImportError:
            raise ImportError(
                "Pachetul 'anthropic' nu este instalat. Rulează: pip install anthropic"
            )

    def complete(self, prompt: str, max_tokens: int = 300, *,
                 system: str | None = None,
                 temperature: float | None = None) -> str:
        kwargs = dict(
            model=_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        msg = self._client.messages.create(**kwargs)
        return msg.content[0].text

    _STRUCTURED_MODEL = "claude-sonnet-4-6"

    def complete_structured(
        self,
        prompt: str,
        schema: dict,
        system: str | None = None,
    ) -> dict | None:
        """Structured output via Anthropic tool_use.

        Forțează modelul să returneze JSON conform schemei.
        Folosește claude-sonnet-4-6 indiferent de modelul default al providerului.

        Returns:
            dict conform schemei sau None dacă nu s-a obținut tool_use block.
        """
        tool_def = {
            "name": "fill_characteristics",
            "description": "Completează caracteristicile produsului cu valori corecte.",
            "input_schema": schema,
        }
        kwargs = dict(
            model=self._STRUCTURED_MODEL,
            max_tokens=1024,
            tools=[tool_def],
            tool_choice={"type": "tool", "name": "fill_characteristics"},
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system

        msg = self._client.messages.create(**kwargs)

        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input  # dict deja, nu string

        return None
