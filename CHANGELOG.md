# Changelog

All notable changes follow Keep a Changelog. Versions use Semantic Versioning.

## [Unreleased]

## [0.5.0-rc.1] - 2026-07-20

### Added

- Official ChatGPT device-code OAuth onboarding and sanitized auth diagnostics.
- Honcho 3.0.12 profiles for deriver, summary, dialectic, deduction, and induction.
- Prompt-free JSON logs, protected status, Prometheus metrics, and Codex usage telemetry.
- Safe request-count and age-based app-server recycling.
- Self-contained non-root AMD64 image using the SDK-bundled Codex runtime.
- Compose quickstart, CI/security workflows, SBOM/provenance release workflow, and soak gate.

### Changed

- Product, package, and primary CLI are now Lariat / `honcho-codex-lariat` / `lariat`.
- The authenticated Codex account chooses the model unless a global or alias profile overrides it.

### Deprecated

- `honcho-codex-bridge` CLI and `CODEX_BRIDGE_*` variables remain aliases until 1.0.

### Fixed

- Dead persistent transports are retired and restarted after a single safe transport retry.
- Readiness now includes both child liveness and OAuth state.

[Unreleased]: https://github.com/nwquillan/honcho-codex-lariat/compare/v0.5.0-rc.1...HEAD
[0.5.0-rc.1]: https://github.com/nwquillan/honcho-codex-lariat/releases/tag/v0.5.0-rc.1
