from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

import yaml
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

from hermes_profile.auth_adapters import (
    export_auth,
    import_auth,
    list_sources,
    push_auth,
)
from hermes_profile.i18n import language, next_language, set_language, t
from hermes_profile.models import LocalLocation, Settings
from hermes_profile.paths import (
    delete_location,
    load_settings,
    save_language,
    set_theme,
)
from hermes_profile.profiles import list_profiles
from hermes_profile.themes import apply_hermes_themes, next_theme
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
from hermes_profile.tui.menus import (
    AuthHubScreen,
    AuthTransferScreen,
    BackupScreen,
    MoreActionsScreen,
)
from hermes_profile.tui.ssh_setup import SshSetupScreen


def status_labels() -> dict[str, str]:
    return {
        "config_drift": t("status_config"),
        "env_drift": t("status_env"),
        "auth_inventory_changed": t("status_auth"),
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
    "more",
    "backup",
    "delete_profile",
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
        f"[b]{t('preview_title', name=name)}[/]",
        "",
        (
            f"{t('preview_key'):<{key_width}}  "
            f"{t('preview_kind'):<6}  {t('preview_contents')}"
        ),
        f"{'─' * key_width}  {'─' * 6}  {'─' * 8}",
    ]
    for key, kind, contents in rows:
        display = key if len(key) <= key_width else f"{key[: key_width - 1]}…"
        lines.append(f"{display:<{key_width}}  {kind:<6}  {contents}")
    lines.extend(
        [
            "",
            t("preview_env", count=env_count),
        ]
    )
    return "\n".join(lines)


def format_preflight(result: dict[str, Any]) -> str:
    config_diff = result.get("config_diff") or t("no_config_changes")
    lines = [f"[b]{t('preflight_title')}[/]", "", config_diff.rstrip(), ""]
    materialization = result.get("materialization_diff", "")
    if materialization:
        lines.extend([f"[b]{t('file_diff')}[/]", materialization.rstrip(), ""])
    if result.get("legacy_managed_layer"):
        lines.append(t("legacy_layer"))
    for key, label_key in (
        ("env_added", "env_added"),
        ("env_changed", "env_changed"),
        ("env_removed", "env_removed"),
    ):
        names = result.get(key, [])
        lines.append(f"{t(label_key)}: {', '.join(names) if names else t('none')}")
    bindings = result.get("bindings")
    if isinstance(bindings, list):
        if not bindings:
            lines.append(t("auth_bindings_none"))
        for item in bindings:
            if not isinstance(item, dict):
                continue
            lines.append(
                t(
                    "auth_binding",
                    provider=item.get("provider"),
                    target=item.get("target"),
                )
            )
    return "\n".join(lines)


