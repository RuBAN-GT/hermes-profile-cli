import asyncio
import os
from pathlib import Path

import pytest
import yaml

from hermes_profile.cli import main
from hermes_profile.env import parse_env
from hermes_profile.mcp_server import build_server
from hermes_profile.ops import Ops
from hermes_profile.paths import initialize_settings
from hermes_profile.profiles import create_profile, write_fragment
from hermes_profile.service import apply, preflight, render_profile
from hermes_profile.transport import LocalTransport
from hermes_profile.tui.app import format_preflight


@pytest.fixture
def profile(tmp_path):
    settings = initialize_settings(tmp_path / "manager.yaml", tmp_path / "managed")
    create_profile(
        settings,
        "alpha",
        config_fragments=("config/base.yaml",),
        env_fragments=("env/base.env", "env/last.env"),
    )
    write_fragment(settings, "config/base.yaml", "plain: visible\n")
    write_fragment(settings, "env/base.env", "")
    write_fragment(settings, "env/last.env", "")
    return settings


def test_ordered_env_and_typed_yaml(profile, monkeypatch):
    monkeypatch.setenv("TOKEN", "process")
    write_fragment(profile, "env/base.env", "TOKEN=${TOKEN}/first\nA=${TOKEN}\n")
    write_fragment(
        profile, "env/last.env", "TOKEN=${TOKEN}/last\nA=${TOKEN}\nTOKEN=${A}/again\n"
    )
    document = {
        "${TOKEN}": "${TOKEN}",
        "bool": True,
        "number": 42,
        "null": None,
        "list": ["${A}", False, {"nested": "prefix ${TOKEN}"}],
        "string": "${NUM}",
    }
    monkeypatch.setenv("NUM", "false: [1, 2]")
    before = dict(os.environ)
    write_fragment(
        profile, "config/base.yaml", yaml.safe_dump(document, sort_keys=False)
    )
    config, env = render_profile(profile, "alpha")
    assert env == {"TOKEN": "process/first/last/again", "A": "process/first/last"}
    assert list(config) == list(document)
    assert config["${TOKEN}"] == env["TOKEN"]
    assert config["bool"] is True and config["number"] == 42 and config["null"] is None
    assert config["list"] == [env["A"], False, {"nested": "prefix " + env["TOKEN"]}]
    assert config["string"] == "false: [1, 2]"
    assert dict(os.environ) == before


def test_defaults_isolation_overrides_and_empty(profile, monkeypatch):
    defaults = {
        "HERMES_PROFILE": "alpha",
        "HERMES_PROFILE_DIR": str(profile.profiles_dir / "alpha"),
        "HERMES_PROFILES_DIR": str(profile.profiles_dir),
        "HERMES_FRAGMENTS_DIR": str(profile.fragments_dir),
        "HERMES_MANAGED_DIR": str(profile.managed_dir),
    }
    for key in [*defaults, "HERMES_HOME"]:
        monkeypatch.delenv(key, raising=False)
    write_fragment(
        profile,
        "config/base.yaml",
        yaml.safe_dump({key: "${" + key + "}" for key in defaults}),
    )
    assert render_profile(profile, "alpha") == (defaults, {})
    assert render_profile(profile, "alpha", preview=True) == (defaults, {})
    monkeypatch.setenv("HERMES_PROFILE", "")
    monkeypatch.setenv("HERMES_PROFILE_DIR", "/explicit")
    config, env = render_profile(profile, "alpha")
    assert config["HERMES_PROFILE"] == ""
    assert config["HERMES_PROFILE_DIR"] == "/explicit"
    assert not env
    write_fragment(profile, "env/base.env", "HERMES_PROFILE=override\n")
    assert render_profile(profile, "alpha")[0]["HERMES_PROFILE"] == "override"
    write_fragment(profile, "config/base.yaml", "home: ${HERMES_HOME}\n")
    with pytest.raises(ValueError, match="^missing environment variable: HERMES_HOME$"):
        render_profile(profile, "alpha")


