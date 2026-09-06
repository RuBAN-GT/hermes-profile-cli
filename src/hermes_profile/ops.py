from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from hermes_profile.env import parse_env
from hermes_profile.models import Host, Profile, Settings
from hermes_profile.paths import (
    PROFILE_NAME,
    fragment_path,
    safe_fragment_reference,
)
from hermes_profile.profiles import (
    create_profile,
    identity_config_ref,
    identity_env_ref,
    is_identity_ref,
    list_fragments,
    list_profiles,
    load_profile,
    read_fragment_view,
    save_profile,
    share_profile_stack,
    write_fragment,
)
from hermes_profile.service import apply, preflight, reconcile, render_profile, status
from hermes_profile.transport import SshTransport

PROFILE_NAME_ERROR = "profile name must use lowercase letters, digits, and hyphens"

GUIDE = """
Hermes profile assembly. profile.yaml stores relative fragment refs only.

Locations
- list_locations, then pass location= on every call (or use_location once).
- local: primary workspace. Other aliases are extra local folders or SSH hosts.

Fragment layout
- config/common.yaml, config/host.yaml: shared defaults
- config/capabilities/*: optional mixins (browser, image-gen, mcp-tududi)
- config/profiles/<name>.yaml: policy plus identity (pet, db path, volumes)
- env/common.env, env/terminal.env: shared non-secrets
- env/profiles/<name>.private.env: tokens and per-profile paths

Maps merge recursively. Lists in a later fragment replace earlier lists.

New profile
1. list_profiles and list_fragments on the target location
2. create_profile(name, share_from=<existing>) to copy shared refs
3. edit config/profiles/<name>.yaml identity if needed
4. fill env/profiles/<name>.private.env keys (never echo values)
5. preflight, then apply(confirm=true)

Do not print env values, tokens, passwords, or auth.json. Env reads return keys only.
""".strip()


