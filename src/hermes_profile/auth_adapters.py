from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hermes_profile.auth_store import (
    copy_auth_providers,
    empty_auth_store,
    identities_dir,
    identity_auth_path,
    load_auth_store,
    providers_contain_refresh_tokens,
    save_auth_store,
    shared_auth_path,
    store_providers,
)
from hermes_profile.models import Host, Settings
from hermes_profile.paths import PROFILE_NAME

HERMES_PROVIDERS = {
    "openai": "openai-codex",
    "chatgpt": "openai-codex",
    "codex": "openai-codex",
    "openai-codex": "openai-codex",
    "xai": "xai-oauth",
    "x-ai": "xai-oauth",
    "xai-oauth": "xai-oauth",
}

OPENCODE_PROVIDERS = {
    "openai-codex": "openai",
    "xai-oauth": "xai",
}


@dataclass(frozen=True)
class AuthSource:
    adapter: str
    provider: str
    hermes_provider: str
    kind: str
    profile: str | None
    path: str
    label: str


class AuthAdapter(Protocol):
    name: str

    def sources(self, path: Path | None = None) -> list[AuthSource]: ...

    def read(
        self,
        path: Path | None,
        provider: str,
        *,
        profile: str | None = None,
    ) -> dict[str, Any]: ...

    def write(
        self,
        path: Path | None,
        store: dict[str, Any],
        provider: str,
        *,
        profile: str | None = None,
    ) -> Path: ...


def normalize_provider(value: str) -> str:
    try:
        return HERMES_PROVIDERS[value]
    except KeyError as error:
        raise ValueError(f"unsupported auth provider: {value}") from error


def adapter(name: str) -> AuthAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as error:
        raise ValueError(
            f"unsupported auth adapter: {name}; use {', '.join(sorted(ADAPTERS))}"
        ) from error


def list_sources(name: str, path: Path | None = None) -> dict[str, object]:
    records = adapter(name).sources(path)
    return {
        "adapter": name,
        "records": [
            {
                "provider": record.provider,
                "hermes_provider": record.hermes_provider,
                "type": record.kind,
                "profile": record.profile,
                "path": record.path,
                "label": record.label,
            }
            for record in records
        ],
    }


def import_auth(
    settings: Settings,
    *,
    source: str,
    identity: str | None,
    provider: str | None,
    source_profile: str | None,
    path: Path | None,
    shared: bool,
    allow_oauth: bool,
) -> dict[str, object]:
    selected_provider = provider or _default_provider(source)
    store = adapter(source).read(path, selected_provider, profile=source_profile)
    providers = store_providers(store)
    if provider:
        providers = [normalize_provider(provider)]
        missing = [item for item in providers if item not in store_providers(store)]
        if missing:
            raise ValueError(f"source has no provider: {', '.join(missing)}")
    if not allow_oauth and providers_contain_refresh_tokens(store, providers):
        raise ValueError(
            "selected providers contain OAuth refresh tokens; pass --allow-oauth"
        )
    target = _target_path(settings, identity=identity, shared=shared)
    if not shared:
        identities_dir(settings).mkdir(parents=True, exist_ok=True)
        identities_dir(settings).chmod(0o700)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o700)
    with_target = load_auth_store(target, missing_ok=True)
    copied = copy_auth_providers(store, with_target, providers)
    save_auth_store(target, with_target)
    return {
        "imported_from": source,
        "providers": copied,
        "path": str(target),
        "identity": identity,
        "shared": shared,
    }


def export_auth(
    settings: Settings,
    *,
    destination: str,
    identity: str | None,
    provider: str | None,
    source_profile: str | None,
    path: Path | None,
    shared: bool,
    allow_oauth: bool,
) -> dict[str, object]:
    source_path = _target_path(settings, identity=identity, shared=shared)
    store = load_auth_store(source_path)
    providers = [normalize_provider(provider)] if provider else store_providers(store)
    if not providers:
        raise ValueError("auth export requires a provider")
    if not allow_oauth and providers_contain_refresh_tokens(store, providers):
        raise ValueError(
            "selected providers contain OAuth refresh tokens; pass --allow-oauth"
        )
    slice_store = empty_auth_store()
    copy_auth_providers(store, slice_store, providers)
    written = adapter(destination).write(
        path, slice_store, providers[0], profile=source_profile
    )
    return {
        "exported_to": destination,
        "providers": providers,
        "path": str(written),
        "identity": identity,
        "shared": shared,
    }


