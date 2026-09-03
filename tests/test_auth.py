import json
from pathlib import Path

import pytest

from hermes_profile.auth_adapters import (
    export_auth,
    import_auth,
    list_sources,
    push_auth,
)
from hermes_profile.auth_map import auth_map_status, bind_profile_auth, load_auth_map
from hermes_profile.auth_store import identity_auth_path, load_auth_store
from hermes_profile.models import Host, Settings
from hermes_profile.profiles import create_profile
from hermes_profile.service import apply, preflight


def _settings(tmp_path: Path) -> Settings:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    settings.fragments_dir.mkdir(parents=True)
    settings.profiles_dir.mkdir(parents=True)
    return settings


def _map(settings: Settings) -> None:
    (settings.fragments_dir / "auth-map.yaml").write_text(
        "defaults:\n"
        "  xai-oauth: shared\n"
        "identities:\n"
        "  codex-gogol:\n"
        "    provider: openai-codex\n"
        "  codex-tyrion:\n"
        "    provider: openai-codex\n"
        "profiles:\n"
        "  gogol:\n"
        "    - codex-gogol\n"
        "  tyrion:\n"
        "    - codex-tyrion\n"
    )


def _profile(settings: Settings, name: str) -> None:
    create_profile(settings, name)
    (settings.fragments_dir / "config").mkdir(parents=True, exist_ok=True)
    (settings.fragments_dir / "config" / "base.yaml").write_text("model: base\n")
    (settings.profiles_dir / name / "profile.yaml").write_text(
        "config:\n  - config/base.yaml\nenv: []\n"
    )


def test_auth_map_resolves_identities_and_shared_defaults(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _map(settings)
    auth_map = load_auth_map(settings)
    assert auth_map.defaults == {"xai-oauth": "shared"}
    assert auth_map.identities["codex-gogol"].provider == "openai-codex"
    assert auth_map.profiles["gogol"] == {"openai-codex": "codex-gogol"}


def test_bind_moves_identity_store_and_leaves_a_pointer(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _map(settings)
    _profile(settings, "gogol")
    identity = identity_auth_path(settings, "codex-gogol")
    identity.parent.mkdir(parents=True)
    identity.write_text(
        json.dumps(
            {
                "providers": {
                    "openai-codex": {
                        "tokens": {
                            "access_token": "access",
                            "refresh_token": "refresh",
                        }
                    }
                },
                "credential_pool": {
                    "openai-codex": [{"id": "gogol", "auth_type": "oauth"}]
                },
            }
        )
    )

    result = bind_profile_auth(settings, "gogol")
    profile_auth = settings.profiles_dir / "gogol" / "auth.json"

    assert result["attached"][0]["action"] == "moved"
    assert profile_auth.is_file()
    assert identity.is_symlink()
    assert identity.resolve() == profile_auth.resolve()
    assert "refresh" in profile_auth.read_text()
    assert identity.read_text() == profile_auth.read_text()


def test_apply_binds_mapped_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _map(settings)
    _profile(settings, "gogol")
    identity = identity_auth_path(settings, "codex-gogol")
    identity.parent.mkdir(parents=True)
    identity.write_text('{"providers":{"openai-codex":{}},"credential_pool":{}}')

    apply(settings, "gogol")

    assert (settings.profiles_dir / "gogol" / "auth.json").is_file()
    assert identity_auth_path(settings, "codex-gogol").is_symlink()


def test_apply_fails_when_mapped_identity_is_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _map(settings)
    _profile(settings, "gogol")

    with pytest.raises(ValueError, match="identity store missing"):
        apply(settings, "gogol")


def test_preflight_and_map_status_omit_secrets(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _map(settings)
    _profile(settings, "gogol")
    identity = identity_auth_path(settings, "codex-gogol")
    identity.parent.mkdir(parents=True)
    identity.write_text(
        '{"providers":{"openai-codex":{"refresh_token":"secret-token"}},'
        '"credential_pool":{}}'
    )

    status = auth_map_status(settings)
    preview = preflight(settings, "gogol")
    dumped = json.dumps(status) + json.dumps(preview)

    assert "secret-token" not in dumped
    assert "codex-gogol" in json.dumps(status)
    assert any(
        item["target"] == "codex-gogol"
        for item in preview["bindings"]
        if isinstance(item, dict)
    )


def test_opencode_native_import_and_export_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    data_home = tmp_path / "xdg-data"
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    native = data_home / "opencode" / "auth.json"
    native.parent.mkdir(parents=True)
    native.write_text(
        json.dumps(
            {
                "openai": {
                    "type": "oauth",
                    "refresh": "oc-refresh",
                    "access": "oc-access",
                    "accountId": "acct-1",
                }
            }
        )
    )

    imported = import_auth(
        settings,
        source="opencode",
        identity="codex-gogol",
        provider="openai",
        source_profile=None,
        path=None,
        shared=False,
        allow_oauth=True,
    )
    store = load_auth_store(identity_auth_path(settings, "codex-gogol"))
    exported = tmp_path / "out-opencode.json"
    export_auth(
        settings,
        destination="opencode",
        identity="codex-gogol",
        provider="openai-codex",
        source_profile=None,
        path=exported,
        shared=False,
        allow_oauth=True,
    )

    assert imported["providers"] == ["openai-codex"]
    assert store["providers"]["openai-codex"]["tokens"]["refresh_token"] == "oc-refresh"
    written = json.loads(exported.read_text())
    assert written["openai"]["refresh"] == "oc-refresh"
    assert written["openai"]["access"] == "oc-access"
    records = list_sources("opencode")
    dumped = json.dumps(records)
    assert "oc-refresh" not in dumped
    assert records["records"][0]["hermes_provider"] == "openai-codex"


def test_opencode_named_profile_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    profile = config_home / "opencode" / "auth-profiles" / "openai" / "work.json"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": "openai",
                "auth": {
                    "type": "oauth",
                    "refresh": "work-refresh",
                    "access": "work-access",
                    "accountId": "work-acct",
                },
            }
        )
    )

    import_auth(
        settings,
        source="opencode",
        identity="codex-tyrion",
        provider="openai",
        source_profile="work",
        path=None,
        shared=False,
        allow_oauth=True,
    )
    store = load_auth_store(identity_auth_path(settings, "codex-tyrion"))
    assert store["providers"]["openai-codex"]["tokens"]["access_token"] == "work-access"


