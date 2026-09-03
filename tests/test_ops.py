from pathlib import Path

from hermes_profile.ops import Ops
from hermes_profile.paths import initialize_settings
from hermes_profile.profiles import create_profile, write_fragment


def _ops(tmp_path: Path) -> Ops:
    settings = initialize_settings(tmp_path / "config.yaml", tmp_path / "managed")
    return Ops(settings)


def test_read_env_fragment_returns_keys_only(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    write_fragment(
        ops.settings,
        "env/profiles/alpha.private.env",
        "TOKEN=not-a-real-token\nHOME=/tmp/alpha\n",
    )
    view = ops.read_fragment("env/profiles/alpha.private.env")
    assert view["kind"] == "env"
    assert view["keys"] == ["TOKEN", "HOME"]
    assert "not-a-real-token" not in str(view)


def test_create_share_from_copies_stack_without_secret_values(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    create_profile(
        ops.settings,
        "alpha",
        config_fragments=("config/common.yaml", "config/profiles/alpha.yaml"),
        env_fragments=("env/profiles/alpha.private.env",),
    )
    identity = ops.settings.fragments_dir / "config" / "profiles" / "alpha.yaml"
    identity.parent.mkdir(parents=True)
    identity.write_text("display:\n  pet: Alpha\n")
    private = ops.settings.fragments_dir / "env" / "profiles" / "alpha.private.env"
    private.parent.mkdir(parents=True)
    private.write_text("TOKEN=not-a-real-token\n")

    ops.create("beta", share_from="alpha")
    shown = ops.show("beta")
    assert shown["config"] == ["config/common.yaml", "config/profiles/beta.yaml"]
    env = ops.read_fragment("env/profiles/beta.private.env")
    assert "TOKEN" in env["keys"]
    assert "not-a-real-token" not in str(env)


def test_apply_requires_confirm(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    create_profile(ops.settings, "alpha")
    try:
        ops.apply("alpha", confirm=False)
    except ValueError as error:
        assert "confirm" in str(error)
    else:
        raise AssertionError("expected confirm error")
