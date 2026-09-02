import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from hermes_profile.env import parse_env, render_env
from hermes_profile.merge import NO_CHANGE, changed_values, merge
from hermes_profile.models import Settings
from hermes_profile.paths import write_private
from hermes_profile.profiles import config_documents, env_documents, load_profile


def render_profile(
    settings: Settings, name: str
) -> tuple[dict[str, Any], dict[str, str]]:
    profile = load_profile(settings, name)
    directory = settings.profiles_dir / profile.name
    config: dict[str, Any] = {}
    for document in config_documents(settings, profile):
        config = merge(config, document)
    config = merge(config, _read_yaml(directory / "runtime-config.yaml"))

    environment: dict[str, str] = {}
    for source, document in env_documents(settings, profile):
        environment.update(parse_env(document, source))
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
    config, environment = render_profile(settings, name)
    directory = settings.profiles_dir / name
    _write_yaml(directory / "config.yaml", config)
    write_private(directory / ".env", render_env(environment))
    _write_yaml(directory / "state" / "applied-config.yaml", config)
    write_private(directory / "state" / "applied.env", render_env(environment))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    write_private(path, yaml.safe_dump(data, allow_unicode=False, sort_keys=True))


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
        return False
    return not applied.is_file() or applied.read_text().strip() != current


def _write_auth_inventory(directory: Path) -> None:
    digest = _auth_inventory_digest(directory / "auth.json")
    if digest is not None:
        write_private(directory / "state" / "auth-inventory.sha256", f"{digest}\n")


def _auth_inventory_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
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
    payload = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