def test_codex_adapter_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    (codex_home / "auth.json").parent.mkdir(parents=True)
    (codex_home / "auth.json").write_text(
        json.dumps(
            {"tokens": {"access_token": "c-access", "refresh_token": "c-refresh"}}
        )
    )

    import_auth(
        settings,
        source="codex",
        identity="codex-gogol",
        provider=None,
        source_profile=None,
        path=None,
        shared=False,
        allow_oauth=True,
    )
    export_auth(
        settings,
        destination="codex",
        identity="codex-gogol",
        provider="openai-codex",
        source_profile=None,
        path=None,
        shared=False,
        allow_oauth=True,
    )
    store = load_auth_store(identity_auth_path(settings, "codex-gogol"))
    written = json.loads((codex_home / "auth.json").read_text())
    assert store["providers"]["openai-codex"]["tokens"]["access_token"] == "c-access"
    assert written["tokens"]["refresh_token"] == "c-refresh"


def test_oauth_import_requires_opt_in(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "hermes.json"
    source.write_text(
        json.dumps({"providers": {"openai-codex": {"refresh_token": "token"}}})
    )
    with pytest.raises(ValueError, match="--allow-oauth"):
        import_auth(
            settings,
            source="hermes",
            identity="codex-gogol",
            provider="openai-codex",
            source_profile=None,
            path=source,
            shared=False,
            allow_oauth=False,
        )


def test_push_merges_remote_identity_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    identity = identity_auth_path(settings, "codex-gogol")
    identity.parent.mkdir(parents=True)
    identity.write_text(
        json.dumps(
            {
                "providers": {"openai-codex": {"access_token": "local"}},
                "credential_pool": {"openai-codex": [{"id": "local"}]},
            }
        )
    )
    host = Host(
        alias="gateway-a",
        ssh_host="gateway.example",
        ssh_user=None,
        ssh_port=None,
        identity_file=None,
        remote_binary="hermes-profile",
        remote_config=Path("/opt/hermes/config.yaml"),
        managed_dir=Path("/opt/hermes/managed"),
        profiles_dir=Path("/opt/hermes/profiles"),
        fragments_dir=Path("/opt/hermes/fragments"),
    )
    written: dict[str, str] = {}

    def fake_read(_self: object, path: Path) -> str | None:
        if path.name == "auth.json":
            return json.dumps(
                {
                    "providers": {"anthropic": {"access_token": "keep"}},
                    "credential_pool": {"anthropic": [{"id": "keep"}]},
                }
            )
        return None

    def fake_write(_self: object, path: Path, content: str) -> None:
        written[str(path)] = content

    monkeypatch.setattr(
        "hermes_profile.transport.SshTransport.read_text_file", fake_read
    )
    monkeypatch.setattr(
        "hermes_profile.transport.SshTransport.write_private_file", fake_write
    )

    result = push_auth(
        settings,
        host,
        identity="codex-gogol",
        providers=["openai-codex"],
        shared=False,
        allow_oauth=False,
    )
    remote = json.loads(next(iter(written.values())))
    assert result["host"] == "gateway-a"
    assert sorted(remote["providers"]) == ["anthropic", "openai-codex"]
    assert remote["providers"]["openai-codex"]["access_token"] == "local"
    assert remote["providers"]["anthropic"]["access_token"] == "keep"


def test_opencode_api_key_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "opencode.json"
    source.write_text(json.dumps({"openai": {"type": "api", "key": "sk-test"}}))
    with pytest.raises(ValueError, match="OAuth only"):
        import_auth(
            settings,
            source="opencode",
            identity="codex-gogol",
            provider="openai",
            source_profile=None,
            path=source,
            shared=False,
            allow_oauth=True,
        )
