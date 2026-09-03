from dataclasses import replace
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, LoadingIndicator
from textual.worker import Worker, WorkerState

from hermes_profile.models import Host
from hermes_profile.paths import upsert_host
from hermes_profile.transport import (
    DEFAULT_REMOTE_BINARY,
    SshTransport,
    normalize_remote_binary,
    parse_ssh_target,
)


class SshSetupScreen(ModalScreen[bool]):
    """Add or edit a remote Hermes manager host without exposing credentials."""

    CSS = """
    SshSetupScreen { align: center middle; background: $background 70%; }
    #ssh-dialog {
        width: 82;
        height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #ssh-title { text-style: bold; color: $primary; height: 1; }
    #ssh-subtitle, .hint { color: $secondary; margin-bottom: 1; }
    #ssh-fields { height: 1fr; }
    #ssh-error { color: $error; height: 2; }
    #ssh-loading { height: 1; margin: 1 0; }
    Input { margin: 1 0; border: round $secondary; }
    Input:focus { border: round $accent; }
    #ssh-actions { height: auto; margin-top: 1; }
    #ssh-actions Button {
        min-height: 1;
        height: 3;
        margin-right: 1;
        border: none;
        background: $panel;
        text-style: bold;
        padding: 0 2;
    }
    #save-ssh { color: $success; }
    #init-ssh { color: $primary; }
    #install-ssh { color: $accent; }
    #cancel-ssh { color: $foreground; }
    #ssh-actions Button:hover { background: $primary; color: $background; }
    """

    def __init__(self, config: Path, host: Host | None = None) -> None:
        super().__init__()
        self.config = config
        self.host = host

    def compose(self) -> ComposeResult:
        editing = self.host is not None
        with Vertical(id="ssh-dialog"):
            yield Label(
                "Edit remote host" if editing else "Add a remote host",
                id="ssh-title",
            )
            yield Label(
                "Uses your existing SSH agent and keys. No passwords are stored.",
                id="ssh-subtitle",
            )
            with VerticalScroll(id="ssh-fields"):
                yield Label("Location alias")
                yield Label("Short TUI name, e.g. gateway-a.", classes="hint")
                yield Input(
                    value=self.host.alias if self.host else "",
                    placeholder="gateway-a",
                    id="host-alias",
                    disabled=editing,
                )
                yield Label("SSH target")
                yield Label("user@host, optional -p PORT.", classes="hint")
                yield Input(
                    value=self._target_value(),
                    placeholder="deploy@gateway.example -p 22",
                    id="ssh-target",
                )
                yield Label("SSH port")
                yield Input(
                    value=(
                        str(self.host.ssh_port)
                        if self.host is not None and self.host.ssh_port
                        else ""
                    ),
                    placeholder="22, or set with -p in the target",
                    id="ssh-port",
                )
                yield Label("Remote managed directory")
                yield Label("Operational root on the remote host.", classes="hint")
                yield Input(
                    value=str(self.host.managed_dir) if self.host else "",
                    placeholder="/opt/hermes/managed",
                    id="remote-managed-dir",
                )
                yield Label("Remote profiles directory")
                yield Input(
                    value=str(self.host.profiles_dir) if self.host else "",
                    placeholder="/opt/hermes/profiles",
                    id="remote-profiles-dir",
                )
                yield Label("Remote fragments directory")
                yield Input(
                    value=str(self.host.fragments_dir) if self.host else "",
                    placeholder="/opt/hermes/managed/fragments",
                    id="remote-fragments-dir",
                )
                yield Label("Remote manager CLI (optional, not the hermes agent)")
                yield Label(
                    "hermes-profile binary or PATH name. Not the hermes agent.",
                    classes="hint",
                )
                yield Input(
                    value=(
                        self.host.remote_binary if self.host else DEFAULT_REMOTE_BINARY
                    ),
                    placeholder="hermes-profile",
                    id="remote-binary",
                )
                yield Label("Remote manager config")
                yield Input(
                    value=str(self.host.remote_config) if self.host else "",
                    placeholder="/opt/hermes/managed/config.yaml",
                    id="remote-config",
                )
                yield LoadingIndicator(id="ssh-loading")
                yield Label("", id="ssh-error")
            with Horizontal(id="ssh-actions"):
                yield Button("Save host", id="save-ssh")
                yield Button("Init dirs", id="init-ssh")
                yield Button("Clone + install", id="install-ssh")
                yield Button("Cancel", id="cancel-ssh")

    def on_mount(self) -> None:
        self.query_one("#ssh-loading", LoadingIndicator).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-ssh":
            self.dismiss(False)
            return
        try:
            host = self._host()
        except ValueError as error:
            self.query_one("#ssh-error", Label).update(str(error))
            return
        if event.button.id == "save-ssh":
            upsert_host(self.config, host)
            self.dismiss(True)
            return
        if event.button.id not in {"init-ssh", "install-ssh"}:
            return
        install = event.button.id == "install-ssh"
        action = "Cloning and installing" if install else "Initializing remote"
        self.query_one("#ssh-loading", LoadingIndicator).display = True
        self.query_one("#ssh-error", Label).update(f"{action} over SSH...")
        for button in self.query(Button):
            button.disabled = True
        self.run_ssh_action(host, install)

    @work(thread=True, exclusive=True, group="ssh-action", exit_on_error=False)
    def run_ssh_action(self, host: Host, install: bool) -> None:
        upsert_host(self.config, host)
        transport = SshTransport(host)
        if install:
            result = transport.install()
            upsert_host(self.config, replace(host, remote_binary=result["binary"]))
            return
        transport.init()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "ssh-action":
            return
        if event.state == WorkerState.ERROR:
            self.query_one("#ssh-loading", LoadingIndicator).display = False
            for button in self.query(Button):
                button.disabled = False
            self.query_one("#ssh-error", Label).update(str(event.worker.error))
            return
        if event.state == WorkerState.SUCCESS:
            self.dismiss(True)

    def _target_value(self) -> str:
        if self.host is None:
            return ""
        if self.host.ssh_user:
            return f"{self.host.ssh_user}@{self.host.ssh_host}"
        return self.host.ssh_host

    def _host(self) -> Host:
        alias = self._value("host-alias")
        target = self._value("ssh-target")
        if not alias or not target:
            raise ValueError("SSH setup needs both an alias and an SSH target")
        if not self._value("remote-managed-dir"):
            raise ValueError("Remote managed directory is required")
        if not self._value("remote-config"):
            raise ValueError("Remote manager config path is required")
        user, hostname, port = parse_ssh_target(target)
        port_text = self._value("ssh-port")
        if port_text:
            if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
                raise ValueError("SSH port must be between 1 and 65535")
            port = int(port_text)
        managed_dir = Path(self._value("remote-managed-dir"))
        profiles = self._value("remote-profiles-dir")
        fragments = self._value("remote-fragments-dir")
        return Host(
            alias=alias,
            ssh_host=hostname,
            ssh_user=user,
            ssh_port=port,
            identity_file=None,
            remote_binary=normalize_remote_binary(self._value("remote-binary")),
            remote_config=Path(self._value("remote-config")),
            managed_dir=managed_dir,
            profiles_dir=Path(profiles) if profiles else managed_dir / "profiles",
            fragments_dir=Path(fragments) if fragments else managed_dir / "fragments",
        )

    def _value(self, identifier: str) -> str:
        return self.query_one(f"#{identifier}", Input).value.strip()
