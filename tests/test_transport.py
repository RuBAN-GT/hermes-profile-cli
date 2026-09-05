import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_profile import __version__
from hermes_profile.models import Host
from hermes_profile.paths import load_settings
from hermes_profile.transport import (
    INSTALL_TIMEOUT_SECONDS,
    SOURCE_REPO,
    SSH_TIMEOUT_SECONDS,
    SshTransport,
    normalize_remote_binary,
    parse_ssh_target,
    remote_arguments,
    ssh_error_message,
)


def _host() -> Host:
    return Host(
        alias="gateway-a",
        ssh_host="gateway.example.internal",
        ssh_user="deploy",
        ssh_port=None,
        identity_file=None,
        remote_binary="/opt/hermes/bin/hermes-profile",
        remote_config=Path("/opt/hermes/etc/config.yaml"),
        managed_dir=Path("/opt/hermes/managed"),
        profiles_dir=Path("/opt/hermes/managed/profiles"),
        fragments_dir=Path("/opt/hermes/managed/fragments"),
    )


def test_load_settings_defaults_missing_remote_binary(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "managed_dir: /opt/hermes/managed\n"
        "hosts:\n"
        "  gateway-a:\n"
        "    ssh_host: gateway-a.example.internal\n"
        "    remote_config: /opt/hermes/etc/config.yaml\n"
        "    managed_dir: /opt/hermes/managed\n"
        "    profiles_dir: /opt/hermes/profiles\n"
        "    fragments_dir: /opt/hermes/fragments\n"
    )

    settings = load_settings(str(config))

    assert settings.hosts["gateway-a"].remote_binary == "hermes-profile"


def test_load_settings_reads_configured_host(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "managed_dir: /opt/hermes/managed\n"
        "hosts:\n"
        "  gateway-a:\n"
        "    ssh_host: gateway-a.example.internal\n"
        "    remote_binary: /opt/hermes/bin/hermes-profile\n"
        "    remote_config: /opt/hermes/etc/config.yaml\n"
        "    managed_dir: /opt/hermes/managed\n"
        "    profiles_dir: /opt/hermes/profiles\n"
        "    fragments_dir: /opt/hermes/fragments\n"
    )

    settings = load_settings(str(config))

    assert settings.hosts["gateway-a"].ssh_host == "gateway-a.example.internal"
    assert settings.hosts["gateway-a"].profiles_dir == Path("/opt/hermes/profiles")


def test_host_paths_must_be_absolute(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "managed_dir: /opt/hermes/managed\n"
        "hosts:\n"
        "  gateway-a:\n"
        "    ssh_host: gateway-a.example.internal\n"
        "    remote_binary: /opt/hermes/bin/hermes-profile\n"
        "    remote_config: relative.yaml\n"
        "    managed_dir: /opt/hermes/managed\n"
        "    profiles_dir: /opt/hermes/profiles\n"
        "    fragments_dir: /opt/hermes/fragments\n"
    )

    with pytest.raises(ValueError, match="absolute path"):
        load_settings(str(config))


def test_parse_ssh_target_reads_port_flag() -> None:
    assert parse_ssh_target("deploy@gateway.example -p 2222") == (
        "deploy",
        "gateway.example",
        2222,
    )
    assert parse_ssh_target("ssh deploy@gateway.example -p2222") == (
        "deploy",
        "gateway.example",
        2222,
    )


def test_version_is_semver() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_ssh_error_explains_missing_git() -> None:
    message = ssh_error_message("gateway-a", "hermes-profile", "NEED:git")
    assert "git is required" in message


def test_ssh_error_explains_old_python() -> None:
    message = ssh_error_message("gateway-a", "hermes-profile", "NEED:python311:3.9")
    assert "3.11+" in message
    assert "3.9" in message


def test_ssh_error_explains_pip_python_requirement() -> None:
    message = ssh_error_message(
        "gateway-a",
        "hermes-profile",
        "ERROR: Package 'hermes-profile-cli' requires a different Python: "
        "3.9.6 not in '>=3.11'",
    )
    assert "3.11+" in message


def test_ssh_error_explains_missing_remote_binary() -> None:
    message = ssh_error_message(
        "gateway-a",
        "/opt/hermes/bin/hermes-profile",
        "zsh:1: no such file or directory: /opt/hermes/bin/hermes-profile",
    )
    assert "remote CLI not found" in message
    assert "hermes-profile" in message


