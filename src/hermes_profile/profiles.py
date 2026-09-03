from pathlib import Path
from typing import Any

import yaml

from hermes_profile.env import parse_env
from hermes_profile.models import Profile, Settings
from hermes_profile.paths import fragment_path, profile_dir, write_private

IDENTITY_CONFIG = "config/profiles/{name}.yaml"
IDENTITY_ENV = "env/profiles/{name}.private.env"


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
    auth = data.get("auth")
    if auth is not None and (not isinstance(auth, str) or not auth):
        raise ValueError(f"{path}: auth must be a map key string")
    return Profile(
        name=name,
        config_fragments=_references(data.get("config", []), path, "config"),
        env_fragments=_references(data.get("env", []), path, "env"),
        auth=auth,
    )


def identity_config_ref(name: str) -> str:
    return IDENTITY_CONFIG.format(name=name)


def identity_env_ref(name: str) -> str:
    return IDENTITY_ENV.format(name=name)


def is_identity_ref(reference: str, name: str) -> bool:
    return reference in {
        identity_config_ref(name),
        identity_env_ref(name),
        f"env/profiles/{name}.env",
    }


def save_profile(settings: Settings, profile: Profile) -> None:
    data: dict[str, object] = {
        "config": list(profile.config_fragments),
        "env": list(profile.env_fragments),
    }
    if profile.auth:
        data["auth"] = profile.auth
    write_private(
        profile_dir(settings, profile.name) / "profile.yaml",
        yaml.safe_dump(data, sort_keys=False),
    )


def create_profile(
    settings: Settings,
    name: str,
    *,
    config_fragments: tuple[str, ...] = (),
    env_fragments: tuple[str, ...] = (),
) -> Path:
    directory = profile_dir(settings, name)
    if directory.exists():
        raise ValueError(f"profile already exists: {name}")
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    state = directory / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    save_profile(
        settings,
        Profile(
            name=name,
            config_fragments=config_fragments,
            env_fragments=env_fragments,
        ),
    )
    return directory


def share_profile_stack(
    settings: Settings,
    source_name: str,
    name: str,
    *,
    extra_config: tuple[str, ...] = (),
    extra_env: tuple[str, ...] = (),
) -> Path:
    source = load_profile(settings, source_name)
    config = (
        tuple(
            reference
            for reference in source.config_fragments
            if not is_identity_ref(reference, source_name)
        )
        + extra_config
    )
    environment = (
        tuple(
            reference
            for reference in source.env_fragments
            if not is_identity_ref(reference, source_name)
        )
        + extra_env
    )
    if identity_config_ref(name) not in config:
        config += (identity_config_ref(name),)
    if identity_env_ref(name) not in environment:
        environment += (identity_env_ref(name),)
    directory = create_profile(
        settings,
        name,
        config_fragments=config,
        env_fragments=environment,
    )
    _write_shared_identity(settings, source_name, name)
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


def list_fragments(settings: Settings) -> list[str]:
    root = settings.fragments_dir
    if not root.is_dir():
        return []
    resolved = root.resolve()
    return sorted(
        str(path.relative_to(resolved))
        for path in resolved.rglob("*")
        if path.is_file()
    )


def read_fragment_view(settings: Settings, reference: str) -> dict[str, Any]:
    path = fragment_path(settings, reference)
    if not path.is_file():
        raise ValueError(f"fragment not found: {reference}")
    if _is_env_reference(reference):
        keys = list(parse_env(path.read_text(), str(path)))
        return {"reference": reference, "kind": "env", "keys": keys}
    document = yaml.safe_load(path.read_text()) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{path}: config fragment must be a mapping")
    return {"reference": reference, "kind": "config", "content": document}


def write_fragment(settings: Settings, reference: str, content: str) -> dict[str, Any]:
    path = fragment_path(settings, reference)
    if _is_env_reference(reference):
        keys = list(parse_env(content, reference))
        write_private(path, content if content.endswith("\n") else f"{content}\n")
        return {"reference": reference, "kind": "env", "keys": keys}
    document = yaml.safe_load(content) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{reference}: config fragment must be a mapping")
    write_private(path, content if content.endswith("\n") else f"{content}\n")
    return {"reference": reference, "kind": "config"}


def _is_env_reference(reference: str) -> bool:
    return reference.endswith(".env") or reference.startswith("env/")


def _write_shared_identity(settings: Settings, source_name: str, name: str) -> None:
    source = fragment_path(settings, identity_config_ref(source_name))
    target = fragment_path(settings, identity_config_ref(name))
    home = str(settings.profiles_dir / name)
    if source.is_file():
        text = source.read_text().replace(
            f"/profiles/{source_name}/", f"/profiles/{name}/"
        )
        document = yaml.safe_load(text) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{source}: config fragment must be a mapping")
        display = document.setdefault("display", {})
        if isinstance(display, dict):
            display["pet"] = name
        write_private(
            target, yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
        )
    else:
        write_private(
            target,
            yaml.safe_dump(
                {
                    "display": {"pet": name},
                    "plugins": {
                        "hermes-memory-store": {"db_path": f"{home}/memory_store.db"}
                    },
                },
                allow_unicode=True,
                sort_keys=False,
            ),
        )
    source_env = fragment_path(settings, identity_env_ref(source_name))
    keys = (
        list(parse_env(source_env.read_text(), str(source_env)))
        if source_env.is_file()
        else ["HERMES_HOME"]
    )
    if "HERMES_HOME" not in keys:
        keys = ["HERMES_HOME", *keys]
    lines = [
        f"HERMES_HOME={home}" if key == "HERMES_HOME" else f"{key}=" for key in keys
    ]
    write_private(
        fragment_path(settings, identity_env_ref(name)),
        "\n".join(lines) + "\n",
    )
