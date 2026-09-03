import asyncio
from pathlib import Path

from textual.widgets import Input, Label, ListView, Static

from hermes_profile.models import LocalLocation, Settings
from hermes_profile.paths import initialize_settings, upsert_local_location
from hermes_profile.profiles import create_profile
from hermes_profile.transport import LocalTransport
from hermes_profile.tui.app import ProfileApp, format_preview, preview_rows
from hermes_profile.tui.help import HelpScreen
from hermes_profile.tui.location_home import LocationHomeScreen
from hermes_profile.tui.setup import LocalSetupScreen, SetupApp


def test_tui_location_home_lists_local_and_opens_profiles(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    create_profile(settings, "tyrion")
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LocationHomeScreen)
            listing = app.screen.query_one("#location-list", ListView)
            assert len(listing.children) == 1
            await pilot.press("enter")
            await pilot.pause()
            profiles = app.query_one("#profiles", ListView)
            assert len(profiles.children) == 1
            await pilot.press("r")

    asyncio.run(run())


def test_local_transport_preview_has_no_environment_values(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    create_profile(settings, "tyrion")
    profile = settings.profiles_dir / "tyrion"
    (settings.fragments_dir / "env").mkdir(parents=True)
    (settings.fragments_dir / "env" / "private.env").write_text("SECRET=redacted\n")
    (profile / "profile.yaml").write_text("config: []\nenv:\n  - env/private.env\n")

    preview = LocalTransport(settings).action("tyrion", "render")

    assert preview == {"config": {}, "environment_count": 1}


def test_tui_lists_additional_local_location(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    location = LocalLocation(
        alias="lab",
        managed_dir=tmp_path / "lab",
        profiles_dir=tmp_path / "lab" / "profiles",
        fragments_dir=tmp_path / "lab" / "fragments",
    )
    settings = Settings(
        root,
        root / "profiles",
        root / "fragments",
        local_locations={"lab": location},
    )
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            listing = app.screen.query_one("#location-list", ListView)
            assert len(listing.children) == 2
            assert {child.id for child in listing.children} == {"local", "local--lab"}

    asyncio.run(run())


def test_tui_refreshes_locations_after_save_callback(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    settings = initialize_settings(config, tmp_path / "managed")
    location = LocalLocation(
        alias="lab",
        managed_dir=tmp_path / "lab",
        profiles_dir=tmp_path / "lab" / "profiles",
        fragments_dir=tmp_path / "lab" / "fragments",
    )
    app = ProfileApp(settings, config)

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            upsert_local_location(config, location)
            app._location_saved(True)
            home = app.screen
            assert isinstance(home, LocationHomeScreen)
            home.refresh_locations()
            await pilot.pause()
            assert len(home.query_one("#location-list", ListView).children) == 2

    asyncio.run(run())


def test_tui_hides_workspace_keys_on_location_home(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.check_action("preview", ()) is False
            await pilot.press("enter")
            await pilot.pause()
            assert app.check_action("preview", ()) is None
            assert app.check_action("init_remote", ()) is False

    asyncio.run(run())


def test_tui_load_error_replaces_loading_copy(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app._show_load_error(
                "remote CLI not found at /opt/hermes/bin/hermes-profile"
            )
            summary = str(app.query_one("#summary", Label).content)
            detail = str(app.query_one("#profile-detail", Static).content)
            assert "unavailable" in summary
            assert "Could not open" in detail
            assert "Loading" not in detail

    asyncio.run(run())


def test_preview_rows_describe_top_level_keys() -> None:
    rows = preview_rows({"agent": {"model": "x"}, "plugins": ["a", "b"], "flag": True})
    assert rows == [
        ("agent", "map", "1 key(s)"),
        ("flag", "bool", "true"),
        ("plugins", "list", "2 item(s)"),
    ]


def test_format_preview_is_tabular() -> None:
    text = format_preview("tyrion", {"agent": {"model": "x"}}, 33)
    assert "tyrion" in text
    assert "Key" in text
    assert "agent" in text
    assert "map" in text
    assert "33 variable" in text
    assert "Top-level config:" not in text


def test_setup_asks_local_or_remote_first(tmp_path: Path) -> None:
    app = SetupApp(tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#choose-local")
            assert app.query_one("#choose-ssh")
            assert not app.query("#local-managed-dir")
            await pilot.click("#choose-local")
            await pilot.pause()
            assert isinstance(app.screen, LocalSetupScreen)
            assert app.screen.query_one("#local-managed-dir")
            assert app.screen.query_one("#local-profiles-dir")
            assert app.screen.query_one("#local-fragments-dir")
            assert not app.screen.query("#host-alias")
            await pilot.click("#back-setup")
            await pilot.pause()
            assert app.query_one("#choose-local")
            await pilot.click("#choose-ssh")
            await pilot.pause()
            assert app.screen.query_one("#host-alias")
            assert not app.screen.query("#choose-local")

    asyncio.run(run())


def test_setup_local_creates_config(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    managed = tmp_path / "managed"
    app = SetupApp(config)

    async def run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#choose-local")
            await pilot.pause()
            app.screen.query_one("#local-managed-dir", Input).value = str(managed)
            await pilot.pause()
            await pilot.click("#local")
            await pilot.pause()

    asyncio.run(run())
    assert config.is_file()
    assert (managed / "profiles").is_dir()
    assert (managed / "fragments").is_dir()


def test_setup_local_custom_paths(tmp_path: Path) -> None:
    default = tmp_path / "default.yaml"
    config = tmp_path / "elsewhere" / "config.yaml"
    managed = tmp_path / "managed"
    profiles = tmp_path / "homes"
    fragments = tmp_path / "snips"
    app = SetupApp(default)

    async def run() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#choose-local")
            await pilot.pause()
            app.screen.query_one("#local-config", Input).value = str(config)
            app.screen.query_one("#local-managed-dir", Input).value = str(managed)
            await pilot.pause()
            app.screen.query_one("#local-profiles-dir", Input).value = str(profiles)
            app.screen.query_one("#local-fragments-dir", Input).value = str(fragments)
            await pilot.click("#local")
            await pilot.pause()

    asyncio.run(run())
    assert config.is_file()
    assert not default.exists()
    assert profiles.is_dir()
    assert fragments.is_dir()


def test_tui_help_opens_guide(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments")
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)

    asyncio.run(run())
