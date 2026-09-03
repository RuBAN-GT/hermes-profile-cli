from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    managed_dir: Path
    profiles_dir: Path
    fragments_dir: Path
    animations: bool = True
    theme: str = "hermes-dracula"
    language: str = "en"
    hosts: dict[str, "Host"] = field(default_factory=dict)
    local_locations: dict[str, "LocalLocation"] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalLocation:
    alias: str
    managed_dir: Path
    profiles_dir: Path
    fragments_dir: Path


@dataclass(frozen=True)
class Host:
    alias: str
    ssh_host: str
    ssh_user: str | None
    ssh_port: int | None
    identity_file: Path | None
    remote_binary: str
    remote_config: Path
    managed_dir: Path
    profiles_dir: Path
    fragments_dir: Path


@dataclass(frozen=True)
class Profile:
    name: str
    config_fragments: tuple[str, ...] = field(default_factory=tuple)
    env_fragments: tuple[str, ...] = field(default_factory=tuple)
    auth: str | None = None
