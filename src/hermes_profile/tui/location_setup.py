from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from hermes_profile.models import LocalLocation
from hermes_profile.paths import (
    PROFILE_NAME,
    _absolute_dir,
    derived_child,
    upsert_local_location,
)


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; background: $background 70%; }
    #confirm-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #confirm-dialog.danger { border: round $error; }
    #confirm-title { text-style: bold; color: $primary; margin-bottom: 1; }
    #confirm-dialog.danger #confirm-title { color: $error; }
    #confirm-body { color: $foreground; margin-bottom: 1; }
    #confirm-actions { height: auto; margin-top: 1; }
    Button { margin-right: 1; border: round $secondary; text-style: bold; }
    #confirm-ok { border: round $success; color: $success; }
    #confirm-dialog.danger #confirm-ok { border: round $error; color: $error; }
    """

    def __init__(
        self,
        title: str,
        body: str,
        confirm: str = "OK",
        *,
        danger: bool = False,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.body_text = body
        self.confirm_label = confirm
        self.danger = danger

    def compose(self) -> ComposeResult:
        classes = "danger" if self.danger else ""
        with Vertical(id="confirm-dialog", classes=classes):
            yield Label(self.title_text, id="confirm-title")
            yield Label(self.body_text, id="confirm-body")
            with Horizontal(id="confirm-actions"):
                yield Button(self.confirm_label, id="confirm-ok")
                yield Button("Cancel", id="confirm-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-ok")


class DeleteLocationScreen(ModalScreen[bool]):
    CSS = """
    DeleteLocationScreen { align: center middle; background: $background 70%; }
    #delete-location {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #delete-title { text-style: bold; color: $error; margin-bottom: 1; }
    Button {
        margin-top: 1;
        margin-right: 1;
        border: round $secondary;
        text-style: bold;
    }
    #confirm-delete { border: round $error; color: $error; }
    """

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-location"):
            yield Label(f"Remove {self.label}?", id="delete-title")
            yield Label("Only the manager record is removed.")
            yield Label("Profiles and remote files stay where they are.")
            with Horizontal():
                yield Button("Remove location", id="confirm-delete")
                yield Button("Cancel", id="cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-delete")


class LocationTypeScreen(ModalScreen[str | None]):
    CSS = """
    LocationTypeScreen { align: center middle; background: $background 70%; }
    #location-type {
        width: 58;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #type-title { text-style: bold; color: $primary; }
    #type-subtitle, .hint { color: $secondary; margin-bottom: 1; }
    Button { margin-top: 1; border: round $secondary; text-style: bold; width: 100%; }
    #local-type { border: round $success; color: $success; }
    #ssh-type { border: round $accent; color: $accent; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="location-type"):
            yield Label("Add a location", id="type-title")
            yield Label("Where should profile files live?", id="type-subtitle")
            yield Label(
                "This computer: another folder on this machine.",
                classes="hint",
            )
            yield Button("This computer", id="local-type")
            yield Label(
                "SSH: existing keys only. Passwords are not stored.",
                classes="hint",
            )
            yield Button("Another machine over SSH", id="ssh-type")
            yield Button("Cancel", id="cancel-type")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choices = {"local-type": "local", "ssh-type": "ssh"}
        self.dismiss(choices.get(event.button.id))


