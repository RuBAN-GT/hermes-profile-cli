from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hermes_profile.auth_store import (
    identity_auth_path,
    shared_auth_path,
    store_providers,
)
from hermes_profile.models import Profile, Settings
from hermes_profile.paths import PROFILE_NAME, profile_dir
from hermes_profile.profiles import load_profile

AUTH_MAP_NAME = "auth-map.yaml"
SHARED = "shared"


@dataclass(frozen=True)
class Identity:
    name: str
    provider: str


@dataclass(frozen=True)
class AuthMap:
    defaults: dict[str, str] = field(default_factory=dict)
    identities: dict[str, Identity] = field(default_factory=dict)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)


def auth_map_path(settings: Settings) -> Path:
    return settings.fragments_dir / AUTH_MAP_NAME


def load_auth_map(settings: Settings) -> AuthMap:
    path = auth_map_path(settings)
    if not path.is_file():
        return AuthMap()
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    identities = _identities(data.get("identities", {}), path)
    defaults = _defaults(data.get("defaults", {}), path)
    profiles = _profile_bindings(data.get("profiles", {}), identities, path)
    return AuthMap(defaults=defaults, identities=identities, profiles=profiles)


def resolve_bindings(auth_map: AuthMap, profile: Profile) -> dict[str, str]:
    key = profile.auth or profile.name
    bindings = dict(auth_map.defaults)
    bindings.update(auth_map.profiles.get(key, {}))
    return bindings


def auth_map_status(settings: Settings) -> dict[str, object]:
    """Describe bindings without exposing credentials."""
    path = auth_map_path(settings)
    auth_map = load_auth_map(settings)
    profiles = []
    for name in sorted(
        set(auth_map.profiles)
        | {profile.name for profile in _declared_profiles(settings)}
    ):
        try:
            profile = load_profile(settings, name)
        except ValueError:
            profile = Profile(name=name)
        profiles.append(_binding_status(settings, auth_map, profile))
    return {
        "path": str(path),
        "present": path.is_file(),
        "identities": [
            {
                "name": identity.name,
                "provider": identity.provider,
                "present": identity_auth_path(settings, identity.name).exists(),
            }
            for identity in auth_map.identities.values()
        ],
        "defaults": dict(auth_map.defaults),
        "profiles": profiles,
    }


def bind_profile_auth(
    settings: Settings, name: str, *, force: bool = False
) -> dict[str, object]:
    """Attach mapped identity stores to a profile without copying tokens."""
    profile = load_profile(settings, name)
    auth_map = load_auth_map(settings)
    bindings = resolve_bindings(auth_map, profile)
    if not bindings:
        return {"profile": name, "attached": [], "shared": []}
    directory = profile_dir(settings, name)
    attached: list[dict[str, str]] = []
    shared: list[str] = []
    for provider, target in bindings.items():
        if target == SHARED:
            shared.append(provider)
            continue
        attached.append(
            _attach_identity(settings, directory, target, provider, force=force)
        )
    return {"profile": name, "attached": attached, "shared": shared}


def auth_preflight(settings: Settings, name: str) -> dict[str, object]:
    profile = load_profile(settings, name)
    return _binding_status(settings, load_auth_map(settings), profile)


def _declared_profiles(settings: Settings) -> list[Profile]:
    from hermes_profile.profiles import list_profiles

    profiles = []
    for name in list_profiles(settings):
        try:
            profiles.append(load_profile(settings, name))
        except ValueError:
            continue
    return profiles


def _binding_status(
    settings: Settings, auth_map: AuthMap, profile: Profile
) -> dict[str, object]:
    bindings = resolve_bindings(auth_map, profile)
    directory = settings.profiles_dir / profile.name
    profile_auth = directory / "auth.json"
    items = []
    missing: list[str] = []
    for provider, target in bindings.items():
        item: dict[str, object] = {"provider": provider, "target": target}
        if target == SHARED:
            item["path"] = str(shared_auth_path(settings))
            item["present"] = shared_auth_path(settings).is_file()
            item["shadowed"] = provider in _local_providers(profile_auth)
            items.append(item)
            continue
        identity_file = identity_auth_path(settings, target)
        item["path"] = str(identity_file)
        item["present"] = identity_file.exists() or profile_auth.exists()
        item["bound"] = _is_bound(profile_auth, identity_file)
        if not item["present"]:
            missing.append(target)
        items.append(item)
    return {
        "profile": profile.name,
        "auth_map_key": profile.auth or profile.name,
        "bindings": items,
        "missing": missing,
    }


def _local_providers(path: Path) -> set[str]:
    if not path.exists():
        return set()
    from hermes_profile.auth_store import load_auth_store

    return set(store_providers(load_auth_store(path, missing_ok=True)))


