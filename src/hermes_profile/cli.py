import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from hermes_profile import __version__
from hermes_profile.auth_adapters import (
    export_auth,
    import_auth,
    list_sources,
    push_auth,
)
from hermes_profile.auth_map import auth_map_status, bind_profile_auth
from hermes_profile.backups import create_backup, list_backups, restore_backup
from hermes_profile.helptext import help_text
from hermes_profile.models import Profile, Settings
from hermes_profile.paths import (
    config_path,
    initialize_settings,
    load_settings,
    upsert_host,
)
from hermes_profile.profiles import (
    create_profile,
    delete_profile,
    list_profiles,
    load_profile,
    save_profile,
    share_profile_stack,
)
from hermes_profile.self_update import self_update
from hermes_profile.service import (
    apply,
    preflight,
    reconcile,
    render_profile,
    shared_auth_status,
    status,
    sync_shared_auth,
)
from hermes_profile.transport import SshTransport, remote_arguments

LOCAL_AUTH_COMMANDS = {"import", "export", "push", "sources"}


def main(argv: list[str] | None = None) -> None:
    original = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    arguments = parser.parse_args(original)
    try:
        if arguments.command == "help":
            print(help_text())
            return
        if arguments.command == "self-update":
            _emit(self_update(), arguments.format)
            return
        path = config_path(arguments.config)
        if arguments.command == "init":
            settings = initialize_settings(
                path,
                arguments.managed_dir,
                profiles_dir=arguments.profiles_dir,
                fragments_dir=arguments.fragments_dir,
            )
            _emit(
                {
                    "initialized": str(path),
                    "managed_dir": str(settings.managed_dir),
                    "profiles_dir": str(settings.profiles_dir),
                    "fragments_dir": str(settings.fragments_dir),
                },
                arguments.format,
            )
            return
        if arguments.command == "tui" and not path.is_file():
            from hermes_profile.tui.setup import SetupApp

            created = SetupApp(path).run()
            if created is not None:
                path = created
            if not path.is_file():
                return
        settings = load_settings(str(path))
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
    initialize = commands.add_parser(
        "init", help="create manager config and directories"
    )
    initialize.add_argument(
        "--managed-dir",
        type=lambda value: Path(value).expanduser(),
        default=Path("~/.local/share/hermes-profile/managed").expanduser(),
        help="local operational root for profiles and fragments",
    )
    initialize.add_argument(
        "--profiles-dir",
        type=lambda value: Path(value).expanduser(),
        default=None,
        help="profile homes; defaults to <managed-dir>/profiles",
    )
    initialize.add_argument(
        "--fragments-dir",
        type=lambda value: Path(value).expanduser(),
        default=None,
        help="shared fragments; defaults to <managed-dir>/fragments",
    )
    commands.add_parser("list", help="list profile names")
    create = commands.add_parser("create")
    create.add_argument("name")
    create.add_argument("--add-config", action="append", default=[])
    create.add_argument("--add-env", action="append", default=[])
    create.add_argument(
        "--share-from",
        help="copy shared fragment refs from an existing profile; identity stays new",
    )
    show = commands.add_parser("show")
    show.add_argument("name")
    profile_status = commands.add_parser("status")
    profile_status.add_argument("name")
    render = commands.add_parser("render")
    render.add_argument("name")
    render.add_argument("--check", action="store_true")
    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("name")
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("name")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("name")
    apply_parser.add_argument("--discard-runtime", action="store_true")
    update = commands.add_parser("update")
    update.add_argument("name")
    update.add_argument("--add-config", action="append", default=[])
    update.add_argument("--add-env", action="append", default=[])
    update.add_argument(
        "--set-config",
        action="append",
        default=[],
        help="replace config fragment refs instead of appending",
    )
    update.add_argument(
        "--set-env",
        action="append",
        default=[],
        help="replace env fragment refs instead of appending",
    )
    delete = commands.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("--confirm", action="store_true")
    auth = commands.add_parser("auth")
    auth_subcommands = auth.add_subparsers(dest="auth_command", required=True)
    auth_subcommands.add_parser(
        "shared-status", help="inspect the Hermes root auth fallback"
    )
    sync_auth = auth_subcommands.add_parser(
        "sync", help="copy selected provider records to the root auth fallback"
    )
    sync_auth.add_argument("--from", dest="source", required=True)
    sync_auth.add_argument("--provider", action="append", required=True)
    sync_auth.add_argument(
        "--allow-oauth",
        action="store_true",
        help="allow copying OAuth refresh tokens into the shared fallback",
    )
    auth_subcommands.add_parser(
        "map-status", help="inspect auth-map bindings without exposing secrets"
    )
    bind_auth = auth_subcommands.add_parser(
        "bind", help="attach mapped identity stores to a profile"
    )
    bind_auth.add_argument("name")
    bind_auth.add_argument(
        "--force",
        action="store_true",
        help="replace an identity pointer when both stores already exist",
    )
    sources = auth_subcommands.add_parser(
        "sources", help="list adapter credentials without exposing secrets"
    )
    sources.add_argument("--from", dest="source", required=True)
    sources.add_argument("--path", type=lambda value: Path(value).expanduser())
    import_auth_cmd = auth_subcommands.add_parser(
        "import", help="import credentials into an identity or shared store"
    )
    _transfer_auth_args(import_auth_cmd, source=True)
    export_auth_cmd = auth_subcommands.add_parser(
        "export", help="export an identity or shared store through an adapter"
    )
    _transfer_auth_args(export_auth_cmd, source=False)
    push_auth_cmd = auth_subcommands.add_parser(
        "push", help="copy an identity or shared provider slice to an SSH host"
    )
    push_auth_cmd.add_argument("--host", dest="push_host", required=True)
    push_auth_cmd.add_argument("--identity")
    push_auth_cmd.add_argument("--shared", action="store_true")
    push_auth_cmd.add_argument("--provider", action="append", default=[])
    push_auth_cmd.add_argument("--allow-oauth", action="store_true")
    backup = commands.add_parser("backup", help="snapshot or restore managed setup")
    backup_subcommands = backup.add_subparsers(dest="backup_command", required=True)
    backup_subcommands.add_parser("create")
    backup_subcommands.add_parser("list")
    restore = backup_subcommands.add_parser("restore")
    restore.add_argument("name")
    restore.add_argument("--confirm", action="store_true")
    ssh = commands.add_parser("ssh")
    ssh_subcommands = ssh.add_subparsers(dest="ssh_command", required=True)
    for name in ("doctor", "init", "install"):
        command = ssh_subcommands.add_parser(name)
        command.add_argument("host")
    commands.add_parser("mcp", help="run MCP server on stdio")
    commands.add_parser("tui", help="open the profile manager TUI")
    commands.add_parser("help", help="show command and TUI help")
    commands.add_parser(
        "self-update",
        help="update this CLI from git and reinstall into the current Python",
    )
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
    if arguments.command == "mcp":
        if arguments.host:
            raise ValueError("mcp runs locally; pass location inside MCP tools")
        from hermes_profile.mcp_server import run_server

        run_server(config)
        return None
    if arguments.command == "tui":
        if arguments.host:
            raise ValueError("select remote hosts from the local TUI")
        from hermes_profile.tui.app import ProfileApp

        ProfileApp(settings, config).run()
        return None
    if (
        arguments.command == "auth"
        and getattr(arguments, "auth_command", None) in LOCAL_AUTH_COMMANDS
    ):
        if arguments.host:
            raise ValueError(
                "auth import/export/push/sources run locally; "
                "use auth push --host ALIAS to copy stores"
            )
        return _run_local(arguments, settings)
    if arguments.host:
        transport = SshTransport(_host(settings, arguments.host))
        return transport.run(remote_arguments(original))
    return _run_local(arguments, settings)


