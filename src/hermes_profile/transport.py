import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml

from hermes_profile.auth_map import auth_map_status, bind_profile_auth
from hermes_profile.backups import create_backup, list_backups, restore_backup
from hermes_profile.models import Host, Settings
from hermes_profile.paths import PROFILE_NAME
from hermes_profile.profiles import create_profile, delete_profile, list_profiles
from hermes_profile.service import (
    _redact_config,
    _redaction_paths,
    apply,
    apply_all,
    preflight,
    reconcile,
    render_profile,
    shared_auth_status,
    status,
    sync_shared_auth,
)

SSH_TIMEOUT_SECONDS = 30
INSTALL_TIMEOUT_SECONDS = 180
DEFAULT_REMOTE_BINARY = "hermes-profile"
SOURCE_REPO = "https://github.com/RuBAN-GT/hermes-profile-cli.git"


class LocalTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def profiles(self) -> list[str]:
        return list_profiles(self.settings)

    def status(self, name: str) -> dict[str, bool]:
        return status(self.settings, name)

    def create(self, name: str) -> None:
        create_profile(self.settings, name)

    def delete(self, name: str) -> None:
        delete_profile(self.settings, name)

    def bind_auth(self, name: str, *, force: bool = False) -> dict[str, Any]:
        return bind_profile_auth(self.settings, name, force=force)

    def auth_map_status(self) -> dict[str, Any]:
        return auth_map_status(self.settings)

    def shared_status(self) -> dict[str, Any]:
        return shared_auth_status(self.settings)

    def backup(self, action: str, name: str | None = None) -> dict[str, Any]:
        if action == "create":
            return create_backup(self.settings)
        if action == "list":
            return list_backups(self.settings)
        if name is None:
            raise ValueError("backup restore requires a snapshot name")
        return restore_backup(self.settings, name)

    def sync_auth(
        self, source: str, providers: list[str], allow_oauth: bool
    ) -> dict[str, Any]:
        return sync_shared_auth(
            self.settings, source, providers, allow_oauth=allow_oauth
        )

    def action(self, name: str, action: str) -> dict[str, Any]:
        if action == "render":
            directory = self.settings.profiles_dir / name
            if not (directory / "profile.yaml").is_file():
                return _existing_profile_preview(directory, name)
            config, environment = render_profile(self.settings, name, preview=True)
            return {"config": config, "environment_count": len(environment)}
        if action == "reconcile":
            return {"reconciled": reconcile(self.settings, name)}
        if action == "preflight":
            return preflight(self.settings, name)
        if action == "apply":
            apply(self.settings, name)
            return {"applied": name}
        if action == "apply-discard":
            apply(self.settings, name, discard_runtime=True)
            return {"applied": name, "discarded_runtime": True}
        raise ValueError(f"unsupported profile action: {action}")

    def apply_all(self) -> list[str]:
        return apply_all(self.settings)


