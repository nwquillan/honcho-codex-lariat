from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import uvicorn
from openai_codex import AsyncCodex, CodexConfig

from . import __version__
from .app import create_app
from .config import Settings
from .sdk_adapter import CodexSdkAdapter
from .telemetry import configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lariat", description="Honcho-to-Codex bridge")
    parser.add_argument("--version", action="version", version=f"Lariat {__version__}")
    parser.add_argument("--config", type=Path, dest="config_path")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="run the HTTP bridge")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-level")
    serve.add_argument("--model", dest="codex_model")
    serve.add_argument("--reasoning-effort")

    auth = subparsers.add_parser("auth", help="manage ChatGPT OAuth")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    auth_subparsers.add_parser("login", help="start official device-code login")
    auth_subparsers.add_parser("status", help="show sanitized authentication status")

    subparsers.add_parser("doctor", help="validate config, runtime, and OAuth")
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    values = {
        key: getattr(args, key, None)
        for key in ("config_path", "host", "port", "log_level", "codex_model", "reasoning_effort")
    }
    return Settings.from_sources(cli=values, config_path=args.config_path)


def _codex_config(settings: Settings) -> CodexConfig:
    return CodexConfig(
        codex_bin=settings.codex_bin,
        cwd=str(settings.workdir),
        config_overrides=(
            "analytics.enabled=false",
            "features.shell_tool=false",
            'web_search="disabled"',
        ),
        client_name="lariat_cli",
        client_title="Lariat CLI",
        client_version=__version__,
    )


async def _auth_login(settings: Settings) -> int:
    async with AsyncCodex(_codex_config(settings)) as codex:
        handle = await codex.login_chatgpt_device_code()
        print(f"Open: {handle.verification_url}")
        print(f"Code: {handle.user_code}")
        print("Waiting for authorization…", flush=True)
        result = await handle.wait()
        if not result.success:
            print(f"Authentication failed: {result.error or 'unknown error'}", file=sys.stderr)
            return 1
        print("ChatGPT OAuth authentication saved successfully.")
        return 0


async def _auth_status(settings: Settings) -> int:
    adapter = CodexSdkAdapter()
    try:
        async with AsyncCodex(_codex_config(settings)) as codex:
            status = await adapter.account_status(codex, refresh=True)
    except Exception as exc:
        print(json.dumps({"authenticated": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps(status, sort_keys=True))
    return 0 if status.get("authenticated") else 1


async def _doctor(settings: Settings) -> int:
    checks: dict[str, Any] = {
        "version": __version__,
        "configuration": "ok",
        "api_key_configured": bool(settings.api_key),
        "workdir_writable": settings.workdir.is_dir(),
        "sdk_runtime": "pending",
        "chatgpt_oauth": "pending",
    }
    adapter = CodexSdkAdapter()
    try:
        async with AsyncCodex(_codex_config(settings)) as codex:
            checks["sdk_runtime"] = "ok"
            account = await adapter.account_status(codex, refresh=True)
            checks["chatgpt_oauth"] = "ok" if account.get("authenticated") else "missing"
            checks["plan_type"] = account.get("plan_type")
    except Exception as exc:
        checks["sdk_runtime"] = "failed"
        checks["chatgpt_oauth"] = "unknown"
        checks["error"] = type(exc).__name__
    print(json.dumps(checks, indent=2, sort_keys=True))
    return (
        0
        if all(
            (
                checks["api_key_configured"],
                checks["workdir_writable"],
                checks["sdk_runtime"] == "ok",
                checks["chatgpt_oauth"] == "ok",
            )
        )
        else 1
    )


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    command = args.command or "serve"
    try:
        settings = _settings(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if command == "auth":
        result = (
            asyncio.run(_auth_login(settings))
            if args.auth_command == "login"
            else asyncio.run(_auth_status(settings))
        )
        raise SystemExit(result)
    if command == "doctor":
        raise SystemExit(asyncio.run(_doctor(settings)))

    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
