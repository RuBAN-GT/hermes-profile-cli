from dataclasses import replace
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Input, Label

from hermes_profile.models import Host
from hermes_profile.paths import initialize_settings, upsert_host
from hermes_profile.transport import (
    DEFAULT_REMOTE_BINARY,
    SshTransport,
    normalize_remote_binary,
    parse_ssh_target,
)


class SetupApp(App[None]):
    """First-run setup for a local or SSH-managed Hermes installation."""

    CSS = """
    Screen { align: center middle; background: #282a36; color: #f8f8f2; }
    Footer { background: #44475a; color: #f8f8f2; }
    FooterKey { background: #6272a4; color: #f8f8f2; }
    #setup {
        width: 84;
        height: auto;
        padding: 2;
        border: tall #bd93f9;
        background: #21222c;
    }
    #error { color: #ff5555; height: 3; }
    Input { margin: 1 0; border: tall #6272a4; background: #282a36; color: #f8f8f2; }
    Input:focus { border: tall #8be9fd; }
    Button {
        margin-right: 1;
        border: tall #6272a4;
        background: #44475a;
        color: #f8f8f2;
        text-style: bold;
    }
    Button:hover { background: #bd93f9; color: #282a36; }
    #local { border: tall #50fa7b; color: #50fa7b; }
    #ssh { border: tall #8be9fd; color: #8be9fd; }
    #ssh-clone { border: tall #ff79c6; color: #ff79c6; }
    #local:hover { background: #50fa7b; color: #282a36; }
    #ssh:hover { background: #8be9fd; color: #282a36; }
    #ssh-clone:hover { background: #ff79c6; color: #282a36; }
    """
    TITLE = "Hermes Profile Setup"

    def __init__(self, config: Path) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        local_root = Path("~/.local/share/hermes-profile/managed").expanduser()
        with Vertical(id="setup"):
            yield Label("Set up Hermes Profile Manager")
            yield Label(f"Local configuration: {self.config}")
            yield Label(
                "Local operational directory (required for local manager state):"
            )
            yield Input(value=str(local_root), id="local-managed-dir")
            yield Label("SSH setup (fill these fields only for a remote-first setup):")
            yield Input(placeholder="gateway-a", id="host-alias")
            yield Input(
                placeholder="deploy@gateway.example -p 22",
                id="ssh-target",
            )
            yield Input(
                placeholder="/opt/hermes/managed",
                id="remote-managed-dir",
            )
            yield Label("Remote manager CLI (optional, not the hermes agent)")
            yield Input(value=DEFAULT_REMOTE_BINARY, id="remote-binary")
            yield Input(
                placeholder="/opt/hermes/managed/config.yaml",
                id="remote-config",
            )
            yield Label("", id="error")
            yield Button("Create local setup", variant="primary", id="local")
            yield Button("Create SSH setup and initialize remote", id="ssh")
            yield Button("Create SSH setup, clone, and install CLI", id="ssh-clone")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "local":
            self._initialize_local()
        elif event.button.id == "ssh":
            self._initialize_ssh(install=False)
        elif event.button.id == "ssh-clone":
            self._initialize_ssh(install=True)

    def _initialize_local(self) -> None:
        try:
            initialize_settings(self.config, self._path("local-managed-dir"))
        except ValueError as error:
            self._error(error)
            return
        self.exit()

    def _initialize_ssh(self, install: bool) -> None:
        try:
            host = self._host()
            initialize_settings(
                self.config, self._path("local-managed-dir"), {host.alias: host}
            )
            transport = SshTransport(host)
            if install:
                result = transport.install()
                upsert_host(self.config, replace(host, remote_binary=result["binary"]))
            else:
                transport.init()
        except ValueError as error:
            self._error(error)
            return
        self.exit()

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

    def _path(self, identifier: str) -> Path:
        return Path(self._value(identifier)).expanduser()

    def _value(self, identifier: str) -> str:
        return self.query_one(f"#{identifier}", Input).value.strip()

    def _error(self, error: ValueError) -> None:
        self.query_one("#error", Label).update(str(error))
