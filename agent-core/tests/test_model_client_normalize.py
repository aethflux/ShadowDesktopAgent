"""Provider-shape conversion tests for ModelClient.

These don't hit the network — they verify that OpenAI-style messages are
translated to Anthropic's shape correctly (and back), which is the most
bug-prone part of the multi-provider refactor.
"""
from __future__ import annotations

from app.services.model_client import (
    _append_json_instruction,
    _anthropic_response_to_openai,
    _openai_messages_to_anthropic,
    _openai_tools_to_anthropic,
    _parse_json_object,
)


def test_system_message_becomes_system_block() -> None:
    system_blocks, messages = _openai_messages_to_anthropic(
        [
            {"role": "system", "content": "You are Shadow."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert len(system_blocks) == 1
    assert system_blocks[0]["text"] == "You are Shadow."
    assert messages == [{"role": "user", "content": "hi"}]


def test_user_image_becomes_base64_block() -> None:
    _, messages = _openai_messages_to_anthropic(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            }
        ]
    )
    content = messages[0]["content"]
    assert content[0] == {"type": "text", "text": "look at this"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[1]["source"]["data"] == "AAAA"


def test_tool_result_message_is_folded_into_user_turn() -> None:
    _, messages = _openai_messages_to_anthropic(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "screen.capture", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "screen.capture", "content": "/tmp/a.png"},
        ]
    )
    # assistant block with tool_use, then a user block containing tool_result.
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0]["type"] == "tool_use"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][0]["type"] == "tool_result"
    assert messages[1]["content"][0]["tool_use_id"] == "call_1"


def test_openai_tools_converted_to_anthropic_shape() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "screen.capture",
                "description": "capture screen",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    converted = _openai_tools_to_anthropic(tools)
    assert converted == [
        {
            "name": "screen.capture",
            "description": "capture screen",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_anthropic_response_is_reshaped_for_openai_consumers() -> None:
    raw = {
        "content": [
            {"type": "text", "text": "Sure, capturing now."},
            {
                "type": "tool_use",
                "id": "tool_1",
                "name": "screen.capture",
                "input": {},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 0,
        },
    }
    shaped = _anthropic_response_to_openai(raw)
    message = shaped["choices"][0]["message"]
    assert message["content"] == "Sure, capturing now."
    assert message["tool_calls"][0]["function"]["name"] == "screen.capture"
    assert shaped["usage"]["cache_read_input_tokens"] == 80


def test_append_json_instruction_targets_last_user_turn() -> None:
    messages = [
        {"role": "system", "content": "You are Shadow."},
        {"role": "assistant", "content": "Previous reply."},
        {"role": "user", "content": "Classify this."},
    ]
    updated = _append_json_instruction(messages)
    assert updated[-1]["content"].endswith("Respond with a single JSON object. No prose.")


def test_parse_json_object_ignores_think_block() -> None:
    text = '<think>internal reasoning</think>\n{"intent":"conversation","confidence":0.5}'
    parsed = _parse_json_object(text)
    assert parsed["intent"] == "conversation"