def test_escaping_single_pass_and_missing_safe_errors(profile, monkeypatch):
    monkeypatch.setenv("INSERT", "${MISSING} $${OTHER}")
    monkeypatch.delenv("MISSING", raising=False)
    write_fragment(profile, "env/base.env", "A=$${MISSING}\nB=${INSERT}\nC=${A}\n")
    write_fragment(profile, "config/base.yaml", "a: $${MISSING}\nb: ${B}\nc: ${C}\n")
    config, env = render_profile(profile, "alpha")
    assert config == {"a": "${MISSING}", "b": "${MISSING} $${OTHER}", "c": "${MISSING}"}
    assert env == {"A": "${MISSING}", "B": "${MISSING} $${OTHER}", "C": "${MISSING}"}
    for reference, text in [
        ("config/base.yaml", "a: secret-prefix-${MISSING}\n"),
        ("env/base.env", "A=secret-prefix-${MISSING}\n"),
    ]:
        write_fragment(profile, reference, text)
        with pytest.raises(ValueError) as error:
            render_profile(profile, "alpha", preview=True)
        assert str(error.value) == "missing environment variable: MISSING"


def test_runtime_is_literal_and_final_env_is_available_to_yaml(profile):
    directory = profile.profiles_dir / "alpha"
    write_fragment(profile, "env/base.env", "A=fragment\nB=${A}\n")
    write_fragment(profile, "config/base.yaml", "a: ${A}\nb: ${B}\nc: original\n")
    (directory / "runtime.env").write_text("A=${MISSING}\n")
    (directory / "runtime-config.yaml").write_text("c: $${MISSING}\n")
    assert render_profile(profile, "alpha") == (
        {"a": "${MISSING}", "b": "fragment", "c": "$${MISSING}"},
        {"A": "${MISSING}", "B": "fragment"},
    )
    assert render_profile(profile, "alpha", include_runtime=False) == (
        {"a": "fragment", "b": "fragment", "c": "original"},
        {"A": "fragment", "B": "fragment"},
    )
    (directory / "runtime.env").write_text("not even valid")
    (directory / "runtime-config.yaml").write_text("[broken")
    render_profile(profile, "alpha", include_runtime=False)


@pytest.mark.parametrize("character", ["\r", "\n", "\0"])
def test_invalid_expanded_env_is_safe(profile, monkeypatch, character):
    # os.environ cannot hold NUL, but a supplied render snapshot can.
    monkeypatch.setattr(os, "environ", {"SOURCE": "secret" + character + "INJECT=bad"})
    write_fragment(profile, "env/base.env", "DEST=${SOURCE}\n")
    with pytest.raises(ValueError) as error:
        render_profile(profile, "alpha")
    assert str(error.value) == "invalid environment value: DEST"


def test_same_fragment_duplicates_use_previous_assignment():
    assert parse_env("A=${A}/1\nA=${A}/2\n", "test", context={"A": "0"}) == {
        "A": "0/1/2"
    }


def test_snapshot_once(profile, monkeypatch):
    import hermes_profile.service as service

    monkeypatch.setenv("VALUE", "snapshot")
    original = service.env_documents

    def documents(*args):
        monkeypatch.setenv("VALUE", "changed-after-snapshot")
        return original(*args)

    monkeypatch.setattr(service, "env_documents", documents)
    write_fragment(profile, "env/base.env", "A=${VALUE}\n")
    write_fragment(profile, "config/base.yaml", "a: ${VALUE}\n")
    assert render_profile(profile, "alpha") == ({"a": "snapshot"}, {"A": "snapshot"})


