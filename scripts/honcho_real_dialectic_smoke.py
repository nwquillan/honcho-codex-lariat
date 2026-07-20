#!/usr/bin/env python3
"""Run a read-only production Honcho dialectic call and print metadata only."""

from __future__ import annotations

import asyncio
import json
import os

from src.cache.client import close_cache, init_cache
from src.dialectic.chat import agentic_chat, agentic_chat_stream


async def main() -> None:
    try:
        workspace = os.environ["SMOKE_WORKSPACE"]
        observer = os.environ["SMOKE_OBSERVER"]
        observed = os.environ["SMOKE_OBSERVED"]
    except KeyError as exc:
        raise ValueError(
            "SMOKE_WORKSPACE, SMOKE_OBSERVER, and SMOKE_OBSERVED are required"
        ) from exc
    query = os.getenv(
        "SMOKE_QUERY",
        "Use the available memory tools to identify one explicit preference of the observed peer.",
    )
    stream = os.getenv("SMOKE_STREAM", "false").lower() == "true"

    await init_cache()
    try:
        if stream:
            chunks = [
                chunk
                async for chunk in agentic_chat_stream(
                    workspace_name=workspace,
                    session_name=None,
                    query=query,
                    observer=observer,
                    observed=observed,
                    reasoning_level="minimal",
                )
            ]
            content = "".join(chunks)
        else:
            chunks = []
            content = await agentic_chat(
                workspace_name=workspace,
                session_name=None,
                query=query,
                observer=observer,
                observed=observed,
                reasoning_level="minimal",
            )
    finally:
        await close_cache()

    print(
        json.dumps(
            {
                "ok": bool(content.strip()),
                "dry_run": True,
                "stream": stream,
                "stream_chunks": len(chunks),
                "answer_chars": len(content),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
