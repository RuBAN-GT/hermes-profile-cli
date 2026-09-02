from pathlib import Path
from typing import Any

import yaml

from hermes_profile.models import Profile, Settings
from hermes_profile.paths import fragment_path, profile_dir, write_private


def list_profiles(settings: Settings) -> list[str]:
    if not settings.profiles_dir.is_dir():
        return []
    return sorted(
        item.name
        for item in settings.profiles_dir.iterdir()
        if item.is_dir()
        and ((item / "profile.yaml").is_file() or (item / "config.yaml").is_file())
    )


def load_profile(settings: Settings, name: str) -> Profile:
    directory = profile_dir(settings, name)
    path = directory / "profile.yaml"
    if not path.is_file():
        raise ValueError(f"profile does not exist: {name}")
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return Profile(
        name=name,
        config_fragments=_references(data.get("config", []), path, "config"),
        env_fragments=_references(data.get("env", []), path, "env"),
    )


def create_profile(settings: Settings, name: str) -> Path:
    directory = profile_dir(settings, name)
    if directory.exists():
        raise ValueError(f"profile already exists: {name}")
    directory.mkdir(parents=True)
    (directory / "state").mkdir()
    write_private(directory / "profile.yaml", "config: []\nenv: []\n")
    return directory


def delete_profile(settings: Settings, name: str) -> None:
    directory = profile_dir(settings, name)
    if not (directory / "profile.yaml").is_file():
        raise ValueError(f"profile does not exist: {name}")
    import shutil

    shutil.rmtree(directory)


def config_documents(settings: Settings, profile: Profile) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for reference in profile.config_fragments:
        path = fragment_path(settings, reference)
        if not path.is_file():
            raise ValueError(f"config fragment not found: {path}")
        document = yaml.safe_load(path.read_text()) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{path}: config fragment must be a mapping")
        documents.append(document)
    return documents


def env_documents(settings: Settings, profile: Profile) -> list[tuple[str, str]]:
    documents = []
    for reference in profile.env_fragments:
        path = fragment_path(settings, reference)
        if not path.is_file():
            raise ValueError(f"environment fragment not found: {path}")
        documents.append((str(path), path.read_text()))
    return documents


def _references(value: object, path: Path, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{path}: {field} must be a list of fragment paths")
    return tuple(value)
