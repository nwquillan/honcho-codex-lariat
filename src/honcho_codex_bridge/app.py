from __future__ import annotations

import hmac
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from . import __version__
from .config import HONCHO_ALIASES, Settings
from .runner import (
    BridgeQueueFull,
    CodexRateLimited,
    CodexRunError,
    CodexRunner,
    CodexRunTimeout,
    create_runner,
)
from .telemetry import Metrics, log_event

LOGGER = logging.getLogger("lariat")


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    stop: str | list[str] | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None


def _openai_usage(raw: dict[str, int]) -> dict[str, Any]:
    input_tokens = raw.get("input_tokens", 0)
    cached_tokens = raw.get("cached_input_tokens", 0)
    output_tokens = raw.get("output_tokens", 0)
    reasoning_tokens = raw.get("reasoning_output_tokens", 0)
    completion_tokens = output_tokens + reasoning_tokens
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": input_tokens + completion_tokens,
        "prompt_tokens_details": {"cached_tokens": cached_tokens},
        "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
    }


def _error(
    status_code: int,
    message: str,
    *,
    error_type: str = "lariat_error",
    code: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
        status_code=status_code,
        headers=headers,
    )


def create_app(settings: Settings | None = None, runner: CodexRunner | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_sources()
    resolved_runner = runner or create_runner(resolved_settings)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        start = getattr(resolved_runner, "start", None)
        if start is not None:
            await start()
        try:
            yield
        finally:
            close = getattr(resolved_runner, "close", None)
            if close is not None:
                await close()

    app = FastAPI(title="Lariat", version=__version__, lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.runner = resolved_runner
    app.state.metrics = metrics

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return _error(
            exc.status_code,
            str(exc.detail),
            error_type="invalid_request_error" if exc.status_code < 500 else "server_error",
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", [])[1:])
        message = first.get("msg", "Invalid request")
        if location:
            message = f"{location}: {message}"
        return _error(400, message, error_type="invalid_request_error")

    @app.middleware("http")
    async def request_log(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.monotonic()
        response = await call_next(request)
        if request.url.path not in {"/livez", "/readyz", "/health", "/metrics"}:
            log_event(
                LOGGER,
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return response

    @app.get("/livez")
    async def livez() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    async def readiness() -> JSONResponse:
        ensure_ready = getattr(resolved_runner, "ensure_ready", None)
        ready = await ensure_ready() if ensure_ready is not None else True
        status = resolved_runner.status
        ready = ready and bool(status.get("ready", True))
        metrics.set_runner({**status, "ready": ready})
        return JSONResponse({"ok": ready, **status}, status_code=200 if ready else 503)

    app.add_api_route("/readyz", readiness, methods=["GET"], response_model=None)
    app.add_api_route("/health", readiness, methods=["GET"], response_model=None)

    @app.get("/status")
    async def status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authenticate(resolved_settings, authorization)
        telemetry = getattr(resolved_runner, "telemetry", None)
        upstream = await telemetry() if telemetry is not None else {"available": False}
        runner_status = resolved_runner.status
        metrics.set_runner(runner_status)
        return {
            "version": __version__,
            "runner": runner_status,
            "upstream": upstream,
            "profiles": {
                alias: {
                    "model": resolved_settings.profile_for(alias).model or "account-default",
                    "reasoning_effort": resolved_settings.profile_for(alias).reasoning_effort,
                }
                for alias in HONCHO_ALIASES
            },
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics(
        authorization: str | None = Header(default=None),
    ) -> str:
        _authenticate(resolved_settings, authorization)
        metrics.set_runner(resolved_runner.status)
        return metrics.render_prometheus()

    @app.get("/v1/models")
    async def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _authenticate(resolved_settings, authorization)
        return {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "owned_by": "lariat"} for model in HONCHO_ALIASES
            ],
        }

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        body: ChatCompletionRequest,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse | StreamingResponse:
        _authenticate(resolved_settings, authorization)
        if body.model not in HONCHO_ALIASES:
            raise HTTPException(status_code=400, detail=f"Unknown Lariat model alias: {body.model}")
        if body.stream and body.tools:
            raise HTTPException(
                status_code=400,
                detail="Streaming tool decisions are not supported; use non-streaming tool calls",
            )

        metrics.request_started(body.model)
        started = time.monotonic()
        try:
            result = await resolved_runner.run(
                body.messages,
                body.response_format,
                body.tools,
                body.tool_choice,
                model_alias=body.model,
            )
        except BridgeQueueFull as exc:
            retry = random.randint(
                resolved_settings.overload_retry_min_seconds,
                resolved_settings.overload_retry_max_seconds,
            )
            metrics.request_finished(
                body.model,
                status="429",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return _error(
                429,
                str(exc),
                error_type="rate_limit_error",
                code="queue_full",
                headers={"Retry-After": str(retry)},
            )
        except CodexRateLimited as exc:
            retry = random.randint(
                resolved_settings.overload_retry_min_seconds,
                resolved_settings.overload_retry_max_seconds,
            )
            metrics.request_finished(
                body.model,
                status="429",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return _error(
                429,
                str(exc),
                error_type="rate_limit_error",
                code="upstream_rate_limit",
                headers={"Retry-After": str(retry)},
            )
        except CodexRunTimeout as exc:
            metrics.request_finished(
                body.model,
                status="504",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return _error(504, str(exc), error_type="server_error", code="upstream_timeout")
        except CodexRunError as exc:
            metrics.request_finished(
                body.model,
                status="502",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return _error(502, str(exc), error_type="server_error", code="upstream_error")

        metrics.request_finished(
            body.model,
            status="200",
            duration_ms=result.duration_ms,
            queue_wait_ms=result.queue_wait_ms,
            usage=result.usage,
        )
        log_event(
            LOGGER,
            "completion",
            alias=body.model,
            status=200,
            duration_ms=result.duration_ms,
            queue_wait_ms=result.queue_wait_ms,
            input_tokens=result.usage.get("input_tokens", 0),
            cached_input_tokens=result.usage.get("cached_input_tokens", 0),
            output_tokens=result.usage.get("output_tokens", 0),
            reasoning_output_tokens=result.usage.get("reasoning_output_tokens", 0),
        )

        completion_id = f"chatcmpl-lariat-{uuid.uuid4().hex}"
        created = int(time.time())
        usage = _openai_usage(result.usage)
        if body.stream:
            return _streaming_completion(completion_id, created, body.model, result.content, usage)

        message: dict[str, Any] = {"role": "assistant", "content": result.content}
        finish_reason = "stop"
        if result.tool_calls:
            message["tool_calls"] = result.tool_calls
            finish_reason = "tool_calls"
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": body.model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": usage,
                "bridge": {
                    "duration_ms": result.duration_ms,
                    "queue_wait_ms": result.queue_wait_ms,
                    "raw_usage": result.usage,
                },
            }
        )

    return app


def _streaming_completion(
    completion_id: str,
    created: int,
    model: str,
    content: str,
    usage: dict[str, Any],
) -> StreamingResponse:
    def event(payload: dict[str, Any]) -> str:
        return "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"

    async def events() -> AsyncIterator[str]:
        base = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
        }
        yield event(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": None,
                    }
                ],
            }
        )
        yield event(
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        yield event({**base, "choices": [], "usage": usage})
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _authenticate(settings: Settings, authorization: str | None) -> None:
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="LARIAT_API_KEY is not configured")
    expected = f"Bearer {settings.api_key}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid Lariat API key")
