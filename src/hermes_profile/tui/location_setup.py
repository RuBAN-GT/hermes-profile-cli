from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from hermes_profile.models import LocalLocation
from hermes_profile.paths import PROFILE_NAME, upsert_local_location


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
    #type-subtitle { color: $secondary; margin-bottom: 1; }
    Button { margin-top: 1; border: round $secondary; text-style: bold; width: 100%; }
    #local-type { border: round $success; color: $success; }
    #ssh-type { border: round $accent; color: $accent; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="location-type"):
            yield Label("Add a location", id="type-title")
            yield Label("Where should profile files live?", id="type-subtitle")
            yield Button("This computer", id="local-type")
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
    #local-subtitle { color: $secondary; margin-bottom: 1; }
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

    def compose(self) -> ComposeResult:
        editing = self.location is not None
        with Vertical(id="local-setup"):
            yield Label(
                "Edit local folder" if editing else "Add a local folder",
                id="local-title",
            )
            yield Label(
                "Alias is a short name. Directory is the operational root.",
                id="local-subtitle",
            )
            yield Input(
                value=self.location.alias if self.location else "",
                placeholder="Alias, e.g. laptop",
                id="local-alias",
                disabled=editing,
            )
            yield Input(
                value=str(self.location.managed_dir) if self.location else "",
                placeholder="Managed directory, e.g. /srv/hermes/managed",
                id="local-managed-dir",
            )
            yield Label("", id="local-error")
            with Horizontal():
                yield Button("Save location", id="save-local")
                yield Button("Cancel", id="cancel-local")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-local":
            self.dismiss(False)
            return
        try:
            alias = self.query_one("#local-alias", Input).value.strip()
            managed = self.query_one("#local-managed-dir", Input).value.strip()
            if not alias:
                raise ValueError("Give this location a short alias, e.g. laptop")
            if not PROFILE_NAME.fullmatch(alias):
                raise ValueError(
                    "Alias must use lowercase letters, digits, and hyphens"
                )
            if not managed:
                raise ValueError("Managed directory is required")
            managed_dir = Path(managed).expanduser()
            if not managed_dir.is_absolute() or ".." in managed_dir.parts:
                raise ValueError("Managed directory must be an absolute path")
            location = LocalLocation(
                alias=alias,
                managed_dir=managed_dir,
                profiles_dir=managed_dir / "profiles",
                fragments_dir=managed_dir / "fragments",
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
