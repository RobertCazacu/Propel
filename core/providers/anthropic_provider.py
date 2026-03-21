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

    def complete(self, prompt: str, max_tokens: int = 300) -> str:
        msg = self._client.messages.create(
            model=_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
