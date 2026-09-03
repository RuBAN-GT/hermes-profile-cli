import os
import re
import tempfile
from pathlib import Path

import yaml

from hermes_profile.i18n import LANGUAGE_NAMES
from hermes_profile.models import Host, LocalLocation, Settings
from hermes_profile.themes import THEME_NAMES

PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def config_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    configured_dir = os.environ.get("HERMES_PROFILE_CONFIG_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser() / "config.yaml"
    return Path("~/.config/hermes-profile/config.yaml").expanduser()


def initialize_settings(
    path: Path,
    managed_dir: Path,
    hosts: dict[str, Host] | None = None,
    *,
    profiles_dir: Path | None = None,
    fragments_dir: Path | None = None,
) -> Settings:
    """Create the first local configuration and its empty operational layout."""
    if path.exists():
        raise ValueError(f"manager config already exists: {path}")
    managed_dir = _absolute_dir(managed_dir, "managed_dir")
    profiles_dir = _absolute_dir(
        managed_dir / "profiles" if profiles_dir is None else profiles_dir,
        "profiles_dir",
    )
    fragments_dir = _absolute_dir(
        managed_dir / "fragments" if fragments_dir is None else fragments_dir,
        "fragments_dir",
    )
    validated_hosts = _load_hosts(
        {alias: _host_data(host) for alias, host in (hosts or {}).items()}
    )
    settings = Settings(
        managed_dir=managed_dir,
        profiles_dir=profiles_dir,
        fragments_dir=fragments_dir,
        hosts=validated_hosts,
    )
    for directory in (
        settings.managed_dir,
        settings.profiles_dir,
        settings.fragments_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    write_private(
        path,
        yaml.safe_dump(_settings_data(settings), sort_keys=False),
    )
    return settings


def _settings_data(settings: Settings) -> dict[str, object]:
    data: dict[str, object] = {
        "managed_dir": str(settings.managed_dir),
        "profiles_dir": str(settings.profiles_dir),
        "fragments_dir": str(settings.fragments_dir),
        "ui": {
            "animations": settings.animations,
            "theme": settings.theme,
            "language": settings.language,
        },
    }
    if settings.hosts:
        data["hosts"] = {
            alias: _host_data(host) for alias, host in settings.hosts.items()
        }
    if settings.local_locations:
        data["local_locations"] = {
            alias: _local_location_data(location)
            for alias, location in settings.local_locations.items()
        }
    return data


def _host_data(host: Host) -> dict[str, object]:
    data: dict[str, object] = {
        "ssh_host": host.ssh_host,
        "remote_binary": host.remote_binary,
        "remote_config": str(host.remote_config),
        "managed_dir": str(host.managed_dir),
        "profiles_dir": str(host.profiles_dir),
        "fragments_dir": str(host.fragments_dir),
    }
    if host.ssh_user is not None:
        data["ssh_user"] = host.ssh_user
    if host.ssh_port is not None:
        data["ssh_port"] = host.ssh_port
    if host.identity_file is not None:
        data["identity_file"] = str(host.identity_file)
    return data


def _local_location_data(location: LocalLocation) -> dict[str, object]:
    return {
        "managed_dir": str(location.managed_dir),
        "profiles_dir": str(location.profiles_dir),
        "fragments_dir": str(location.fragments_dir),
    }


def load_settings(value: str | None) -> Settings:
    path = config_path(value)
    if not path.is_file():
        raise ValueError(
            f"manager config not found: {path}; copy config.example.yaml "
            "outside the repository"
        )
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict) or not isinstance(data.get("managed_dir"), str):
        raise ValueError("manager config requires a string managed_dir")

    managed_dir = _absolute_dir(Path(data["managed_dir"]), "managed_dir")
    profiles_dir = _absolute_dir(
        Path(data.get("profiles_dir", managed_dir / "profiles")), "profiles_dir"
    )
    fragments_dir = _absolute_dir(
        Path(data.get("fragments_dir", managed_dir / "fragments")), "fragments_dir"
    )
    ui = data.get("ui", {})
    if not isinstance(ui, dict):
        raise ValueError("ui must be a mapping")
    theme = ui.get("theme", "hermes-dracula")
    if not isinstance(theme, str) or theme not in THEME_NAMES:
        raise ValueError(f"ui.theme must be one of: {', '.join(sorted(THEME_NAMES))}")
    hosts = _load_hosts(data.get("hosts", {}))
    local_locations = _load_local_locations(data.get("local_locations", {}))
    animations = ui.get("animations", True)
    if not isinstance(animations, bool):
        raise ValueError("ui.animations must be a boolean")
    language = ui.get("language", "en")
    if not isinstance(language, str) or language not in LANGUAGE_NAMES:
        raise ValueError("ui.language must be en or ru")
    return Settings(
        managed_dir=managed_dir,
        profiles_dir=profiles_dir,
        fragments_dir=fragments_dir,
        animations=animations,
        theme=theme,
        language=language,
        hosts=hosts,
        local_locations=local_locations,
    )


def upsert_host(path: Path, host: Host) -> None:
    """Add or replace one host while preserving unrelated manager settings."""
    if not path.is_file():
        raise ValueError(f"manager config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    hosts = data.setdefault("hosts", {})
    if not isinstance(hosts, dict):
        raise ValueError(f"{path}: hosts must be a mapping")
    hosts[host.alias] = _host_data(host)
    _load_hosts(hosts)
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def upsert_local_location(path: Path, location: LocalLocation) -> None:
    if not path.is_file():
        raise ValueError(f"manager config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    locations = data.setdefault("local_locations", {})
    if not isinstance(locations, dict):
        raise ValueError(f"{path}: local_locations must be a mapping")
    locations[location.alias] = _local_location_data(location)
    _load_local_locations(locations)
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def update_local_settings(
    path: Path, managed_dir: Path, profiles_dir: Path, fragments_dir: Path
) -> None:
    """Update the primary local workspace without changing other locations."""
    if not path.is_file():
        raise ValueError(f"manager config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    data.update(
        {
            "managed_dir": str(_absolute_dir(managed_dir, "managed_dir")),
            "profiles_dir": str(_absolute_dir(profiles_dir, "profiles_dir")),
            "fragments_dir": str(_absolute_dir(fragments_dir, "fragments_dir")),
        }
    )
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def delete_location(path: Path, kind: str, alias: str) -> None:
    """Remove only a manager location record, never its operational files."""
    if kind not in {"local", "ssh"}:
        raise ValueError("location kind must be local or ssh")
    if not path.is_file():
        raise ValueError(f"manager config not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    key = "local_locations" if kind == "local" else "hosts"
    locations = data.get(key)
    if not isinstance(locations, dict) or alias not in locations:
        raise ValueError(f"{kind} location does not exist: {alias}")
    del locations[alias]
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def set_theme(path: Path, theme: str) -> None:
    if theme not in THEME_NAMES:
        return
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    ui = data.setdefault("ui", {})
    if not isinstance(ui, dict):
        raise ValueError(f"{path}: ui must be a mapping")
    ui["theme"] = theme
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def save_language(path: Path, language: str) -> None:
    if language not in LANGUAGE_NAMES:
        return
    if not path.is_file():
        return
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    ui = data.setdefault("ui", {})
    if not isinstance(ui, dict):
        raise ValueError(f"{path}: ui must be a mapping")
    ui["language"] = language
    write_private(path, yaml.safe_dump(data, sort_keys=False))


def _load_hosts(value: object) -> dict[str, Host]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("hosts must be a mapping")
    hosts: dict[str, Host] = {}
    for alias, data in value.items():
        if not isinstance(alias, str) or not PROFILE_NAME.fullmatch(alias):
            raise ValueError("host aliases use lowercase letters, digits, and hyphens")
        if not isinstance(data, dict) or not isinstance(data.get("ssh_host"), str):
            raise ValueError(f"hosts.{alias} requires ssh_host")
        hosts[alias] = Host(
            alias=alias,
            ssh_host=data["ssh_host"],
            ssh_user=_optional_string(data, "ssh_user", alias),
            ssh_port=_optional_port(data, alias),
            identity_file=_optional_path(data, "identity_file", alias),
            remote_binary=_optional_string(data, "remote_binary", alias)
            or "hermes-profile",
            remote_config=_required_path(data, "remote_config", alias),
            managed_dir=_required_path(data, "managed_dir", alias),
            profiles_dir=_required_path(data, "profiles_dir", alias),
            fragments_dir=_required_path(data, "fragments_dir", alias),
        )
    return hosts


def _load_local_locations(value: object) -> dict[str, LocalLocation]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("local_locations must be a mapping")
    locations: dict[str, LocalLocation] = {}
    for alias, data in value.items():
        if not isinstance(alias, str) or not PROFILE_NAME.fullmatch(alias):
            raise ValueError("local aliases use lowercase letters, digits, and hyphens")
        if not isinstance(data, dict):
            raise ValueError(f"local_locations.{alias} must be a mapping")
        locations[alias] = LocalLocation(
            alias=alias,
            managed_dir=_required_path(data, "managed_dir", alias, "local_locations"),
            profiles_dir=_required_path(data, "profiles_dir", alias, "local_locations"),
            fragments_dir=_required_path(
                data, "fragments_dir", alias, "local_locations"
            ),
        )
    return locations


def _required_string(
    data: dict[object, object], name: str, alias: str, group: str = "hosts"
) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{group}.{alias} requires {name}")
    return value


def _optional_string(data: dict[object, object], name: str, alias: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"hosts.{alias}.{name} must be a non-empty string")
    return value


def _optional_port(data: dict[object, object], alias: str) -> int | None:
    value = data.get("ssh_port")
    if value is None:
        return None
    if not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"hosts.{alias}.ssh_port must be between 1 and 65535")
    return value


def _required_path(
    data: dict[object, object], name: str, alias: str, group: str = "hosts"
) -> Path:
    value = _required_string(data, name, alias, group)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"{group}.{alias}.{name} must be an absolute path without '..'"
        )
    return path


def _optional_path(data: dict[object, object], name: str, alias: str) -> Path | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"hosts.{alias}.{name} must be a non-empty path")
    return Path(value).expanduser()


def profile_dir(settings: Settings, name: str) -> Path:
    if not PROFILE_NAME.fullmatch(name):
        raise ValueError("profile name must use lowercase letters, digits, and hyphens")
    return settings.profiles_dir / name


def fragment_path(settings: Settings, reference: str) -> Path:
    path = (settings.fragments_dir / reference).resolve()
    root = settings.fragments_dir.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"fragment escapes fragments_dir: {reference}")
    return path


def derived_child(managed: Path, previous: Path, current: str, name: str) -> str:
    text = current.strip()
    if not text or Path(text).expanduser() == previous / name:
        return str(managed.expanduser() / name)
    return current


def _absolute_dir(path: Path, name: str) -> Path:
    path = path.expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute path without '..'")
    return path


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
