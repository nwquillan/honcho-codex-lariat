# Operations

`lariat doctor` validates configuration, the bundled Codex runtime, and
ChatGPT OAuth without printing account identifiers. `lariat auth status` is a
smaller authentication-only check.

## Prometheus

Scrape `/metrics` with the same bearer token Honcho uses. Important series:

- `lariat_requests_total`
- `lariat_responses_200_total`, `lariat_responses_429_total`, and error totals
- `lariat_request_duration_ms_total` and `lariat_queue_wait_ms_total`
- input, cached-input, output, and reasoning-token totals
- `lariat_runner_ready`, `lariat_queue_waiting`, child restarts, and recycles
- per-alias workload counters

Example Prometheus job:

```yaml
- job_name: lariat
  authorization:
    type: Bearer
    credentials_file: /run/secrets/lariat_api_key
  static_configs:
    - targets: ["lariat:5055"]
```

Useful Grafana panels are request rate, error rate, queue depth, cache ratio,
completion latency, child restart/recycle count, and token totals by alias. A
high queue with no errors usually means Codex is processing a long dream cycle;
a rising restart count indicates transport instability.

## Structured logs

Logs are one JSON object per line. Completion events contain alias, latency,
queue wait, status, and aggregate usage only. Forward them with any collector
that accepts stdout JSON. Health and metrics probes are deliberately not logged.

## Release-candidate soak gate

Before making v0.5 public, run `scripts/release_gate.py` once per monitoring
snapshot for seven consecutive days. Publication requires at least 250
successful completions, four automatic dream cycles, every alias exercised,
no failed or stranded Honcho jobs, no unhandled Lariat 5xx responses or OOMs,
and successful idle-child and active-request kill drills.
