import subprocess
import sys
from pathlib import Path

import pytest

from hermes_profile.self_update import self_update


def test_self_update_fetches_and_reinstalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = "0.2.0\n" if command[:2] == [sys.executable, "-c"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("hermes_profile.self_update._source_checkout", lambda: src)
    monkeypatch.setattr("hermes_profile.self_update.subprocess.run", fake_run)

    result = self_update()

    assert result["source"] == str(src)
    assert result["version"] == "0.2.0"
    assert any(call[0] == "git" and "fetch" in call for call in calls)
    assert any(call[0] == "git" and "reset" in call for call in calls)
    assert any("-m" in call and "pip" in call for call in calls)
