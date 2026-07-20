from __future__ import annotations

from pathlib import Path

import pytest

from honcho_codex_bridge.config import Settings


def test_configuration_precedence(tmp_path: Path) -> None:
    config = tmp_path / "lariat.toml"
    config.write_text(
        """
[server]
port = 1000
[codex]
model = "toml-model"
reasoning_effort = "low"
[profiles.codex-summary]
model = "summary-model"
reasoning_effort = "high"
"""
    )
    settings = Settings.from_sources(
        config_path=config,
        environ={"LARIAT_PORT": "2000", "CODEX_BRIDGE_MODEL": "legacy-model"},
        cli={"port": 3000, "codex_model": "cli-model"},
    )
    assert settings.port == 3000
    assert settings.codex_model == "cli-model"
    assert settings.reasoning_effort == "low"
    assert settings.profile_for("codex-summary").model == "summary-model"
    assert settings.profile_for("codex-summary").reasoning_effort == "high"


def test_new_environment_wins_over_legacy(tmp_path: Path) -> None:
    settings = Settings.from_sources(
        config_path=tmp_path / "missing.toml",
        environ={"LARIAT_MODEL": "new", "CODEX_BRIDGE_MODEL": "old"},
    )
    assert settings.codex_model == "new"


def test_model_defaults_to_authenticated_account_default(tmp_path: Path) -> None:
    settings = Settings.from_sources(config_path=tmp_path / "missing.toml", environ={})
    assert settings.codex_model is None
    assert settings.profile_for("codex-deriver").model is None


def test_recycling_can_be_disabled(tmp_path: Path) -> None:
    settings = Settings.from_sources(
        config_path=tmp_path / "missing.toml",
        environ={"LARIAT_RECYCLE_REQUESTS": "0", "LARIAT_RECYCLE_SECONDS": "0"},
    )
    assert settings.recycle_requests == 0
    assert settings.recycle_seconds == 0


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "lariat.toml"
    config.write_text("[profiles.unknown]\nmodel='x'\n")
    with pytest.raises(ValueError, match="Unknown Honcho profile"):
        Settings.from_sources(config_path=config, environ={})