def test_apply_and_all_preview_paths(profile, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SECRET", "process-secret-old")
    write_fragment(profile, "env/base.env", "TOKEN=env-secret-old\n")
    write_fragment(
        profile,
        "config/base.yaml",
        yaml.safe_dump(
            {
                "scalar": "${SECRET}",
                "items": ["public", {"token": "${TOKEN}"}],
                "plain": "visible",
            }
        ),
    )
    apply(profile, "alpha")
    directory = profile.profiles_dir / "alpha"
    actual = yaml.safe_load((directory / "config.yaml").read_text())
    assert actual["scalar"] == "process-secret-old"
    assert actual["items"][1]["token"] == "env-secret-old"
    assert (directory / ".env").read_text() == "TOKEN=env-secret-old\n"
    metadata = (directory / "state/interpolation.yaml").read_text()
    assert "secret-old" not in metadata
    monkeypatch.setenv("SECRET", "process-secret-new")
    write_fragment(profile, "env/base.env", "TOKEN=env-secret-new\n")
    write_fragment(
        profile,
        "config/base.yaml",
        yaml.safe_dump(
            {
                "scalar": "${SECRET}",
                "items": [{"token": "${TOKEN}"}],
                "plain": "changed",
            }
        ),
    )
    (profile.managed_dir / "config.yaml").write_text(
        "scalar: legacy-secret\nitems: [legacy-list-secret]\n"
    )
    previews = [
        render_profile(profile, "alpha", preview=True),
        Ops(profile).render("alpha"),
        LocalTransport(profile).action("alpha", "render"),
        preflight(profile, "alpha"),
        format_preflight(preflight(profile, "alpha")),
    ]
    manager = str(tmp_path / "manager.yaml")
    for action in ["render", "preflight"]:
        main(["--config", manager, "--format", "json", action, "alpha"])
        previews.append(capsys.readouterr().out)
    server = build_server(Path(manager))
    for tool in ["render_profile", "preflight_profile"]:
        previews.append(asyncio.run(server.call_tool(tool, {"name": "alpha"})))
    for preview in previews:
        assert "secret-old" not in str(preview)
        assert "secret-new" not in str(preview)
        assert "legacy-secret" not in str(preview)
        assert "legacy-list-secret" not in str(preview)
        assert "changed" in str(preview)
    # Historical provenance protects removed fields, lists, and type changes.
    write_fragment(profile, "config/base.yaml", "plain: changed\n")
    assert "secret" not in str(preflight(profile, "alpha"))
    (directory / "profile.yaml").unlink()
    assert "secret" not in str(LocalTransport(profile).action("alpha", "render"))


@pytest.mark.parametrize(
    "replacement",
    [
        {},
        {"items": []},
        {"items": "literal-replacement"},
        {"items": [{"token": "literal-replacement"}, "public"]},
    ],
)
def test_historical_list_paths_survive_replacement_and_apply(
    profile,
    monkeypatch,
    replacement,
):
    monkeypatch.setenv("SECRET", "old-secret")
    write_fragment(profile, "config/base.yaml", "items: [public, '${SECRET}']\n")
    apply(profile, "alpha")
    directory = profile.profiles_dir / "alpha"
    original_metadata = (directory / "state/interpolation.yaml").read_text()
    write_fragment(profile, "config/base.yaml", yaml.safe_dump(replacement))
    preview = preflight(profile, "alpha")
    assert "old-secret" not in str(preview)
    assert "literal-replacement" not in str(preview)
    assert preview["config_diff"]
    assert preview["materialization_diff"]
    apply(profile, "alpha")
    assert yaml.safe_load((directory / "config.yaml").read_text()) == replacement
    assert (directory / "state/interpolation.yaml").read_text() == original_metadata
    assert preflight(profile, "alpha")["config_diff"] == ""
    assert "literal-replacement" not in str(
        render_profile(profile, "alpha", preview=True)
    )


def test_runtime_override_on_interpolated_path_is_redacted(profile, monkeypatch):
    monkeypatch.setenv("SECRET", "fragment-secret")
    write_fragment(profile, "config/base.yaml", "nested:\n  token: ${SECRET}\n")
    directory = profile.profiles_dir / "alpha"
    (directory / "runtime-config.yaml").write_text("nested:\n  token: runtime-secret\n")
    assert render_profile(profile, "alpha")[0] == {
        "nested": {"token": "runtime-secret"}
    }
    assert render_profile(profile, "alpha", preview=True)[0] == {
        "nested": {"token": "<redacted>"}
    }
    assert "runtime-secret" not in str(preflight(profile, "alpha"))


@pytest.mark.parametrize(
    "template", ["token: ${SECRET}\n", "items: [public, {token: '${SECRET}'}]\n"]
)
def test_secret_only_config_diff_is_visible_without_values(
    profile, monkeypatch, template
):
    monkeypatch.setenv("SECRET", "old-process-secret")
    write_fragment(profile, "config/base.yaml", template)
    apply(profile, "alpha")
    unchanged = preflight(profile, "alpha")
    assert unchanged["config_diff"] == unchanged["materialization_diff"] == ""
    monkeypatch.setenv("SECRET", "new-process-secret")
    changed = preflight(profile, "alpha")
    assert (
        changed["env_changed"] == changed["env_added"] == changed["env_removed"] == []
    )
    for field in ["config_diff", "materialization_diff"]:
        assert "<redacted: changed>" in changed[field]
        assert "old-process-secret" not in changed[field]
        assert "new-process-secret" not in changed[field]
    assert "No effective config changes" not in format_preflight(changed)
    # Each diff must compare its own current layer, not reuse the other markers.
    (profile.managed_dir / "config.yaml").write_text(
        template.replace("${SECRET}", "new-process-secret")
    )
    layered = preflight(profile, "alpha")
    assert layered["config_diff"] == ""
    assert "<redacted: changed>" in layered["materialization_diff"]
