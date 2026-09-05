import json
from pathlib import Path

import pytest

from hermes_profile.cli import main
from hermes_profile.paths import initialize_settings
from hermes_profile.profiles import create_profile


def test_help_command_prints_guide(capsys: pytest.CaptureFixture[str]) -> None:
    main(["help"])
    out = capsys.readouterr().out
    assert "TUI keys" in out
    assert "self-update" in out
    assert "manager config" in out
    assert "preflight NAME" in out
    assert "apply-all" in out
    assert "auth sync" in out
    assert "auth import" in out
    assert "auth push" in out
    assert "backup create" in out
    assert "hermes-profile mcp" in out


def test_preflight_text_keeps_empty_diff_on_its_own_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hermes_profile.cli import _emit

    _emit(
        {
            "config_diff": "",
            "materialization_diff": "",
            "env_added": [],
            "env_changed": [],
            "env_removed": [],
        },
        "text",
    )
    out = capsys.readouterr().out
    assert out.startswith("No effective config changes.\n")
    assert "No effective config changes.env" not in out


def test_apply_all_materializes_every_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.yaml"
    settings = initialize_settings(config, tmp_path / "managed")
    create_profile(settings, "alpha")
    create_profile(settings, "beta")

    main(["--config", str(config), "--format", "json", "apply-all"])

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "applied": ["alpha", "beta"],
    }
    assert (settings.profiles_dir / "alpha" / "config.yaml").is_file()
    assert (settings.profiles_dir / "beta" / "config.yaml").is_file()
