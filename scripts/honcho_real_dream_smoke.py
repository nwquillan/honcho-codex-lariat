#!/usr/bin/env python3
"""Exercise a production-shaped dream specialist loop without database writes."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.dreamer.specialists import SPECIALISTS
from src.llm import honcho_llm_call


def _validate_required(tool: dict[str, Any], value: dict[str, Any]) -> None:
    schema = tool["input_schema"]
    missing = [key for key in schema.get("required", []) if key not in value]
    if missing:
        raise ValueError(f"{tool['name']} missing required keys: {missing}")


def _validate_action(name: str, value: dict[str, Any]) -> None:
    observations = value.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError(f"{name} requires a nonempty observations list")
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError(f"{name} observation must be an object")
        required = {"content", "source_ids"}
        if name == "create_observations_deductive":
            required.add("premises")
        else:
            required.update({"sources", "pattern_type", "confidence"})
        missing = required.difference(observation)
        if missing:
            raise ValueError(f"{name} observation missing keys: {sorted(missing)}")
        source_ids = observation.get("source_ids")
        minimum = 2 if name == "create_observations_inductive" else 1
        if not isinstance(source_ids, list) or len(source_ids) < minimum:
            raise ValueError(f"{name} returned too few source_ids")


async def main() -> None:
    specialist_name = os.getenv("SMOKE_SPECIALIST", "deduction")
    if specialist_name not in {"deduction", "induction"}:
        raise ValueError("SMOKE_SPECIALIST must be deduction or induction")

    specialist = SPECIALISTS[specialist_name]
    observed_peer = os.getenv("SMOKE_OBSERVED", "synthetic-peer")
    tools = specialist.get_tools(peer_card_enabled=True)
    tools_by_name = {tool["name"]: tool for tool in tools}
    action_name = {
        "deduction": "create_observations_deductive",
        "induction": "create_observations_inductive",
    }[specialist_name]
    calls: list[str] = []

    async def fake_executor(tool_name: str, tool_input: dict[str, Any]) -> str:
        tool = tools_by_name.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown specialist tool: {tool_name}")
        _validate_required(tool, tool_input)
        calls.append(tool_name)

        if tool_name == action_name:
            _validate_action(tool_name, tool_input)
            return "DRY-RUN action validated successfully. Output only DONE now."
        if tool_name in {"get_recent_observations", "search_memory", "search_messages"}:
            if specialist_name == "deduction":
                return (
                    "[id:obs-rule-1] All production memory services must avoid provider X.\n"
                    "[id:obs-service-2] The deduction specialist is a production memory service.\n"
                    "These are synthetic dry-run observations."
                )
            return (
                "[id:obs-tea-1] The observed peer chose tea on Monday.\n"
                "[id:obs-tea-2] The observed peer chose tea again on Wednesday.\n"
                "These are synthetic dry-run observations."
            )
        return "DRY-RUN: no mutation performed. Continue the requested validation sequence."

    if specialist_name == "deduction":
        validation_task = (
            "Compatibility validation: first call get_recent_observations. Then call "
            "create_observations_deductive with one logically valid conclusion grounded "
            "in the two synthetic IDs returned by that tool. Do not call delete_observations "
            "or update_peer_card. After the dry-run action succeeds, output only DONE."
        )
    else:
        validation_task = (
            "Compatibility validation: first call get_recent_observations. Then call "
            "create_observations_inductive with one preference pattern grounded in both "
            "synthetic IDs returned by that tool. Do not call any other search tool. "
            "After the dry-run action succeeds, output only DONE."
        )

    messages = [
        {
            "role": "system",
            "content": specialist.build_system_prompt(observed_peer, peer_card_enabled=True),
        },
        {
            "role": "user",
            "content": specialist.build_user_prompt(
                observed=observed_peer, hints=None, peer_card=None
            )
            + "\n\n"
            + validation_task,
        },
    ]

    response = await honcho_llm_call(
        model_config=specialist.get_model_config(),
        prompt="",
        max_tokens=specialist.get_max_tokens(),
        tools=tools,
        tool_choice="required",
        tool_executor=fake_executor,
        max_tool_iterations=4,
        messages=messages,
    )
    passed = (
        "get_recent_observations" in calls
        and action_name in calls
        and str(response.content).strip() == "DONE"
    )
    print(
        json.dumps(
            {
                "ok": passed,
                "dry_run": True,
                "specialist": specialist_name,
                "tool_calls": calls,
                "iterations": response.iterations,
                "input_tokens": response.input_tokens,
                "cache_read_input_tokens": response.cache_read_input_tokens,
                "output_tokens": response.output_tokens,
                "content_is_done": str(response.content).strip() == "DONE",
                "database_executor_used": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
