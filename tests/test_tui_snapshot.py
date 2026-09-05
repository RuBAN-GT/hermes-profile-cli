from pathlib import Path

from hermes_profile.models import Settings
from hermes_profile.profiles import create_profile
from hermes_profile.tui.app import ProfileApp

SNAPSHOT_SIZE = (120, 40)


def test_workspace_snapshot(snap_compare, tmp_path: Path, monkeypatch) -> None:
    # Keep snapshots stable in terminals and CI that disable color.
    monkeypatch.delenv("NO_COLOR", raising=False)
    root = tmp_path / "managed"
    settings = Settings(root, root / "profiles", root / "fragments", animations=False)
    create_profile(settings, "tyrion")
    app = ProfileApp(settings, tmp_path / "config.yaml")

    async def run_before(pilot) -> None:
        await pilot.press("enter")
        await pilot.pause()

    assert snap_compare(app, terminal_size=SNAPSHOT_SIZE, run_before=run_before)
