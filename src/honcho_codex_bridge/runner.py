from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, cast

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    CodexConfig,
    Sandbox,
    TransportClosedError,
)
from openai_codex.generated.v2_all import ReasoningEffort

from .config import ModelProfile, Settings
from .sdk_adapter import CodexSdkAdapter


class CodexRunError(RuntimeError):
    pass


class CodexRunTimeout(CodexRunError):
    pass


class BridgeQueueFull(CodexRunError):
    pass


class CodexRateLimited(CodexRunError):
    pass


@dataclass(frozen=True)
class CodexResult:
    content: str
    thread_id: str | None
    usage: dict[str, int]
    duration_ms: int
    queue_wait_ms: int
    model: str = "account-default"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


MINIMAL_BASE_INSTRUCTIONS = """You are a stateless text-generation backend.
The user input contains a serialized conversation. Honor its message roles,
with SYSTEM instructions taking precedence over USER instructions. Produce only
the requested assistant response. Do not invoke runtime tools, inspect the
environment, or add commentary. When JSON is requested, return only valid JSON."""


def _content_as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                raise CodexRunError("Only text message content is supported")
        return "\n".join(parts)
    raise CodexRunError("Only text message content is supported")


def _message_metadata(message: dict[str, Any]) -> str:
    metadata: dict[str, Any] = {}
    for key in ("name", "tool_call_id", "tool_calls"):
        if key in message and message[key] is not None:
            metadata[key] = message[key]
    if not metadata:
        return ""
    return "\nMESSAGE_METADATA_JSON=" + json.dumps(metadata, separators=(",", ":"))


def _normalized_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for tool in tools or []:
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise CodexRunError("Each tool must contain a function name")
        parameters = function.get("parameters", function.get("input_schema", {}))
        normalized.append(
            {
                "name": function["name"],
                "description": str(function.get("description", "")),
                "parameters": parameters if isinstance(parameters, dict) else {},
            }
        )
    return normalized


def _required_tool_name(tool_choice: Any) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return cast(str, function["name"])
    if isinstance(tool_choice.get("name"), str):
        return cast(str, tool_choice["name"])
    return None


def _tool_envelope_schema(tools: list[dict[str, Any]], tool_choice: Any) -> dict[str, Any]:
    names = [tool["name"] for tool in tools]
    required_name = _required_tool_name(tool_choice)
    if required_name is not None:
        if required_name not in names:
            raise CodexRunError(f"Requested unknown tool: {required_name}")
        allowed_names = [required_name]
    elif tool_choice in {"required", "any"}:
        allowed_names = names
    elif tool_choice == "none":
        allowed_names = [""]
    else:
        allowed_names = ["", *names]
    return {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "enum": allowed_names},
            "arguments_json": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["tool_name", "arguments_json", "content"],
        "additionalProperties": False,
    }


def _tool_instructions(tools: list[dict[str, Any]], tool_choice: Any) -> str:
    choice = tool_choice if tool_choice is not None else "auto"
    return (
        "AVAILABLE HONCHO FUNCTIONS (describe a decision; do not execute them):\n"
        + json.dumps(tools, separators=(",", ":"))
        + "\nFUNCTION CHOICE MODE: "
        + json.dumps(choice, separators=(",", ":"))
        + "\nReturn one JSON object with exactly tool_name, arguments_json, and content. "
        "To request a function, set tool_name to its exact name, arguments_json to "
        "a JSON-encoded object satisfying its parameters, and content to an empty string. "
        "To answer without a function, set tool_name to an empty string, "
        'arguments_json to "{}", and content to the final answer.'
    )