def _is_bound(profile_auth: Path, identity_file: Path) -> bool:
    if not profile_auth.exists() and not identity_file.exists():
        return False
    if profile_auth.exists() and identity_file.is_symlink():
        try:
            return identity_file.resolve() == profile_auth.resolve()
        except OSError:
            return False
    if profile_auth.is_symlink():
        try:
            return profile_auth.resolve() == identity_file.resolve()
        except OSError:
            return False
    return False


def _attach_identity(
    settings: Settings,
    directory: Path,
    identity_name: str,
    provider: str,
    *,
    force: bool,
) -> dict[str, str]:
    identity_file = identity_auth_path(settings, identity_name)
    profile_file = directory / "auth.json"
    identity_file.parent.mkdir(parents=True, exist_ok=True)
    identity_file.parent.chmod(0o700)

    if _is_bound(profile_file, identity_file):
        return {
            "identity": identity_name,
            "provider": provider,
            "path": str(profile_file),
            "action": "bound",
        }

    if profile_file.exists() and identity_file.exists() and not force:
        raise ValueError(
            f"{directory.name}: auth.json and identity {identity_name} both exist; "
            "pass --force to replace the identity pointer"
        )

    if profile_file.exists() and not identity_file.exists():
        _symlink(identity_file, profile_file)
        return {
            "identity": identity_name,
            "provider": provider,
            "path": str(profile_file),
            "action": "linked-identity",
        }

    if identity_file.exists() and not profile_file.exists():
        identity_file.replace(profile_file)
        _symlink(identity_file, profile_file)
        return {
            "identity": identity_name,
            "provider": provider,
            "path": str(profile_file),
            "action": "moved",
        }

    if force and profile_file.exists() and identity_file.exists():
        if identity_file.is_symlink() or identity_file.is_file():
            identity_file.unlink()
        _symlink(identity_file, profile_file)
        return {
            "identity": identity_name,
            "provider": provider,
            "path": str(profile_file),
            "action": "relinked",
        }

    raise ValueError(f"identity store missing: {identity_name}; import or login first")


def _symlink(link: Path, target: Path) -> None:
    link.unlink(missing_ok=True)
    link.symlink_to(_relative_to(link, target))


def _relative_to(link: Path, target: Path) -> Path:
    return Path(os_relpath(target, link.parent))


def os_relpath(target: Path, start: Path) -> str:
    import os

    return os.path.relpath(target, start)


def _identities(value: object, path: Path) -> dict[str, Identity]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: identities must be a mapping")
    identities: dict[str, Identity] = {}
    for name, data in value.items():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ValueError(
                f"{path}: identity names use lowercase letters, digits, and hyphens"
            )
        if not isinstance(data, dict) or not isinstance(data.get("provider"), str):
            raise ValueError(f"{path}: identities.{name} requires provider")
        identities[name] = Identity(name=name, provider=data["provider"])
    return identities


def _defaults(value: object, path: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: defaults must be a mapping")
    defaults: dict[str, str] = {}
    for provider, target in value.items():
        if not isinstance(provider, str) or target != SHARED:
            raise ValueError(f"{path}: defaults.{provider} must be '{SHARED}'")
        defaults[provider] = SHARED
    return defaults


def _profile_bindings(
    value: object, identities: dict[str, Identity], path: Path
) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: profiles must be a mapping")
    profiles: dict[str, dict[str, str]] = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise ValueError(
                f"{path}: profile keys use lowercase letters, digits, and hyphens"
            )
        profiles[name] = _profile_targets(spec, identities, path, name)
    return profiles


def _profile_targets(
    value: object,
    identities: dict[str, Identity],
    path: Path,
    name: str,
) -> dict[str, str]:
    if isinstance(value, list):
        targets: dict[str, str] = {}
        for item in value:
            if not isinstance(item, str) or item not in identities:
                raise ValueError(f"{path}: profiles.{name} unknown identity: {item}")
            provider = identities[item].provider
            targets[provider] = item
        return targets
    if isinstance(value, dict):
        targets = {}
        for provider, target in value.items():
            if not isinstance(provider, str) or not isinstance(target, str):
                raise ValueError(f"{path}: profiles.{name} bindings must be strings")
            if target != SHARED and target not in identities:
                raise ValueError(
                    f"{path}: profiles.{name}.{provider} unknown identity: {target}"
                )
            if target != SHARED and identities[target].provider != provider:
                raise ValueError(
                    f"{path}: profiles.{name}.{provider} identity provider mismatch"
                )
            targets[provider] = target
        return targets
    raise ValueError(f"{path}: profiles.{name} must be a list or mapping")
