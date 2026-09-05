from pathlib import Path

import pytest
import yaml

from hermes_profile.models import Settings
from hermes_profile.profiles import create_profile, list_profiles
from hermes_profile.service import (
    apply,
    preflight,
    reconcile,
    render_profile,
    shared_auth_status,
    status,
    sync_shared_auth,
)


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


def test_apply_discard_runtime_removes_runtime_overlays(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    apply(settings, "tyrion")
    directory = settings.profiles_dir / "tyrion"
    (directory / "runtime-config.yaml").write_text("model:\n  name: runtime\n")
    (directory / "runtime.env").write_text("ONE=runtime\nTWO=two\n")
    (directory / "config.yaml").write_text("model:\n  name: changed\n")

    apply(settings, "tyrion", discard_runtime=True)

    assert render_profile(settings, "tyrion") == (
        {"model": {"name": "base"}},
        {"ONE": "one"},
    )
    assert not (directory / "runtime-config.yaml").exists()
    assert not (directory / "runtime.env").exists()


def test_preflight_shows_config_diff_and_redacted_environment_names(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    apply(settings, "tyrion")
    (settings.fragments_dir / "config" / "base.yaml").write_text(
        "model:\n  name: changed\n"
    )
    (settings.fragments_dir / "env" / "base.env").write_text("SECRET=changed\n")

    result = preflight(settings, "tyrion")

    assert "+  name: changed" in result["config_diff"]
    assert "+  name: changed" in result["materialization_diff"]
    assert result["env_added"] == ["SECRET"]
    assert result["env_changed"] == []
    assert result["env_removed"] == ["ONE"]


def test_preflight_separates_effective_and_materialization_diffs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    (settings.fragments_dir / "config" / "cron.yaml").write_text(
        "cron:\n  wrap_response: false\n"
    )
    (settings.profiles_dir / "tyrion" / "profile.yaml").write_text(
        "config:\n  - config/base.yaml\n  - config/cron.yaml\nenv:\n  - env/base.env\n"
    )
    apply(settings, "tyrion")
    (settings.profiles_dir / "tyrion" / "config.yaml").write_text(
        "model:\n  name: base\n"
    )
    (settings.managed_dir / "config.yaml").write_text("cron:\n  wrap_response: false\n")

    result = preflight(settings, "tyrion")

    assert result["config_diff"] == ""
    assert result["legacy_managed_layer"] is True
    assert "cron" in result["materialization_diff"]


def test_apply_sorts_top_level_yaml_keys(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    (settings.fragments_dir / "config" / "base.yaml").write_text("zebra: 1\nalpha: 2\n")
    apply(settings, "tyrion")
    text = (settings.profiles_dir / "tyrion" / "config.yaml").read_text()
    assert text.index("alpha:") < text.index("zebra:")


def test_apply_preserves_cyrillic_values_in_materialized_yaml(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    (settings.fragments_dir / "config" / "base.yaml").write_text(
        "display:\n  pet: Вилли\n"
    )

    apply(settings, "tyrion")

    text = (settings.profiles_dir / "tyrion" / "config.yaml").read_text()
    assert "pet: Вилли" in text
    assert "\\u" not in text


def test_reconcile_reports_removed_auth_inventory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _profile(settings)
    auth = settings.profiles_dir / "tyrion" / "auth.json"
    auth.write_text('{"credential_pool": {}}')
    reconcile(settings, "tyrion")
    auth.unlink()

    assert status(settings, "tyrion")["auth_inventory_changed"] is True
    reconcile(settings, "tyrion")
    assert status(settings, "tyrion")["auth_inventory_changed"] is False


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


def test_shared_auth_status_uses_the_hermes_root_fallback(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth = settings.profiles_dir.parent / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(
        '{"credential_pool":{"openai-codex":[{"id":"one",'
        '"auth_type":"oauth","source":"manual","access_token":"redacted"}]}}'
    )

    assert shared_auth_status(settings) == {
        "path": str(auth),
        "present": True,
        "providers": ["openai-codex"],
    }


def test_sync_shared_auth_copies_only_selected_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = settings.profiles_dir / "tyrion"
    source.mkdir(parents=True)
    (source / "auth.json").write_text(
        '{"providers":{"xai-oauth":{"access_token":"xai"},'
        '"openai-codex":{"access_token":"codex"}},'
        '"credential_pool":{"xai-oauth":[{"id":"xai"}],'
        '"openai-codex":[{"id":"codex"}]}}'
    )
    target = settings.profiles_dir.parent / "auth.json"
    target.write_text(
        '{"providers":{"anthropic":{"access_token":"anthropic"}},'
        '"credential_pool":{"anthropic":[{"id":"anthropic"}]}}'
    )

    source_before = (source / "auth.json").read_bytes()
    result = sync_shared_auth(settings, "tyrion", ["xai-oauth"], allow_oauth=False)

    assert result == {
        "synced_from": "tyrion",
        "providers": ["xai-oauth"],
        "path": str(target),
    }
    shared = yaml.safe_load(target.read_text())
    assert sorted(shared["providers"]) == ["anthropic", "xai-oauth"]
    assert sorted(shared["credential_pool"]) == ["anthropic", "xai-oauth"]
    assert shared["providers"]["xai-oauth"]["access_token"] == "xai"
    assert (source / "auth.json").read_bytes() == source_before


def test_sync_shared_auth_requires_opt_in_for_oauth(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = settings.profiles_dir / "tyrion"
    source.mkdir(parents=True)
    (source / "auth.json").write_text(
        '{"providers":{"openai-codex":{"refresh_token":"token"}}}'
    )

    with pytest.raises(ValueError, match="--allow-oauth"):
        sync_shared_auth(settings, "tyrion", ["openai-codex"], allow_oauth=False)


def test_sync_shared_auth_replaces_the_provider_slice(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = settings.profiles_dir / "tyrion"
    source.mkdir(parents=True)
    (source / "auth.json").write_text(
        '{"credential_pool":{"openai-codex":[{"id":"source"}]}}'
    )
    target = settings.profiles_dir.parent / "auth.json"
    target.write_text(
        '{"providers":{"openai-codex":{"access_token":"stale"}},'
        '"credential_pool":{"openai-codex":[{"id":"stale"}]}}'
    )

    sync_shared_auth(settings, "tyrion", ["openai-codex"], allow_oauth=False)

    shared = yaml.safe_load(target.read_text())
    assert "openai-codex" not in shared["providers"]
    assert shared["credential_pool"]["openai-codex"] == [{"id": "source"}]
