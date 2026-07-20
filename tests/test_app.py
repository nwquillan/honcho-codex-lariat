from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from honcho_codex_bridge.app import create_app
from honcho_codex_bridge.config import Settings
from honcho_codex_bridge.runner import (
    BridgeQueueFull,
    CodexRateLimited,
    CodexResult,
    CodexRunError,
    CodexRunTimeout,
)


class FakeRunner:
    status = {
        "active": False,
        "waiting": 0,
        "max_queue": 2,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
    }

    async def run(
        self, messages, response_format=None, tools=None, tool_choice=None, model_alias=None
    ) -> CodexResult:
        return CodexResult(
            content='{"observations":[]}',
            thread_id="thread-test",
            usage={
                "input_tokens": 4000,
                "cached_input_tokens": 3000,
                "output_tokens": 20,
                "reasoning_output_tokens": 0,
            },
            duration_ms=50,
            queue_wait_ms=0,
        )


class UnreadyRunner(FakeRunner):
    status = {**FakeRunner.status, "ready": False}

    async def ensure_ready(self) -> bool:
        return False


def settings() -> Settings:
    return Settings(
        api_key="test-secret",
        codex_bin="/bin/false",
        codex_model="gpt-5.6-luna",
        reasoning_effort="none",
        timeout_seconds=30,
        max_queue=2,
        max_prompt_chars=10_000,
        workdir=Path("/tmp"),
    )


def test_requires_authentication() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "codex-deriver", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 401


def test_health_reports_unready_runner() -> None:
    client = TestClient(create_app(settings(), UnreadyRunner()))
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_lists_supported_honcho_model_aliases() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.get("/v1/models", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert {model["id"] for model in response.json()["data"]} == {
        "codex-deriver",
        "codex-summary",
        "codex-dialectic",
        "codex-dream-deduction",
        "codex-dream-induction",
    }


def test_returns_openai_compatible_completion() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={
            "model": "codex-deriver",
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == '{"observations":[]}'
    assert body["usage"]["prompt_tokens_details"]["cached_tokens"] == 3000


class FakeToolRunner(FakeRunner):
    async def run(
        self, messages, response_format=None, tools=None, tool_choice=None, model_alias=None
    ) -> CodexResult:
        return CodexResult(
            content="",
            thread_id="thread-tool",
            usage={"input_tokens": 10, "output_tokens": 4},
            duration_ms=10,
            queue_wait_ms=0,
            tool_calls=[
                {
                    "id": "call-test",
                    "type": "function",
                    "function": {"name": "search_memory", "arguments": '{"query":"tea"}'},
                }
            ],
        )


def test_returns_openai_tool_call() -> None:
    client = TestClient(create_app(settings(), FakeToolRunner()))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={
            "model": "codex-dialectic",
            "messages": [{"role": "user", "content": "What does Alice like?"}],
            "tools": [{"type": "function", "function": {"name": "search_memory"}}],
            "tool_choice": "auto",
        },
    )
    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "search_memory"


def test_streams_one_compatible_final_chunk() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={
            "model": "codex-dialectic",
            "messages": [{"role": "user", "content": "Answer."}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"finish_reason":"stop"' in response.text
    assert "data: [DONE]" in response.text


def test_rejects_streaming_tool_decisions() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={
            "model": "codex-deriver",
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "search"}}],
            "stream": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_liveness_does_not_require_authentication() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.get("/livez")
    assert response.status_code == 200
    assert response.json()["version"] == "0.5.0-rc.1"


def test_unknown_alias_is_rejected() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={"model": "gpt-anything", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] is None


def test_metrics_are_bearer_protected_and_prompt_free() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    denied = client.get("/metrics")
    assert denied.status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert "lariat_info" in response.text
    assert "observations" not in response.text


def test_status_is_bearer_protected() -> None:
    client = TestClient(create_app(settings(), FakeRunner()))
    assert client.get("/status").status_code == 401
    response = client.get("/status", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert set(response.json()["profiles"]) == {
        "codex-deriver",
        "codex-summary",
        "codex-dialectic",
        "codex-dream-deduction",
        "codex-dream-induction",
    }


class ErrorRunner(FakeRunner):
    error: Exception

    async def run(self, *_args, **_kwargs):
        raise self.error


def test_queue_overload_is_openai_shaped_with_retry_after() -> None:
    runner = ErrorRunner()
    runner.error = BridgeQueueFull("full")
    response = TestClient(create_app(settings(), runner)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={"model": "codex-deriver", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"].isdigit()
    assert response.json()["error"]["code"] == "queue_full"


def test_upstream_rate_limit_is_429() -> None:
    runner = ErrorRunner()
    runner.error = CodexRateLimited("upstream busy")
    response = TestClient(create_app(settings(), runner)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={"model": "codex-deriver", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "upstream_rate_limit"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (CodexRunTimeout("late"), 504, "upstream_timeout"),
        (CodexRunError("bad response"), 502, "upstream_error"),
    ],
)
def test_upstream_failures_are_openai_shaped(error, status, code) -> None:
    runner = ErrorRunner()
    runner.error = error
    response = TestClient(create_app(settings(), runner)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={"model": "codex-deriver", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
