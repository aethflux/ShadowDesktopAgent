"""Multi-provider LLM client with Prompt Caching support.

Supports five providers, switchable via environment variables:

- ``vllm``      — local vLLM server (OpenAI-compatible)
- ``openai``    — OpenAI Chat Completions API (automatic prompt caching on stable prefixes)
- ``anthropic`` — Anthropic Messages API with explicit ``cache_control`` breakpoints
- ``minimax``   — MiniMax OpenAI-compatible chat completions
- ``modelscope`` — ModelScope OpenAI-compatible chat completions

The public surface exposes a small unified interface used by the agents:

- ``chat(messages, tools, tool_choice, temperature)`` — tool-calling loop (OpenAI tool schema)
- ``complete_structured(system_prompt, user_prompt)`` — returns parsed JSON dict
- ``complete_structured_messages(messages)``        — same, but caller supplies full messages
- ``extract_message`` / ``extract_text``            — normalize response across providers

Provider-specific responses are converted to the OpenAI shape so that agents
do not need separate branches for each vendor.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import uuid
from typing import Any

import httpx

from app.config import Provider, settings
from app.logging import get_logger

logger = get_logger("services.model_client")

# A single, lazily-initialized AsyncClient is reused across every LLM call so
# that we get HTTP/1.1 keep-alive and a real connection pool. Building one per
# request — as the previous version did — defeats pooling, slows tool-calling
# loops dramatically, and burns ephemeral ports.
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOCK = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        async with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
                _HTTP_CLIENT = httpx.AsyncClient(
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    limits=httpx.Limits(
                        max_connections=32,
                        max_keepalive_connections=16,
                        keepalive_expiry=30.0,
                    ),
                )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
    _HTTP_CLIENT = None


# HTTP statuses where a retry has a real chance of succeeding. 4xx errors that
# are not 408/429 are caller mistakes (bad payload, auth) — we surface them
# immediately instead of looping.
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


# --------------------------------------------------------------------------- #
#  Message normalization helpers
# --------------------------------------------------------------------------- #

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.IGNORECASE | re.DOTALL)


def _strip_reasoning_blocks(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text).strip()


def _append_json_instruction(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cloned = json.loads(json.dumps(messages))
    instruction = "Respond with a single JSON object. No prose."
    for message in reversed(cloned):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{content}\n\n{instruction}"
        elif isinstance(content, list):
            content.append({"type": "text", "text": instruction})
        break
    return cloned


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_reasoning_blocks(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for idx, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[idx:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise
    if not isinstance(value, dict):
        raise ValueError("Structured completion did not return a JSON object.")
    return value

def _content_to_text(content: Any) -> str:
    """Flatten an OpenAI-style content field into plain text."""
    if isinstance(content, str):
        return _strip_reasoning_blocks(content)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(_strip_reasoning_blocks(part.get("text", "")))
                elif "text" in part:
                    parts.append(_strip_reasoning_blocks(str(part["text"])))
        return "".join(parts)
    return ""


def _messages_include_images(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                return True
    return False


def _openai_messages_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert OpenAI-style messages to Anthropic (system, messages) shape.

    Returns ``(system_blocks, anthropic_messages)``. System blocks are returned as
    a list so the caller can attach ``cache_control`` to them.
    """
    system_texts: list[str] = []
    out: list[dict[str, Any]] = []

    # Buffer tool results so consecutive tool messages collapse into a single user turn.
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_texts.append(_content_to_text(content))
            continue

        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id") or msg.get("id") or "",
                    "content": _content_to_text(content),
                }
            )
            continue

        flush_tool_results()

        if role == "user":
            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if url.startswith("data:"):
                            header, _, data = url.partition(",")
                            media_type = header.split(";")[0].removeprefix("data:") or "image/png"
                            blocks.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": data,
                                    },
                                }
                            )
                        else:
                            blocks.append({"type": "text", "text": f"[image: {url}]"})
                out.append({"role": "user", "content": blocks})
            else:
                out.append({"role": "user", "content": _content_to_text(content)})
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = _content_to_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for tool_call in msg.get("tool_calls") or []:
                fn = tool_call.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id") or "",
                        "name": fn.get("name", ""),
                        "input": args,
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue

    flush_tool_results()

    system_blocks = [{"type": "text", "text": text} for text in system_texts if text]
    return system_blocks, out