class LocalLocationScreen(ModalScreen[bool]):
    CSS = """
    LocalLocationScreen { align: center middle; background: $background 70%; }
    #local-setup {
        width: 74;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #local-title { text-style: bold; color: $primary; }
    #local-subtitle, .hint { color: $secondary; margin-bottom: 1; }
    #local-fields { height: auto; max-height: 18; }
    #local-error { color: $error; height: 2; }
    Input { margin: 1 0; border: round $secondary; }
    Input:focus { border: round $accent; }
    Button { margin-right: 1; border: round $secondary; text-style: bold; }
    #save-local { border: round $success; color: $success; }
    """

    def __init__(self, config: Path, location: LocalLocation | None = None) -> None:
        super().__init__()
        self.config = config
        self.location = location
        self._managed = location.managed_dir if location else Path()

    def compose(self) -> ComposeResult:
        editing = self.location is not None
        with Vertical(id="local-setup"):
            yield Label(
                "Edit local folder" if editing else "Add a local folder",
                id="local-title",
            )
            yield Label(
                "A separate profile root on this machine. Alias is the TUI name.",
                id="local-subtitle",
            )
            with VerticalScroll(id="local-fields"):
                yield Label("Alias")
                yield Label("Lowercase letters, digits, and hyphens.", classes="hint")
                yield Input(
                    value=self.location.alias if self.location else "",
                    placeholder="Alias, e.g. laptop",
                    id="local-alias",
                    disabled=editing,
                )
                yield Label("Managed directory")
                yield Label(
                    "Operational root. Profiles and fragments follow this path "
                    "until you edit them.",
                    classes="hint",
                )
                yield Input(
                    value=str(self.location.managed_dir) if self.location else "",
                    placeholder="Managed directory, e.g. /srv/hermes/managed",
                    id="local-managed-dir",
                )
                yield Label("Profiles directory")
                yield Label(
                    "One folder per profile. Leave empty to use <managed>/profiles.",
                    classes="hint",
                )
                yield Input(
                    value=str(self.location.profiles_dir) if self.location else "",
                    placeholder="Defaults to <managed>/profiles",
                    id="local-profiles-dir",
                )
                yield Label("Fragments directory")
                yield Label(
                    "Shared YAML and env snippets. Leave empty for "
                    "<managed>/fragments.",
                    classes="hint",
                )
                yield Input(
                    value=str(self.location.fragments_dir) if self.location else "",
                    placeholder="Defaults to <managed>/fragments",
                    id="local-fragments-dir",
                )
            yield Label("", id="local-error")
            with Horizontal():
                yield Button("Save location", id="save-local")
                yield Button("Cancel", id="cancel-local")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "local-managed-dir":
            return
        raw = event.value.strip()
        if not raw:
            return
        managed = Path(raw).expanduser()
        profiles = self.query_one("#local-profiles-dir", Input)
        fragments = self.query_one("#local-fragments-dir", Input)
        profiles.value = derived_child(
            managed, self._managed, profiles.value, "profiles"
        )
        fragments.value = derived_child(
            managed, self._managed, fragments.value, "fragments"
        )
        self._managed = managed

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-local":
            self.dismiss(False)
            return
        try:
            alias = self.query_one("#local-alias", Input).value.strip()
            managed = self.query_one("#local-managed-dir", Input).value.strip()
            profiles = self.query_one("#local-profiles-dir", Input).value.strip()
            fragments = self.query_one("#local-fragments-dir", Input).value.strip()
            if not alias:
                raise ValueError("Give this location a short alias, e.g. laptop")
            if not PROFILE_NAME.fullmatch(alias):
                raise ValueError(
                    "Alias must use lowercase letters, digits, and hyphens"
                )
            if not managed:
                raise ValueError("Managed directory is required")
            managed_dir = _absolute_dir(Path(managed), "managed_dir")
            profiles_dir = _absolute_dir(
                Path(profiles) if profiles else managed_dir / "profiles",
                "profiles_dir",
            )
            fragments_dir = _absolute_dir(
                Path(fragments) if fragments else managed_dir / "fragments",
                "fragments_dir",
            )
            location = LocalLocation(
                alias=alias,
                managed_dir=managed_dir,
                profiles_dir=profiles_dir,
                fragments_dir=fragments_dir,
            )
            for directory in (
                location.managed_dir,
                location.profiles_dir,
                location.fragments_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
                directory.chmod(0o700)
            upsert_local_location(self.config, location)
        except ValueError as error:
            self.query_one("#local-error", Label).update(str(error))
            return
        self.dismiss(True)


class CreateProfileScreen(ModalScreen[str | None]):
    CSS = """
    CreateProfileScreen { align: center middle; background: $background 70%; }
    #create-profile {
        width: 58;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #create-title { text-style: bold; color: $primary; }
    #create-subtitle { color: $secondary; margin-bottom: 1; }
    #create-error { color: $error; height: 2; }
    Input { margin: 1 0; border: round $secondary; }
    Input:focus { border: round $accent; }
    Button { margin-right: 1; border: round $secondary; text-style: bold; }
    #save-profile { border: round $success; color: $success; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="create-profile"):
            yield Label("New profile", id="create-title")
            yield Label(
                "Lowercase letters, digits, and hyphens.",
                id="create-subtitle",
            )
            yield Input(placeholder="Name, e.g. tyrion", id="profile-name")
            yield Label("", id="create-error")
            with Horizontal():
                yield Button("Create", id="save-profile")
                yield Button("Cancel", id="cancel-profile")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-profile":
            self.dismiss(None)
            return
        name = self.query_one("#profile-name", Input).value.strip()
        if not PROFILE_NAME.fullmatch(name):
            self.query_one("#create-error", Label).update(
                "Name must use lowercase letters, digits, and hyphens"
            )
            return
        self.dismiss(name)
