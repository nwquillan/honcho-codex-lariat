from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

HONCHO_ALIASES = (
    "codex-deriver",
    "codex-summary",
    "codex-dialectic",
    "codex-dream-deduction",
    "codex-dream-induction",
)
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


@dataclass(frozen=True)
class ModelProfile:
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    codex_bin: str | None = None
    codex_model: str | None = None
    reasoning_effort: str = "medium"
    timeout_seconds: int = 180
    max_queue: int = 32
    max_prompt_chars: int = 250_000
    workdir: Path = Path("/tmp/lariat-workdir")
    runner_mode: str = "app-server"
    host: str = "127.0.0.1"
    port: int = 5055
    log_level: str = "info"
    recycle_requests: int = 50
    recycle_seconds: int = 21_600
    telemetry_cache_seconds: int = 60
    overload_retry_min_seconds: int = 2
    overload_retry_max_seconds: int = 10
    config_path: Path = Path("/etc/lariat/lariat.toml")
    profiles: Mapping[str, ModelProfile] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        """Backward-compatible environment-only constructor."""
        return cls.from_sources()

    @classmethod
    def from_sources(
        cls,
        *,
        cli: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        config_path: Path | str | None = None,
    ) -> Settings:
        env = dict(os.environ if environ is None else environ)
        cli_values = {key: value for key, value in (cli or {}).items() if value is not None}
        selected_path = Path(
            config_path
            or cli_values.get("config_path")
            or env.get("LARIAT_CONFIG")
            or env.get("CODEX_BRIDGE_CONFIG")
            or "/etc/lariat/lariat.toml"
        ).expanduser()
        toml_values, toml_profiles = _load_toml(selected_path)

        values: dict[str, Any] = {
            "config_path": selected_path,
            **toml_values,
        }
        env_mapping = {
            "api_key": ("LARIAT_API_KEY", "CODEX_BRIDGE_API_KEY"),
            "codex_bin": ("LARIAT_CODEX_BIN", "CODEX_BRIDGE_CODEX_BIN"),
            "codex_model": ("LARIAT_MODEL", "CODEX_BRIDGE_MODEL"),
            "reasoning_effort": (
                "LARIAT_REASONING_EFFORT",
                "CODEX_BRIDGE_REASONING_EFFORT",
            ),
            "timeout_seconds": (
                "LARIAT_TIMEOUT_SECONDS",
                "CODEX_BRIDGE_TIMEOUT_SECONDS",
            ),
            "max_queue": ("LARIAT_MAX_QUEUE", "CODEX_BRIDGE_MAX_QUEUE"),
            "max_prompt_chars": (
                "LARIAT_MAX_PROMPT_CHARS",
                "CODEX_BRIDGE_MAX_PROMPT_CHARS",
            ),
            "workdir": ("LARIAT_WORKDIR", "CODEX_BRIDGE_WORKDIR"),
            "runner_mode": ("LARIAT_RUNNER", "CODEX_BRIDGE_RUNNER"),
            "host": ("LARIAT_HOST", "CODEX_BRIDGE_HOST"),
            "port": ("LARIAT_PORT", "CODEX_BRIDGE_PORT"),
            "log_level": ("LARIAT_LOG_LEVEL", "CODEX_BRIDGE_LOG_LEVEL"),
            "recycle_requests": ("LARIAT_RECYCLE_REQUESTS",),
            "recycle_seconds": ("LARIAT_RECYCLE_SECONDS",),
            "telemetry_cache_seconds": ("LARIAT_TELEMETRY_CACHE_SECONDS",),
            "overload_retry_min_seconds": ("LARIAT_OVERLOAD_RETRY_MIN_SECONDS",),
            "overload_retry_max_seconds": ("LARIAT_OVERLOAD_RETRY_MAX_SECONDS",),
        }
        for field_name, names in env_mapping.items():
            for name in names:
                if name in env:
                    values[field_name] = env[name]
                    break
        values.update(cli_values)

        profile_values = dict(toml_profiles)
        for alias in HONCHO_ALIASES:
            prefix = alias.removeprefix("codex-").replace("-", "_").upper()
            model = env.get(f"LARIAT_PROFILE_{prefix}_MODEL")
            effort = env.get(f"LARIAT_PROFILE_{prefix}_REASONING_EFFORT")
            if model is not None or effort is not None:
                previous = profile_values.get(alias, ModelProfile())
                profile_values[alias] = ModelProfile(
                    model=model if model is not None else previous.model,
                    reasoning_effort=(effort if effort is not None else previous.reasoning_effort),
                )
        values["profiles"] = profile_values
        return _validated_settings(cls(**_coerce_values(values)))

    def profile_for(self, alias: str) -> ModelProfile:
        profile = self.profiles.get(alias, ModelProfile())
        return ModelProfile(
            model=profile.model if profile.model is not None else self.codex_model,
            reasoning_effort=(
                profile.reasoning_effort
                if profile.reasoning_effort is not None
                else self.reasoning_effort
            ),
        )

    def with_overrides(self, **values: Any) -> Settings:
        return _validated_settings(
            replace(self, **{key: value for key, value in values.items() if value is not None})
        )