def _openai_tools_to_anthropic(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") or {}
        converted.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


def _anthropic_response_to_openai(response: dict[str, Any]) -> dict[str, Any]:
    """Shape an Anthropic response like an OpenAI chat completion."""
    content_blocks = response.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content_blocks:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = response.get("usage") or {}
    return {
        "choices": [{"message": message, "finish_reason": response.get("stop_reason")}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        },
        "_provider": "anthropic",
    }


# --------------------------------------------------------------------------- #
#  ModelClient
# --------------------------------------------------------------------------- #

class ModelClient:
    """Provider-agnostic LLM client.

    All public methods return data shaped like an OpenAI chat completion so that
    upstream agents remain provider-agnostic.

    Provider/model resolution is *dynamic*: the client reads ``settings.provider``
    (or ``settings.vision_provider`` when ``purpose='vision'``) on every call,
    so a runtime settings change via ``/api/settings`` is picked up immediately
    without rebuilding the agent graph.

    Pass ``provider=`` or ``model=`` to pin a specific value (mostly used by
    tests). Pass ``purpose='vision'`` to opt into the vision config slot.
    """

    def __init__(
        self,
        provider: Provider | None = None,
        model: str | None = None,
        purpose: str = "default",
    ) -> None:
        self._provider_override = provider
        self._model_override = model
        self._purpose = purpose

    @property
    def provider(self) -> Provider:
        if self._provider_override:
            return self._provider_override
        if self._purpose == "vision":
            return settings.vision_provider
        return settings.provider

    @property
    def model(self) -> str | None:
        return self._model_override

    def resolved_model(self) -> str:
        if self._model_override:
            return self._model_override
        if self._purpose == "vision":
            return settings.vision_model
        return settings.resolved_model(self.provider)

    def supports_vision(self) -> bool:
        model = self.resolved_model().lower()
        if self.provider == "anthropic":
            return True
        if self.provider == "openai":
            return any(
                hint in model
                for hint in ("gpt-4o", "gpt-4.1", "omni", "vision", "vl")
            )
        if self.provider == "vllm":
            return any(
                hint in model
                for hint in ("vl", "vision", "llava", "internvl", "qwen-vl")
            )
        if self.provider == "minimax":
            return any(hint in model for hint in ("vl", "vision"))
        if self.provider == "modelscope":
            return any(
                hint in model
                for hint in ("vl", "vision", "omni", "qwen3-vl", "qwen2.5-vl")
            )
        return False

    def _assert_image_support(self, messages: list[dict[str, Any]]) -> None:
        if not _messages_include_images(messages):
            return
        if self.supports_vision():
            return
        raise ValueError(
            f"Provider '{self.provider}' with model '{self.resolved_model()}' does not support image inputs."
        )

    # ---- HTTP plumbing --------------------------------------------------- #

    async def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        client = await get_http_client()
        max_attempts = max(1, settings.model_max_retries)
        backoff_base = max(0.0, settings.model_retry_backoff_seconds)
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise RuntimeError(f"Network error calling {url} after {attempt} attempts: {exc}") from exc
                await self._sleep_backoff(attempt, backoff_base)
                continue

            if response.is_error:
                detail = response.text.strip()
                if len(detail) > 600:
                    detail = detail[:600] + "..."
                if response.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
                    logger.warning(
                        "Retrying %s after %d (attempt %d/%d): %s",
                        url, response.status_code, attempt, max_attempts, detail,
                    )
                    await self._sleep_backoff(attempt, backoff_base)
                    continue
                raise RuntimeError(f"{response.status_code} from {url}: {detail}")
            return response.json()

        # Loop fell through without returning — should be unreachable, but keep the
        # type-checker happy and surface the last error if it ever happens.
        raise RuntimeError(f"Exhausted retries calling {url}: {last_exc}")

    @staticmethod
    async def _sleep_backoff(attempt: int, base: float) -> None:
        if base <= 0:
            return
        # Exponential backoff with a small jitter so multiple concurrent
        # callers don't synchronize their retries.
        delay = base * (2 ** (attempt - 1)) + random.uniform(0, base / 2)
        await asyncio.sleep(min(delay, 8.0))

    def _openai_headers(self) -> dict[str, str]:
        token = settings.resolved_api_key(self.provider)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _openai_base(self) -> str:
        return settings.resolved_api_base(self.provider)

    def _anthropic_headers(self) -> dict[str, str]:
        return {
            "x-api-key": settings.resolved_api_key("anthropic"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    # ---- OpenAI / vLLM branch ------------------------------------------- #

    async def _openai_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.resolved_model(),
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        return await self._post(
            f"{self._openai_base()}/chat/completions",
            self._openai_headers(),
            payload,
        )

    # ---- Anthropic branch ----------------------------------------------- #

    async def _anthropic_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        force_json: bool = False,
    ) -> dict[str, Any]:
        system_blocks, anthropic_messages = _openai_messages_to_anthropic(messages)

        # Prompt caching: attach cache_control to the *last* system block.
        # One breakpoint is enough — Anthropic caches everything up to that point.
        if settings.enable_prompt_cache and system_blocks:
            system_blocks[-1] = {**system_blocks[-1], "cache_control": {"type": "ephemeral"}}

        if force_json and anthropic_messages:
            # Nudge the model toward strict JSON without response_format support.
            last = anthropic_messages[-1]
            if last["role"] == "user":
                content = last["content"]
                if isinstance(content, str):
                    last["content"] = content + "\n\nRespond with a single JSON object. No prose."
                elif isinstance(content, list):
                    content.append(
                        {"type": "text", "text": "Respond with a single JSON object. No prose."}
                    )

        payload: dict[str, Any] = {
            "model": self.resolved_model(),
            "max_tokens": settings.anthropic_max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }
        if system_blocks:
            payload["system"] = system_blocks
        anthropic_tools = _openai_tools_to_anthropic(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        raw = await self._post(
            f"{settings.resolved_api_base(self.provider)}/v1/messages",
            self._anthropic_headers(),
            payload,
        )
        return _anthropic_response_to_openai(raw)

    # ---- Public API ------------------------------------------------------ #

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        self._assert_image_support(messages)
        if self.provider == "anthropic":
            return await self._anthropic_chat(messages, tools, temperature)
        return await self._openai_chat(messages, tools, tool_choice, temperature)

    async def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.complete_structured_messages(messages, tools=tools)

    async def complete_structured_messages(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._assert_image_support(messages)
        if self.provider == "anthropic":
            response = await self._anthropic_chat(
                messages, tools=tools, temperature=temperature, force_json=True
            )
        elif self.provider in {"minimax", "modelscope"}:
            response = await self._openai_chat(
                _append_json_instruction(messages),
                tools=tools,
                tool_choice="auto" if tools else None,
                temperature=temperature,
                response_format=None,
            )
        else:
            response = await self._openai_chat(
                messages,
                tools=tools,
                tool_choice="auto" if tools else None,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        text = self.extract_text(response)
        return _parse_json_object(text)

    # ---- Response unwrap (stable across providers) ---------------------- #

    def extract_message(self, response: dict[str, Any]) -> dict[str, Any]:
        return response["choices"][0]["message"]

    def extract_text(self, response: dict[str, Any]) -> str:
        return _content_to_text(self.extract_message(response).get("content"))
