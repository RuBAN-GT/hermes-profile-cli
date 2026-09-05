from dataclasses import replace
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator
from textual.worker import Worker, WorkerState

from hermes_profile import __version__
from hermes_profile.i18n import next_language, set_language, t
from hermes_profile.models import Host
from hermes_profile.paths import derived_child, initialize_settings, upsert_host
from hermes_profile.themes import apply_hermes_themes, next_theme
from hermes_profile.transport import (
    DEFAULT_REMOTE_BINARY,
    INSTALL_TIMEOUT_SECONDS,
    SSH_TIMEOUT_SECONDS,
    SshTransport,
    normalize_remote_binary,
    parse_ssh_target,
)
from hermes_profile.tui.help import HelpScreen

DEFAULT_LOCAL_MANAGED = Path("~/.local/share/hermes-profile/managed").expanduser()

SETUP_CSS = """
Screen { align: center middle; background: $background; color: $foreground; }
#setup {
    width: 84;
    height: auto;
    padding: 2;
    border: tall $primary;
    background: $surface;
}
#setup-title { text-style: bold; color: $text-primary; }
#setup-subtitle { color: $text-muted; margin-bottom: 1; }
#setup-fields { height: auto; max-height: 22; }
#setup-hint, .hint { color: $text-muted; }
#error { color: $text-error; height: 3; }
#setup-loading { height: 1; margin: 1 0; }
Input {
    margin: 1 0;
    border: tall $secondary;
    background: $background;
    color: $foreground;
}
Input:focus { border: tall $accent; }
Button {
    margin-top: 1;
    border: tall $secondary;
    background: $panel;
    color: $foreground;
    text-style: bold;
    width: 100%;
}
Button:hover { background: $primary; color: $text; }
#choose-local, #local { border: tall $success; color: $text-success; }
#choose-ssh, #ssh { border: tall $accent; color: $text-accent; }
#ssh-clone { border: tall $warning; color: $text-warning; }
#back-setup { border: tall $secondary; color: $foreground; }
#choose-local:hover, #local:hover { background: $success; color: $text; }
#choose-ssh:hover, #ssh:hover { background: $accent; color: $text; }
#ssh-clone:hover { background: $warning; color: $text; }
#back-setup:hover { background: $secondary; color: $text; }
"""


class SetupApp(App[Path | None]):
    """First-run setup for a local or SSH-managed Hermes installation."""

    CSS = SETUP_CSS
    TITLE = f"Hermes Profile Setup v{__version__}"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+t", "cycle_theme", "Theme"),
        Binding("ctrl+l", "cycle_language", "Lang"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, config: Path) -> None:
        super().__init__()
        self.config = config

    def on_mount(self) -> None:
        apply_hermes_themes(self)

    def action_cycle_theme(self) -> None:
        self.theme = next_theme(self.theme)

    def action_cycle_language(self) -> None:
        set_language(next_language())
        self.query_one("#setup-title", Label).update(t("setup_title"))
        self.query_one("#setup-subtitle", Label).update(t("setup_subtitle"))
        self.query_one("#choose-local", Button).label = t("this_computer")
        self.query_one("#choose-ssh", Button).label = t("another_ssh")

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="setup"):
            yield Label(t("setup_title"), id="setup-title")
            yield Label(t("setup_subtitle"), id="setup-subtitle")
            yield Label(t("setup_local_hint"), classes="hint")
            yield Button(t("this_computer"), id="choose-local")
            yield Label(t("setup_ssh_hint"), classes="hint")
            yield Button(t("another_ssh"), id="choose-ssh")
            yield Label(t("press_help"), classes="hint")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "choose-local":
            self.push_screen(LocalSetupScreen(self.config), self._setup_done)
        elif event.button.id == "choose-ssh":
            self.push_screen(RemoteSetupScreen(self.config), self._setup_done)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def _setup_done(self, created: Path | None) -> None:
        if created is not None:
            self.exit(created)


class _SetupForm(Screen[Path | None]):
    CSS = SETUP_CSS
    BINDINGS = [
        Binding("escape", "back", "Back", key_display="esc"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, config: Path) -> None:
        super().__init__()
        self.config = config

    def action_back(self) -> None:
        self.dismiss(None)

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def _path(self, identifier: str) -> Path:
        return Path(self._value(identifier)).expanduser()

    def _value(self, identifier: str) -> str:
        return self.query_one(f"#{identifier}", Input).value.strip()

    def _error(self, error: ValueError) -> None:
        self.query_one("#error", Label).update(str(error))


class LocalSetupScreen(_SetupForm):
    def __init__(self, config: Path) -> None:
        super().__init__(config)
        self._managed = DEFAULT_LOCAL_MANAGED

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="setup"):
            yield Label("Local setup", id="setup-title")
            yield Label("Where files live on this machine:", id="setup-subtitle")
            with VerticalScroll(id="setup-fields"):
                yield Label("Manager config")
                yield Label(
                    "This YAML lists locations. Default is fine unless you "
                    "already keep Hermes config elsewhere.",
                    classes="hint",
                )
                yield Input(value=str(self.config), id="local-config")
                yield Label("Managed directory")
                yield Label(
                    "Operational root. Profiles and fragments follow this path "
                    "until you edit them.",
                    classes="hint",
                )
                yield Input(value=str(DEFAULT_LOCAL_MANAGED), id="local-managed-dir")
                yield Label("Profiles directory")
                yield Label(
                    "One folder per profile. Each later gets config.yaml, .env, "
                    "and state/.",
                    classes="hint",
                )
                yield Input(
                    value=str(DEFAULT_LOCAL_MANAGED / "profiles"),
                    id="local-profiles-dir",
                )
                yield Label("Fragments directory")
                yield Label(
                    "Shared YAML and env snippets. profile.yaml only stores "
                    "relative references here.",
                    classes="hint",
                )
                yield Input(
                    value=str(DEFAULT_LOCAL_MANAGED / "fragments"),
                    id="local-fragments-dir",
                )
                yield Label("", id="error")
            yield Button("Create local setup", variant="primary", id="local")
            yield Button("Back", id="back-setup")
        yield Footer()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "local-managed-dir":
            return
        raw = event.value.strip() or str(DEFAULT_LOCAL_MANAGED)
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
        if event.button.id == "back-setup":
            self.dismiss(None)
            return
        if event.button.id != "local":
            return
        try:
            config = self._path("local-config")
            initialize_settings(
                config,
                self._path("local-managed-dir"),
                profiles_dir=self._path("local-profiles-dir"),
                fragments_dir=self._path("local-fragments-dir"),
            )
        except ValueError as error:
            self._error(error)
            return
        self.dismiss(config)


