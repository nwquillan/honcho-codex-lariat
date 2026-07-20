# Security policy

## Supported versions

Only the newest v0.5 release candidate is supported before the first stable
release.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security-advisory flow for this repository and include the affected version,
reproduction steps, impact, and any suggested mitigation. Please remove OAuth
tokens, bearer secrets, prompts, responses, peer names, and account identifiers
from reports and logs.

## Deployment boundary

Lariat is designed for a private network shared with Honcho. Protected routes
require a dedicated bearer token, but Lariat does not implement TLS or public
internet hardening. Terminate TLS at a trusted private reverse proxy if remote
access is required. Never publish port 5055 directly from a VPS.

The official image runs as a non-root user, disables shell tools and web search,
uses an empty read-only Codex sandbox work directory, and stores ChatGPT OAuth in
a dedicated volume. Lariat never needs a user's general Codex home directory.
