# Troubleshooting

## `/readyz` returns 503

Run `lariat auth status`. If authentication is missing or expired, stop the
service and run `lariat auth login` against the same `CODEX_HOME` or named
volume. If authentication is healthy, run `lariat doctor` and inspect the
prompt-free `last_transport_error` field in `/status`.

## Broken pipe or closed transport

Lariat retires a dead child and retries one transport failure. The failed child
is never reused. If failures continue, verify that the image uses its bundled
Codex runtime and remove any `LARIAT_CODEX_BIN` override. A host binary from a
different SDK version is unsupported.

## Timeout

Timeouts return 504 and retire the child, but are not retried because an
upstream turn may have completed. Increase `LARIAT_TIMEOUT_SECONDS` for dream
specialists only after checking queue latency and container memory.

## 429 responses

Lariat returns `Retry-After` with bounded jitter when its queue is full or Codex
reports overload. Reduce Honcho worker concurrency, increase the queue modestly,
or wait for the account rate-limit window. Do not run multiple Lariat workers
against the same interactive allowance to bypass upstream limits.

## High memory use

The Codex app-server holds caches and runtime state. The reference container
allows 768 MiB RAM and 1 GiB combined memory/swap. Automatic child recycling
limits long-lived growth without interrupting an active request. If memory is
not released after recycling, capture sanitized metrics and open an issue.

## Honcho receives malformed tool output

Confirm the request is non-streaming and uses one of the five Lariat aliases.
Lariat constrains the Codex output to a tool envelope and validates the selected
name and JSON arguments before returning an OpenAI-shaped tool call. Multimodal
input and streaming tool decisions are intentionally unsupported.
