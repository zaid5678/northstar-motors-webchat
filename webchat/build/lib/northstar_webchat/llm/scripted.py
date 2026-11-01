"""A model that says exactly what a test tells it to, including lies."""

from __future__ import annotations

from collections.abc import Sequence

from .types import LLMUnavailable, Message


class ScriptedLLM:
    def __init__(self, script: Sequence[str | LLMUnavailable] = ()) -> None:
        self._script = list(script)
        self.calls: list[list[Message]] = []
        self.schemas: list[dict[str, object] | None] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        max_tokens: int = 600,
    ) -> str:
        self.calls.append(messages)
        self.schemas.append(schema)
        assert self._script, "the model was called more times than this test expected"
        step = self._script.pop(0)
        if isinstance(step, LLMUnavailable):
            raise step
        return step
