#!/usr/bin/env python3
"""Run one read-only real-message deriver call from inside Honcho.

This imports Honcho's production prompt, response schema, formatter, and OpenAI
backend. It accepts one message on standard input but never writes an
observation or changes a queue record. Output contains aggregate metadata only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from openai import AsyncOpenAI
from src.deriver.prompts import minimal_deriver_prompt
from src.llm.backends.openai import OpenAIBackend
from src.utils.formatting import format_new_turn_with_timestamp, parse_datetime_iso
from src.utils.representation import PromptRepresentation


def _validated_representation(content: object) -> PromptRepresentation:
    if isinstance(content, PromptRepresentation):
        return content
    if isinstance(content, str):
        return PromptRepresentation.model_validate_json(content)
    return PromptRepresentation.model_validate(content)


async def main() -> None:
    base_url = os.environ["CODEX_BRIDGE_BASE_URL"]
    api_key = os.environ["CODEX_BRIDGE_API_KEY"]
    message = json.load(sys.stdin)

    formatted_message = format_new_turn_with_timestamp(
        message["content"],
        parse_datetime_iso(message["created_at"]),
        message["peer_name"],
    )
    prompt = minimal_deriver_prompt(
        peer_id=message["peer_name"],
        messages=formatted_message,
        custom_instructions=None,
    )

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=240.0)
    try:
        backend = OpenAIBackend(client)
        result = await backend.complete(
            model="codex-deriver",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
            response_format=PromptRepresentation,
            extra_params={"structured_output_mode": "json_object"},
        )
    finally:
        await client.close()

    representation = _validated_representation(result.content)
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": True,
                "message_chars": len(message["content"]),
                "prompt_chars": len(prompt),
                "validated_schema": "PromptRepresentation",
                "observation_count": len(representation.explicit),
                "input_tokens": result.input_tokens,
                "cache_read_input_tokens": result.cache_read_input_tokens,
                "output_tokens": result.output_tokens,
                "finish_reason": result.finish_reason,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
