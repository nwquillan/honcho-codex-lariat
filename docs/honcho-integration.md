# Honcho 3.0.12 integration

Run Lariat as a service in Honcho's Compose project so `lariat:5055` is reachable
on Honcho's private backend network. Do not publish Lariat on a public VPS
interface.

```yaml
services:
  lariat:
    image: ghcr.io/nwquillan/honcho-codex-lariat:0.5.0-rc.1
    platform: linux/amd64
    restart: unless-stopped
    init: true
    mem_limit: 768m
    memswap_limit: 1g
    environment:
      LARIAT_API_KEY: ${LARIAT_API_KEY}
      LARIAT_REASONING_EFFORT: medium
      LARIAT_MAX_QUEUE: "8"
      LARIAT_TIMEOUT_SECONDS: "240"
    volumes:
      - lariat-codex-auth:/var/lib/lariat/codex
    networks: [honcho-backend, honcho-egress]

volumes:
  lariat-codex-auth:
```

Authenticate that dedicated volume before starting Honcho:

```bash
docker compose run --rm lariat auth login
docker compose run --rm lariat doctor
docker compose up -d lariat
```

Add `LARIAT_API_KEY` to every Honcho container that performs LLM work. Then use
these settings in Honcho's environment:

```dotenv
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=codex-deriver
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=http://lariat:5055/v1
DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=LARIAT_API_KEY
DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE=json_object

SUMMARY_MODEL_CONFIG__TRANSPORT=openai
SUMMARY_MODEL_CONFIG__MODEL=codex-summary
SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL=http://lariat:5055/v1
SUMMARY_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=LARIAT_API_KEY

DREAM_DEDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_DEDUCTION_MODEL_CONFIG__MODEL=codex-dream-deduction
DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://lariat:5055/v1
DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=LARIAT_API_KEY

DREAM_INDUCTION_MODEL_CONFIG__TRANSPORT=openai
DREAM_INDUCTION_MODEL_CONFIG__MODEL=codex-dream-induction
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=http://lariat:5055/v1
DREAM_INDUCTION_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=LARIAT_API_KEY
```

For each configured dialectic level (`minimal`, `low`, `medium`, `high`, and
`max`), use transport `openai`, model `codex-dialectic`, the same base URL, and
`LARIAT_API_KEY` as the API-key environment variable.

Restart Lariat first, wait for `/readyz`, then restart Honcho's API and worker
services. Run the read-only smoke scripts in `scripts/` before replaying failed
queues. The scripts intentionally do not mutate memories.
