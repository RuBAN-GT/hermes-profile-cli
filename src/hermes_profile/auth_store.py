import copy
import fcntl
import hashlib
import json
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from hermes_profile.models import Settings
from hermes_profile.paths import PROFILE_NAME, write_private


def hermes_root(settings: Settings) -> Path:
    if settings.profiles_dir.name != "profiles":
        raise ValueError(
            "shared auth requires profiles_dir to be named 'profiles' so Hermes "
            "can resolve its root fallback"
        )
    return settings.profiles_dir.parent


def shared_auth_path(settings: Settings) -> Path:
    return hermes_root(settings) / "auth.json"


def identities_dir(settings: Settings) -> Path:
    return hermes_root(settings) / "identities"


def identity_auth_path(settings: Settings, name: str) -> Path:
    if not PROFILE_NAME.fullmatch(name):
        raise ValueError(
            "identity name must use lowercase letters, digits, and hyphens"
        )
    return identities_dir(settings) / name / "auth.json"


def empty_auth_store() -> dict[str, Any]:
    return {"version": 1, "providers": {}, "credential_pool": {}}


def load_auth_store(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.is_file() and not path.is_symlink():
        if missing_ok:
            return empty_auth_store()
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
    data.setdefault("version", 1)
    return data


def save_auth_store(path: Path, store: dict[str, Any]) -> None:
    target = path.resolve() if path.is_symlink() else path
    write_private(target, json.dumps(store, indent=2, sort_keys=True) + "\n")


def copy_auth_providers(
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


def providers_contain_refresh_tokens(
    source: dict[str, Any], providers: list[str]
) -> bool:
    for provider in providers:
        for section in (source["providers"], source["credential_pool"]):
            if provider in section and contains_refresh_token(section[provider]):
                return True
    return False


def contains_refresh_token(value: object) -> bool:
    if isinstance(value, dict):
        return "refresh_token" in value or any(
            contains_refresh_token(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_refresh_token(item) for item in value)
    return False


def auth_inventory_changed(directory: Path) -> bool:
    current = auth_inventory_digest(directory / "auth.json")
    applied = directory / "state" / "auth-inventory.sha256"
    if current is None:
        return applied.is_file()
    return not applied.is_file() or applied.read_text().strip() != current


def write_auth_inventory(directory: Path) -> None:
    digest = auth_inventory_digest(directory / "auth.json")
    if digest is not None:
        write_private(directory / "state" / "auth-inventory.sha256", f"{digest}\n")
    else:
        (directory / "state" / "auth-inventory.sha256").unlink(missing_ok=True)


def auth_inventory_digest(path: Path) -> str | None:
    inventory = auth_inventory(path)
    if inventory is None:
        return None
    payload = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def auth_inventory(path: Path) -> dict[str, list[dict[str, str]]] | None:
    if not path.is_file() and not path.is_symlink():
        return None
    if path.is_symlink() and not path.exists():
        return None
    data = load_auth_store(path)
    pool = data.get("credential_pool", {})
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


def store_providers(store: dict[str, Any]) -> list[str]:
    return sorted(set(store["providers"]) | set(store["credential_pool"]))


@contextmanager
def auth_locks(*stores: Path):
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
