from pathlib import Path

import pytest

from hermes_profile.models import Host, LocalLocation
from hermes_profile.paths import (
    delete_location,
    derived_child,
    initialize_settings,
    load_settings,
    upsert_host,
    upsert_local_location,
    write_private,
)
from hermes_profile.profiles import create_profile


def test_initialize_creates_config_and_operational_layout(tmp_path: Path) -> None:
    config = tmp_path / "config" / "config.yaml"
    managed = tmp_path / "managed"

    settings = initialize_settings(config, managed)

    assert load_settings(str(config)) == settings
    assert settings.profiles_dir.is_dir()
    assert settings.fragments_dir.is_dir()
    assert config.stat().st_mode & 0o777 == 0o600
    assert managed.stat().st_mode & 0o777 == 0o700


def test_create_profile_uses_private_directories(tmp_path: Path) -> None:
    settings = initialize_settings(tmp_path / "config.yaml", tmp_path / "managed")

    directory = create_profile(settings, "tyrion")

    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "state").stat().st_mode & 0o777 == 0o700


def test_write_private_ignores_predictable_temporary_symlink(tmp_path: Path) -> None:
    target = tmp_path / "secret.env"
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("unchanged")
    (tmp_path / "secret.env.tmp").symlink_to(sentinel)

    write_private(target, "TOKEN=new\n")

    assert sentinel.read_text() == "unchanged"
    assert target.read_text() == "TOKEN=new\n"


def test_load_settings_rejects_relative_local_paths(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("managed_dir: relative\n")

    with pytest.raises(ValueError, match="managed_dir must be an absolute path"):
        load_settings(str(config))


def test_load_settings_requires_boolean_animations(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"managed_dir: {tmp_path / 'managed'}\nui:\n  animations: 'false'\n"
    )

    with pytest.raises(ValueError, match="ui.animations must be a boolean"):
        load_settings(str(config))


def test_initialize_accepts_custom_profiles_and_fragments(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    managed = tmp_path / "managed"
    profiles = tmp_path / "homes"
    fragments = tmp_path / "shared" / "fragments"

    settings = initialize_settings(
        config,
        managed,
        profiles_dir=profiles,
        fragments_dir=fragments,
    )

    loaded = load_settings(str(config))
    assert settings.profiles_dir == profiles
    assert loaded.profiles_dir == profiles
    assert loaded.fragments_dir == fragments
    assert profiles.is_dir()
    assert fragments.is_dir()


def test_derived_child_follows_managed_until_edited(tmp_path: Path) -> None:
    previous = tmp_path / "old"
    managed = tmp_path / "new"
    followed = derived_child(managed, previous, str(previous / "profiles"), "profiles")
    custom = derived_child(managed, previous, str(tmp_path / "custom"), "profiles")
    assert followed == str(managed / "profiles")
    assert custom == str(tmp_path / "custom")


def test_initialize_refuses_to_overwrite_existing_config(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    initialize_settings(config, tmp_path / "managed")

    with pytest.raises(ValueError, match="already exists"):
        initialize_settings(config, tmp_path / "other")


def test_initialize_serializes_remote_host(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    host = Host(
        alias="gateway-a",
        ssh_host="gateway-a.example.internal",
        ssh_user="deploy",
        ssh_port=None,
        identity_file=None,
        remote_binary="/opt/hermes/bin/hermes-profile",
        remote_config=Path("/opt/hermes/etc/config.yaml"),
        managed_dir=Path("/opt/hermes/managed"),
        profiles_dir=Path("/opt/hermes/managed/profiles"),
        fragments_dir=Path("/opt/hermes/managed/fragments"),
    )

    initialize_settings(config, tmp_path / "managed", {host.alias: host})

    loaded = load_settings(str(config))
    assert loaded.hosts == {"gateway-a": host}


def test_upsert_host_preserves_existing_manager_settings(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    initialize_settings(config, tmp_path / "managed")
    host = Host(
        alias="gateway-a",
        ssh_host="gateway-a.example.internal",
        ssh_user=None,
        ssh_port=None,
        identity_file=None,
        remote_binary="/opt/hermes/bin/hermes-profile",
        remote_config=Path("/opt/hermes/etc/config.yaml"),
        managed_dir=Path("/opt/hermes/managed"),
        profiles_dir=Path("/opt/hermes/managed/profiles"),
        fragments_dir=Path("/opt/hermes/managed/fragments"),
    )

    upsert_host(config, host)

    loaded = load_settings(str(config))
    assert loaded.managed_dir == tmp_path / "managed"
    assert loaded.hosts == {"gateway-a": host}


def test_upsert_local_location_preserves_primary_location(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    initialize_settings(config, tmp_path / "managed")
    location = LocalLocation(
        alias="lab",
        managed_dir=tmp_path / "lab",
        profiles_dir=tmp_path / "lab" / "profiles",
        fragments_dir=tmp_path / "lab" / "fragments",
    )

    upsert_local_location(config, location)

    loaded = load_settings(str(config))
    assert loaded.managed_dir == tmp_path / "managed"
    assert loaded.local_locations == {"lab": location}


def test_delete_location_removes_only_manager_record(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    initialize_settings(config, tmp_path / "managed")
    location = LocalLocation(
        alias="lab",
        managed_dir=tmp_path / "lab",
        profiles_dir=tmp_path / "lab" / "profiles",
        fragments_dir=tmp_path / "lab" / "fragments",
    )
    upsert_local_location(config, location)
    location.managed_dir.mkdir()

    delete_location(config, "local", "lab")

    assert load_settings(str(config)).local_locations == {}
    assert location.managed_dir.is_dir()
