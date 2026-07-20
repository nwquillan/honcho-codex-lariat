from __future__ import annotations

import asyncio
from types import SimpleNamespace

from honcho_codex_bridge.sdk_adapter import CodexSdkAdapter


class Dumpable:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class FakeClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, *, response_model):
        self.calls.append((method, params, response_model.__name__))
        return Dumpable({"method": method})


class FakeCodex:
    def __init__(self):
        self._client = FakeClient()

    async def account(self, *, refresh_token=False):
        account = SimpleNamespace(
            root=SimpleNamespace(
                type="chatgpt",
                email="must-not-be-returned@example.invalid",
                plan_type=SimpleNamespace(value="plus"),
            )
        )
        return SimpleNamespace(account=account, requires_openai_auth=True)


def test_account_status_is_sanitized() -> None:
    result = asyncio.run(CodexSdkAdapter.account_status(FakeCodex(), refresh=True))
    assert result == {
        "authenticated": True,
        "auth_type": "chatgpt",
        "plan_type": "plus",
        "requires_openai_auth": True,
    }
    assert "email" not in result


def test_low_level_telemetry_is_centralized() -> None:
    codex = FakeCodex()
    usage = asyncio.run(CodexSdkAdapter.usage(codex))
    limits = asyncio.run(CodexSdkAdapter.rate_limits(codex))
    assert usage["method"] == "account/usage/read"
    assert limits["method"] == "account/rateLimits/read"
    assert [call[0] for call in codex._client.calls] == [
        "account/usage/read",
        "account/rateLimits/read",
    ]
