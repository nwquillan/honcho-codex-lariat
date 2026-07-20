from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from honcho_codex_bridge.config import Settings
from honcho_codex_bridge.runner import (
    AppServerCodexRunner,
    CodexRunError,
    CodexRunTimeout,
    build_app_server_prompt,
    build_codex_prompt,
    parse_codex_jsonl,
    sdk_usage_to_dict,
)


def test_build_prompt_preserves_roles_and_requests_json() -> None:
    prompt = build_codex_prompt(
        [
            {"role": "system", "content": "Return a profile."},
            {"role": "user", "content": "Alice likes tea."},
        ],
        {"type": "json_object"},
    )
    assert "ROLE=SYSTEM" in prompt
    assert "ROLE=USER" in prompt
    assert "Alice likes tea." in prompt
    assert "valid JSON" in prompt


def test_parse_codex_jsonl_extracts_final_message_and_usage() -> None:
    lines = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"facts":["tea"]}'},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 4000,
                "cached_input_tokens": 3500,
                "output_tokens": 20,
                "reasoning_output_tokens": 5,
            },
        },
    ]
    raw = "\n".join(json.dumps(line) for line in lines).encode()
    content, thread_id, usage = parse_codex_jsonl(raw)
    assert json.loads(content) == {"facts": ["tea"]}
    assert thread_id == "thread-1"
    assert usage["cached_input_tokens"] == 3500


def test_parse_codex_jsonl_raises_on_failed_turn() -> None:
    raw = json.dumps({"type": "turn.failed", "error": {"message": "rate limited"}}).encode()
    with pytest.raises(CodexRunError, match="rate limited"):
        parse_codex_jsonl(raw)


def test_app_server_prompt_is_compact() -> None:
    prompt = build_app_server_prompt(
        [{"role": "user", "content": "Alice likes tea."}],
        {"type": "json_object"},
    )
    assert "Alice likes tea." in prompt
    assert "valid JSON" in prompt
    assert "deterministic language-model backend" not in prompt


def test_app_server_prompt_preserves_tool_history() -> None:
    prompt = build_app_server_prompt(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "search_memory", "arguments": '{"query":"tea"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "Alice likes tea."},
        ],
        None,
        [
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "Search memory",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "auto",
    )
    assert "MESSAGE_METADATA_JSON" in prompt
    assert "call-1" in prompt
    assert "AVAILABLE HONCHO FUNCTIONS" in prompt


def test_sdk_usage_uses_last_turn_not_thread_total() -> None:
    usage = SimpleNamespace(
        last=SimpleNamespace(
            input_tokens=123,
            cached_input_tokens=100,
            output_tokens=9,
            reasoning_output_tokens=0,
        ),
        total=SimpleNamespace(input_tokens=9999),
    )
    assert sdk_usage_to_dict(usage) == {
        "input_tokens": 123,
        "cached_input_tokens": 100,
        "output_tokens": 9,
        "reasoning_output_tokens": 0,
    }


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class FakeThread:
    id = "thread-recovered"

    async def run(self, *_args, **_kwargs):
        return SimpleNamespace(
            final_response="recovered",
            usage=SimpleNamespace(
                last=SimpleNamespace(
                    input_tokens=10,
                    cached_input_tokens=8,
                    output_tokens=2,
                    reasoning_output_tokens=0,
                )
            ),
        )


class FakeCodex:
    def __init__(self, fail_transport: bool) -> None:
        self.fail_transport = fail_transport
        self.process = FakeProcess()
        self._client = SimpleNamespace(_sync=SimpleNamespace(_proc=self.process))
        self.closed = False

    async def __aenter__(self):
        return self

    async def close(self) -> None:
        self.closed = True
        self.process.returncode = 0

    async def thread_start(self, **_kwargs):
        if self.fail_transport:
            self.process.returncode = 1
            raise BrokenPipeError(32, "Broken pipe")
        return FakeThread()


def test_app_server_restarts_once_after_transport_failure() -> None:
    instances: list[FakeCodex] = []

    def factory(_config):
        codex = FakeCodex(fail_transport=not instances)
        instances.append(codex)
        return codex

    runner = AppServerCodexRunner(
        Settings(
            api_key="test",
            codex_bin="/bin/false",
            codex_model="gpt-5.6-luna",
            reasoning_effort="medium",
            timeout_seconds=30,
            max_queue=2,
            max_prompt_chars=10_000,
            workdir=Path("/tmp"),
        ),
        codex_factory=factory,
    )

    content, thread_id, usage = asyncio.run(runner._invoke("hello"))

    assert content == "recovered"
    assert thread_id == "thread-recovered"
    assert usage["cached_input_tokens"] == 8
    assert instances[0].closed is True
    assert runner.status["ready"] is True
    assert runner.status["start_count"] == 2
    assert runner.status["restart_count"] == 1
    assert "Broken pipe" in runner.status["last_transport_error"]