class ProfileTransport(Protocol):
    def profiles(self) -> list[str]: ...

    def status(self, name: str) -> dict[str, bool]: ...

    def action(self, name: str, action: str) -> dict[str, Any]: ...

    def create(self, name: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def bind_auth(self, name: str, *, force: bool = False) -> dict[str, Any]: ...

    def auth_map_status(self) -> dict[str, Any]: ...

    def shared_status(self) -> dict[str, Any]: ...

    def backup(self, action: str, name: str | None = None) -> dict[str, Any]: ...

    def sync_auth(
        self, source: str, providers: list[str], allow_oauth: bool
    ) -> dict[str, Any]: ...


class ProfileApp(App[None]):
    """Host-aware dashboard for inspecting and applying profile state."""

    CSS = """
    Screen { background: $background; color: $foreground; }
    Header { background: $primary; color: $text; text-style: bold; }
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
        color: $text;
    }
    #more { color: $text-muted; }
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
        Binding("a", "apply", "Apply"),
        Binding("u", "auth", "Auth"),
        Binding("m", "more", "More"),
        Binding("ctrl+t", "cycle_theme", "Theme"),
        Binding("ctrl+l", "cycle_language", "Lang"),
        Binding("c", "reconcile", "Reconcile", show=False),
        Binding("b", "backup", "Backup", show=False),
        Binding("d", "delete_profile", "Delete", show=False),
        Binding("i", "init_remote", "Init remote", show=False),
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
                yield Button(t("locations"), id="back-locations")
                yield Label(t("summary_open"), id="summary")
                yield LoadingIndicator(id="loading")
            with Horizontal(id="workspace-body"):
                with Vertical(id="profile-panel"):
                    yield Label(t("profiles"), id="profiles-title")
                    yield ListView(id="profiles")
                    yield Button(t("new_profile"), id="add-profile")
                with Vertical(id="detail"):
                    yield Static(t("pick_location"), id="profile-detail")
                    with Horizontal(id="actions"):
                        yield Button(
                            t("preview"),
                            id="preview",
                            disabled=True,
                            tooltip=t("tooltip_preview"),
                        )
                        yield Button(
                            t("preflight"),
                            id="preflight",
                            disabled=True,
                            tooltip=t("tooltip_preflight"),
                        )
                        yield Button(
                            t("reconcile"),
                            id="reconcile",
                            disabled=True,
                            tooltip=t("tooltip_reconcile"),
                        )
                        yield Button(
                            t("apply"),
                            id="apply",
                            disabled=True,
                            tooltip=t("tooltip_apply"),
                        )
                        yield Button(
                            t("auth"),
                            id="auth",
                            disabled=True,
                            tooltip=t("tooltip_auth"),
                        )
                        yield Button(
                            t("more"),
                            id="more",
                            disabled=True,
                            tooltip=t("tooltip_more"),
                        )
        yield Footer()

    def on_mount(self) -> None:
        set_language(self.settings.language)
        apply_hermes_themes(self, self.settings.theme)
        self.query_one("#loading", LoadingIndicator).display = False
        self._relabel_workspace()
        self.push_screen(LocationHomeScreen(self))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in WORKSPACE_ACTIONS and len(self.screen_stack) > 1:
            return False
        if action == "init_remote" and not self.selected_host.startswith("ssh--"):
            return False
        if (
            action
            in {
                "preview",
                "preflight",
                "reconcile",
                "apply",
                "auth",
                "more",
                "delete_profile",
            }
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
            self.notify(t("select_profile_first"), severity="warning")
            return
        self.push_screen(
            ConfirmScreen(
                t("apply_profile"),
                t("apply_body", name=self.selected_profile),
                t("apply"),
            ),
            lambda confirmed: self._start_action("apply") if confirmed else None,
        )

    def action_cycle_theme(self) -> None:
        self.theme = next_theme(self.theme)
        self.notify(t("theme_set", theme=self.theme))

    def action_cycle_language(self) -> None:
        lang = set_language(next_language(language()))
        save_language(self.config, lang)
        self._relabel_workspace()
        screen = self.screen
        if isinstance(screen, LocationHomeScreen):
            screen.relabel()
        self.notify(t("language_set", language=lang.upper()))

    def _relabel_workspace(self) -> None:
        self.query_one("#back-locations", Button).label = t("locations")
        self.query_one("#summary", Label).update(t("summary_open"))
        self.query_one("#profiles-title", Label).update(t("profiles"))
        self.query_one("#add-profile", Button).label = t("new_profile")
        self.query_one("#preview", Button).label = t("preview")
        self.query_one("#preflight", Button).label = t("preflight")
        self.query_one("#reconcile", Button).label = t("reconcile")
        self.query_one("#apply", Button).label = t("apply")
        self.query_one("#auth", Button).label = t("auth")
        self.query_one("#more", Button).label = t("more")
        for button_id, tip in (
            ("preview", "tooltip_preview"),
            ("preflight", "tooltip_preflight"),
            ("reconcile", "tooltip_reconcile"),
            ("apply", "tooltip_apply"),
            ("auth", "tooltip_auth"),
            ("more", "tooltip_more"),
        ):
            self.query_one(f"#{button_id}", Button).tooltip = t(tip)

    def action_auth(self) -> None:
        if self.selected_profile is None:
            self.notify(t("select_profile_first"), severity="warning")
            return
        self.push_screen(AuthHubScreen(), self._auth_hub_chosen)

    def action_more(self) -> None:
        if self.selected_profile is None:
            self.notify(t("select_profile_first"), severity="warning")
            return
        self.push_screen(MoreActionsScreen(), self._more_chosen)

    def action_backup(self) -> None:
        self._set_busy(f"{self.location_title}: listing backups...")
        self.run_extra("Backups", "backup-list", {})

    def action_delete_profile(self) -> None:
        if self.selected_profile is None:
            self.notify(t("select_profile_first"), severity="warning")
            return
        name = self.selected_profile
        self.push_screen(
            ConfirmScreen(
                t("delete_profile"),
                t("delete_profile_body", name=name),
                t("delete"),
                danger=True,
            ),
            lambda confirmed: self._start_delete_profile(name) if confirmed else None,
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
        elif event.button.id == "more":
            self.action_more()

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
        elif event.worker.group == "extra":
            self._handle_extra(event)
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
                ListItem(Label(f"● {name}  {t(state)}", classes=state), id=name)
            )
        self.query_one("#profiles-title", Label).update(
            t("profiles_n", count=len(result))
        )
        if not result:
            self.selected_profile = None
            self._set_actions(False)
            self.query_one("#profile-detail", Static).update(
                t("no_profiles", location=self.location_title)
            )
            self.query_one("#summary", Label).update(
                t("empty_workspace", location=self.location_title)
            )
            return
        drift = t("drifted", count=drifted) if drifted else t("all_clean")
        self.query_one("#summary", Label).update(
            t(
                "summary_profiles",
                location=self.location_title,
                count=len(result),
                drift=drift,
            )
        )
        if self.selected_profile in self.profile_status:
            self._show_status(
                self.selected_profile, self.profile_status[self.selected_profile]
            )
        else:
            self.selected_profile = None
            self._set_actions(False)
            self.query_one("#profile-detail", Static).update(t("select_profile"))

    def _show_load_error(self, error: object) -> None:
        message = str(error)
        self.selected_profile = None
        self.profile_status = {}
        self._set_actions(False)
        self.query_one("#profiles-title", Label).update(t("profiles"))
        self.query_one("#summary", Label).update(
            t("unavailable", location=self.location_title)
        )
        hint = t("hint_retry")
        if self.selected_host.startswith("ssh--"):
            hint += t("hint_init")
        self.query_one("#profile-detail", Static).update(
            t(
                "could_not_open",
                location=self.location_title,
                message=message,
                hint=hint,
            )
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
            self.notify(t("select_profile_first"), severity="warning")
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
            label = status_labels().get(key, key)
            lines.append(f"[{color}]●[/{color}] {label}  {t(state)}")
        if any(current.values()):
            lines.extend(["", t("runtime_differs")])
        else:
            lines.extend(["", t("status_clean")])
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
                    t("sync_oauth"),
                    t("sync_oauth_body"),
                    t("sync"),
                    danger=True,
                ),
                lambda confirmed: (
                    self._start_auth_sync(providers, allow_oauth) if confirmed else None
                ),
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

    def _auth_hub_chosen(self, action: str | None) -> None:
        if action is None:
            return
        if action == "sync":
            if self.selected_profile is None:
                return
            self.push_screen(
                AuthSyncScreen(self.selected_profile),
                self._auth_sync_requested,
            )
            return
        if action == "bind":
            self._start_bind()
            return
        if action in {"shared-status", "map-status"}:
            title = "Shared auth" if action == "shared-status" else "Auth map"
            self._set_busy(f"{self.location_title}: {title}...")
            self.run_extra(title, action, {})
            return
        if action in {"import", "export", "push", "sources"}:
            self.push_screen(
                AuthTransferScreen(action, sorted(self.settings.hosts)),
                self._auth_transfer_requested,
            )

    def _more_chosen(self, action: str | None) -> None:
        if action == "reconcile":
            self.action_reconcile()
        elif action == "discard":
            self._confirm_discard_apply()
        elif action == "bind":
            self._start_bind()
        elif action == "backup":
            self.action_backup()
        elif action == "delete":
            self.action_delete_profile()

    def _confirm_discard_apply(self) -> None:
        if self.selected_profile is None:
            return
        self.push_screen(
            ConfirmScreen(
                t("discard_apply"),
                t("discard_body", name=self.selected_profile),
                t("discard_confirm"),
                danger=True,
            ),
            lambda confirmed: (
                self._start_action("apply-discard") if confirmed else None
            ),
        )

    def _start_bind(self) -> None:
        if self.selected_profile is None:
            return
        self._set_busy(
            f"{self.selected_profile}: binding auth...",
            "[b]Auth bind[/]\n\nAttaching mapped identity stores...",
        )
        self.run_extra("Auth bind", "bind", {"name": self.selected_profile})

    def _start_delete_profile(self, name: str) -> None:
        self._set_busy(f"{name}: deleting...")
        self.run_extra("Delete profile", "delete", {"name": name})

    def _auth_transfer_requested(self, payload: dict[str, object] | None) -> None:
        if payload is None:
            return
        if payload.get("allow_oauth"):
            self.push_screen(
                ConfirmScreen(
                    t("copy_oauth"),
                    t("copy_oauth_body"),
                    t("continue"),
                    danger=True,
                ),
                lambda confirmed: self._start_transfer(payload) if confirmed else None,
            )
            return
        self._start_transfer(payload)

    def _start_transfer(self, payload: dict[str, object]) -> None:
        mode = str(payload["mode"])
        self._set_busy(f"{mode} in progress...")
        self.run_extra(mode.title(), mode, payload)

    def _open_backup_screen(self, backups: list[str]) -> None:
        self.push_screen(BackupScreen(backups), self._backup_chosen)

    def _backup_chosen(self, request: tuple[str, str | None] | None) -> None:
        if request is None:
            return
        action, name = request
        if action == "create":
            self._set_busy("Creating backup...")
            self.run_extra("Backup create", "backup-create", {})
            return
        if not name:
            self.notify(t("select_backup_first"), severity="warning")
            return
        self.push_screen(
            ConfirmScreen(
                t("restore_backup"),
                t("restore_body", name=name),
                t("restore"),
                danger=True,
            ),
            lambda confirmed: self._start_backup_restore(name) if confirmed else None,
        )

    def _start_backup_restore(self, name: str) -> None:
        self._set_busy(f"Restoring {name}...")
        self.run_extra("Backup restore", "backup-restore", {"name": name})

    @work(thread=True, exclusive=True, group="extra", exit_on_error=False)
    def run_extra(
        self, title: str, kind: str, payload: dict[str, object]
    ) -> tuple[str, dict[str, Any]]:
        return title, self._run_extra(kind, payload)

    def _run_extra(self, kind: str, payload: dict[str, object]) -> dict[str, Any]:
        if kind == "shared-status":
            return self.transport.shared_status()
        if kind == "map-status":
            return self.transport.auth_map_status()
        if kind == "bind":
            return self.transport.bind_auth(str(payload["name"]))
        if kind == "delete":
            name = str(payload["name"])
            self.transport.delete(name)
            return {"deleted": name}
        if kind == "backup-list":
            return self.transport.backup("list")
        if kind == "backup-create":
            return self.transport.backup("create")
        if kind == "backup-restore":
            return self.transport.backup("restore", str(payload["name"]))
        if kind == "sources":
            path = payload.get("path")
            return list_sources(
                str(payload["adapter"]),
                path if isinstance(path, Path) else None,
            )
        settings = self._local_settings()
        if kind == "import":
            return import_auth(
                settings,
                source=str(payload["adapter"]),
                identity=_optional_str(payload.get("identity")),
                provider=_optional_str(payload.get("provider")),
                source_profile=_optional_str(payload.get("source_profile")),
                path=_payload_path(payload),
                shared=bool(payload.get("shared")),
                allow_oauth=bool(payload.get("allow_oauth")),
            )
        if kind == "export":
            return export_auth(
                settings,
                destination=str(payload["adapter"]),
                identity=_optional_str(payload.get("identity")),
                provider=_optional_str(payload.get("provider")),
                source_profile=_optional_str(payload.get("source_profile")),
                path=_payload_path(payload),
                shared=bool(payload.get("shared")),
                allow_oauth=bool(payload.get("allow_oauth")),
            )
        if kind == "push":
            return push_auth(
                settings,
                self.settings.hosts[str(payload["host"])],
                identity=_optional_str(payload.get("identity")),
                providers=list(payload.get("providers") or []),
                shared=bool(payload.get("shared")),
                allow_oauth=bool(payload.get("allow_oauth")),
            )
        raise ValueError(f"unsupported extra action: {kind}")

    def _local_settings(self) -> Settings:
        if isinstance(self.transport, LocalTransport):
            return self.transport.settings
        return self.settings

    def _handle_extra(self, event: Worker.StateChanged) -> None:
        if event.state not in {WorkerState.ERROR, WorkerState.SUCCESS}:
            return
        self._set_busy(None)
        if event.state == WorkerState.ERROR:
            self.notify(str(event.worker.error), severity="error")
            self.query_one("#profile-detail", Static).update(
                f"[b]Action failed[/]\n\n{event.worker.error}"
            )
            return
        title, result = event.worker.result
        if title == "Backups":
            backups = result.get("backups", [])
            if not isinstance(backups, list):
                backups = []
            self._open_backup_screen([str(item) for item in backups])
            return
        if title == "Delete profile":
            deleted = result.get("deleted")
            self.selected_profile = None
            self.notify(f"Deleted {deleted}")
            self.action_refresh()
            return
        self.query_one("#summary", Label).update(f"{self.location_title} · {title}")
        self.query_one("#profile-detail", Static).update(
            f"[b]{title}[/]\n\n"
            + yaml.safe_dump(result, allow_unicode=False, sort_keys=False)
        )
        self.notify(title)
        if title in {"Auth bind", "Backup create", "Backup restore"}:
            self.action_refresh()

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
        items = [("local", "local", t("kind_local"))]
        items.extend(
            (f"local--{alias}", alias, t("kind_folder"))
            for alias in sorted(self.settings.local_locations)
        )
        items.extend(
            (f"ssh--{alias}", alias, t("kind_ssh"))
            for alias in sorted(self.settings.hosts)
        )
        return items

    def location_summary(self, identifier: str) -> tuple[str, str, str]:
        if identifier == "local":
            count = len(list_profiles(self.settings))
            return (
                "local",
                t("this_machine"),
                f"{self.settings.profiles_dir}\n\n"
                + t("profiles_in_workspace", count=count),
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
                t("local_folder"),
                f"{location.profiles_dir}\n\n" + t("profiles_in_folder", count=count),
            )
        host = self.settings.hosts[alias]
        target = (
            host.ssh_host if not host.ssh_user else f"{host.ssh_user}@{host.ssh_host}"
        )
        if host.ssh_port:
            target = f"{target} -p {host.ssh_port}"
        return (
            alias,
            t("ssh_host"),
            t(
                "ssh_detail",
                target=target,
                profiles=host.profiles_dir,
                config=host.remote_config,
            ),
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
            self.notify(t("remote_init_only"), severity="warning")
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


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _payload_path(payload: dict[str, object]) -> Path | None:
    path = payload.get("path")
    return path if isinstance(path, Path) else None