class SshTransport:
    """Manage a remote host via CLI when present, otherwise via SSH files."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self._cli_known: bool | None = None

    def run(self, arguments: list[str]) -> dict[str, Any]:
        command = [
            self.host.remote_binary,
            "--config",
            str(self.host.remote_config),
            "--format",
            "json",
            *arguments,
        ]
        completed = self._ssh(command)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"remote {self.host.alias} returned invalid JSON: {completed.stdout!r}"
            ) from error
        if not isinstance(result, dict):
            raise ValueError(f"remote {self.host.alias} returned an invalid response")
        return result

    def profiles(self) -> list[str]:
        if self._cli_available():
            profiles = self.run(["list"]).get("profiles")
            if not isinstance(profiles, list) or not all(
                isinstance(profile, str) for profile in profiles
            ):
                raise ValueError(f"remote {self.host.alias} returned invalid profiles")
            return profiles
        return self._file_profiles()

    def status(self, name: str) -> dict[str, bool]:
        if self._cli_available():
            result = self.run(["status", name])
            return {
                key: bool(result.get(key, False))
                for key in ("config_drift", "env_drift", "auth_inventory_changed")
            }
        return self._file_status(name)

    def create(self, name: str) -> None:
        if self._cli_available():
            self.run(["create", name])
            return
        raise ValueError(
            f"{self.host.alias}: creating a profile needs hermes-profile "
            "on the remote host"
        )

    def delete(self, name: str) -> None:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: deleting a profile needs hermes-profile "
                "on the remote host"
            )
        self.run(["delete", name, "--confirm"])

    def bind_auth(self, name: str, *, force: bool = False) -> dict[str, Any]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: auth bind needs hermes-profile on the remote host"
            )
        arguments = ["auth", "bind", name]
        if force:
            arguments.append("--force")
        return self.run(arguments)

    def auth_map_status(self) -> dict[str, Any]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: auth map-status needs hermes-profile "
                "on the remote host"
            )
        return self.run(["auth", "map-status"])

    def shared_status(self) -> dict[str, Any]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: auth shared-status needs hermes-profile "
                "on the remote host"
            )
        return self.run(["auth", "shared-status"])

    def backup(self, action: str, name: str | None = None) -> dict[str, Any]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: backup needs hermes-profile on the remote host"
            )
        if action == "create":
            return self.run(["backup", "create"])
        if action == "list":
            return self.run(["backup", "list"])
        if name is None:
            raise ValueError("backup restore requires a snapshot name")
        return self.run(["backup", "restore", name, "--confirm"])

    def sync_auth(
        self, source: str, providers: list[str], allow_oauth: bool
    ) -> dict[str, Any]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: auth sync needs hermes-profile on the remote host"
            )
        arguments = ["auth", "sync", "--from", source]
        for provider in providers:
            arguments.extend(["--provider", provider])
        if allow_oauth:
            arguments.append("--allow-oauth")
        return self.run(arguments)

    def action(self, name: str, action: str) -> dict[str, Any]:
        if self._cli_available():
            if action == "apply-discard":
                return self.run(["apply", name, "--discard-runtime"])
            return self.run([action, name])
        if action == "render":
            return self._file_preview(name)
        raise ValueError(
            f"{self.host.alias}: {action} needs hermes-profile on the remote host. "
            f"Install it with: hermes-profile ssh install {self.host.alias}"
        )

    def apply_all(self) -> list[str]:
        if not self._cli_available():
            raise ValueError(
                f"{self.host.alias}: apply-all needs hermes-profile on the remote "
                "host. "
                f"Install it with: hermes-profile ssh install {self.host.alias}"
            )
        applied = self.run(["apply-all"]).get("applied")
        if not isinstance(applied, list) or not all(
            isinstance(profile, str) for profile in applied
        ):
            raise ValueError(
                f"remote {self.host.alias} returned invalid applied profiles"
            )
        return applied

    def doctor(self) -> dict[str, Any]:
        version = self._ssh([self.host.remote_binary, "--version"]).stdout.strip()
        result = self.run(["list"])
        return {
            "host": self.host.alias,
            "version": version,
            "profiles": result["profiles"],
        }

    def init(self) -> dict[str, str]:
        config = yaml.safe_dump(
            {
                "managed_dir": str(self.host.managed_dir),
                "profiles_dir": str(self.host.profiles_dir),
                "fragments_dir": str(self.host.fragments_dir),
                "ui": {"animations": True},
            },
            sort_keys=False,
        )
        directories = [
            self.host.managed_dir,
            self.host.profiles_dir,
            self.host.fragments_dir,
            self.host.remote_config.parent,
        ]
        mkdir = " ".join(shlex.quote(str(path)) for path in directories)
        target = shlex.quote(str(self.host.remote_config))
        payload = shlex.quote(config)
        remote = (
            "umask 077 && "
            f"mkdir -p {mkdir} && "
            f"chmod 700 {mkdir} && "
            f"if [ ! -e {target} ]; then "
            f"printf '%s\\n' {payload} > {target} && chmod 600 {target}; "
            "fi"
        )
        self._ssh_shell(remote)
        return {"host": self.host.alias, "config": str(self.host.remote_config)}

    def install(self) -> dict[str, str]:
        layout = self.init()
        repo = shlex.quote(SOURCE_REPO)
        script = f"""
umask 077
set -e
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH
root="$HOME/.local/share/hermes-profile"
src="$root/src"
venv="$root/venv"
if ! command -v git >/dev/null 2>&1; then echo NEED:git >&2; exit 1; fi
py=""
seen="missing"
for cand in python3.14 python3.13 python3.12 python3.11 python3; do
  cmd=$(command -v "$cand" 2>/dev/null) || continue
  seen=$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
  then
    py=$cmd
    break
  fi
