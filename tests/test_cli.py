import pytest

from hermes_profile.cli import main


def test_help_command_prints_guide(capsys: pytest.CaptureFixture[str]) -> None:
    main(["help"])
    out = capsys.readouterr().out
    assert "TUI keys" in out
    assert "self-update" in out
    assert "manager config" in out
    assert "preflight NAME" in out
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
