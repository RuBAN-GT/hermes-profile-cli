import difflib
from pathlib import Path
from typing import Any

import yaml

from hermes_profile.auth_map import auth_preflight, bind_profile_auth, load_auth_map
from hermes_profile.auth_store import (
    auth_inventory_changed,
    auth_locks,
    copy_auth_providers,
    load_auth_store,
    providers_contain_refresh_tokens,
    save_auth_store,
    shared_auth_path,
    store_providers,
    write_auth_inventory,
)
from hermes_profile.env import parse_env, render_env
from hermes_profile.merge import NO_CHANGE, changed_values, merge
from hermes_profile.models import Settings
from hermes_profile.paths import profile_dir, write_private
from hermes_profile.profiles import (
    config_documents,
    env_documents,
    list_profiles,
    load_profile,
)


def render_profile(
    settings: Settings, name: str, *, include_runtime: bool = True
) -> tuple[dict[str, Any], dict[str, str]]:
    profile = load_profile(settings, name)
    directory = settings.profiles_dir / profile.name
    config: dict[str, Any] = {}
    for document in config_documents(settings, profile):
        config = merge(config, document)
    if include_runtime:
        config = merge(config, _read_yaml(directory / "runtime-config.yaml"))

    environment: dict[str, str] = {}
    for source, document in env_documents(settings, profile):
        environment.update(parse_env(document, source))
    if include_runtime:
        environment.update(_read_env(directory / "runtime.env"))
    return config, environment


def status(settings: Settings, name: str) -> dict[str, bool]:
    directory = settings.profiles_dir / name
    if (directory / "profile.yaml").is_file():
        load_profile(settings, name)
    elif not (directory / "config.yaml").is_file():
        raise ValueError(f"profile does not exist: {name}")
    return {
        "config_drift": _is_drifted(
            directory / "config.yaml", directory / "state" / "applied-config.yaml"
        ),
        "env_drift": _is_drifted(
            directory / ".env", directory / "state" / "applied.env"
        ),
        "auth_inventory_changed": auth_inventory_changed(directory),
    }


def preflight(settings: Settings, name: str) -> dict[str, object]:
    """Show the changes an apply would make without writing files."""
    directory = settings.profiles_dir / name
    proposed_config, proposed_environment = render_profile(settings, name)
    current_config = _read_yaml(directory / "config.yaml")
    current_environment = _read_env(directory / ".env")
    legacy_managed = _read_yaml(settings.managed_dir / "config.yaml")
    effective_current = merge(current_config, legacy_managed)
    return {
        "config_diff": _yaml_diff(
            effective_current, proposed_config, "effective config", "rendered config"
        ),
        "materialization_diff": _yaml_diff(
            current_config, proposed_config, "config.yaml", "rendered config.yaml"
        ),
        "legacy_managed_layer": bool(legacy_managed),
        "env_added": sorted(set(proposed_environment) - set(current_environment)),
        "env_changed": sorted(
            key
            for key in set(proposed_environment) & set(current_environment)
            if proposed_environment[key] != current_environment[key]
        ),
        "env_removed": sorted(set(current_environment) - set(proposed_environment)),
        **auth_preflight(settings, name),
    }


