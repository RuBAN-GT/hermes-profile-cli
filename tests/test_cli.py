import pytest

from hermes_profile.cli import main


def test_help_command_prints_guide(capsys: pytest.CaptureFixture[str]) -> None:
    main(["help"])
    out = capsys.readouterr().out
    assert "TUI keys" in out
    assert "self-update" in out
    assert "manager config" in out