def test_app_server_recycles_after_configured_completion_limit() -> None:
    instances: list[FakeCodex] = []

    def factory(_config):
        codex = FakeCodex(fail_transport=False)
        instances.append(codex)
        return codex

    runner = AppServerCodexRunner(
        Settings(
            api_key="test",
            codex_bin=None,
            codex_model=None,
            reasoning_effort="medium",
            timeout_seconds=30,
            max_queue=2,
            max_prompt_chars=10_000,
            workdir=Path("/tmp"),
            recycle_requests=1,
            recycle_seconds=0,
        ),
        codex_factory=factory,
    )

    asyncio.run(runner._invoke("one"))
    asyncio.run(runner._invoke("two"))

    assert len(instances) == 2
    assert instances[0].closed is True
    assert runner.status["recycle_count"] == 1
    assert runner.status["restart_count"] == 0
    assert runner.status["last_recycle_reason"] == "request_limit"


def test_profile_selects_model_and_reasoning_effort() -> None:
    seen: dict[str, object] = {}

    class CapturingThread(FakeThread):
        async def run(self, *_args, **kwargs):
            seen["effort"] = kwargs["effort"].value
            return await super().run(*_args, **kwargs)

    class CapturingCodex(FakeCodex):
        async def thread_start(self, **kwargs):
            seen["model"] = kwargs["model"]
            return CapturingThread()

    from honcho_codex_bridge.config import ModelProfile

    runner = AppServerCodexRunner(
        Settings(
            api_key="test",
            max_prompt_chars=10_000,
            workdir=Path("/tmp"),
            profiles={
                "codex-summary": ModelProfile(model="summary-test-model", reasoning_effort="high")
            },
        ),
        codex_factory=lambda _config: CapturingCodex(False),
    )
    result = asyncio.run(
        runner.run(
            [{"role": "user", "content": "summarize"}],
            model_alias="codex-summary",
        )
    )
    assert seen == {"model": "summary-test-model", "effort": "high"}
    assert result.model == "summary-test-model"


def test_timeout_retires_child_without_retrying() -> None:
    instances: list[FakeCodex] = []

    class SlowThread(FakeThread):
        async def run(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    class SlowCodex(FakeCodex):
        async def thread_start(self, **_kwargs):
            return SlowThread()

    def factory(_config):
        codex = SlowCodex(False)
        instances.append(codex)
        return codex

    runner = AppServerCodexRunner(
        Settings(
            api_key="test",
            timeout_seconds=0.01,
            max_prompt_chars=10_000,
            workdir=Path("/tmp"),
        ),
        codex_factory=factory,
    )
    with pytest.raises(CodexRunTimeout):
        asyncio.run(runner._invoke("slow"))
    assert len(instances) == 1
    assert instances[0].closed is True


def test_close_gracefully_stops_child() -> None:
    instance = FakeCodex(False)
    runner = AppServerCodexRunner(
        Settings(api_key="test", max_prompt_chars=10_000, workdir=Path("/tmp")),
        codex_factory=lambda _config: instance,
    )

    async def lifecycle() -> None:
        await runner.start()
        await runner.close()

    asyncio.run(lifecycle())
    assert instance.closed is True
    assert runner.status["ready"] is False


def test_structured_request_rejects_malformed_json() -> None:
    class InvalidThread(FakeThread):
        async def run(self, *_args, **_kwargs):
            result = await super().run(*_args, **_kwargs)
            result.final_response = "not json"
            return result

    class InvalidCodex(FakeCodex):
        async def thread_start(self, **_kwargs):
            return InvalidThread()

    runner = AppServerCodexRunner(
        Settings(api_key="test", max_prompt_chars=10_000, workdir=Path("/tmp")),
        codex_factory=lambda _config: InvalidCodex(False),
    )
    with pytest.raises(CodexRunError, match="invalid JSON"):
        asyncio.run(
            runner.run(
                [{"role": "user", "content": "json"}],
                response_format={"type": "json_object"},
            )
        )