def _yaml_diff(
    current: dict[str, Any], proposed: dict[str, Any], fromfile: str, tofile: str
) -> str:
    current_yaml = yaml.safe_dump(current, allow_unicode=False, sort_keys=True)
    proposed_yaml = yaml.safe_dump(proposed, allow_unicode=False, sort_keys=True)
    return "".join(
        difflib.unified_diff(
            current_yaml.splitlines(keepends=True),
            proposed_yaml.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def shared_auth_status(settings: Settings) -> dict[str, object]:
    """Report the Hermes root auth fallback without exposing credentials."""
    path = shared_auth_path(settings)
    store = load_auth_store(path, missing_ok=True)
    return {
        "path": str(path),
        "present": path.is_file(),
        "providers": store_providers(store),
    }


def sync_shared_auth(
    settings: Settings, source_name: str, providers: list[str], *, allow_oauth: bool
) -> dict[str, object]:
    """Copy selected provider records into the Hermes root fallback store."""
    source = profile_dir(settings, source_name) / "auth.json"
    target = shared_auth_path(settings)
    if not source.is_file():
        raise ValueError(f"profile auth store not found: {source}")
    if not providers:
        raise ValueError("auth sync requires at least one --provider")

    with auth_locks(source, target):
        source_store = load_auth_store(source)
        target_store = load_auth_store(target, missing_ok=True)
        if not allow_oauth and providers_contain_refresh_tokens(
            source_store, providers
        ):
            raise ValueError(
                "selected providers contain OAuth refresh tokens; pass --allow-oauth "
                "only after planning removal of the profile-local override"
            )
        copied = copy_auth_providers(source_store, target_store, providers)
        save_auth_store(target, target_store)
    return {"synced_from": source_name, "providers": copied, "path": str(target)}


def reconcile(settings: Settings, name: str) -> dict[str, bool]:
    directory = settings.profiles_dir / name
    load_profile(settings, name)
    changes = status(settings, name)

    if changes["config_drift"]:
        applied = _read_yaml(directory / "state" / "applied-config.yaml")
        actual = _read_yaml(directory / "config.yaml")
        delta = changed_values(applied, actual)
        if delta is not NO_CHANGE:
            overlay = merge(_read_yaml(directory / "runtime-config.yaml"), delta)
            _write_yaml(directory / "runtime-config.yaml", overlay)
        _write_yaml(directory / "state" / "applied-config.yaml", actual)

    if changes["env_drift"]:
        applied = _read_env(directory / "state" / "applied.env")
        actual = _read_env(directory / ".env")
        delta = {
            key: value for key, value in actual.items() if applied.get(key) != value
        }
        if delta:
            overlay = _read_env(directory / "runtime.env")
            overlay.update(delta)
            write_private(directory / "runtime.env", render_env(overlay))
        write_private(directory / "state" / "applied.env", render_env(actual))

    write_auth_inventory(directory)

    return changes


def apply(settings: Settings, name: str, discard_runtime: bool = False) -> None:
    current = status(settings, name)
    if not discard_runtime and (current["config_drift"] or current["env_drift"]):
        raise ValueError(
            "profile has runtime drift; run reconcile or pass --discard-runtime"
        )
    config, environment = render_profile(
        settings, name, include_runtime=not discard_runtime
    )
    directory = settings.profiles_dir / name
    _write_yaml(directory / "config.yaml", config)
    write_private(directory / ".env", render_env(environment))
    _write_yaml(directory / "state" / "applied-config.yaml", config)
    write_private(directory / "state" / "applied.env", render_env(environment))
    if discard_runtime:
        (directory / "runtime-config.yaml").unlink(missing_ok=True)
        (directory / "runtime.env").unlink(missing_ok=True)
    auth_map = load_auth_map(settings)
    if auth_map.profiles or auth_map.defaults:
        bind_profile_auth(settings, name)


def apply_all(settings: Settings, discard_runtime: bool = False) -> list[str]:
    names = list_profiles(settings)
    for name in names:
        apply(settings, name, discard_runtime)
    return names


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    write_private(path, _yaml_text(data))


def _yaml_text(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        {key: data[key] for key in sorted(data)}, allow_unicode=True, sort_keys=False
    )


def _read_env(path: Path) -> dict[str, str]:
    return parse_env(path.read_text(), str(path)) if path.is_file() else {}


def _is_drifted(actual: Path, applied: Path) -> bool:
    if not actual.exists() and not applied.exists():
        return False
    if not actual.exists() or not applied.exists():
        return True
    return actual.read_bytes() != applied.read_bytes()
