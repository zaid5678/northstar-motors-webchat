"""The OpenAI-compatible adapter, the scripted fake, and the circuit breaker."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from northstar_webchat.llm.adapter import (
    GuardedLLM,
    LLMSettings,
    OpenAICompatibleLLM,
    UnconfiguredLLM,
)
from northstar_webchat.llm.scripted import ScriptedLLM
from northstar_webchat.llm.types import FailureKind, LLMClient, LLMUnavailable, Message

BASE = "http://llm.test/v1"
KEY = "secret-llm-key"
SETTINGS = LLMSettings(base_url=BASE, api_key=KEY, model="test-model")
HELLO = [Message(role="user", content="hello")]
URL = f"{BASE}/chat/completions"


def choice(content: Any, finish: str = "stop") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


@pytest.fixture
def api() -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def llm() -> Any:
    adapter = OpenAICompatibleLLM(SETTINGS)
    yield adapter
    await adapter.aclose()


class TestSettings:
    def test_all_three_come_from_the_environment(self) -> None:
        settings = LLMSettings.from_env(
            {"LLM_BASE_URL": BASE, "LLM_API_KEY": KEY, "LLM_MODEL": "m"}
        )
        assert settings is not None
        assert (settings.base_url, settings.api_key, settings.model) == (BASE, KEY, "m")

    @pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_MODEL"])
    def test_missing_settings_mean_no_llm_not_a_crash(self, missing: str) -> None:
        env = {"LLM_BASE_URL": BASE, "LLM_MODEL": "m"}
        del env[missing]
        assert LLMSettings.from_env(env) is None

    def test_a_local_model_needs_no_key(self) -> None:
        settings = LLMSettings.from_env({"LLM_BASE_URL": BASE, "LLM_MODEL": "m"})
        assert settings is not None

    def test_reasoning_effort_is_optional(self) -> None:
        settings = LLMSettings.from_env(
            {"LLM_BASE_URL": BASE, "LLM_MODEL": "m", "LLM_REASONING_EFFORT": "none"}
        )
        assert settings is not None and settings.reasoning_effort == "none"

    def test_the_key_never_appears_in_repr(self) -> None:
        assert KEY not in repr(SETTINGS)


class TestRequests:
    async def test_it_sends_the_openai_shape(self, api: Any, llm: OpenAICompatibleLLM) -> None:
        route = api.post(URL).mock(httpx.Response(200, json=choice("hi")))
        assert await llm.complete(HELLO) == "hi"
        request = route.calls.last.request
        assert request.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert "response_format" not in body

    async def test_a_schema_asks_for_structured_output(
        self, api: Any, llm: OpenAICompatibleLLM
    ) -> None:
        route = api.post(URL).mock(httpx.Response(200, json=choice("{}")))
        await llm.complete(HELLO, schema={"type": "object"})
        body = json.loads(route.calls.last.request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}

    async def test_reasoning_effort_is_sent_when_set(self, api: Any) -> None:
        route = api.post(URL).mock(httpx.Response(200, json=choice("hi")))
        settings = LLMSettings(base_url=BASE, api_key="", model="m", reasoning_effort="none")
        adapter = OpenAICompatibleLLM(settings)
        await adapter.complete(HELLO)
        await adapter.aclose()
        body = json.loads(route.calls.last.request.content)
        assert body["reasoning_effort"] == "none"

    async def test_no_key_means_no_auth_header(self, api: Any) -> None:
        route = api.post(URL).mock(httpx.Response(200, json=choice("hi")))
        adapter = OpenAICompatibleLLM(LLMSettings(base_url=BASE, api_key="", model="m"))
        await adapter.complete(HELLO)
        await adapter.aclose()
        assert "authorization" not in route.calls.last.request.headers


class TestFailures:
    async def test_429_is_rate_limited_with_its_wait(
        self, api: Any, llm: OpenAICompatibleLLM
    ) -> None:
        api.post(URL).mock(httpx.Response(429, headers={"Retry-After": "7"}))
        with pytest.raises(LLMUnavailable) as caught:
            await llm.complete(HELLO)
        assert caught.value.kind is FailureKind.RATE_LIMITED
        assert caught.value.retry_after == 7.0

    async def test_a_429_is_not_retried_inside_a_customer_turn(
        self, api: Any, llm: OpenAICompatibleLLM
    ) -> None:
        route = api.post(URL).mock(httpx.Response(429))
        with pytest.raises(LLMUnavailable):
            await llm.complete(HELLO)
        assert route.call_count == 1

    @pytest.mark.parametrize(
        ("failure", "kind"),
        [
            (httpx.ReadTimeout("slow"), FailureKind.TIMEOUT),
            (httpx.ConnectError("refused"), FailureKind.UNREACHABLE),
            (httpx.Response(500), FailureKind.SERVER_ERROR),
            (httpx.Response(503), FailureKind.SERVER_ERROR),
            (httpx.Response(401), FailureKind.REJECTED),
            (httpx.Response(404), FailureKind.REJECTED),
        ],
    )
    async def test_failures_are_classified(
        self, api: Any, llm: OpenAICompatibleLLM, failure: Any, kind: FailureKind
    ) -> None:
        api.post(URL).mock(side_effect=[failure])
        with pytest.raises(LLMUnavailable) as caught:
            await llm.complete(HELLO)
        assert caught.value.kind is kind

    @pytest.mark.parametrize(
        "body",
        [
            "not json",
            "{}",
            '{"choices": []}',
            '{"choices": [{"message": {}}]}',
            json.dumps(choice(None)),
            json.dumps(choice("")),
            json.dumps(choice("cut off", "length")),
        ],
    )
    async def test_an_unusable_body_returns_empty_so_validation_rejects_it(
        self, api: Any, llm: OpenAICompatibleLLM, body: str
    ) -> None:
        """A reply can only be trusted if it finished, so a cut-off one counts as empty."""
        api.post(URL).mock(httpx.Response(200, text=body))
        assert await llm.complete(HELLO) == ""

    async def test_the_key_is_not_in_a_failure(self, api: Any, llm: OpenAICompatibleLLM) -> None:
        api.post(URL).mock(httpx.Response(401))
        with pytest.raises(LLMUnavailable) as caught:
            await llm.complete(HELLO)
        assert KEY not in repr(caught.value)


class DripTransport(httpx.AsyncBaseTransport):
    """Sends a little at a time, so no single read ever times out. Only a total limit stops it."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async def drip() -> AsyncIterator[bytes]:
            while True:
                await asyncio.sleep(0.01)
                yield b" "

        return httpx.Response(200, stream=DripStream(drip()))


