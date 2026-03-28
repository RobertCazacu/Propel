"""Tests for AnthropicProvider.complete_structured() via tool_use."""
import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def provider():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            from core.providers.anthropic_provider import AnthropicProvider
            p = AnthropicProvider.__new__(AnthropicProvider)
            p._client = mock_anthropic_cls.return_value
            yield p


def _make_tool_response(data: dict):
    """Simulează un răspuns Anthropic cu tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = data
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=150, output_tokens=50)
    return response


def test_complete_structured_returns_dict(provider):
    schema = {
        "type": "object",
        "properties": {"Culoare": {"type": "string", "enum": ["Negru", "Alb"]}},
        "required": ["Culoare"],
    }
    provider._client.messages.create.return_value = _make_tool_response({"Culoare": "Negru"})

    result = provider.complete_structured(
        prompt="Produs: tricou negru din bumbac",
        schema=schema,
    )
    assert result == {"Culoare": "Negru"}


def test_complete_structured_calls_tool_use(provider):
    schema = {"type": "object", "properties": {"X": {"type": "string"}}, "required": ["X"]}
    provider._client.messages.create.return_value = _make_tool_response({"X": "val"})

    provider.complete_structured(prompt="test", schema=schema)

    call_kwargs = provider._client.messages.create.call_args[1]
    assert "tools" in call_kwargs
    assert call_kwargs["tool_choice"]["type"] == "tool"


def test_complete_structured_returns_none_on_no_tool_block(provider):
    """Dacă modelul nu returnează tool_use block, returnăm None."""
    block = MagicMock()
    block.type = "text"
    block.text = "Nu am putut genera."
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=50, output_tokens=10)
    provider._client.messages.create.return_value = response

    result = provider.complete_structured(prompt="test", schema={"type": "object", "properties": {}, "required": []})
    assert result is None
