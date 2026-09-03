import copy
import difflib
import fcntl
import hashlib
import json
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import yaml

from hermes_profile.env import parse_env, render_env
from hermes_profile.merge import NO_CHANGE, changed_values, merge
from hermes_profile.models import Settings
from hermes_profile.paths import profile_dir, write_private
from hermes_profile.profiles import config_documents, env_documents, load_profile


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
        "auth_inventory_changed": _auth_inventory_changed(directory),
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
    path = _shared_auth_path(settings)
    store = _load_auth_store(path, missing_ok=True)
    providers = sorted(set(store["providers"]) | set(store["credential_pool"]))
    return {
        "path": str(path),
        "present": path.is_file(),
        "providers": providers,
    }


def sync_shared_auth(
    settings: Settings, source_name: str, providers: list[str], *, allow_oauth: bool
) -> dict[str, object]:
    """Copy selected provider records into the Hermes root fallback store."""
    source = profile_dir(settings, source_name) / "auth.json"
    target = _shared_auth_path(settings)
    if not source.is_file():
        raise ValueError(f"profile auth store not found: {source}")
    if not providers:
        raise ValueError("auth sync requires at least one --provider")

    with _auth_locks(source, target):
        source_store = _load_auth_store(source)
        target_store = _load_auth_store(target, missing_ok=True)
        if not allow_oauth and _providers_contain_refresh_tokens(
            source_store, providers
        ):
            raise ValueError(
                "selected providers contain OAuth refresh tokens; pass --allow-oauth "
                "only after planning removal of the profile-local override"
            )
        copied = _copy_auth_providers(source_store, target_store, providers)
        write_private(target, json.dumps(target_store, indent=2, sort_keys=True) + "\n")
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

    _write_auth_inventory(directory)

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
        {key: data[key] for key in sorted(data)}, allow_unicode=False, sort_keys=False
    )


def _read_env(path: Path) -> dict[str, str]:
    return parse_env(path.read_text(), str(path)) if path.is_file() else {}


def _is_drifted(actual: Path, applied: Path) -> bool:
    if not actual.exists() and not applied.exists():
        return False
    if not actual.exists() or not applied.exists():
        return True
    return actual.read_bytes() != applied.read_bytes()


def _auth_inventory_changed(directory: Path) -> bool:
    current = _auth_inventory_digest(directory / "auth.json")
    applied = directory / "state" / "auth-inventory.sha256"
    if current is None:
        return applied.is_file()
    return not applied.is_file() or applied.read_text().strip() != current


def _write_auth_inventory(directory: Path) -> None:
    digest = _auth_inventory_digest(directory / "auth.json")
    if digest is not None:
        write_private(directory / "state" / "auth-inventory.sha256", f"{digest}\n")
    else:
        (directory / "state" / "auth-inventory.sha256").unlink(missing_ok=True)


def _auth_inventory_digest(path: Path) -> str | None:
    inventory = _auth_inventory(path)
    if inventory is None:
        return None
    payload = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _auth_inventory(path: Path) -> dict[str, list[dict[str, str]]] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid auth store JSON") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: auth store must be an object")
    pool = data.get("credential_pool", {})
    if not isinstance(pool, dict):
        raise ValueError(f"{path}: credential_pool must be an object")
    inventory: dict[str, list[dict[str, str]]] = {}
    for provider, entries in pool.items():
        if not isinstance(provider, str) or not isinstance(entries, list):
            continue
        sanitized = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            sanitized.append(
                {
                    field: value
                    for field in ("id", "auth_type", "source")
                    if isinstance((value := entry.get(field)), str)
                }
            )
        inventory[provider] = sorted(sanitized, key=lambda entry: json.dumps(entry))
    return inventory


def _load_auth_store(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if missing_ok:
            return {"version": 1, "providers": {}, "credential_pool": {}}
        raise ValueError(f"auth store not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid auth store JSON") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: auth store must be an object")
    for field in ("providers", "credential_pool"):
        value = data.get(field, {})
        if not isinstance(value, dict):
            raise ValueError(f"{path}: {field} must be an object")
        data[field] = value
    return data


def _copy_auth_providers(
    source: dict[str, Any], target: dict[str, Any], providers: list[str]
) -> list[str]:
    source_states = source["providers"]
    source_pool = source["credential_pool"]
    target_states = target["providers"]
    target_pool = target["credential_pool"]
    copied: list[str] = []
    for provider in dict.fromkeys(providers):
        if provider not in source_states and provider not in source_pool:
            raise ValueError(f"source auth store has no provider: {provider}")
        if provider in source_states:
            target_states[provider] = copy.deepcopy(source_states[provider])
        else:
            target_states.pop(provider, None)
        if provider in source_pool:
            target_pool[provider] = copy.deepcopy(source_pool[provider])
        else:
            target_pool.pop(provider, None)
        copied.append(provider)
    return copied


def _providers_contain_refresh_tokens(
    source: dict[str, Any], providers: list[str]
) -> bool:
    for provider in providers:
        for section in (source["providers"], source["credential_pool"]):
            if provider in section and _contains_refresh_token(section[provider]):
                return True
    return False


def _contains_refresh_token(value: object) -> bool:
    if isinstance(value, dict):
        return "refresh_token" in value or any(
            _contains_refresh_token(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_refresh_token(item) for item in value)
    return False


def _shared_auth_path(settings: Settings) -> Path:
    if settings.profiles_dir.name != "profiles":
        raise ValueError(
            "shared auth requires profiles_dir to be named 'profiles' so Hermes "
            "can resolve its root fallback"
        )
    return settings.profiles_dir.parent / "auth.json"


@contextmanager
def _auth_locks(*stores: Path):
    """Use Hermes-compatible advisory locks for a multi-store sync."""
    lock_paths: list[Path] = []
    seen: set[Path] = set()
    for store in stores:
        path = store.with_suffix(".lock").resolve(strict=False)
        if path not in seen:
            seen.add(path)
            lock_paths.append(path)
    with ExitStack() as stack:
        for path in lock_paths:
            stack.enter_context(_auth_lock(path))
        yield


@contextmanager
def _auth_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as file:
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError, PermissionError) as error:
                if time.monotonic() >= deadline:
                    raise ValueError(
                        f"timed out waiting for auth lock: {path}"
                    ) from error
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