done
if [ -z "$py" ]; then echo NEED:python311:$seen >&2; exit 1; fi
mkdir -p "$root"
if [ -d "$src/.git" ]; then
  git -C "$src" fetch --depth 1 origin main
  git -C "$src" reset --hard origin/main
else
  git clone --depth 1 --branch main {repo} "$src"
fi
rm -rf "$venv"
"$py" -m venv "$venv"
"$venv/bin/pip" install -U pip
"$venv/bin/pip" install "$src"
echo BINARY:"$venv/bin/hermes-profile"
"""
        completed = self._ssh_shell(script, timeout=INSTALL_TIMEOUT_SECONDS)
        for line in completed.stdout.splitlines():
            if line.startswith("BINARY:"):
                binary = line.split(":", 1)[1].strip()
                if binary:
                    return {**layout, "binary": binary, "source": SOURCE_REPO}
        raise ValueError(
            f"{self.host.alias}: remote install did not report a binary path"
        )

    def _cli_available(self) -> bool:
        if self._cli_known is not None:
            return self._cli_known
        if is_hermes_agent_binary(self.host.remote_binary):
            self._cli_known = False
            return False
        binary = shlex.quote(self.host.remote_binary)
        script = (
            f"if command -v {binary} >/dev/null 2>&1; then "
            f"cmd=$(command -v {binary}); "
            f"elif [ -x {binary} ]; then cmd={binary}; "
            f"else echo no; exit 0; fi; "
            f'if "$cmd" --help 2>&1 | grep -q hermes-profile; '
            f"then echo yes; else echo no; fi"
        )
        self._cli_known = self._ssh_shell(script).stdout.strip() == "yes"
        return self._cli_known

    def _file_profiles(self) -> list[str]:
        directory = shlex.quote(str(self.host.profiles_dir))
        script = (
            f"if [ ! -d {directory} ]; then exit 0; fi; "
            f"for d in {directory}/*; do "
            '[ -d "$d" ] || continue; '
            'if [ -f "$d/profile.yaml" ] || [ -f "$d/config.yaml" ]; then '
            'basename "$d"; fi; '
            "done"
        )
        return sorted(
            line.strip()
            for line in self._ssh_shell(script).stdout.splitlines()
            if line.strip()
        )

    def _file_status(self, name: str) -> dict[str, bool]:
        _validate_profile_name(name)
        directory = shlex.quote(str(self.host.profiles_dir / name))
        script = f"""
d={directory}
if [ ! -d "$d" ]; then echo MISSING; exit 0; fi
if [ ! -f "$d/profile.yaml" ] && [ ! -f "$d/config.yaml" ]; then
  echo MISSING; exit 0
fi
drift() {{
  a="$1"; b="$2"
  if [ ! -e "$a" ] && [ ! -e "$b" ]; then echo 0
  elif [ ! -e "$a" ] || [ ! -e "$b" ]; then echo 1
  elif cmp -s "$a" "$b"; then echo 0
  else echo 1
  fi
}}
echo CONFIG:$(drift "$d/config.yaml" "$d/state/applied-config.yaml")
echo ENV:$(drift "$d/.env" "$d/state/applied.env")
if [ -f "$d/auth.json" ] && [ ! -f "$d/state/auth-inventory.sha256" ]; then echo AUTH:1
else echo AUTH:0
fi
"""
        output = self._ssh_shell(script).stdout
        if "MISSING" in output:
            raise ValueError(f"profile does not exist: {name}")
        flags = dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
        return {
            "config_drift": flags.get("CONFIG", "0") == "1",
            "env_drift": flags.get("ENV", "0") == "1",
            "auth_inventory_changed": flags.get("AUTH", "0") == "1",
        }

    def _file_preview(self, name: str) -> dict[str, Any]:
        _validate_profile_name(name)
        directory = shlex.quote(str(self.host.profiles_dir / name))
        script = f"""
d={directory}
if [ ! -d "$d" ]; then echo MISSING; exit 0; fi
if [ -f "$d/config.yaml" ]; then
  echo __CONFIG__
  cat "$d/config.yaml"
  echo
  echo __END_CONFIG__
fi
if [ -f "$d/.env" ]; then
  echo -n __ENV_COUNT__
  grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' "$d/.env" || true
fi
if [ -f "$d/state/interpolation.yaml" ]; then
  echo __REDACTIONS__
  cat "$d/state/interpolation.yaml"
  echo
  echo __END_REDACTIONS__