class DripStream(httpx.AsyncByteStream):
    def __init__(self, source: AsyncIterator[bytes]) -> None:
        self.source = source

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.source:
            yield chunk


class TestDeadline:
    async def test_the_limit_covers_the_whole_call(self) -> None:
        adapter = OpenAICompatibleLLM(
            LLMSettings(base_url=BASE, api_key="", model="m", timeout=0.05),
            transport=DripTransport(),
        )
        started = time.monotonic()
        with pytest.raises(LLMUnavailable) as caught:
            await adapter.complete(HELLO)
        await adapter.aclose()
        assert caught.value.kind is FailureKind.TIMEOUT
        assert time.monotonic() - started < 1.0


class TestScripted:
    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(ScriptedLLM(), LLMClient)
        assert isinstance(OpenAICompatibleLLM(SETTINGS), LLMClient)
        assert isinstance(UnconfiguredLLM(), LLMClient)

    async def test_it_plays_its_script_and_records_calls(self) -> None:
        llm = ScriptedLLM(["one", LLMUnavailable(FailureKind.TIMEOUT), "two"])
        assert await llm.complete(HELLO) == "one"
        with pytest.raises(LLMUnavailable):
            await llm.complete(HELLO)
        assert await llm.complete(HELLO) == "two"
        assert len(llm.calls) == 3

    async def test_running_out_of_script_is_a_test_failure(self) -> None:
        with pytest.raises(AssertionError):
            await ScriptedLLM().complete(HELLO)

    async def test_unconfigured_is_always_unavailable(self) -> None:
        with pytest.raises(LLMUnavailable) as caught:
            await UnconfiguredLLM().complete(HELLO)
        assert caught.value.kind is FailureKind.NOT_CONFIGURED


class Clock:
    def __init__(self) -> None:
        self.moment = 0.0

    def __call__(self) -> float:
        return self.moment


class TestBreaker:
    async def test_a_rate_limit_opens_it_for_the_retry_after(self) -> None:
        clock = Clock()
        inner = ScriptedLLM([LLMUnavailable(FailureKind.RATE_LIMITED, retry_after=7), "back"])
        guarded = GuardedLLM(inner, now=clock)
        with pytest.raises(LLMUnavailable):
            await guarded.complete(HELLO)
        assert guarded.is_open
        with pytest.raises(LLMUnavailable):
            await guarded.complete(HELLO)
        assert len(inner.calls) == 1
        clock.moment = 7.1
        assert await guarded.complete(HELLO) == "back"
        assert not guarded.is_open

    async def test_repeated_failures_open_it(self) -> None:
        clock = Clock()
        down = LLMUnavailable(FailureKind.TIMEOUT)
        inner = ScriptedLLM([down, down, down])
        guarded = GuardedLLM(inner, failures_to_open=3, cooldown=30, now=clock)
        for _ in range(3):
            with pytest.raises(LLMUnavailable):
                await guarded.complete(HELLO)
        assert guarded.is_open

    async def test_a_success_clears_the_count(self) -> None:
        down = LLMUnavailable(FailureKind.TIMEOUT)
        inner = ScriptedLLM([down, "ok", down, "ok", down])
        guarded = GuardedLLM(inner, failures_to_open=2, now=Clock())
        for step in range(5):
            try:
                await guarded.complete(HELLO)
            except LLMUnavailable:
                pass
            assert not guarded.is_open, step

    async def test_a_bad_key_stays_open_rather_than_retrying_every_turn(self) -> None:
        inner = ScriptedLLM([LLMUnavailable(FailureKind.REJECTED)])
        guarded = GuardedLLM(inner, now=Clock())
        with pytest.raises(LLMUnavailable):
            await guarded.complete(HELLO)
        assert guarded.is_open