def push_auth(
    settings: Settings,
    host: Host,
    *,
    identity: str | None,
    providers: list[str],
    shared: bool,
    allow_oauth: bool,
) -> dict[str, object]:
    from hermes_profile.transport import SshTransport

    source = _target_path(settings, identity=identity, shared=shared)
    store = load_auth_store(source)
    selected = (
        [normalize_provider(item) for item in providers]
        if providers
        else store_providers(store)
    )
    if not selected:
        raise ValueError("auth push requires a provider or identity store")
    if not allow_oauth and providers_contain_refresh_tokens(store, selected):
        raise ValueError(
            "selected providers contain OAuth refresh tokens; pass --allow-oauth"
        )
    payload = empty_auth_store()
    copy_auth_providers(store, payload, selected)
    remote_root = host.profiles_dir.parent
    if host.profiles_dir.name != "profiles":
        raise ValueError(
            "auth push requires the remote profiles_dir to be named 'profiles'"
        )
    if shared:
        remote = remote_root / "auth.json"
    else:
        if not identity:
            raise ValueError("auth push requires --identity unless --shared")
        remote = remote_root / "identities" / identity / "auth.json"
    transport = SshTransport(host)
    existing_text = transport.read_text_file(remote)
    if existing_text:
        try:
            existing = json.loads(existing_text)
        except json.JSONDecodeError as error:
            raise ValueError(f"{host.alias}: invalid remote auth store") from error
        if not isinstance(existing, dict):
            raise ValueError(f"{host.alias}: remote auth store must be an object")
        existing.setdefault("providers", {})
        existing.setdefault("credential_pool", {})
        if not isinstance(existing["providers"], dict) or not isinstance(
            existing["credential_pool"], dict
        ):
            raise ValueError(f"{host.alias}: remote auth store is invalid")
        copy_auth_providers(payload, existing, selected)
        payload = existing
    transport.write_private_file(
        remote, json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return {
        "host": host.alias,
        "identity": identity,
        "shared": shared,
        "providers": selected,
        "path": str(remote),
    }


def _target_path(settings: Settings, *, identity: str | None, shared: bool) -> Path:
    if shared:
        return shared_auth_path(settings)
    if not identity:
        raise ValueError("auth import/export requires --identity unless --shared")
    return identity_auth_path(settings, identity)


def _default_provider(source: str) -> str:
    if source == "codex":
        return "openai-codex"
    raise ValueError("auth import requires --provider")


class HermesAdapter:
    name = "hermes"

    def sources(self, path: Path | None = None) -> list[AuthSource]:
        target = path
        if target is None or not target.is_file():
            return []
        store = load_auth_store(target, missing_ok=True)
        return [
            AuthSource(
                adapter=self.name,
                provider=provider,
                hermes_provider=provider,
                kind="oauth" if _has_oauth(store, provider) else "token",
                profile=None,
                path=str(target),
                label="",
            )
            for provider in store_providers(store)
        ]

    def read(
        self, path: Path | None, provider: str, *, profile: str | None = None
    ) -> dict[str, Any]:
        if path is None:
            raise ValueError("hermes adapter requires --path")
        store = load_auth_store(path)
        selected = normalize_provider(provider)
        result = empty_auth_store()
        copy_auth_providers(store, result, [selected])
        return result

    def write(
        self,
        path: Path | None,
        store: dict[str, Any],
        provider: str,
        *,
        profile: str | None = None,
    ) -> Path:
        if path is None:
            raise ValueError("hermes adapter requires --path")
        current = load_auth_store(path, missing_ok=True)
        copy_auth_providers(store, current, [normalize_provider(provider)])
        save_auth_store(path, current)
        return path


class CodexAdapter:
    name = "codex"

    def sources(self, path: Path | None = None) -> list[AuthSource]:
        target = path or _codex_auth_path()
        if not target.is_file():
            return []
        tokens = _codex_tokens(target)
        if tokens is None:
            return []
        return [
            AuthSource(
                adapter=self.name,
                provider="openai-codex",
                hermes_provider="openai-codex",
                kind="oauth",
                profile=None,
                path=str(target),
                label="",
            )
        ]

    def read(
        self, path: Path | None, provider: str, *, profile: str | None = None
    ) -> dict[str, Any]:
        selected = normalize_provider(provider)
        if selected != "openai-codex":
            raise ValueError("codex adapter only supports openai-codex")
        target = path or _codex_auth_path()
        tokens = _codex_tokens(target)
        if tokens is None:
            raise ValueError(f"codex auth store not found or incomplete: {target}")
        return _hermes_oauth_store(
            "openai-codex",
            access=tokens["access_token"],
            refresh=tokens["refresh_token"],
            label="codex",
            source="manual:codex",
            extra_provider={"auth_mode": "chatgpt"},
        )

    def write(
        self,
        path: Path | None,
        store: dict[str, Any],
        provider: str,
        *,
        profile: str | None = None,
    ) -> Path:
        if normalize_provider(provider) != "openai-codex":
            raise ValueError("codex adapter only supports openai-codex")
        target = path or _codex_auth_path()
        access, refresh, _ = _tokens_from_hermes(store, "openai-codex")
        payload = _read_json_object(target) if target.is_file() else {}
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        tokens["access_token"] = access
        tokens["refresh_token"] = refresh
        payload["tokens"] = tokens
        _write_json(target, payload)
        return target


class OpenCodeAdapter:
    name = "opencode"

    def sources(self, path: Path | None = None) -> list[AuthSource]:
        records: list[AuthSource] = []
        native = path or _opencode_auth_path()
        if native.is_file():
            data = _read_json_object(native)
            for provider, entry in data.items():
                if not isinstance(provider, str) or not isinstance(entry, dict):
                    continue
                if provider not in HERMES_PROVIDERS:
                    continue
                records.append(
                    AuthSource(
                        adapter=self.name,
                        provider=provider,
                        hermes_provider=HERMES_PROVIDERS[provider],
                        kind=str(entry.get("type") or "unknown"),
                        profile=None,
                        path=str(native),
                        label=_opencode_label(entry),
                    )
                )
        profiles_root = _opencode_profiles_dir()
        if profiles_root.is_dir():
            for provider_dir in sorted(profiles_root.iterdir()):
                if (
                    not provider_dir.is_dir()
                    or provider_dir.name not in HERMES_PROVIDERS
                ):
                    continue
                for file in sorted(provider_dir.glob("*.json")):
                    if file.name == "active.json":
                        continue
                    entry = _opencode_profile_auth(_read_json_object(file))
                    if entry is None:
                        continue
                    records.append(
                        AuthSource(
                            adapter=self.name,
                            provider=provider_dir.name,
                            hermes_provider=HERMES_PROVIDERS[provider_dir.name],
                            kind=str(entry.get("type") or "unknown"),
                            profile=file.stem,
                            path=str(file),
                            label=_opencode_label(entry),
                        )
                    )
        return records

    def read(
        self, path: Path | None, provider: str, *, profile: str | None = None
    ) -> dict[str, Any]:
        selected = normalize_provider(provider)
        opencode_provider = OPENCODE_PROVIDERS[selected]
        entry = _read_opencode_entry(path, opencode_provider, profile)
        if entry.get("type") != "oauth":
            raise ValueError(
                "opencode adapter imports OAuth only; API keys belong in env fragments"
            )
        access = entry.get("access")
        refresh = entry.get("refresh")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise ValueError("opencode OAuth entry requires access and refresh")
        extra: dict[str, Any] = {}
        if selected == "openai-codex":
            extra["auth_mode"] = "chatgpt"
        elif selected == "xai-oauth":
            extra["auth_mode"] = "oauth_device_code"
        return _hermes_oauth_store(
            selected,
            access=access,
            refresh=refresh,
            label=_opencode_label(entry) or "opencode",
            source="manual:opencode",
            extra_provider=extra,
        )

    def write(
        self,
        path: Path | None,
        store: dict[str, Any],
        provider: str,
        *,
        profile: str | None = None,
    ) -> Path:
        selected = normalize_provider(provider)
        opencode_provider = OPENCODE_PROVIDERS[selected]
        access, refresh, label = _tokens_from_hermes(store, selected)
        auth = {
            "type": "oauth",
            "refresh": refresh,
            "access": access,
            "expires": 0,
        }
        if label:
            auth["accountId"] = label
        if profile:
            target = path or (
                _opencode_profiles_dir() / opencode_provider / f"{profile}.json"
            )
            if not PROFILE_NAME.fullmatch(profile):
                raise ValueError(
                    "opencode profile names use lowercase letters, digits, and hyphens"
                )
            _write_json(
                target,
                {"version": 1, "provider": opencode_provider, "auth": auth},
            )
            return target
        target = path or _opencode_auth_path()
        payload = _read_json_object(target) if target.is_file() else {}
        payload[opencode_provider] = auth
        _write_json(target, payload)
        return target


def _read_opencode_entry(
    path: Path | None, provider: str, profile: str | None
) -> dict[str, Any]:
    if profile:
        target = path or (_opencode_profiles_dir() / provider / f"{profile}.json")
        data = _read_json_object(target)
        entry = _opencode_profile_auth(data)
        if entry is None:
            raise ValueError(f"opencode profile auth not found: {target}")
        return entry
    target = path or _opencode_auth_path()
    data = _read_json_object(target)
    entry = data.get(provider)
    if not isinstance(entry, dict):
        raise ValueError(f"opencode store has no provider {provider}: {target}")
    return entry


def _opencode_profile_auth(data: dict[str, Any]) -> dict[str, Any] | None:
    auth = data.get("auth")
    if isinstance(auth, dict):
        return auth
    if data.get("type") in {"oauth", "api", "wellknown"}:
        return data
    return None


def _opencode_label(entry: dict[str, Any]) -> str:
    label = entry.get("accountId")
    return label if isinstance(label, str) else ""


def _opencode_auth_path() -> Path:
    override = os.environ.get("OPENCODE_AUTH")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local/share"
    return root / "opencode" / "auth.json"


def _opencode_profiles_dir() -> Path:
    override = os.environ.get("OPENCODE_AUTH_PROFILES")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "opencode" / "auth-profiles"


def _codex_auth_path() -> Path:
    override = os.environ.get("CODEX_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".codex"
    return root / "auth.json"


def _codex_tokens(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    payload = _read_json_object(path)
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        return None
    return {"access_token": access, "refresh_token": refresh}


def _hermes_oauth_store(
    provider: str,
    *,
    access: str,
    refresh: str,
    label: str,
    source: str,
    extra_provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = empty_auth_store()
    state: dict[str, Any] = {
        "tokens": {"access_token": access, "refresh_token": refresh},
        "label": label,
    }
    if extra_provider:
        state.update(extra_provider)
    store["providers"][provider] = state
    store["credential_pool"][provider] = [
        {
            "id": _credential_id(provider, label, source),
            "label": label,
            "auth_type": "oauth",
            "priority": 0,
            "source": source,
            "access_token": access,
            "refresh_token": refresh,
        }
    ]
    return store


def _tokens_from_hermes(store: dict[str, Any], provider: str) -> tuple[str, str, str]:
    state = store.get("providers", {}).get(provider, {})
    access = ""
    refresh = ""
    label = ""
    if isinstance(state, dict):
        tokens = state.get("tokens")
        if isinstance(tokens, dict):
            access = tokens.get("access_token") or ""
            refresh = tokens.get("refresh_token") or ""
        access = access or state.get("access_token") or ""
        refresh = refresh or state.get("refresh_token") or ""
        maybe_label = state.get("label")
        if isinstance(maybe_label, str):
            label = maybe_label
    pool = store.get("credential_pool", {}).get(provider, [])
    if isinstance(pool, list):
        for entry in pool:
            if not isinstance(entry, dict):
                continue
            access = access or entry.get("access_token") or ""
            refresh = refresh or entry.get("refresh_token") or ""
            maybe_label = entry.get("label")
            if not label and isinstance(maybe_label, str):
                label = maybe_label
    if (
        not isinstance(access, str)
        or not isinstance(refresh, str)
        or not access
        or not refresh
    ):
        raise ValueError(f"hermes store has no OAuth pair for {provider}")
    return access, refresh, label


def _credential_id(provider: str, label: str, source: str) -> str:
    if label and PROFILE_NAME.fullmatch(label.replace("_", "-").lower()):
        return label.replace("_", "-").lower()[:63]
    suffix = source.rsplit(":", 1)[-1]
    return f"{suffix}-{provider}"[:63]


def _has_oauth(store: dict[str, Any], provider: str) -> bool:
    return providers_contain_refresh_tokens(store, [provider])


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ValueError(f"auth store not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected an object")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    from hermes_profile.paths import write_private

    write_private(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


ADAPTERS: dict[str, AuthAdapter] = {
    "hermes": HermesAdapter(),
    "opencode": OpenCodeAdapter(),
    "codex": CodexAdapter(),
}
