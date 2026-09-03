from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
)
from textual.worker import Worker, WorkerState

from hermes_profile.models import LocalLocation, Settings
from hermes_profile.paths import delete_location, load_settings, set_theme
from hermes_profile.profiles import list_profiles
from hermes_profile.themes import THEMES
from hermes_profile.transport import SSH_TIMEOUT_SECONDS, LocalTransport, SshTransport
from hermes_profile.tui.help import HelpScreen
from hermes_profile.tui.location_home import LocationHomeScreen
from hermes_profile.tui.location_setup import (
    AuthSyncScreen,
    ConfirmScreen,
    CreateProfileScreen,
    DeleteLocationScreen,
    LocalLocationScreen,
    LocationTypeScreen,
)
from hermes_profile.tui.ssh_setup import SshSetupScreen

STATUS_LABELS = {
    "config_drift": "Config",
    "env_drift": "Environment",
    "auth_inventory_changed": "Auth inventory",
}
WORKSPACE_ACTIONS = {
    "refresh",
    "preview",
    "preflight",
    "reconcile",
    "apply",
    "back",
    "create_profile",
    "auth",
    "init_remote",
}


def preview_kind(value: object) -> tuple[str, str]:
    if isinstance(value, dict):
        return "map", f"{len(value)} key(s)"
    if isinstance(value, list):
        return "list", f"{len(value)} item(s)"
    if isinstance(value, bool):
        return "bool", "true" if value else "false"
    if value is None:
        return "null", "—"
    if isinstance(value, (int, float)):
        return "number", str(value)
    if isinstance(value, str):
        text = value.replace("\n", " ")
        if len(text) > 40:
            text = f"{text[:37]}..."
        return "string", text
    return "value", "—"


def preview_rows(config: object) -> list[tuple[str, str, str]]:
    if not isinstance(config, dict) or not config:
        return [("—", "empty", "no config keys")]
    return [(key, *preview_kind(value)) for key, value in sorted(config.items())]


def format_preview(name: str, config: object, env_count: object) -> str:
    rows = preview_rows(config)
    key_width = max((len(key) for key, _, _ in rows), default=3)
    key_width = min(max(key_width, 8), 28)
    lines = [
        f"[b]{name}[/]  preview",
        "",
        f"{'Key':<{key_width}}  {'Kind':<6}  Contents",
        f"{'─' * key_width}  {'─' * 6}  {'─' * 8}",
    ]
    for key, kind, contents in rows:
        display = key if len(key) <= key_width else f"{key[: key_width - 1]}…"
        lines.append(f"{display:<{key_width}}  {kind:<6}  {contents}")
    lines.extend(
        [
            "",
            f"Environment: {env_count} variable(s), values redacted",
        ]
    )
    return "\n".join(lines)


def format_preflight(result: dict[str, Any]) -> str:
    config_diff = result.get("config_diff") or "No effective config changes."
    lines = ["[b]Preflight[/]", "", config_diff.rstrip(), ""]
    materialization = result.get("materialization_diff", "")
    if materialization:
        lines.extend(["[b]File materialization diff[/]", materialization.rstrip(), ""])
    if result.get("legacy_managed_layer"):
        lines.append("legacy managed layer: present")
    for key, label in (
        ("env_added", "Environment added"),
        ("env_changed", "Environment changed"),
        ("env_removed", "Environment removed"),
    ):
        names = result.get(key, [])
        lines.append(f"{label}: {', '.join(names) if names else 'none'}")
    return "\n".join(lines)


class ProfileTransport(Protocol):
    def profiles(self) -> list[str]: ...

    def status(self, name: str) -> dict[str, bool]: ...

    def action(self, name: str, action: str) -> dict[str, Any]: ...

    def create(self, name: str) -> None: ...

    def sync_auth(
        self, source: str, providers: list[str], allow_oauth: bool
    ) -> dict[str, Any]: ...