@dataclass
class Ops:
    settings: Settings
    location: str = "local"

    def list_locations(self) -> list[dict[str, str]]:
        rows = [
            {
                "alias": "local",
                "kind": "local",
                "profiles_dir": str(self.settings.profiles_dir),
                "fragments_dir": str(self.settings.fragments_dir),
            }
        ]
        for alias, location in sorted(self.settings.local_locations.items()):
            rows.append(
                {
                    "alias": alias,
                    "kind": "folder",
                    "profiles_dir": str(location.profiles_dir),
                    "fragments_dir": str(location.fragments_dir),
                }
            )
        for alias, host in sorted(self.settings.hosts.items()):
            rows.append(
                {
                    "alias": alias,
                    "kind": "ssh",
                    "ssh_host": host.ssh_host,
                    "profiles_dir": str(host.profiles_dir),
                    "fragments_dir": str(host.fragments_dir),
                }
            )
        return rows

    def use_location(self, alias: str) -> dict[str, str]:
        self.location = self._resolve_alias(alias)
        return {"location": self.location}

    def profiles(self, location: str | None = None) -> list[str]:
        ops = self._at(location)
        if ops._host() is not None:
            return SshTransport(ops._host()).profiles()
        return list_profiles(ops._local())

    def show(self, name: str, location: str | None = None) -> dict[str, Any]:
        ops = self._at(location)
        profile = ops._load(name)
        return {
            "name": profile.name,
            "config": list(profile.config_fragments),
            "env": list(profile.env_fragments),
            "auth": profile.auth,
        }

    def fragments(self, location: str | None = None) -> list[str]:
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            return SshTransport(host).list_files(host.fragments_dir)
        return list_fragments(ops._local())

    def read_fragment(
        self, reference: str, location: str | None = None
    ) -> dict[str, Any]:
        ops = self._at(location)
        host = ops._host()
        if host is None:
            return read_fragment_view(ops._local(), reference)
        text = SshTransport(host).read_text_file(ops._remote_fragment(reference))
        if text is None:
            raise ValueError(f"fragment not found: {reference}")
        if _env_ref(reference):
            return {
                "reference": reference,
                "kind": "env",
                "keys": list(parse_env(text, reference)),
            }
        document = yaml.safe_load(text) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{reference}: config fragment must be a mapping")
        return {"reference": reference, "kind": "config", "content": document}

    def write_fragment(
        self, reference: str, content: str, location: str | None = None
    ) -> dict[str, Any]:
        ops = self._at(location)
        host = ops._host()
        if host is None:
            return write_fragment(ops._local(), reference, content)
        if _env_ref(reference):
            keys = list(parse_env(content, reference))
            SshTransport(host).write_private_file(
                ops._remote_fragment(reference),
                content if content.endswith("\n") else f"{content}\n",
            )
            return {"reference": reference, "kind": "env", "keys": keys}
        document = yaml.safe_load(content) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{reference}: config fragment must be a mapping")
        SshTransport(host).write_private_file(
            ops._remote_fragment(reference),
            content if content.endswith("\n") else f"{content}\n",
        )
        return {"reference": reference, "kind": "config"}

    def create(
        self,
        name: str,
        *,
        share_from: str | None = None,
        add_config: list[str] | None = None,
        add_env: list[str] | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        ops = self._at(location)
        extra_config = tuple(add_config or ())
        extra_env = tuple(add_env or ())
        host = ops._host()
        if host is None:
            settings = ops._local()
            if share_from:
                path = share_profile_stack(
                    settings,
                    share_from,
                    name,
                    extra_config=extra_config,
                    extra_env=extra_env,
                )
            else:
                path = create_profile(
                    settings,
                    name,
                    config_fragments=extra_config,
                    env_fragments=extra_env,
                )
            return {"created": str(path), "location": ops.location}
        return ops._remote_create(name, share_from, extra_config, extra_env)

    def update(
        self,
        name: str,
        *,
        add_config: list[str] | None = None,
        add_env: list[str] | None = None,
        set_config: list[str] | None = None,
        set_env: list[str] | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        ops = self._at(location)
        profile = ops._load(name)
        if not add_config and not add_env and not set_config and not set_env:
            raise ValueError("update requires fragment refs to add or set")
        config = tuple(set_config) if set_config else profile.config_fragments
        environment = tuple(set_env) if set_env else profile.env_fragments
        updated = Profile(
            name=name,
            config_fragments=config + tuple(add_config or ()),
            env_fragments=environment + tuple(add_env or ()),
            auth=profile.auth,
        )
        ops._save(updated)
        return {"updated": name, "location": ops.location}

    def render(self, name: str, location: str | None = None) -> dict[str, Any]:
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            result = SshTransport(host).action(name, "render")
            raw = result.get("config")
            config = raw if isinstance(raw, dict) else {}
            return {
                "config": config,
                "environment_count": result.get("environment_count", 0),
            }
        config, environment = render_profile(ops._local(), name, preview=True)
        return {
            "config": config,
            "environment_count": len(environment),
            "env_keys": sorted(environment),
        }

    def preflight(self, name: str, location: str | None = None) -> dict[str, Any]:
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            return SshTransport(host).action(name, "preflight")
        return preflight(ops._local(), name)

    def status(self, name: str, location: str | None = None) -> dict[str, bool]:
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            return SshTransport(host).status(name)
        return status(ops._local(), name)

    def apply(
        self,
        name: str,
        *,
        confirm: bool,
        discard_runtime: bool = False,
        location: str | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("apply requires confirm=true")
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            action = "apply-discard" if discard_runtime else "apply"
            return SshTransport(host).action(name, action)
        apply(ops._local(), name, discard_runtime)
        return {"applied": name, "location": ops.location}

    def reconcile(self, name: str, location: str | None = None) -> dict[str, Any]:
        ops = self._at(location)
        host = ops._host()
        if host is not None:
            return SshTransport(host).action(name, "reconcile")
        return {"reconciled": reconcile(ops._local(), name), "location": ops.location}

    def _at(self, location: str | None) -> "Ops":
        alias = self.location if location is None else location
        return Ops(self.settings, self._resolve_alias(alias))

    def _resolve_alias(self, alias: str) -> str:
        if alias in {"", "local"}:
            return "local"
        if alias in self.settings.local_locations or alias in self.settings.hosts:
            return alias
        raise ValueError(f"unknown location: {alias}")

    def _host(self) -> Host | None:
        if self.location == "local":
            return None
        return self.settings.hosts.get(self.location)

    def _local(self) -> Settings:
        if self.location == "local":
            return self.settings
        folder = self.settings.local_locations.get(self.location)
        if folder is None:
            raise ValueError(f"unknown location: {self.location}")
        return replace(
            self.settings,
            managed_dir=folder.managed_dir,
            profiles_dir=folder.profiles_dir,
            fragments_dir=folder.fragments_dir,
        )

    def _load(self, name: str) -> Profile:
        host = self._host()
        if host is None:
            return load_profile(self._local(), name)
        if not PROFILE_NAME.fullmatch(name):
            raise ValueError(PROFILE_NAME_ERROR)
        text = SshTransport(host).read_text_file(
            host.profiles_dir / name / "profile.yaml"
        )
        if text is None:
            raise ValueError(f"profile does not exist: {name}")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{name}: expected a mapping")
        auth = data.get("auth")
        if auth is not None and (not isinstance(auth, str) or not auth):
            raise ValueError(f"{name}: auth must be a map key string")
        return Profile(
            name=name,
            config_fragments=_refs(data.get("config", []), "config"),
            env_fragments=_refs(data.get("env", []), "env"),
            auth=auth,
        )

    def _save(self, profile: Profile) -> None:
        host = self._host()
        data: dict[str, object] = {
            "config": list(profile.config_fragments),
            "env": list(profile.env_fragments),
        }
        if profile.auth:
            data["auth"] = profile.auth
        payload = yaml.safe_dump(data, sort_keys=False)
        if host is None:
            save_profile(self._local(), profile)
            return
        SshTransport(host).write_private_file(
            host.profiles_dir / profile.name / "profile.yaml", payload
        )

    def _remote_fragment(self, reference: str) -> Path:
        host = self._host()
        if host is None:
            return fragment_path(self._local(), reference)
        safe_fragment_reference(reference)
        return host.fragments_dir / reference

    def _remote_create(
        self,
        name: str,
        share_from: str | None,
        extra_config: tuple[str, ...],
        extra_env: tuple[str, ...],
    ) -> dict[str, Any]:
        host = self._host()
        if host is None:
            raise ValueError("remote create requires an SSH host")
        if not PROFILE_NAME.fullmatch(name):
            raise ValueError(PROFILE_NAME_ERROR)
        transport = SshTransport(host)
        directory = host.profiles_dir / name
        if transport.read_text_file(directory / "profile.yaml") is not None:
            raise ValueError(f"profile already exists: {name}")
        config = extra_config
        environment = extra_env
        if share_from:
            source = self._load(share_from)
            config = (
                tuple(
                    reference
                    for reference in source.config_fragments
                    if not is_identity_ref(reference, share_from)
                )
                + extra_config
            )
            environment = (
                tuple(
                    reference
                    for reference in source.env_fragments
                    if not is_identity_ref(reference, share_from)
                )
                + extra_env
            )
        if identity_config_ref(name) not in config:
            config += (identity_config_ref(name),)
        if identity_env_ref(name) not in environment:
            environment += (identity_env_ref(name),)
        transport.ensure_private_dir(directory)
        transport.ensure_private_dir(directory / "state")
        save = Profile(name=name, config_fragments=config, env_fragments=environment)
        self._save(save)
        if share_from:
            self._remote_identity(transport, host, share_from, name)
        else:
            transport.write_private_file(
                host.fragments_dir / identity_config_ref(name),
                yaml.safe_dump({"display": {"pet": name}}, allow_unicode=True),
            )
            transport.write_private_file(
                host.fragments_dir / identity_env_ref(name),
                f"HERMES_HOME={directory}\n",
            )
        return {"created": str(directory), "location": self.location}

    def _remote_identity(
        self, transport: SshTransport, host: Host, source_name: str, name: str
    ) -> None:
        source = transport.read_text_file(
            host.fragments_dir / identity_config_ref(source_name)
        )
        home = str(host.profiles_dir / name)
        if source:
            text = source.replace(f"/profiles/{source_name}/", f"/profiles/{name}/")
            document = yaml.safe_load(text) or {}
            if isinstance(document, dict):
                display = document.setdefault("display", {})
                if isinstance(display, dict):
                    display["pet"] = name
                payload = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
            else:
                payload = yaml.safe_dump({"display": {"pet": name}}, allow_unicode=True)
        else:
            payload = yaml.safe_dump({"display": {"pet": name}}, allow_unicode=True)
        transport.write_private_file(
            host.fragments_dir / identity_config_ref(name), payload
        )
        env_text = transport.read_text_file(
            host.fragments_dir / identity_env_ref(source_name)
        )
        keys = list(parse_env(env_text, source_name)) if env_text else ["HERMES_HOME"]
        if "HERMES_HOME" not in keys:
            keys = ["HERMES_HOME", *keys]
        lines = [
            f"HERMES_HOME={home}" if key == "HERMES_HOME" else f"{key}=" for key in keys
        ]
        transport.write_private_file(
            host.fragments_dir / identity_env_ref(name),
            "\n".join(lines) + "\n",
        )


def _env_ref(reference: str) -> bool:
    return reference.endswith(".env") or reference.startswith("env/")


def _refs(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of fragment paths")
    return tuple(value)
