# Configuration

Configuration precedence is CLI flags, `LARIAT_*` environment variables,
deprecated `CODEX_BRIDGE_*` variables, TOML, then defaults. Legacy names remain
accepted through Lariat 1.0.

The default TOML path is `/etc/lariat/lariat.toml`; override it with `--config`
or `LARIAT_CONFIG`. Start from `lariat.toml.example`.

## Server

| TOML | Environment | Default |
|---|---|---|
| `server.host` | `LARIAT_HOST` | `127.0.0.1` |
| `server.port` | `LARIAT_PORT` | `5055` |
| `server.api_key` | `LARIAT_API_KEY` | unset; required for protected routes |
| `server.log_level` | `LARIAT_LOG_LEVEL` | `info` |

Prefer an environment secret for the API key. This key only authenticates
Honcho to Lariat; it is unrelated to ChatGPT OAuth.

## Codex

| TOML | Environment | Default |
|---|---|---|
| `codex.bin` | `LARIAT_CODEX_BIN` | SDK-bundled runtime |
| `codex.model` | `LARIAT_MODEL` | authenticated account default |
| `codex.reasoning_effort` | `LARIAT_REASONING_EFFORT` | `medium` |
| `codex.runner` | `LARIAT_RUNNER` | `app-server` |
| `codex.workdir` | `LARIAT_WORKDIR` | `/tmp/lariat-workdir` |

The `exec` runner exists only for comparison and troubleshooting. The
persistent `app-server` runner is the production path.

## Runtime

| TOML key | Environment | Default |
|---|---|---:|
| `timeout_seconds` | `LARIAT_TIMEOUT_SECONDS` | 180 |
| `max_queue` | `LARIAT_MAX_QUEUE` | 32 |
| `max_prompt_chars` | `LARIAT_MAX_PROMPT_CHARS` | 250000 |
| `recycle_requests` | `LARIAT_RECYCLE_REQUESTS` | 50 |
| `recycle_seconds` | `LARIAT_RECYCLE_SECONDS` | 21600 |
| `telemetry_cache_seconds` | `LARIAT_TELEMETRY_CACHE_SECONDS` | 60 |
| `overload_retry_min_seconds` | `LARIAT_OVERLOAD_RETRY_MIN_SECONDS` | 2 |
| `overload_retry_max_seconds` | `LARIAT_OVERLOAD_RETRY_MAX_SECONDS` | 10 |

Set either recycling value to `0` to disable that trigger. Recycling occurs
only between serialized requests. Lariat never retries a timeout because the
upstream turn may have completed after the local deadline.

## Per-alias profiles

```toml
[profiles.codex-summary]
model = "account-visible-model-name"
reasoning_effort = "low"

[profiles.codex-dream-deduction]
reasoning_effort = "high"
```

Environment form uses uppercase workload names, for example
`LARIAT_PROFILE_DREAM_DEDUCTION_REASONING_EFFORT=high`. Profile values override
global Codex values. Valid effort values are `none`, `minimal`, `low`, `medium`,
`high`, and `xhigh`; upstream model support still determines what succeeds.
