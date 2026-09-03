from pathlib import Path

from hermes_profile.cli import main
from hermes_profile.paths import initialize_settings
from hermes_profile.profiles import create_profile, load_profile, share_profile_stack


def _settings(tmp_path: Path):
    return initialize_settings(tmp_path / "config.yaml", tmp_path / "managed")


def test_create_profile_can_seed_fragment_refs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    create_profile(
        settings,
        "tyrion",
        config_fragments=("config/common.yaml",),
        env_fragments=("env/common.env",),
    )
    profile = load_profile(settings, "tyrion")
    assert profile.config_fragments == ("config/common.yaml",)
    assert profile.env_fragments == ("env/common.env",)


def test_share_profile_stack_retargets_identity_and_blanks_secrets(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    create_profile(
        settings,
        "gogol",
        config_fragments=(
            "config/common.yaml",
            "config/profiles/gogol.yaml",
        ),
        env_fragments=("env/terminal.env", "env/profiles/gogol.private.env"),
    )
    identity = settings.fragments_dir / "config" / "profiles" / "gogol.yaml"
    identity.parent.mkdir(parents=True)
    identity.write_text(
        "display:\n  pet: Alpha\n"
        "plugins:\n  hermes-memory-store:\n"
        f"    db_path: {settings.profiles_dir}/gogol/memory_store.db\n"
        "terminal:\n  docker_volumes:\n"
        f"  - {settings.profiles_dir}/gogol/workspace:/workspace:rw\n"
    )
    private = settings.fragments_dir / "env" / "profiles" / "gogol.private.env"
    private.parent.mkdir(parents=True)
    private.write_text(
        f"HERMES_HOME={settings.profiles_dir}/gogol\nTELEGRAM_BOT_TOKEN=secret\n"
    )
    terminal = settings.fragments_dir / "env" / "terminal.env"
    terminal.write_text("TERMINAL_ENV=docker\n")

    share_profile_stack(settings, "gogol", "ned")
    ned = load_profile(settings, "ned")
    assert ned.config_fragments == (
        "config/common.yaml",
        "config/profiles/ned.yaml",
    )
    assert ned.env_fragments == (
        "env/terminal.env",
        "env/profiles/ned.private.env",
    )
    written = (settings.fragments_dir / "config" / "profiles" / "ned.yaml").read_text()
    assert "ned" in written
    assert "gogol" not in written
    assert "Alpha" not in written
    env_path = settings.fragments_dir / "env" / "profiles" / "ned.private.env"
    env_text = env_path.read_text()
    assert f"HERMES_HOME={settings.profiles_dir}/ned" in env_text
    assert "TELEGRAM_BOT_TOKEN=" in env_text
    assert "secret" not in env_text


def test_update_set_config_replaces_fragment_list(
    tmp_path: Path, capsys: object
) -> None:
    settings = _settings(tmp_path)
    create_profile(
        settings,
        "tyrion",
        config_fragments=("config/old.yaml",),
        env_fragments=("env/old.env",),
    )
    main(
        [
            "--config",
            str(tmp_path / "config.yaml"),
            "update",
            "tyrion",
            "--set-config",
            "config/common.yaml",
            "--set-config",
            "config/profiles/tyrion.yaml",
            "--add-env",
            "env/common.env",
        ]
    )
    profile = load_profile(settings, "tyrion")
    assert profile.config_fragments == (
        "config/common.yaml",
        "config/profiles/tyrion.yaml",
    )
    assert profile.env_fragments == ("env/old.env", "env/common.env")