def test_ssh_error_explains_hermes_agent_binary() -> None:
    message = ssh_error_message(
        "gateway-a",
        "/usr/local/bin/hermes",
        "hermes: error: argument command: invalid choice: "
        "'/opt/hermes/etc/config.yaml' "
        "(choose from 'chat', 'model', 'moa')",
    )
    assert "Hermes agent" in message
    assert "hermes-profile" in message


def test_normalize_remote_binary_defaults_and_rejects_agent() -> None:
    assert normalize_remote_binary("") == "hermes-profile"
    assert normalize_remote_binary("  /opt/hermes/bin/hermes-profile ") == (
        "/opt/hermes/bin/hermes-profile"
    )
    with pytest.raises(ValueError, match="not the hermes agent"):
        normalize_remote_binary("/usr/local/bin/hermes")


def test_ssh_lists_files_when_remote_binary_is_hermes_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        remote = command[-1]
        calls.append(remote)
        if "command -v" in remote or "--help" in remote:
            raise AssertionError("hermes agent should not be probed as manager CLI")
        if "basename" in remote:
            return subprocess.CompletedProcess(command, 0, stdout="gogol\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    host = replace(_host(), remote_binary="/usr/local/bin/hermes")
    assert SshTransport(host).profiles() == ["gogol"]
    assert calls


def test_ssh_lists_runtime_profiles_without_remote_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        remote = command[-1]
        calls.append(remote)
        if "command -v" in remote or "[ -x " in remote:
            return subprocess.CompletedProcess(command, 0, stdout="no\n", stderr="")
        if "basename" in remote:
            return subprocess.CompletedProcess(
                command, 0, stdout="gogol\ntyrion\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SshTransport(_host()).profiles() == ["gogol", "tyrion"]
    assert any("command -v" in item or "[ -x " in item for item in calls)


def test_ssh_preview_reads_remote_config_without_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        remote = command[-1]
        if "command -v" in remote or "[ -x " in remote:
            return subprocess.CompletedProcess(command, 0, stdout="no\n", stderr="")
        if "__CONFIG__" in remote:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "__CONFIG__\nmodel:\n  name: base\n__END_CONFIG__\n__ENV_COUNT__2\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SshTransport(_host()).action("tyrion", "render") == {
        "config": {"model": {"name": "base"}},
        "environment_count": 2,
    }


@pytest.mark.parametrize("name", ("../outside", "../../tmp", "Tyrion"))
def test_ssh_file_fallback_rejects_invalid_profile_names(name: str) -> None:
    transport = SshTransport(_host())

    with pytest.raises(ValueError, match="profile name"):
        transport._file_status(name)
    with pytest.raises(ValueError, match="profile name"):
        transport._file_preview(name)


def test_ssh_install_clones_repo_and_reports_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        remote = command[-1]
        calls.append((remote, kwargs.get("timeout")))
        if "git clone" in remote:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "BINARY:/home/deploy/.local/share/hermes-profile"
                    "/venv/bin/hermes-profile\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SshTransport(_host()).install()
    assert result["binary"].endswith("hermes-profile")
    assert result["source"] == SOURCE_REPO
    assert any("git clone" in remote for remote, _timeout in calls)
    assert any("python3.11" in remote for remote, _timeout in calls)
    assert any(timeout == INSTALL_TIMEOUT_SECONDS for _remote, timeout in calls)


def test_ssh_init_does_not_use_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    SshTransport(_host()).init()
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("input") is None
    assert kwargs.get("stdin") is subprocess.DEVNULL
    assert kwargs.get("timeout") == SSH_TIMEOUT_SECONDS
    remote = captured["command"]
    assert isinstance(remote, list)
    assert "printf" in remote[-1]


def test_remote_arguments_remove_client_only_options() -> None:
    assert remote_arguments(
        [
            "--config",
            "/local/config.yaml",
            "--host",
            "gateway-a",
            "--format=json",
            "apply",
            "tyrion",
        ]
    ) == ["apply", "tyrion"]


def test_ssh_apply_all_uses_remote_command(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = SshTransport(_host())
    calls: list[list[str]] = []
    monkeypatch.setattr(transport, "_cli_available", lambda: True)
    monkeypatch.setattr(
        transport,
        "run",
        lambda arguments: calls.append(arguments) or {"applied": ["alpha", "beta"]},
    )

    assert transport.apply_all() == ["alpha", "beta"]
    assert calls == [["apply-all"]]
