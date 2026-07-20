#!/usr/bin/env python3
"""Run one production Honcho short-summary call without saving it."""

from __future__ import annotations

import asyncio
import json
import sys

from src.utils.formatting import format_new_turn_with_timestamp, parse_datetime_iso
from src.utils.summarizer import create_short_summary
from src.utils.tokens import estimate_tokens


async def main() -> None:
    message = json.load(sys.stdin)
    formatted = format_new_turn_with_timestamp(
        message["content"],
        parse_datetime_iso(message["created_at"]),
        message["peer_name"],
    )
    result = await create_short_summary(
        formatted_messages=formatted,
        input_tokens=estimate_tokens(formatted),
        previous_summary=None,
        workspace_name=message["workspace_name"],
    )
    content = str(result.content)
    print(
        json.dumps(
            {
                "ok": bool(content.strip()),
                "dry_run": True,
                "message_chars": len(message["content"]),
                "summary_chars": len(content),
                "input_tokens": result.input_tokens,
                "cache_read_input_tokens": result.cache_read_input_tokens,
                "output_tokens": result.output_tokens,
                "finish_reasons": result.finish_reasons,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