class RemoteSetupScreen(_SetupForm):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="setup"):
            yield Label("Remote SSH setup", id="setup-title")
            yield Label(
                "This still writes a local manager config, then talks to the host.",
                id="setup-subtitle",
            )
            with VerticalScroll(id="setup-fields"):
                yield Label("Local manager config")
                yield Label(
                    "Saved on this machine. Lists the SSH location.",
                    classes="hint",
                )
                yield Input(value=str(self.config), id="local-config")
                yield Label("Local managed directory")
                yield Label(
                    "Local operational root. Needed even for a remote-first setup.",
                    classes="hint",
                )
                yield Input(value=str(DEFAULT_LOCAL_MANAGED), id="local-managed-dir")
                yield Label("Location alias")
                yield Label(
                    "Short name shown in the TUI, e.g. gateway-a.",
                    classes="hint",
                )
                yield Input(placeholder="gateway-a", id="host-alias")
                yield Label("SSH target")
                yield Label(
                    "user@host, optional -p PORT. Uses your SSH agent and keys.",
                    classes="hint",
                )
                yield Input(
                    placeholder="deploy@gateway.example -p 22",
                    id="ssh-target",
                )
                yield Label("Remote managed directory")
                yield Label(
                    "Operational root on the remote host. Profiles/fragments "
                    "default to children of this path.",
                    classes="hint",
                )
                yield Input(
                    placeholder="/opt/hermes/managed",
                    id="remote-managed-dir",
                )
                yield Label("Remote manager CLI")
                yield Label(
                    "hermes-profile on the remote PATH or an absolute path. "
                    "Not the hermes agent.",
                    classes="hint",
                )
                yield Input(value=DEFAULT_REMOTE_BINARY, id="remote-binary")
                yield Label("Remote manager config")
                yield Label(
                    "YAML on the remote host. Init creates it if missing.",
                    classes="hint",
                )
                yield Input(
                    placeholder="/opt/hermes/managed/config.yaml",
                    id="remote-config",
                )
                yield LoadingIndicator(id="setup-loading")
                yield Label("", id="error")
            yield Button("Create SSH setup and initialize remote", id="ssh")
            yield Button("Create SSH setup, clone, and install CLI", id="ssh-clone")
            yield Button("Back", id="back-setup")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#setup-loading", LoadingIndicator).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-setup":
            self.dismiss(None)
            return
        if event.button.id == "ssh":
            self._start_ssh(install=False)
        elif event.button.id == "ssh-clone":
            self._start_ssh(install=True)

    def _start_ssh(self, install: bool) -> None:
        try:
            host = self._host()
            managed = self._path("local-managed-dir")
            self.config = self._path("local-config")
        except ValueError as error:
            self._error(error)
            return
        limit = INSTALL_TIMEOUT_SECONDS if install else SSH_TIMEOUT_SECONDS
        action = "Cloning and installing" if install else "Initializing remote"
        self.query_one("#setup-loading", LoadingIndicator).display = True
        self.query_one("#error", Label).update(f"{action} over SSH (up to {limit}s)...")
        for button in self.query(Button):
            button.disabled = True
        self.run_remote_setup(host, managed, install)

    @work(thread=True, exclusive=True, group="remote-setup", exit_on_error=False)
    def run_remote_setup(self, host: Host, managed: Path, install: bool) -> None:
        initialize_settings(self.config, managed, {host.alias: host})
        transport = SshTransport(host)
        if install:
            result = transport.install()
            upsert_host(self.config, replace(host, remote_binary=result["binary"]))
            return
        transport.init()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "remote-setup":
            return
        if event.state == WorkerState.ERROR:
            self.query_one("#setup-loading", LoadingIndicator).display = False
            for button in self.query(Button):
                button.disabled = False
            self._error(ValueError(str(event.worker.error)))
            return
        if event.state == WorkerState.SUCCESS:
            self.dismiss(self.config)

    def _host(self) -> Host:
        alias = self._value("host-alias")
        target = self._value("ssh-target")
        if not alias or not target:
            raise ValueError("SSH setup requires both host alias and SSH target")
        user, hostname, port = parse_ssh_target(target)
        managed_dir = self._path("remote-managed-dir")
        return Host(
            alias=alias,
            ssh_host=hostname,
            ssh_user=user,
            ssh_port=port,
            identity_file=None,
            remote_binary=normalize_remote_binary(self._value("remote-binary")),
            remote_config=self._path("remote-config"),
            managed_dir=managed_dir,
            profiles_dir=managed_dir / "profiles",
            fragments_dir=managed_dir / "fragments",
        )
