from pathlib import Path

import pytest

from hermes_profile.backups import create_backup, list_backups, restore_backup
from hermes_profile.models import Settings
from hermes_profile.profiles import create_profile


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "managed"
    return Settings(root, root / "profiles", root / "fragments")


def test_backup_and_restore_managed_setup_without_runtime_or_auth(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    create_profile(settings, "tyrion")
    fragment = settings.fragments_dir / "config" / "base.yaml"
    fragment.parent.mkdir(parents=True)
    fragment.write_text("model: base\n")
    profile = settings.profiles_dir / "tyrion"
    (profile / "profile.yaml").write_text("config: []\nenv: []\n")
    (profile / "runtime.env").write_text("ONE=one\n")
    (profile / "config.yaml").write_text("generated: old\n")
    (profile / "auth.json").write_text('{"credential_pool": {}}')

    created = create_backup(settings)

    fragment.write_text("model: changed\n")
    (profile / "profile.yaml").write_text("config: [changed]\nenv: []\n")
    (profile / "runtime.env").unlink()
    (profile / "config.yaml").write_text("generated: live\n")
    (profile / "auth.json").write_text('{"credential_pool": {"x": []}}')
    restored = restore_backup(settings, created["created"])

    assert list_backups(settings) == {"backups": [created["created"]]}
    assert restored == {"restored": created["created"], "files": 3}
    assert fragment.read_text() == "model: base\n"
    assert (profile / "profile.yaml").read_text() == "config: []\nenv: []\n"
    assert (profile / "runtime.env").read_text() == "ONE=one\n"
    assert (profile / "config.yaml").read_text() == "generated: live\n"
    assert (profile / "auth.json").read_text() == '{"credential_pool": {"x": []}}'


def test_restore_rejects_names_outside_the_backup_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(ValueError, match="backup name"):
        restore_backup(settings, "../outside.tar.gz")
