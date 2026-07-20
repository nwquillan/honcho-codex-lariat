# Lariat

Lariat connects self-hosted Honcho 3.0.12 to an authenticated Codex app-server.
It exposes the small OpenAI Chat Completions surface Honcho needs while using
official ChatGPT device-code OAuth instead of an OpenAI Platform API key.

> **Release status:** `0.5.0-rc.1`. Honcho 3.0.12 is the tested target. Other
> Honcho 3.0.x releases are best-effort until their compatibility tests pass.

Lariat does not scrape cookies, automate a browser, or call private ChatGPT web
endpoints. It uses the official `openai-codex` Python SDK and its bundled,
version-matched Codex runtime.

```mermaid
flowchart LR
    H[Honcho 3.0.12] -->|Chat Completions| L[Lariat]
    L -->|one serialized queue| C[Persistent Codex app-server]
    C -->|ChatGPT OAuth| O[Codex service]
    L --> S[JSON status and logs]
    L --> P[Prometheus metrics]
```

## What works

- representation derivation;
- short and long summaries;
- every dialectic reasoning level;
- deduction and induction dream specialists;
- Honcho function-tool decisions and iterative tool histories;
- structured JSON and JSON Schema responses;
- Honcho's streamed final-answer path, emitted as one final SSE content chunk.

Requests are serialized to respect interactive Codex concurrency limits. The
persistent child is restarted after a transport failure and recycled safely
between requests after 50 completions or six hours by default.

## Quick start with Docker Compose

Linux AMD64 is the supported container platform for v0.5.

```bash
cp .env.example .env
openssl rand -hex 32
# Put that value in .env as LARIAT_API_KEY.
docker compose build
docker compose run --rm lariat auth login
docker compose up -d
```

The login command prints an official device-code URL and one-time code. Open
the URL in a browser, authorize the account, then let the command finish. OAuth
state is stored in the dedicated `lariat-codex-auth` volume.

```bash
curl -fsS http://127.0.0.1:5055/livez
curl -fsS http://127.0.0.1:5055/status \
  -H "Authorization: Bearer $LARIAT_API_KEY"
```

For source development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
export LARIAT_API_KEY="$(openssl rand -hex 32)"
.venv/bin/lariat auth status
.venv/bin/lariat serve
```

## Honcho configuration

Place Lariat on the same private Docker network as Honcho and point every
Honcho LLM route at `http://lariat:5055/v1`. The exact environment variables
and a Compose service are in [docs/honcho-integration.md](docs/honcho-integration.md).

The five public model aliases are:

- `codex-deriver`
- `codex-summary`
- `codex-dialectic`
- `codex-dream-deduction`
- `codex-dream-induction`

By default all aliases use the authenticated account's Codex model and medium
reasoning. A global model override or per-alias model/reasoning profile can be
set when an account exposes suitable model names. See
[docs/configuration.md](docs/configuration.md).

## HTTP surface

| Route | Authentication | Purpose |
|---|---|---|
| `GET /livez` | none | Process liveness |
| `GET /readyz` | none | Codex child and OAuth readiness |
| `GET /health` | none | Backward-compatible readiness alias |
| `GET /v1/models` | bearer | Honcho aliases |
| `POST /v1/chat/completions` | bearer | Honcho completion traffic |
| `GET /status` | bearer | Sanitized runtime and account telemetry |
| `GET /metrics` | bearer | Prometheus metrics |

`/status`, `/metrics`, and structured logs never contain OAuth tokens, account
IDs, emails, prompts, responses, peer names, message IDs, or Codex thread IDs.
There is no call-home analytics.

## Compatibility boundaries

Lariat is a Honcho adapter, not a general OpenAI proxy. It supports text
messages, structured output, function tools, and final-only streaming. It
rejects multimodal message content and streaming tool decisions.

Honcho fields such as `temperature`, `top_p`, token limits, penalties, `seed`,
and `stop` are accepted for request compatibility but ignored because the Codex
SDK's thread API does not expose equivalent controls. Per-request
`reasoning_effort` is also ignored; configure reasoning globally or by alias.

Codex carries its own agent/runtime envelope, so reported input token counts can
be substantially larger than the visible Honcho prompt. Persistent app-server
use improves prefix caching but does not remove that fixed overhead. Dream
specialists can make several sequential calls and are the most expensive
workload.

## Operations and security

- [Configuration reference](docs/configuration.md)
- [Honcho integration](docs/honcho-integration.md)
- [Operations and Grafana examples](docs/operations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

Lariat is licensed under Apache-2.0. Honcho is a separate AGPL-3.0 project;
Lariat interoperates with its public HTTP configuration and contains no copied
Honcho source.