def _run_local(arguments: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    if arguments.command == "list":
        return {"profiles": list_profiles(settings)}
    if arguments.command == "create":
        if arguments.share_from:
            path = share_profile_stack(
                settings,
                arguments.share_from,
                arguments.name,
                extra_config=tuple(arguments.add_config),
                extra_env=tuple(arguments.add_env),
            )
        else:
            path = create_profile(
                settings,
                arguments.name,
                config_fragments=tuple(arguments.add_config),
                env_fragments=tuple(arguments.add_env),
            )
        return {"created": str(path)}
    if arguments.command == "show":
        return _profile_data(load_profile(settings, arguments.name))
    if arguments.command == "status":
        return status(settings, arguments.name)
    if arguments.command == "render":
        config, environment = render_profile(settings, arguments.name)
        return {"config": config, "environment_count": len(environment), "valid": True}
    if arguments.command == "preflight":
        return preflight(settings, arguments.name)
    if arguments.command == "reconcile":
        return {"reconciled": reconcile(settings, arguments.name), "ok": True}
    if arguments.command == "apply":
        apply(settings, arguments.name, arguments.discard_runtime)
        return {"ok": True, "applied": arguments.name}
    if arguments.command == "update":
        _update(
            settings,
            arguments.name,
            arguments.add_config,
            arguments.add_env,
            arguments.set_config,
            arguments.set_env,
        )
        return {"ok": True, "updated": arguments.name}
    if arguments.command == "delete":
        if not arguments.confirm:
            raise ValueError("delete requires --confirm")
        delete_profile(settings, arguments.name)
        return {"ok": True, "deleted": arguments.name}
    if arguments.command == "auth":
        if arguments.auth_command == "shared-status":
            return shared_auth_status(settings)
        if arguments.auth_command == "sync":
            return sync_shared_auth(
                settings,
                arguments.source,
                arguments.provider,
                allow_oauth=arguments.allow_oauth,
            )
        if arguments.auth_command == "map-status":
            return auth_map_status(settings)
        if arguments.auth_command == "bind":
            return bind_profile_auth(settings, arguments.name, force=arguments.force)
        if arguments.auth_command == "sources":
            return list_sources(arguments.source, arguments.path)
        if arguments.auth_command == "import":
            return import_auth(
                settings,
                source=arguments.source,
                identity=arguments.identity,
                provider=arguments.provider,
                source_profile=arguments.source_profile,
                path=arguments.path,
                shared=arguments.shared,
                allow_oauth=arguments.allow_oauth,
            )
        if arguments.auth_command == "export":
            return export_auth(
                settings,
                destination=arguments.destination,
                identity=arguments.identity,
                provider=arguments.provider,
                source_profile=arguments.source_profile,
                path=arguments.path,
                shared=arguments.shared,
                allow_oauth=arguments.allow_oauth,
            )
        if arguments.auth_command == "push":
            return push_auth(
                settings,
                _host(settings, arguments.push_host),
                identity=arguments.identity,
                providers=arguments.provider,
                shared=arguments.shared,
                allow_oauth=arguments.allow_oauth,
            )
        raise ValueError(f"unsupported auth command: {arguments.auth_command}")
    if arguments.command == "backup":
        if arguments.backup_command == "create":
            return create_backup(settings)
        if arguments.backup_command == "list":
            return list_backups(settings)
        if not arguments.confirm:
            raise ValueError("backup restore requires --confirm")
        return restore_backup(settings, arguments.name)
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
    elif "config_diff" in result:
        diff = result["config_diff"] or "No effective config changes.\n"
        if not str(diff).endswith("\n"):
            diff = f"{diff}\n"
        print(diff, end="")
        materialization = result.get("materialization_diff", "")
        if materialization:
            print("File materialization diff:")
            if not str(materialization).endswith("\n"):
                materialization = f"{materialization}\n"
            print(materialization, end="")
        if result.get("legacy_managed_layer"):
            print("legacy managed layer: present")
        for key, label in (
            ("env_added", "added"),
            ("env_changed", "changed"),
            ("env_removed", "removed"),
        ):
            names = result[key]
            print(f"env {label}: {', '.join(names) if names else 'none'}")
        bindings = result.get("bindings")
        if isinstance(bindings, list):
            if not bindings:
                print("auth bindings: none")
            for item in bindings:
                if not isinstance(item, dict):
                    continue
                target = item.get("target")
                provider = item.get("provider")
                extra = []
                if item.get("missing") or (
                    isinstance(result.get("missing"), list)
                    and target in result["missing"]
                ):
                    extra.append("missing")
                if item.get("shadowed"):
                    extra.append("shadows shared")
                if item.get("bound"):
                    extra.append("bound")
                suffix = f" ({', '.join(extra)})" if extra else ""
                print(f"auth {provider}: {target}{suffix}")
    else:
        print(yaml.safe_dump(result, allow_unicode=False, sort_keys=False), end="")


def _update(
    settings: Settings,
    name: str,
    config: list[str],
    environment: list[str],
    set_config: list[str],
    set_environment: list[str],
) -> None:
    profile = load_profile(settings, name)
    if not config and not environment and not set_config and not set_environment:
        raise ValueError(
            "update requires --add-config, --add-env, --set-config, or --set-env"
        )
    config_fragments = tuple(set_config) if set_config else profile.config_fragments
    env_fragments = tuple(set_environment) if set_environment else profile.env_fragments
    save_profile(
        settings,
        Profile(
            name=name,
            config_fragments=config_fragments + tuple(config),
            env_fragments=env_fragments + tuple(environment),
            auth=profile.auth,
        ),
    )


def _profile_data(profile: Profile) -> dict[str, object]:
    data: dict[str, object] = {
        "config": list(profile.config_fragments),
        "env": list(profile.env_fragments),
    }
    if profile.auth:
        data["auth"] = profile.auth
    return data


def _transfer_auth_args(parser: argparse.ArgumentParser, *, source: bool) -> None:
    if source:
        parser.add_argument("--from", dest="source", required=True)
    else:
        parser.add_argument("--to", dest="destination", required=True)
    parser.add_argument("--identity")
    parser.add_argument("--provider")
    parser.add_argument("--source-profile")
    parser.add_argument("--path", type=lambda value: Path(value).expanduser())
    parser.add_argument("--shared", action="store_true")
    parser.add_argument("--allow-oauth", action="store_true")
