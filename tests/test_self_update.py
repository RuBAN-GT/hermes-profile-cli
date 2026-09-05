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
    assert any("merge" in call and "--ff-only" in call for call in calls)
    assert not any("reset" in call for call in calls)
    assert any("-m" in call and "pip" in call for call in calls)


@pytest.mark.parametrize(
    "change", ["untracked", "modified", "diverged", "clean", "shallow"]
)
def test_self_update_preserves_local_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    src = tmp_path / "src"
    remote = tmp_path / "remote"
    real_run = subprocess.run

    def git(*args: str, cwd: Path = src) -> str:
        return real_run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    remote.mkdir()
    git("init", "-b", "main", cwd=remote)
    git("config", "user.email", "test@example.com", cwd=remote)
    git("config", "user.name", "Test", cwd=remote)
    (remote / "file").write_text("original")
    git("add", ".", cwd=remote)
    git("commit", "-m", "initial", cwd=remote)
    clone_options = ["--depth", "1"] if change == "shallow" else []
    git("clone", *clone_options, remote.as_uri(), str(src), cwd=tmp_path)
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    if change == "untracked":
        (src / "notes").write_text("keep me")
    elif change in {"modified", "diverged"}:
        (src / "file").write_text("keep me")
        if change == "diverged":
            git("commit", "-am", "local work")
    before = git("rev-parse", "HEAD")
    (remote / "update").write_text("new release")
    git("add", ".", cwd=remote)
    git("commit", "-m", "release", cwd=remote)
    installs = []

    def run(command: list[str], **kwargs: object):
        if command[0] == "git":
            return real_run(command, **kwargs)
        installs.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="0.8.0\n", stderr="")

    monkeypatch.setattr("hermes_profile.self_update._source_checkout", lambda: src)
    monkeypatch.setattr("hermes_profile.self_update.subprocess.run", run)
    if change in {"clean", "shallow"}:
        self_update()
        assert git("rev-parse", "HEAD") == git("rev-parse", "HEAD", cwd=remote)
        assert any("pip" in command for command in installs)
    else:
        with pytest.raises(ValueError):
            self_update()
        assert git("rev-parse", "HEAD") == before
        assert (
            src / ("notes" if change == "untracked" else "file")
        ).read_text() == "keep me"
        assert installs == []