fi
"""
        output = self._ssh_shell(script).stdout
        if "MISSING" in output:
            raise ValueError(f"profile does not exist: {name}")
        config: dict[str, Any] = {}
        if "__CONFIG__" in output:
            block = output.split("__CONFIG__", 1)[1].split("__END_CONFIG__", 1)[0]
            loaded = yaml.safe_load(block) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{name}: config.yaml must be a mapping")
            config = loaded
        env_count = 0
        if "__ENV_COUNT__" in output:
            raw = output.split("__ENV_COUNT__", 1)[1].splitlines()[0].strip()
            env_count = int(raw or "0")
        if "__REDACTIONS__" in output:
            block = output.split("__REDACTIONS__", 1)[1].split(
                "__END_REDACTIONS__", 1
            )[0]
            paths = (yaml.safe_load(block) or {}).get("paths", [])
            config = _redact_config(config, paths)
        return {"config": config, "environment_count": env_count}

    def ensure_private_dir(self, path: Path) -> None:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("remote path must be absolute without '..'")
        target = shlex.quote(str(path))
        self._ssh_shell(f"umask 077 && mkdir -p {target} && chmod 700 {target}")

    def list_files(self, root: Path) -> list[str]:
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("remote path must be absolute without '..'")
        quoted = shlex.quote(str(root))
        script = f"if [ ! -d {quoted} ]; then exit 0; fi; find {quoted} -type f | sort"
        prefix = str(root).rstrip("/") + "/"
        result: list[str] = []
        for line in self._ssh_shell(script).stdout.splitlines():
            path = line.strip()
            if path.startswith(prefix):
                result.append(path[len(prefix) :])
        return result

    def write_private_file(self, path: Path, content: str) -> None:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("remote path must be absolute without '..'")
        directory = shlex.quote(str(path.parent))
        target = shlex.quote(str(path))
        remote = (
            "umask 077 && "
            f"mkdir -p {directory} && chmod 700 {directory} && "
            f"cat > {target} && chmod 600 {target}"
        )
        self._ssh_shell(remote, input_text=content)

    def read_text_file(self, path: Path) -> str | None:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("remote path must be absolute without '..'")
        target = shlex.quote(str(path))
        script = (
            f"if [ ! -f {target} ]; then echo __ABSENT__; exit 0; fi; "
            f"echo __PRESENT__; cat {target}"
        )
        output = self._ssh_shell(script).stdout
        if output.startswith("__ABSENT__"):
            return None
        if output.startswith("__PRESENT__\n"):
            return output.split("\n", 1)[1]
        if output.startswith("__PRESENT__"):
            return output[len("__PRESENT__") :].lstrip("\n")
        raise ValueError(f"{self.host.alias}: unexpected remote file response")

    def _ssh(self, remote_arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self._ssh_shell(shlex.join(remote_arguments))

    def _ssh_shell(
        self,
        remote_command: str,
        input_text: str | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
        ]
        if self.host.ssh_port is not None:
            command.extend(["-p", str(self.host.ssh_port)])
        if self.host.identity_file is not None:
            command.extend(["-i", str(self.host.identity_file)])
        destination = self.host.ssh_host
        if self.host.ssh_user is not None:
            destination = f"{self.host.ssh_user}@{destination}"
        command.extend([destination, remote_command])
        limit = SSH_TIMEOUT_SECONDS if timeout is None else timeout
        run_args: dict[str, Any] = {
            "text": True,
            "capture_output": True,
            "check": False,
            "timeout": limit,
        }
        if input_text is None:
            run_args["stdin"] = subprocess.DEVNULL
        else:
            run_args["input"] = input_text
        try:
            completed = subprocess.run(command, **run_args)
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                f"{self.host.alias}: SSH timed out after {limit}s"
            ) from error
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ValueError(
                ssh_error_message(self.host.alias, self.host.remote_binary, detail)
            )
        return completed


def _existing_profile_preview(directory: Path, name: str) -> dict[str, Any]:
    """Preview a Hermes-owned profile that has no declarative profile.yaml."""
    _validate_profile_name(name)
    config_path = directory / "config.yaml"
    if not config_path.is_file():
        raise ValueError(f"profile does not exist: {name}")
    config = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(config, dict):
        raise ValueError(f"{name}: config.yaml must be a mapping")
    env_path = directory / ".env"
    env_count = (
        sum(
            1
            for line in env_path.read_text().splitlines()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line)
        )
        if env_path.is_file()
        else 0
    )
    return {
        "config": _redact_config(config, _redaction_paths(directory)),
        "environment_count": env_count,
    }


def parse_ssh_target(value: str) -> tuple[str | None, str, int | None]:
    text = value.strip()
    if text.startswith("ssh "):
        text = text[4:].strip()
    port: int | None = None
    match = re.search(r"(?:^|\s)-p\s*(\d+)\b", text)
    if match:
        port = int(match.group(1))
        text = f"{text[: match.start()]} {text[match.end() :]}".strip()
    user, separator, hostname = text.partition("@")
    if not separator:
        user = ""
        hostname = text
    if hostname.count(":") == 1 and not hostname.startswith("["):
        hostname, _, port_text = hostname.rpartition(":")
        if port_text.isdigit():
            port = int(port_text)
    hostname = hostname.strip()
    if not hostname or " " in hostname or "@" in hostname:
        raise ValueError("SSH target must be host, user@host, or user@host -p port")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    return (user or None), hostname, port


def normalize_remote_binary(value: str) -> str:
    binary = value.strip() or DEFAULT_REMOTE_BINARY
    if is_hermes_agent_binary(binary):
        raise ValueError(
            "This field is hermes-profile (this manager), not the hermes agent. "
            "Leave it as hermes-profile if that command is on the remote PATH."
        )
    return binary


def is_hermes_agent_binary(binary: str) -> bool:
    return Path(binary).name == "hermes"


def _validate_profile_name(name: str) -> None:
    if not PROFILE_NAME.fullmatch(name):
        raise ValueError("profile name must use lowercase letters, digits, and hyphens")


def ssh_error_message(alias: str, binary: str, detail: str) -> str:
    lowered = detail.lower()
    if looks_like_hermes_agent_error(binary, detail):
        return (
            f"{alias}: {binary} is the Hermes agent, not hermes-profile. "
            "Set the remote manager CLI to hermes-profile (this tool), "
            "or leave the default if it is on PATH. "
            "List and Preview work without that CLI."
        )
    if "need:git" in lowered:
        return f"{alias}: git is required on the remote host to clone hermes-profile."
    if "need:python3" in lowered or "need:python311" in lowered:
        found = "unknown"
        for line in detail.splitlines():
            if "NEED:python311:" in line:
                found = line.split("NEED:python311:", 1)[1].strip() or found
        return (
            f"{alias}: remote Python must be 3.11+. Found {found}. "
            "Install python3.11+ on that host (macOS: brew install python@3.12) "
            "and retry Clone + install."
        )
    if "requires a different python" in lowered:
        return (
            f"{alias}: remote Python must be 3.11+. "
            "Install python3.11+ on that host (macOS: brew install python@3.12) "
            "and retry Clone + install."
        )
    if "no such file or directory" in lowered:
        if binary in detail or binary.rsplit("/", 1)[-1] in detail:
            return (
                f"{alias}: remote CLI not found at {binary}. "
                "Install hermes-profile on that host, or set hosts.*.remote_binary "
                "to the real path (or `hermes-profile` if it is on PATH)."
            )
        return f"{alias}: remote path not found. {detail}"
    if "permission denied" in lowered:
        return f"{alias}: SSH permission denied. Check your agent keys and ssh_user."
    if "connection refused" in lowered:
        return f"{alias}: SSH connection refused. Is the host reachable?"
    if "could not resolve" in lowered or "name or service not known" in lowered:
        return f"{alias}: could not resolve the SSH host name."
    if "timed out" in lowered or "timeout" in lowered:
        return f"{alias}: SSH timed out."
    return f"{alias}: SSH failed. {detail}"


def looks_like_hermes_agent_error(binary: str, detail: str) -> bool:
    lowered = detail.lower()
    if "invalid choice" in lowered and "chat" in lowered:
        return True
    return is_hermes_agent_binary(binary) and "usage: hermes" in lowered


def remote_arguments(original: list[str]) -> list[str]:
    """Remove client-only global options before forwarding a command remotely."""
    forwarded: list[str] = []
    index = 0
    while index < len(original):
        value = original[index]
        if value in {"--host", "--config", "--format"}:
            index += 2
        elif any(
            value.startswith(prefix) for prefix in ("--host=", "--config=", "--format=")
        ):
            index += 1
        else:
            forwarded.append(value)
            index += 1
    return forwarded
