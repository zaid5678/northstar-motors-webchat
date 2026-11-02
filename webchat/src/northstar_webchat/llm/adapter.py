"""OpenAI-compatible chat completions, configured only from the environment.

A failure inside a customer turn is reported at once and never retried here: the turn falls back to
degraded mode (D-006). `GuardedLLM` then stops later turns waiting on a model that is known to be
down or rate-limited.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from .. import ops
from .types import FailureKind, LLMClient, LLMUnavailable, Message


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    reasoning_effort: str | None = None
    timeout: float = 20.0  # A customer is waiting: past this, the turn uses its template.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMSettings | None:
        """None means no model is configured, and the app runs in degraded mode."""
        values = os.environ if env is None else env
        base_url, model = values.get("LLM_BASE_URL"), values.get("LLM_MODEL")
        if not base_url or not model:
            return None
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=values.get("LLM_API_KEY", ""),
            model=model,
            reasoning_effort=values.get("LLM_REASONING_EFFORT") or None,
        )


class OpenAICompatibleLLM:
    def __init__(
        self, settings: LLMSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=settings.timeout, transport=transport)

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        settings = self._settings
        body: dict[str, Any] = {
            "model": settings.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if settings.reasoning_effort:
            body["reasoning_effort"] = settings.reasoning_effort
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema},
            }
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        try:
            # httpx timeouts only limit the gap between bytes, not the whole call.
            async with asyncio.timeout(settings.timeout):
                response = await self._http.post(
                    f"{settings.base_url}/chat/completions", json=body, headers=headers
                )
        except (TimeoutError, httpx.TimeoutException) as error:
            raise LLMUnavailable(FailureKind.TIMEOUT) from error
        except httpx.TransportError as error:
            raise LLMUnavailable(FailureKind.UNREACHABLE) from error
        if response.status_code == 429:
            raise LLMUnavailable(
                FailureKind.RATE_LIMITED, retry_after=_retry_after(response)
            )
        if response.status_code >= 500:
            raise LLMUnavailable(FailureKind.SERVER_ERROR)
        if not response.is_success:
            raise LLMUnavailable(FailureKind.REJECTED)
        return _content(response)

    async def aclose(self) -> None:
        await self._http.aclose()


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(str(response.headers.get("Retry-After", "")))
    except ValueError:
        return None


def _content(response: httpx.Response) -> str:
    """Anything unusable becomes empty, which later validation rejects. A reply cut off by the
    token limit is unusable, because half a sentence can state something untrue."""
    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
    if choice.get("finish_reason") == "length" or not isinstance(content, str):
        return ""
    return content


class UnconfiguredLLM:
    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        raise LLMUnavailable(FailureKind.NOT_CONFIGURED)


IMMEDIATE = frozenset({FailureKind.RATE_LIMITED, FailureKind.REJECTED})


class GuardedLLM:
    """A circuit breaker. While open, calls fail at once, so customers get buttons, not a wait."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        failures_to_open: int = 3,
        cooldown: float = 30.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._failures_to_open = failures_to_open
        self._cooldown = cooldown
        self._now = now
        self._failures = 0
        self._open_until = 0.0
        self._last = FailureKind.UNREACHABLE

    @property
    def is_open(self) -> bool:
        return self._now() < self._open_until

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        if self.is_open:
            raise LLMUnavailable(self._last)
        try:
            reply = await self._inner.complete(messages, schema=schema, max_tokens=max_tokens)
        except LLMUnavailable as error:
            self._last = error.kind
            self._failures += 1
            opened = error.kind in IMMEDIATE or self._failures >= self._failures_to_open
            if opened:
                self._open_until = self._now() + (error.retry_after or self._cooldown)
                self._failures = 0
            ops.warn("llm_failure", kind=error.kind.value, breaker_opened=opened)
            raise
        self._failures = 0
        return reply


def build_llm(env: Mapping[str, str] | None = None) -> LLMClient:
    settings = LLMSettings.from_env(env)
    if settings is None:
        return UnconfiguredLLM()
    return GuardedLLM(OpenAICompatibleLLM(settings))
