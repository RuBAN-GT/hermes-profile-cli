from pathlib import Path
from typing import Any

from hermes_profile import __version__
from hermes_profile.ops import GUIDE, Ops
from hermes_profile.paths import load_settings


def build_server(config: Path):
    try:
        from mcp.server.mcpserver import MCPServer
    except ModuleNotFoundError as error:
        raise ValueError(
            "MCP extra required: pip install 'hermes-profile-cli[mcp]'"
        ) from error

    settings = load_settings(str(config))
    ops = Ops(settings)
    server = MCPServer(
        "hermes-profile",
        instructions=GUIDE,
        version=__version__,
    )

    @server.tool(description="How to assemble profiles and fragments. Read this first.")
    def guide() -> str:
        return GUIDE

    @server.tool(description="List local folders and SSH hosts.")
    def list_locations() -> list[dict[str, str]]:
        return ops.list_locations()

    @server.tool(description="Set the default location for later calls.")
    def use_location(alias: str) -> dict[str, str]:
        return ops.use_location(alias)

    @server.tool(description="List profile names on a location.")
    def list_profiles(location: str | None = None) -> list[str]:
        return ops.profiles(location)

    @server.tool(description="Show fragment references for a profile. No secrets.")
    def show_profile(name: str, location: str | None = None) -> dict[str, Any]:
        return ops.show(name, location)

    @server.tool(description="List fragment paths relative to fragments_dir.")
    def list_fragments(location: str | None = None) -> list[str]:
        return ops.fragments(location)

    @server.tool(
        description="Read a fragment. Env files return keys only, never values."
    )
    def read_fragment(reference: str, location: str | None = None) -> dict[str, Any]:
        return ops.read_fragment(reference, location)

    @server.tool(
        description="Write a fragment. Env responses return keys only, never values."
    )
    def write_fragment(
        reference: str, content: str, location: str | None = None
    ) -> dict[str, Any]:
        return ops.write_fragment(reference, content, location)

    @server.tool(
        description="Create a profile. share_from copies shared refs, not secrets."
    )
    def create_profile(
        name: str,
        share_from: str | None = None,
        add_config: list[str] | None = None,
        add_env: list[str] | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        return ops.create(
            name,
            share_from=share_from,
            add_config=add_config,
            add_env=add_env,
            location=location,
        )

    @server.tool(description="Add or replace fragment refs on an existing profile.")
    def update_profile(
        name: str,
        add_config: list[str] | None = None,
        add_env: list[str] | None = None,
        set_config: list[str] | None = None,
        set_env: list[str] | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        return ops.update(
            name,
            add_config=add_config,
            add_env=add_env,
            set_config=set_config,
            set_env=set_env,
            location=location,
        )

    @server.tool(
        description="Render assembled config. Env values are omitted; only keys/count."
    )
    def render_profile(name: str, location: str | None = None) -> dict[str, Any]:
        return ops.render(name, location)

    @server.tool(description="Diff apply would make. Env changes listed by name only.")
    def preflight_profile(name: str, location: str | None = None) -> dict[str, Any]:
        return ops.preflight(name, location)

    @server.tool(description="File drift and auth inventory digest. No tokens.")
    def status_profile(name: str, location: str | None = None) -> dict[str, bool]:
        return ops.status(name, location)

    @server.tool(description="Materialize fragments into config.yaml and .env.")
    def apply_profile(
        name: str,
        confirm: bool = False,
        discard_runtime: bool = False,
        location: str | None = None,
    ) -> dict[str, Any]:
        return ops.apply(
            name,
            confirm=confirm,
            discard_runtime=discard_runtime,
            location=location,
        )

    @server.tool(description="Keep Hermes runtime edits in the overlay.")
    def reconcile_profile(name: str, location: str | None = None) -> dict[str, Any]:
        return ops.reconcile(name, location)

    return server


def run_server(config: Path) -> None:
    build_server(config).run()
