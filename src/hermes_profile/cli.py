import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from hermes_profile import __version__
from hermes_profile.models import Profile, Settings
from hermes_profile.paths import (
    config_path,
    initialize_settings,
    load_settings,
    upsert_host,
    write_private,
)
from hermes_profile.profiles import (
    create_profile,
    delete_profile,
    list_profiles,
    load_profile,
)
from hermes_profile.service import apply, reconcile, render_profile, status
from hermes_profile.transport import SshTransport, remote_arguments


def main(argv: list[str] | None = None) -> None:
    original = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    arguments = parser.parse_args(original)
    try:
        path = config_path(arguments.config)
        if arguments.command == "init":
            settings = initialize_settings(path, arguments.managed_dir)
            _emit(
                {"initialized": str(path), "managed_dir": str(settings.managed_dir)},
                arguments.format,
            )
            return
        if arguments.command == "tui" and not path.is_file():
            from hermes_profile.tui.setup import SetupApp

            SetupApp(path).run()
            if not path.is_file():
                return
        settings = load_settings(arguments.config)
        result = _dispatch(arguments, original, settings, path)
        if result is not None:
            _emit(result, arguments.format)
    except ValueError as error:
        parser.error(str(error))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-profile")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", help="manager configuration file")
    parser.add_argument("--host", help="configured remote host alias")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument(
        "--managed-dir",
        type=lambda value: Path(value).expanduser(),
        default=Path("~/.local/share/hermes-profile/managed").expanduser(),
        help="local operational root for profiles and fragments",
    )
    commands.add_parser("list")
    create = commands.add_parser("create")
    create.add_argument("name")
    show = commands.add_parser("show")
    show.add_argument("name")
    profile_status = commands.add_parser("status")
    profile_status.add_argument("name")
    render = commands.add_parser("render")
    render.add_argument("name")
    render.add_argument("--check", action="store_true")
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("name")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("name")
    apply_parser.add_argument("--discard-runtime", action="store_true")
    update = commands.add_parser("update")
    update.add_argument("name")
    update.add_argument("--add-config", action="append", default=[])
    update.add_argument("--add-env", action="append", default=[])
    delete = commands.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("--confirm", action="store_true")
    auth = commands.add_parser("auth")
    auth_subcommands = auth.add_subparsers(dest="auth_command", required=True)
    pull = auth_subcommands.add_parser("pull")
    pull.add_argument("target")
    pull.add_argument("--from", dest="source", required=True)
    pull.add_argument("--provider", action="append", default=["all"])
    ssh = commands.add_parser("ssh")
    ssh_subcommands = ssh.add_subparsers(dest="ssh_command", required=True)
    for name in ("doctor", "init", "install"):
        command = ssh_subcommands.add_parser(name)
        command.add_argument("host")
    commands.add_parser("tui")
    return parser


def _dispatch(
    arguments: argparse.Namespace,
    original: list[str],
    settings: Settings,
    config: Path,
) -> dict[str, Any] | None:
    if arguments.command == "ssh":
        host = _host(settings, arguments.host)
        transport = SshTransport(host)
        if arguments.ssh_command == "doctor":
            return transport.doctor()
        if arguments.ssh_command == "install":
            result = transport.install()
            upsert_host(config, replace(host, remote_binary=result["binary"]))
            return result
        return transport.init()
    if arguments.command == "tui":
        if arguments.host:
            raise ValueError("select remote hosts from the local TUI")
        from hermes_profile.tui.app import ProfileApp

        ProfileApp(settings, config).run()
        return None
    if arguments.host:
        transport = SshTransport(_host(settings, arguments.host))
        return transport.run(remote_arguments(original))
    return _run_local(arguments, settings)


def _run_local(arguments: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    if arguments.command == "list":
        return {"profiles": list_profiles(settings)}
    if arguments.command == "create":
        return {"created": str(create_profile(settings, arguments.name))}
    if arguments.command == "show":
        return _profile_data(load_profile(settings, arguments.name))
    if arguments.command == "status":
        return status(settings, arguments.name)
    if arguments.command == "render":
        config, environment = render_profile(settings, arguments.name)
        return {"config": config, "environment_count": len(environment), "valid": True}
    if arguments.command == "reconcile":
        return {"reconciled": reconcile(settings, arguments.name), "ok": True}
    if arguments.command == "apply":
        apply(settings, arguments.name, arguments.discard_runtime)
        return {"ok": True, "applied": arguments.name}
    if arguments.command == "update":
        _update(settings, arguments.name, arguments.add_config, arguments.add_env)
        return {"ok": True, "updated": arguments.name}
    if arguments.command == "delete":
        if not arguments.confirm:
            raise ValueError("delete requires --confirm")
        delete_profile(settings, arguments.name)
        return {"ok": True, "deleted": arguments.name}
    if arguments.command == "auth":
        raise ValueError(
            "auth pull is not implemented: it requires Hermes auth-store locking "
            "and validation"
        )
    raise ValueError(f"unsupported command: {arguments.command}")


def _host(settings: Settings, alias: str) -> Any:
    try:
        return settings.hosts[alias]
    except KeyError as error:
        raise ValueError(f"unknown host: {alias}") from error


def _emit(result: dict[str, Any], output: str) -> None:
    if output == "json":
        print(json.dumps(result, sort_keys=True))
        return
    if "profiles" in result:
        print("\n".join(result["profiles"]))
    elif {"config_drift", "env_drift", "auth_inventory_changed"} <= result.keys():
        for key, value in result.items():
            print(f"{key}: {'changed' if value else 'clean'}")
    elif "config" in result:
        rendered = yaml.safe_dump(result["config"], allow_unicode=False, sort_keys=True)
        print(rendered, end="")
        print(
            f"environment: {result['environment_count']} variable(s), values redacted"
        )
    else:
        print(yaml.safe_dump(result, allow_unicode=False, sort_keys=False), end="")


def _update(
    settings: Settings, name: str, config: list[str], environment: list[str]
) -> None:
    profile = load_profile(settings, name)
    if not config and not environment:
        raise ValueError("update requires --add-config or --add-env")
    updated = Profile(
        name=name,
        config_fragments=profile.config_fragments + tuple(config),
        env_fragments=profile.env_fragments + tuple(environment),
    )
    path = settings.profiles_dir / name / "profile.yaml"
    write_private(path, yaml.safe_dump(_profile_data(updated), sort_keys=False))


def _profile_data(profile: Profile) -> dict[str, list[str]]:
    return {
        "config": list(profile.config_fragments),
        "env": list(profile.env_fragments),
    }