def build_codex_prompt(
    messages: list[dict[str, Any]],
    response_format: Any,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    sections = [
        "Act only as a deterministic language-model backend for the messages below.",
        "Do not inspect files, run commands, browse, call tools, or discuss this wrapper.",
        "Honor the message roles: SYSTEM instructions outrank USER instructions.",
        "Return only the assistant response that the supplied conversation requests.",
    ]

    response_type = response_format.get("type") if isinstance(response_format, dict) else None
    if response_type in {"json_object", "json_schema"}:
        sections.append(
            "The final response must be one valid JSON value with no Markdown fence "
            "and no text before or after it."
        )

    sections.append("\nBEGIN SUPPLIED CONVERSATION")
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role", "user")).upper()
        content = _content_as_text(message.get("content", ""))
        sections.append(
            f"\nMESSAGE {index} ROLE={role}{_message_metadata(message)} CONTENT_BEGIN\n"
            f"{content}\n"
            f"MESSAGE {index} CONTENT_END"
        )
    sections.append("\nEND SUPPLIED CONVERSATION")
    normalized_tools = _normalized_tools(tools)
    if normalized_tools:
        sections.append("\n" + _tool_instructions(normalized_tools, tool_choice))
    sections.append("\nProduce the assistant response now.")
    return "\n".join(sections)


def build_app_server_prompt(
    messages: list[dict[str, Any]],
    response_format: Any,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    sections = ["BEGIN CONVERSATION"]
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role", "user")).upper()
        content = _content_as_text(message.get("content", ""))
        sections.append(
            f"MESSAGE {index} ROLE={role}{_message_metadata(message)}\n"
            f"CONTENT_BEGIN\n{content}\nCONTENT_END"
        )
    sections.append("END CONVERSATION")
    normalized_tools = _normalized_tools(tools)
    if normalized_tools:
        sections.append(_tool_instructions(normalized_tools, tool_choice))
    response_type = response_format.get("type") if isinstance(response_format, dict) else None
    if response_type in {"json_object", "json_schema"}:
        sections.append("Return exactly one valid JSON value with no Markdown fence.")
    return "\n\n".join(sections)


def parse_codex_jsonl(raw: bytes) -> tuple[str, str | None, dict[str, int]]:
    content: str | None = None
    thread_id: str | None = None
    usage: dict[str, int] = {}
    failure: str | None = None

    for raw_line in raw.decode("utf-8", errors="replace").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
        elif event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                content = item["text"]
        elif event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = {
                key: int(value) for key, value in event["usage"].items() if isinstance(value, int)
            }
        elif event_type in {"turn.failed", "error"}:
            error = event.get("error")
            if isinstance(error, dict):
                failure = str(error.get("message") or error)
            else:
                failure = str(event.get("message") or error or "unknown Codex error")

    if content is None:
        raise CodexRunError(failure or "Codex returned no final agent message")
    return content, thread_id, usage


class CodexRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._run_lock = asyncio.Lock()
        self._waiting = 0
        self._active = False

    @property
    def status(self) -> dict[str, Any]:
        return {
            "ready": True,
            "active": self._active,
            "waiting": self._waiting,
            "max_queue": self.settings.max_queue,
            "model": self.settings.codex_model or "account-default",
            "reasoning_effort": self.settings.reasoning_effort,
            "runner": "exec",
        }

    async def ensure_ready(self) -> bool:
        return True

    def _build_prompt(
        self,
        messages: list[dict[str, Any]],
        response_format: Any,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
    ) -> str:
        return build_codex_prompt(messages, response_format, tools, tool_choice)

    async def run(
        self,
        messages: list[dict[str, Any]],
        response_format: Any = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        model_alias: str = "codex-deriver",
    ) -> CodexResult:
        profile = self.settings.profile_for(model_alias)
        normalized_tools = _normalized_tools(tools)
        effective_response_format = response_format
        if normalized_tools:
            effective_response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "honcho_tool_decision",
                    "schema": _tool_envelope_schema(normalized_tools, tool_choice),
                },
            }
        prompt = self._build_prompt(messages, response_format, normalized_tools, tool_choice)
        if len(prompt) > self.settings.max_prompt_chars:
            raise CodexRunError(f"Prompt exceeds {self.settings.max_prompt_chars} character limit")
        if self._waiting >= self.settings.max_queue:
            raise BridgeQueueFull("Codex bridge queue is full")

        queued_at = time.monotonic()
        self._waiting += 1
        waiting_counted = True
        try:
            async with self._run_lock:
                self._waiting -= 1
                waiting_counted = False
                queue_wait_ms = int((time.monotonic() - queued_at) * 1000)
                self._active = True
                started_at = time.monotonic()
                try:
                    content, thread_id, usage = await self._invoke(
                        prompt, effective_response_format, profile
                    )
                finally:
                    self._active = False
        except BaseException:
            if waiting_counted:
                self._waiting -= 1
            raise

        tool_calls: list[dict[str, Any]] = []
        if normalized_tools:
            try:
                envelope = json.loads(content)
            except json.JSONDecodeError as exc:
                raise CodexRunError("Codex returned invalid JSON for a tool decision") from exc
            tool_name = envelope.get("tool_name")
            arguments_json = envelope.get("arguments_json")
            final_content = envelope.get("content")
            allowed_names = {tool["name"] for tool in normalized_tools}
            if tool_name:
                try:
                    arguments = json.loads(arguments_json)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise CodexRunError("Codex returned invalid tool arguments JSON") from exc
                if tool_name not in allowed_names or not isinstance(arguments, dict):
                    raise CodexRunError("Codex returned an invalid tool decision")
                tool_calls.append(
                    {
                        "id": f"call_codex_{uuid.uuid4().hex}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, separators=(",", ":")),
                        },
                    }
                )
                content = ""
            elif isinstance(final_content, str):
                content = final_content
            else:
                raise CodexRunError("Codex returned an invalid final tool envelope")

        response_type = response_format.get("type") if isinstance(response_format, dict) else None
        if response_type in {"json_object", "json_schema"}:
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise CodexRunError("Codex returned invalid JSON for a structured request") from exc

        return CodexResult(
            content=content,
            thread_id=thread_id,
            usage=usage,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            queue_wait_ms=queue_wait_ms,
            model=profile.model or "account-default",
            tool_calls=tool_calls,
        )

    async def _invoke(
        self,
        prompt: str,
        response_format: Any = None,
        profile: ModelProfile | None = None,
    ) -> tuple[str, str | None, dict[str, int]]:
        profile = profile or self.settings.profile_for("codex-deriver")
        codex_bin = self.settings.codex_bin or shutil.which("codex")
        if not codex_bin:
            raise CodexRunError(
                "exec runner requires LARIAT_CODEX_BIN or codex on PATH; "
                "the app-server runner uses the bundled SDK runtime"
            )
        command = [
            codex_bin,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--disable",
            "shell_tool",
            "-c",
            'web_search="disabled"',
            "-c",
            f'model_reasoning_effort="{profile.reasoning_effort}"',
            "--cd",
            str(self.settings.workdir),
            "-",
        ]
        if profile.model:
            command[command.index("-c") : command.index("-c")] = ["--model", profile.model]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self.settings.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise CodexRunTimeout(
                f"Codex exceeded {self.settings.timeout_seconds}s timeout"
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if len(detail) > 1000:
                detail = detail[-1000:]
            raise CodexRunError(detail or f"Codex exited with status {process.returncode}")
        return parse_codex_jsonl(stdout)


def _schema_from_response_format(response_format: Any) -> dict[str, Any] | None:
    if not isinstance(response_format, dict) or response_format.get("type") != "json_schema":
        return None
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return None
    schema = json_schema.get("schema")
    return schema if isinstance(schema, dict) else None


def sdk_usage_to_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    last = getattr(usage, "last", None)
    if last is None:
        return {}
    return {
        "input_tokens": int(getattr(last, "input_tokens", 0)),
        "cached_input_tokens": int(getattr(last, "cached_input_tokens", 0)),
        "output_tokens": int(getattr(last, "output_tokens", 0)),
        "reasoning_output_tokens": int(getattr(last, "reasoning_output_tokens", 0)),
    }


class AppServerCodexRunner(CodexRunner):
    """Codex runner backed by one persistent official app-server process."""

    def __init__(
        self,
        settings: Settings,
        codex_factory: Callable[[CodexConfig], AsyncCodex] = AsyncCodex,
    ) -> None:
        super().__init__(settings)
        self._codex: AsyncCodex | None = None
        self._sdk = CodexSdkAdapter(codex_factory)
        self._start_lock = asyncio.Lock()
        self._start_count = 0
        self._recycle_count = 0
        self._completed_on_child = 0
        self._child_started_at: float | None = None
        self._last_recycle_reason: str | None = None
        self._last_transport_error: str | None = None
        self._telemetry_cache: tuple[float, dict[str, Any]] | None = None

    def _process_is_running(self, codex: AsyncCodex | None = None) -> bool:
        candidate = codex if codex is not None else self._codex
        if candidate is None:
            return False
        return self._sdk.process_is_running(candidate)

    @staticmethod
    def _is_transport_failure(exc: BaseException) -> bool:
        current: BaseException | None = exc
        while current is not None:
            if isinstance(
                current,
                (BrokenPipeError, ConnectionError, EOFError, TransportClosedError),
            ):
                return True
            message = str(current).lower()
            if any(
                marker in message
                for marker in (
                    "broken pipe",
                    "process closed stdout",
                    "process is not running",
                    "transport closed",
                )
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @property
    def status(self) -> dict[str, Any]:
        ready = self._process_is_running()
        return {
            **super().status,
            "runner": "app-server",
            "ready": ready,
            "persistent_process": ready,
            "start_count": self._start_count,
            "restart_count": max(0, self._start_count - 1 - self._recycle_count),
            "recycle_count": self._recycle_count,
            "completed_on_child": self._completed_on_child,
            "child_age_seconds": (
                int(time.monotonic() - self._child_started_at)
                if ready and self._child_started_at is not None
                else None
            ),
            "last_recycle_reason": self._last_recycle_reason,
            "last_transport_error": self._last_transport_error,
            "base_instructions": "minimal-override",
        }

    async def start(self) -> None:
        if self._process_is_running():
            return
        async with self._start_lock:
            if self._process_is_running():
                return
            stale = self._codex
            self._codex = None
            if stale is not None:
                with suppress(BaseException):
                    await stale.close()
            config = CodexConfig(
                codex_bin=self.settings.codex_bin,
                cwd=str(self.settings.workdir),
                config_overrides=(
                    "analytics.enabled=false",
                    "features.shell_tool=false",
                    'web_search="disabled"',
                ),
                client_name="lariat",
                client_title="Lariat",
                client_version="0.5.0-rc.1",
            )
            codex = self._sdk.create(config)
            try:
                await codex.__aenter__()
            except BaseException as exc:
                self._last_transport_error = f"startup failed: {exc}"
                with suppress(BaseException):
                    await codex.close()
                raise
            self._codex = codex
            self._start_count += 1
            self._completed_on_child = 0
            self._child_started_at = time.monotonic()
            self._telemetry_cache = None

    async def ensure_ready(self) -> bool:
        if not self._process_is_running():
            try:
                await self.start()
            except BaseException as exc:
                self._last_transport_error = f"startup failed: {exc}"
                return False
        if not self._process_is_running() or self._codex is None:
            return False
        try:
            account = await self._sdk.account_status(self._codex)
        except BaseException as exc:
            self._last_transport_error = f"account check failed: {exc}"
            return False
        return bool(account.get("authenticated"))

    async def telemetry(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._telemetry_cache is not None
            and now - self._telemetry_cache[0] < self.settings.telemetry_cache_seconds
        ):
            return self._telemetry_cache[1]
        if not await self.ensure_ready() or self._codex is None:
            return {"account": {"authenticated": False}, "available": False}
        payload: dict[str, Any] = {"available": True}
        try:
            payload["account"] = await self._sdk.account_status(self._codex)
            payload["rate_limits"] = await self._sdk.rate_limits(self._codex)
            payload["usage"] = await self._sdk.usage(self._codex)
        except BaseException as exc:
            payload["telemetry_error"] = type(exc).__name__
        self._telemetry_cache = (now, payload)
        return payload

    async def _retire(self, codex: AsyncCodex, reason: BaseException) -> None:
        async with self._start_lock:
            if self._codex is not codex:
                return
            self._codex = None
            self._last_transport_error = str(reason) or type(reason).__name__
            with suppress(BaseException):
                await codex.close()
            self._telemetry_cache = None

    async def _recycle_if_needed(self) -> None:
        codex = self._codex
        if codex is None or not self._process_is_running(codex):
            return
        age = time.monotonic() - self._child_started_at if self._child_started_at is not None else 0
        reason: str | None = None
        if (
            self.settings.recycle_requests
            and self._completed_on_child >= self.settings.recycle_requests
        ):
            reason = "request_limit"
        elif self.settings.recycle_seconds and age >= self.settings.recycle_seconds:
            reason = "age_limit"
        if reason is None:
            return
        async with self._start_lock:
            if self._codex is not codex:
                return
            self._codex = None
            try:
                await codex.close()
            finally:
                self._recycle_count += 1
                self._last_recycle_reason = reason
                self._telemetry_cache = None

    async def close(self) -> None:
        async with self._start_lock:
            codex = self._codex
            self._codex = None
            if codex is not None:
                await codex.close()
            self._child_started_at = None

    def _build_prompt(
        self,
        messages: list[dict[str, Any]],
        response_format: Any,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
    ) -> str:
        return build_app_server_prompt(messages, response_format, tools, tool_choice)

    async def _invoke(
        self,
        prompt: str,
        response_format: Any = None,
        profile: ModelProfile | None = None,
    ) -> tuple[str, str | None, dict[str, int]]:
        profile = profile or self.settings.profile_for("codex-deriver")
        await self._recycle_if_needed()
        for attempt in range(2):
            try:
                await self.start()
            except Exception as exc:
                raise CodexRunError(f"Codex app-server failed to start: {exc}") from exc
            codex = self._codex
            if codex is None:
                raise CodexRunError("Codex app-server failed to start")

            try:
                thread = await codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    base_instructions=MINIMAL_BASE_INSTRUCTIONS,
                    cwd=str(self.settings.workdir),
                    ephemeral=True,
                    model=profile.model,
                    sandbox=Sandbox.read_only,
                )
                result = await asyncio.wait_for(
                    thread.run(
                        prompt,
                        approval_mode=ApprovalMode.deny_all,
                        effort=ReasoningEffort(profile.reasoning_effort or "medium"),
                        output_schema=_schema_from_response_format(response_format),
                        sandbox=Sandbox.read_only,
                    ),
                    timeout=self.settings.timeout_seconds,
                )
            except TimeoutError as exc:
                await self._retire(codex, exc)
                raise CodexRunTimeout(
                    f"Codex exceeded {self.settings.timeout_seconds}s timeout"
                ) from exc
            except CodexRunError:
                raise
            except BaseException as exc:
                transport_failed = self._is_transport_failure(exc) or not self._process_is_running(
                    codex
                )
                if transport_failed:
                    await self._retire(codex, exc)
                    if attempt == 0:
                        continue
                if any(
                    marker in str(exc).lower()
                    for marker in ("429", "rate limit", "too many requests", "overloaded")
                ):
                    raise CodexRateLimited(str(exc)) from exc
                raise CodexRunError(f"Codex app-server request failed: {exc}") from exc

            if result.final_response is None:
                raise CodexRunError("Codex app-server returned no final response")
            self._completed_on_child += 1
            return result.final_response, thread.id, sdk_usage_to_dict(result.usage)

        raise CodexRunError("Codex app-server transport recovery failed")


def create_runner(settings: Settings) -> CodexRunner:
    if settings.runner_mode == "exec":
        return CodexRunner(settings)
    return AppServerCodexRunner(settings)
