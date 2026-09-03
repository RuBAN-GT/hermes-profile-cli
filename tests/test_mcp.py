import asyncio
from pathlib import Path

from hermes_profile.mcp_server import build_server
from hermes_profile.paths import initialize_settings


def test_mcp_server_exposes_profile_tools(tmp_path: Path) -> None:
    initialize_settings(tmp_path / "config.yaml", tmp_path / "managed")
    server = build_server(tmp_path / "config.yaml")
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {
        "guide",
        "list_locations",
        "use_location",
        "list_profiles",
        "list_fragments",
        "read_fragment",
        "create_profile",
        "apply_profile",
    } <= names
    assert server.instructions
    assert "keys only" in server.instructions.lower()