class ProfileApp(App[None]):
    """Host-aware dashboard for inspecting and applying profile state."""

    CSS = """
    Screen { background: $background; color: $foreground; }
    Header { background: $primary; color: $foreground; text-style: bold; }
    Footer { background: $secondary; color: $foreground; }
    FooterKey { background: $primary; color: $foreground; }
    #content { height: 1fr; padding: 0 1 0 1; }
    #workspace-bar { height: 1; margin: 1 0; }
    #back-locations {
        width: auto;
        min-height: 1;
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
        color: $foreground;
    }
    #summary { width: 1fr; height: 1; color: $secondary; }
    #loading { width: auto; height: 1; margin-left: 1; }
    #workspace-body { height: 1fr; layout: horizontal; }
    #profile-panel { width: 36; height: 100%; margin-right: 1; }
    #profiles-title { height: 1; color: $primary; text-style: bold; }
    #profiles {
        height: 1fr;
        border: round $secondary;
        background: $surface;
        padding: 0 1;
    }
    #profiles:focus { border: round $primary; }
    #add-profile {
        width: 100%;
        min-height: 1;
        height: 1;
        margin-top: 1;
        border: none;
        color: $primary;
        text-style: bold;
    }
    #detail {
        width: 1fr;
        height: 100%;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #profile-detail { height: 1fr; }
    ListView:focus { border: round $primary; }
    ListItem { padding: 0 1; height: 1; }
    ListItem:hover { background: $panel; }
    ListItem.--highlight {
        background: $panel;
        color: $foreground;
        text-style: bold;
    }
    #actions { height: 1; margin-top: 1; }
    #actions Button {
        min-height: 1;
        height: 1;
        margin-right: 1;
        border: none;
        padding: 0 1;
        background: $panel;
        color: $foreground;
        text-style: bold;
    }
    #back-locations:hover, #add-profile:hover, #actions Button:hover {
        background: $primary;
        color: $background;
    }
    #preview { color: $primary; }
    #preflight { color: $accent; }
    #reconcile { color: $warning; }
    #apply { color: $success; }
    #actions Button:disabled { color: $secondary; background: $surface; }
    .clean { color: $success; }
    .changed { color: $warning; }
    .selected { color: $primary; }
    """
    TITLE = "Hermes Profiles"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "back", "Locations", key_display="esc"),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "create_profile", "New"),
        Binding("p", "preview", "Preview"),
        Binding("f", "preflight", "Preflight"),
        Binding("c", "reconcile", "Reconcile"),
         Binding("a", "apply", "Apply"),
        Binding("u", "auth", "Auth"),
        Binding("i", "init_remote", "Init remote"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, settings: Settings, config: Path) -> None:
        super().__init__()
        self.settings = settings
        self.config = config
        self.transport: ProfileTransport = LocalTransport(settings)
        self.selected_host = "local"
        self.selected_profile: str | None = None
        self.profile_status: dict[str, dict[str, bool]] = {}
        self.location_title = "local"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="content"):
            with Horizontal(id="workspace-bar"):
                yield Button("← Locations", id="back-locations")
                yield Label("Open a location to manage its profiles.", id="summary")
                yield LoadingIndicator(id="loading")
            with Horizontal(id="workspace-body"):
                with Vertical(id="profile-panel"):
                    yield Label("Profiles", id="profiles-title")
                    yield ListView(id="profiles")
                    yield Button("New profile", id="add-profile")
                with Vertical(id="detail"):
                    yield Static(
                        "Pick a location, then select a profile.",
                        id="profile-detail",
                    )
                    with Horizontal(id="actions"):
                        yield Button("Preview", id="preview", disabled=True)
                        yield Button("Preflight", id="preflight", disabled=True)
                        yield Button("Reconcile", id="reconcile", disabled=True)
                        yield Button("Apply", id="apply", disabled=True)
                        yield Button("Auth", id="auth", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = self.settings.theme
        self.query_one("#loading", LoadingIndicator).display = False
        self.push_screen(LocationHomeScreen(self))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in WORKSPACE_ACTIONS and len(self.screen_stack) > 1:
            return False
        if action == "init_remote" and not self.selected_host.startswith("ssh--"):
            return False
        if (
            action in {"preview", "preflight", "reconcile", "apply", "auth"}
            and self.selected_profile is None
        ):
            return None
        return True

    def watch_theme(self, theme: str) -> None:
        if self.config.is_file():
            set_theme(self.config, theme)

    def action_refresh(self) -> None:
        self._set_busy(f"{self.location_title} · loading profiles...")
        self.load_profiles()

    def action_preview(self) -> None:
        self._start_action("render")

    def action_preflight(self) -> None:
        self._start_action("preflight")

    def action_reconcile(self) -> None:
        self._start_action("reconcile")

    def action_apply(self) -> None:
        if self.selected_profile is None:
            self.notify("Select a profile first.", severity="warning")
            return
        self.push_screen(
            ConfirmScreen(
                "Apply profile",
                f"Write rendered config and env for {self.selected_profile}?\n"
                "This overwrites config.yaml and .env when there is no drift.",
                "Apply",
            ),
            lambda confirmed: self._start_action("apply") if confirmed else None,
        )

    def action_auth(self) -> None:
        if self.selected_profile is None:
            self.notify("Select a source profile first.", severity="warning")
            return
        self.push_screen(
            AuthSyncScreen(self.selected_profile),
            self._auth_sync_requested,
        )

    def action_back(self) -> None:
        self.push_screen(LocationHomeScreen(self))

    def action_init_remote(self) -> None:
        self.confirm_init_remote(self.selected_host)

    def action_create_profile(self) -> None:
        self.push_screen(CreateProfileScreen(), self._profile_created)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-locations":
            self.action_back()
        elif event.button.id == "add-profile":
            self.action_create_profile()
        elif event.button.id == "preview":
            self.action_preview()
        elif event.button.id == "preflight":
            self.action_preflight()
        elif event.button.id == "reconcile":
            self.action_reconcile()
        elif event.button.id == "apply":
            self.action_apply()
        elif event.button.id == "auth":
            self.action_auth()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id is None or event.list_view.id != "profiles":
            return
        self.selected_profile = event.item.id
        self._set_actions(True)
        self._show_status(
            self.selected_profile, self.profile_status[self.selected_profile]
        )

    @work(thread=True, exclusive=True, group="profiles", exit_on_error=False)
    def load_profiles(self) -> list[tuple[str, dict[str, bool]]]:
        return [
            (name, self.transport.status(name)) for name in self.transport.profiles()
        ]

    @work(thread=True, exclusive=True, group="operation", exit_on_error=False)
    def run_profile_operation(
        self, name: str, action: str
    ) -> tuple[str, str, dict[str, Any]]:
        return name, action, self.transport.action(name, action)

    @work(thread=True, exclusive=True, group="auth", exit_on_error=False)
    def run_auth_sync(
        self, source: str, providers: list[str], allow_oauth: bool
    ) -> dict[str, Any]:
        return self.transport.sync_auth(source, providers, allow_oauth)

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group == "profiles":
            await self._handle_profile_load(event)
        elif event.worker.group == "operation":
            self._handle_operation(event)
        elif event.worker.group == "auth":
            self._handle_auth_sync(event)
        elif event.worker.group == "remote-init":
            self._handle_remote_init(event)

    async def _handle_profile_load(self, event: Worker.StateChanged) -> None:
        self._set_busy(None)
        if event.state == WorkerState.ERROR:
            await self.query_one("#profiles", ListView).clear()
            self._show_load_error(event.worker.error)
            return
        if event.state != WorkerState.SUCCESS:
            return
        profiles = self.query_one("#profiles", ListView)
        await profiles.clear()
        result = event.worker.result
        self.profile_status = dict(result)
        drifted = 0
        for name, current in result:
            dirty = any(current.values())
            drifted += int(dirty)
            state = "changed" if dirty else "clean"
            profiles.append(
                ListItem(Label(f"● {name}  {state}", classes=state), id=name)
            )
        self.query_one("#profiles-title", Label).update(f"Profiles ({len(result)})")
        if not result:
            self.selected_profile = None
            self._set_actions(False)
            self.query_one("#profile-detail", Static).update(
                f"No profiles in {self.location_title} yet.\n\n"
                "Press n or use New profile to create one."
            )
            self.query_one("#summary", Label).update(
                f"{self.location_title} · empty workspace"
            )
            return
        self.query_one("#summary", Label).update(
            f"{self.location_title} · {len(result)} profile(s)"
            + (f" · {drifted} drifted" if drifted else " · all clean")
        )
        if self.selected_profile in self.profile_status:
            self._show_status(
                self.selected_profile, self.profile_status[self.selected_profile]
            )
        else:
            self.selected_profile = None
            self._set_actions(False)
            self.query_one("#profile-detail", Static).update(
                "Select a profile to inspect it.\n\n"
                "Preview shows rendered config. Reconcile keeps runtime edits. "
                "Apply writes config.yaml and .env."
            )

    def _show_load_error(self, error: object) -> None:
        message = str(error)
        self.selected_profile = None
        self.profile_status = {}
        self._set_actions(False)
        self.query_one("#profiles-title", Label).update("Profiles")
        self.query_one("#summary", Label).update(f"{self.location_title} · unavailable")
        hint = "esc back to locations · r to retry"
        if self.selected_host.startswith("ssh--"):
            hint += " · i create remote dirs/config"
        self.query_one("#profile-detail", Static).update(
            f"[b]Could not open {self.location_title}[/]\n\n{message}\n\n[dim]{hint}[/]"
        )

    def _handle_operation(self, event: Worker.StateChanged) -> None:
        if event.state not in {WorkerState.ERROR, WorkerState.SUCCESS}:
            return
        self._set_busy(None)
        if event.state == WorkerState.ERROR:
            message = str(event.worker.error)
            self.query_one("#summary", Label).update(
                f"{self.location_title} · {message}"
            )
            self.query_one("#profile-detail", Static).update(
                f"[b]Action failed[/]\n\n{message}\n\n[dim]r to retry[/]"
            )
            return
        name, action, result = event.worker.result
        if action == "render":
            self.query_one("#profile-detail", Static).update(
                format_preview(
                    name,
                    result.get("config"),
                    result.get("environment_count", 0),
                )
            )
            self.query_one("#summary", Label).update(f"{name} · preview")
            self.notify(f"Previewed {name}")
            return
        if action == "preflight":
            self.query_one("#profile-detail", Static).update(format_preflight(result))
            self.query_one("#summary", Label).update(f"{name} · preflight")
            self.notify(f"Preflighted {name}")
            return
        self.notify(f"{name}: {action} completed")
        self.query_one("#summary", Label).update(f"{name}: {action} completed")
        self.action_refresh()

    def _handle_auth_sync(self, event: Worker.StateChanged) -> None:
        if event.state not in {WorkerState.ERROR, WorkerState.SUCCESS}:
            return
        self._set_busy(None)
        if event.state == WorkerState.ERROR:
            self.notify(str(event.worker.error), severity="error")
            return
        result = event.worker.result
        providers = ", ".join(result["providers"])
        self.query_one("#summary", Label).update(f"Shared auth synced: {providers}")
        self.query_one("#profile-detail", Static).update(
            f"[b]Shared auth updated[/]\n\nProviders: {providers}\n"
            f"Store: {result['path']}\n\n"
            "Existing profile-local provider records still take precedence."
        )
        self.notify("Shared auth synced")

    def _start_action(self, action: str) -> None:
        if self.selected_profile is None:
            self.notify("Select a profile first.", severity="warning")
            return
        remote = self.selected_host.startswith("ssh--")
        via = " over SSH" if remote else ""
        hint = (
            f"\n\n[dim]Remote commands wait up to {SSH_TIMEOUT_SECONDS}s "
            "before timing out.[/]"
            if remote
            else ""
        )
        self._set_busy(
            f"{self.selected_profile}: {action} in progress...",
            f"[b]{self.selected_profile}[/]  {action}\n\nWorking{via}...{hint}",
        )
        self.run_profile_operation(self.selected_profile, action)

    def _set_busy(self, summary: str | None, detail: str | None = None) -> None:
        self.query_one("#loading", LoadingIndicator).display = (
            summary is not None and self.settings.animations
        )
        if summary is not None:
            self.query_one("#summary", Label).update(summary)
        if detail is not None:
            self.query_one("#profile-detail", Static).update(detail)

    def _show_status(self, name: str, current: dict[str, bool]) -> None:
        lines = [f"[b]{name}[/]", ""]
        for key, value in current.items():
            state = "changed" if value else "clean"
            color = "warning" if value else "success"
            label = STATUS_LABELS.get(key, key)
            lines.append(f"[{color}]●[/{color}] {label}  {state}")
        if any(current.values()):
            lines.extend(
                [
                    "",
                    "Runtime files differ from the last apply.",
                    "Reconcile keeps those edits, then Apply writes the result.",
                ]
            )
        else:
            lines.extend(["", "Clean. Preview the render, or Apply to write files."])
        self.query_one("#profile-detail", Static).update("\n".join(lines))

    def _set_actions(self, enabled: bool) -> None:
        for button in self.query("#actions Button"):
            button.disabled = not enabled
        self.refresh_bindings()

    def _profile_created(self, name: str | None) -> None:
        if not name:
            return
        try:
            self.transport.create(name)
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        self.selected_profile = name
        self.notify(f"Created {name}")
        self.action_refresh()

    def _auth_sync_requested(self, request: tuple[list[str], bool] | None) -> None:
        if request is None or self.selected_profile is None:
            return
        providers, allow_oauth = request
        if allow_oauth:
            self.push_screen(
                ConfirmScreen(
                    "Sync OAuth credentials",
                    "Copy selected OAuth providers to the shared fallback?\n"
                    "Existing local providers will continue to shadow it.",
                    "Sync",
                    danger=True,
                ),
                lambda confirmed: self._start_auth_sync(providers, allow_oauth)
                if confirmed
                else None,
            )
            return
        self._start_auth_sync(providers, allow_oauth)

    def _start_auth_sync(self, providers: list[str], allow_oauth: bool) -> None:
        if self.selected_profile is None:
            return
        self._set_busy(
            f"{self.selected_profile}: syncing shared auth...",
            "[b]Shared auth[/]\n\nSyncing selected provider records...",
        )
        self.run_auth_sync(self.selected_profile, providers, allow_oauth)

    def edit_location(self, identifier: str, callback: Any) -> None:
        if identifier == "local":
            self.push_screen(
                LocalLocationScreen(
                    self.config,
                    LocalLocation(
                        "local",
                        self.settings.managed_dir,
                        self.settings.profiles_dir,
                        self.settings.fragments_dir,
                    ),
                    primary=True,
                ),
                lambda saved: self._location_saved_then(saved, callback),
            )
            return
        kind, alias = identifier.split("--", 1)
        if kind == "ssh":
            self.push_screen(
                SshSetupScreen(self.config, self.settings.hosts[alias]),
                lambda saved: self._location_saved_then(saved, callback),
            )
            return
        self.push_screen(
            LocalLocationScreen(self.config, self.settings.local_locations[alias]),
            lambda saved: self._location_saved_then(saved, callback),
        )

    def push_location_type(self, callback: Any) -> None:
        self.push_screen(
            LocationTypeScreen(),
            lambda kind: self._location_type_selected(kind, callback),
        )

    def _location_type_selected(self, kind: str | None, callback: Any) -> None:
        if kind == "local":
            self.push_screen(
                LocalLocationScreen(self.config),
                lambda saved: self._location_saved_then(saved, callback),
            )
        elif kind == "ssh":
            self.push_screen(
                SshSetupScreen(self.config),
                lambda saved: self._location_saved_then(saved, callback),
            )

    def _location_saved_then(self, saved: bool | None, callback: Any) -> None:
        self._location_saved(saved)
        callback(saved)

    def _location_saved(self, saved: bool | None) -> None:
        if saved:
            self.settings = load_settings(str(self.config))
            self.notify("Location saved")

    def location_items(self) -> list[tuple[str, str, str]]:
        items = [("local", "local", "this machine")]
        items.extend(
            (f"local--{alias}", alias, "local folder")
            for alias in sorted(self.settings.local_locations)
        )
        items.extend(
            (f"ssh--{alias}", alias, "ssh") for alias in sorted(self.settings.hosts)
        )
        return items

    def location_summary(self, identifier: str) -> tuple[str, str, str]:
        if identifier == "local":
            count = len(list_profiles(self.settings))
            return (
                "local",
                "This machine",
                f"{self.settings.profiles_dir}\n\n"
                f"{count} profile(s) in the primary workspace.\n"
                "Press Enter to open.",
            )
        kind, alias = identifier.split("--", 1)
        if kind == "local":
            location = self.settings.local_locations[alias]
            count = len(
                list_profiles(
                    replace(
                        self.settings,
                        managed_dir=location.managed_dir,
                        profiles_dir=location.profiles_dir,
                        fragments_dir=location.fragments_dir,
                    )
                )
            )
            return (
                alias,
                "Local folder",
                f"{location.profiles_dir}\n\n"
                f"{count} profile(s) in this folder.\n"
                "Press Enter to open.",
            )
        host = self.settings.hosts[alias]
        target = (
            host.ssh_host if not host.ssh_user else f"{host.ssh_user}@{host.ssh_host}"
        )
        if host.ssh_port:
            target = f"{target} -p {host.ssh_port}"
        return (
            alias,
            "SSH host",
            f"{target}\nprofiles: {host.profiles_dir}\n"
            f"config: {host.remote_config}\n\n"
            "e edits paths. i creates remote dirs/config if missing.\n"
            "Press Enter to open.",
        )

    def open_location(self, identifier: str) -> None:
        title, kind, _detail = self.location_summary(identifier)
        self.selected_host = identifier
        self.selected_profile = None
        self.profile_status = {}
        self.location_title = title
        self.sub_title = f"{title} · {kind}"
        self.transport = self._transport_for(identifier)
        self._set_actions(False)
        self.query_one("#profile-detail", Static).update(
            f"Loading profiles from {title}..."
        )
        self.action_refresh()

    def confirm_init_remote(self, identifier: str) -> None:
        if not identifier.startswith("ssh--"):
            self.notify("Remote init is only for SSH locations.", severity="warning")
            return
        _, alias = identifier.split("--", 1)
        host = self.settings.hosts[alias]
        self.push_screen(
            ConfirmScreen(
                "Initialize remote layout",
                f"Create missing directories and config on {alias}.\n"
                f"{host.remote_config}\n\n"
                "Existing files are left alone. "
                "This does not install the hermes-profile binary.",
                "Initialize",
            ),
            lambda confirmed: self._start_remote_init(alias) if confirmed else None,
        )

    def _start_remote_init(self, alias: str) -> None:
        host = self.settings.hosts[alias]
        self._set_busy(
            f"{alias}: initializing remote...",
            f"[b]Initializing {alias}[/]\n\n"
            f"Creating {host.remote_config} and managed dirs over SSH.\n"
            "This does not install hermes-profile.\n\n"
            f"[dim]Remote commands wait up to {SSH_TIMEOUT_SECONDS}s "
            "before timing out.[/]",
        )
        self.run_remote_init(alias)

    @work(thread=True, exclusive=True, group="remote-init", exit_on_error=False)
    def run_remote_init(self, alias: str) -> str:
        SshTransport(self.settings.hosts[alias]).init()
        return alias

    def _handle_remote_init(self, event: Worker.StateChanged) -> None:
        self._set_busy(None)
        if event.state == WorkerState.ERROR:
            message = str(event.worker.error)
            self.query_one("#summary", Label).update(
                f"{self.location_title} · init failed"
            )
            self.query_one("#profile-detail", Static).update(
                f"[b]Remote init failed[/]\n\n{message}\n\n"
                "[dim]i to retry init · r to open profiles · esc back[/]"
            )
            return
        if event.state != WorkerState.SUCCESS:
            return
        alias = event.worker.result
        host = self.settings.hosts[alias]
        self.query_one("#summary", Label).update(f"{alias} · remote layout ready")
        self.query_one("#profile-detail", Static).update(
            f"[b]Remote layout ready[/]\n\n"
            f"Dirs and config are in place at {host.remote_config}.\n\n"
            f"Apply/Reconcile still need hermes-profile on {alias}.\n"
            "List and Preview work without that CLI.\n"
            "[dim]r to retry opening profiles[/]"
        )

    def confirm_delete_location(self, identifier: str, callback: Any) -> None:
        if identifier == "local":
            self.notify(
                "The default local location cannot be removed.",
                severity="warning",
            )
            callback(False)
            return
        self.push_screen(
            DeleteLocationScreen(identifier),
            lambda confirmed: self._delete_location_confirmed(
                confirmed, identifier, callback
            ),
        )

    def _delete_location_confirmed(
        self, confirmed: bool, identifier: str, callback: Any
    ) -> None:
        if not confirmed or identifier == "local":
            callback(False)
            return
        kind, alias = identifier.split("--", 1)
        try:
            delete_location(self.config, kind, alias)
        except ValueError as error:
            self.notify(str(error), severity="error")
            callback(False)
            return
        self.settings = load_settings(str(self.config))
        if self.selected_host == identifier:
            self.selected_host = "local"
            self.transport = LocalTransport(self.settings)
        self.notify(f"Removed {alias}")
        callback(True)

    def _transport_for(self, location: str) -> ProfileTransport:
        if location == "local":
            return LocalTransport(self.settings)
        kind, alias = location.split("--", 1)
        if kind == "local":
            item = self.settings.local_locations[alias]
            return LocalTransport(
                replace(
                    self.settings,
                    managed_dir=item.managed_dir,
                    profiles_dir=item.profiles_dir,
                    fragments_dir=item.fragments_dir,
                )
            )
        return SshTransport(self.settings.hosts[alias])
