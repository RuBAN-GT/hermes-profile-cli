from pathlib import Path

import yaml

from hermes_profile.models import Settings
from hermes_profile.profiles import create_profile, list_profiles
from hermes_profile.service import apply, reconcile, render_profile, status


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "managed"
    return Settings(root, root / "profiles", root / "fragments")


def _profile(settings: Settings) -> None:
    create_profile(settings, "tyrion")
    (settings.fragments_dir / "config").mkdir(parents=True)
    (settings.fragments_dir / "env").mkdir(parents=True)
    (settings.fragments_dir / "config" / "base.yaml").write_text(
        "model:\n  name: base\n"
    )
    (settings.fragments_dir / "env" / "base.env").write_text("ONE=one\n")
    (settings.profiles_dir / "tyrion" / "profile.yaml").write_text(
        "config:\n  - config/base.yaml\nenv:\n  - env/base.env\n"
    )


def test_list_includes_hermes_runtime_profiles_without_profile_yaml(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    runtime = settings.profiles_dir / "gogol"
    runtime.mkdir(parents=True)
    (runtime / "config.yaml").write_text("model: {}\n")

    assert list_profiles(settings) == ["gogol"]
    assert status(settings, "gogol")["config_drift"] is True


def test_apply_materializes_fragments_and_captures_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)

    apply(settings, "tyrion")

    config, environment = render_profile(settings, "tyrion")
    assert config == {"model": {"name": "base"}}
    assert environment == {"ONE": "one"}
    assert status(settings, "tyrion") == {
        "config_drift": False,
        "env_drift": False,
        "auth_inventory_changed": False,
    }


def test_reconcile_preserves_runtime_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    apply(settings, "tyrion")
    (settings.profiles_dir / "tyrion" / "config.yaml").write_text(
        yaml.safe_dump({"model": {"name": "runtime"}, "plugins": {"x": True}})
    )
    (settings.profiles_dir / "tyrion" / ".env").write_text("ONE=runtime\nTWO=two\n")

    reconcile(settings, "tyrion")
    apply(settings, "tyrion")

    config, environment = render_profile(settings, "tyrion")
    assert config == {"model": {"name": "runtime"}, "plugins": {"x": True}}
    assert environment == {"ONE": "runtime", "TWO": "two"}


def test_reconcile_acknowledges_auth_inventory_without_copying_auth(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    auth = settings.profiles_dir / "tyrion" / "auth.json"
    auth.write_text(
        '{"credential_pool":{"x-ai":[{"id":"one","auth_type":"oauth",'
        '"source":"manual","access_token":"not-a-real-token"}]}}'
    )

    assert status(settings, "tyrion")["auth_inventory_changed"] is True
    reconcile(settings, "tyrion")

    assert status(settings, "tyrion")["auth_inventory_changed"] is False
    assert "access_token" in auth.read_text()