def _load_toml(path: Path) -> tuple[dict[str, Any], dict[str, ModelProfile]]:
    if not path.exists():
        return {}, {}
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    server = document.get("server", {})
    codex = document.get("codex", {})
    runtime = document.get("runtime", {})
    if not all(isinstance(section, dict) for section in (server, codex, runtime)):
        raise ValueError("Lariat TOML sections must be tables")
    values = {
        **{key: server[key] for key in ("host", "port", "api_key", "log_level") if key in server},
        **{
            key: codex[key]
            for key in ("bin", "model", "reasoning_effort", "workdir", "runner")
            if key in codex
        },
        **{
            key: runtime[key]
            for key in (
                "timeout_seconds",
                "max_queue",
                "max_prompt_chars",
                "recycle_requests",
                "recycle_seconds",
                "telemetry_cache_seconds",
                "overload_retry_min_seconds",
                "overload_retry_max_seconds",
            )
            if key in runtime
        },
    }
    if "bin" in values:
        values["codex_bin"] = values.pop("bin")
    if "model" in values:
        values["codex_model"] = values.pop("model")
    if "runner" in values:
        values["runner_mode"] = values.pop("runner")
    profiles: dict[str, ModelProfile] = {}
    raw_profiles = document.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ValueError("[profiles] must be a TOML table")
    for alias, raw in raw_profiles.items():
        if alias not in HONCHO_ALIASES:
            raise ValueError(f"Unknown Honcho profile alias: {alias}")
        if not isinstance(raw, dict):
            raise ValueError(f"Profile {alias} must be a TOML table")
        profiles[alias] = ModelProfile(
            model=_none_if_blank(raw.get("model")),
            reasoning_effort=_none_if_blank(raw.get("reasoning_effort")),
        )
    return values, profiles


def _none_if_blank(value: Any) -> Any:
    return None if value == "" else value


def _coerce_values(values: Mapping[str, Any]) -> dict[str, Any]:
    coerced = dict(values)
    for key in (
        "port",
        "timeout_seconds",
        "max_queue",
        "max_prompt_chars",
        "recycle_requests",
        "recycle_seconds",
        "telemetry_cache_seconds",
        "overload_retry_min_seconds",
        "overload_retry_max_seconds",
    ):
        if key in coerced:
            try:
                coerced[key] = int(coerced[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer") from exc
    if "workdir" in coerced:
        coerced["workdir"] = Path(coerced["workdir"]).expanduser().resolve()
    coerced["codex_bin"] = _none_if_blank(coerced.get("codex_bin"))
    coerced["codex_model"] = _none_if_blank(coerced.get("codex_model"))
    return coerced


def _validated_settings(settings: Settings) -> Settings:
    if settings.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError("reasoning_effort must be none, minimal, low, medium, high, or xhigh")
    if settings.runner_mode not in {"app-server", "exec"}:
        raise ValueError("runner must be app-server or exec")
    positive = (
        "port",
        "timeout_seconds",
        "max_queue",
        "max_prompt_chars",
        "telemetry_cache_seconds",
    )
    for name in positive:
        if getattr(settings, name) <= 0:
            raise ValueError(f"{name} must be positive")
    for name in ("recycle_requests", "recycle_seconds"):
        if getattr(settings, name) < 0:
            raise ValueError(f"{name} must be zero or positive")
    if settings.overload_retry_min_seconds < 1:
        raise ValueError("overload_retry_min_seconds must be positive")
    if settings.overload_retry_max_seconds < settings.overload_retry_min_seconds:
        raise ValueError("overload retry maximum must be at least the minimum")
    for alias, profile in settings.profiles.items():
        if alias not in HONCHO_ALIASES:
            raise ValueError(f"Unknown Honcho profile alias: {alias}")
        if profile.reasoning_effort not in REASONING_EFFORTS | {None}:
            raise ValueError(f"Invalid reasoning effort for profile {alias}")
    settings.workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if settings.codex_bin:
        path = Path(settings.codex_bin).expanduser()
        if path.is_absolute() and not path.exists():
            raise ValueError(f"Codex executable not found: {path}")
    return settings
