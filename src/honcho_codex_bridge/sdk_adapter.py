from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from openai_codex import AsyncCodex, CodexConfig
from openai_codex.generated.v2_all import (
    GetAccountRateLimitsResponse,
    GetAccountTokenUsageResponse,
)


class CodexSdkAdapter:
    """The only Lariat module allowed to touch unstable SDK internals."""

    def __init__(self, factory: Callable[[CodexConfig], AsyncCodex] = AsyncCodex) -> None:
        self._factory = factory

    def create(self, config: CodexConfig) -> AsyncCodex:
        return self._factory(config)

    @staticmethod
    def process_is_running(codex: Any) -> bool:
        try:
            process = codex._client._sync._proc
        except AttributeError:
            return True
        return process is not None and process.poll() is None

    @staticmethod
    async def account_status(codex: Any, *, refresh: bool = False) -> dict[str, Any]:
        response = await codex.account(refresh_token=refresh)
        if response.account is None:
            return {"authenticated": False, "requires_openai_auth": response.requires_openai_auth}
        account = response.account.root
        plan = getattr(account, "plan_type", None)
        return {
            "authenticated": True,
            "auth_type": getattr(account, "type", "unknown"),
            "plan_type": getattr(plan, "value", plan),
            "requires_openai_auth": response.requires_openai_auth,
        }

    @staticmethod
    async def usage(codex: Any) -> dict[str, Any]:
        response = await codex._client.request(
            "account/usage/read",
            None,
            response_model=GetAccountTokenUsageResponse,
        )
        return cast(
            dict[str, Any],
            response.model_dump(mode="json", by_alias=True, exclude_none=True),
        )

    @staticmethod
    async def rate_limits(codex: Any) -> dict[str, Any]:
        response = await codex._client.request(
            "account/rateLimits/read",
            None,
            response_model=GetAccountRateLimitsResponse,
        )
        return cast(
            dict[str, Any],
            response.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
