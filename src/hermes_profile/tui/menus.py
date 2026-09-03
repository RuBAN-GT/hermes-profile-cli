from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, ListItem, ListView, Static

from hermes_profile.auth_adapters import ADAPTERS
from hermes_profile.i18n import t
from hermes_profile.paths import PROFILE_NAME


class MoreActionsScreen(ModalScreen[str | None]):
    CSS = """
    MoreActionsScreen { align: center middle; background: $background 70%; }
    #more-actions {
        width: 64; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #more-title { text-style: bold; color: $text-primary; margin-bottom: 1; }
    #more-hint { color: $text-muted; margin-bottom: 1; }
    Button { margin-top: 1; width: 100%; border: round $secondary; text-style: bold; }
    #more-reconcile { color: $text-warning; }
    #more-discard { color: $text-error; }
    #more-bind { color: $text-accent; }
    #more-backup { color: $text-primary; }
    #more-delete { color: $text-error; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="more-actions"):
            yield Label(t("more_title"), id="more-title")
            yield Label(t("more_hint"), id="more-hint")
            yield Button(t("more_reconcile"), id="more-reconcile")
            yield Button(t("more_discard"), id="more-discard")
            yield Button(t("more_bind"), id="more-bind")
            yield Button(t("more_backup"), id="more-backup")
            yield Button(t("more_delete"), id="more-delete")
            yield Button(t("cancel"), id="more-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "more-reconcile": "reconcile",
            "more-discard": "discard",
            "more-bind": "bind",
            "more-backup": "backup",
            "more-delete": "delete",
        }
        self.dismiss(mapping.get(event.button.id))


class AuthHubScreen(ModalScreen[str | None]):
    CSS = """
    AuthHubScreen { align: center middle; background: $background 70%; }
    #auth-hub {
        width: 70; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #auth-hub-title { text-style: bold; color: $text-primary; }
    #auth-hub-hint { color: $text-muted; margin-bottom: 1; }
    Button { margin-top: 1; width: 100%; border: round $secondary; text-style: bold; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-hub"):
            yield Label(t("auth_hub_title"), id="auth-hub-title")
            yield Label(t("auth_hub_hint"), id="auth-hub-hint")
            yield Button(t("auth_shared_status"), id="auth-shared-status")
            yield Button(t("auth_map_status"), id="auth-map-status")
            yield Button(t("auth_bind"), id="auth-bind")
            yield Button(t("auth_sync"), id="auth-sync")
            yield Button(t("auth_sources"), id="auth-sources")
            yield Button(t("auth_import"), id="auth-import")
            yield Button(t("auth_export"), id="auth-export")
            yield Button(t("auth_push"), id="auth-push")
            yield Button(t("cancel"), id="auth-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "auth-shared-status": "shared-status",
            "auth-map-status": "map-status",
            "auth-bind": "bind",
            "auth-sync": "sync",
            "auth-sources": "sources",
            "auth-import": "import",
            "auth-export": "export",
            "auth-push": "push",
        }
        self.dismiss(mapping.get(event.button.id))


class AuthTransferScreen(ModalScreen[dict[str, object] | None]):
    CSS = """
    AuthTransferScreen { align: center middle; background: $background 70%; }
    #auth-transfer {
        width: 74; height: auto; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #auth-transfer-title { text-style: bold; color: $text-primary; }
    #auth-transfer-hint { color: $text-muted; margin-bottom: 1; }
    #auth-transfer-error { color: $text-error; height: 2; }
    Input { margin: 1 0; border: round $secondary; }
    Input:focus { border: round $accent; }
    Button { margin-right: 1; border: round $secondary; text-style: bold; }
    #start-auth-transfer { border: round $success; color: $text-success; }
    """

    def __init__(self, mode: str, hosts: list[str]) -> None:
        super().__init__()
        self.mode = mode
        self.hosts = hosts

    def compose(self) -> ComposeResult:
        adapters = ", ".join(sorted(ADAPTERS))
        title = {
            "import": t("import_title"),
            "export": t("export_title"),
            "push": t("push_title"),
            "sources": t("sources_title"),
        }[self.mode]
        with Vertical(id="auth-transfer"):
            yield Label(title, id="auth-transfer-title")
            yield Label(
                t("adapters_hint", adapters=adapters),
                id="auth-transfer-hint",
            )
            with VerticalScroll():
                if self.mode in {"import", "sources"}:
                    yield Label(t("from_adapter"))
                    yield Input(value="opencode", id="auth-adapter")
                elif self.mode == "export":
                    yield Label(t("to_adapter"))
                    yield Input(value="opencode", id="auth-adapter")
                if self.mode == "push":
                    yield Label(t("ssh_alias"))
                    yield Input(
                        value=self.hosts[0] if self.hosts else "",
                        placeholder="gateway-a",
                        id="auth-host",
                    )
                if self.mode != "sources":
                    yield Label(t("identity_name"))
                    yield Input(placeholder="codex-gogol", id="auth-identity")
                    yield Label(t("provider_optional"))
                    yield Input(
                        placeholder="openai or openai-codex",
                        id="auth-provider",
                    )
                if self.mode in {"import", "export"}:
                    yield Label(t("opencode_profile"))
                    yield Input(placeholder="work", id="auth-source-profile")
                    yield Label(t("explicit_path"))
                    yield Input(placeholder="/path/to/auth.json", id="auth-path")
                    yield Checkbox(t("shared_instead"), id="auth-shared")
                if self.mode == "push":
                    yield Label(t("provider_comma"))
                    yield Input(placeholder="openai-codex", id="auth-provider")
                    yield Checkbox(t("push_shared"), id="auth-shared")
                if self.mode != "sources":
                    yield Checkbox(t("allow_oauth"), id="allow-oauth")
            yield Label("", id="auth-transfer-error")
            with Horizontal():
                yield Button(t("continue"), id="start-auth-transfer")
                yield Button(t("cancel"), id="cancel-auth-transfer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-auth-transfer":
            self.dismiss(None)
            return
        error = self.query_one("#auth-transfer-error", Label)
        payload: dict[str, object] = {"mode": self.mode}
        if self.mode in {"import", "export", "sources"}:
            adapter = self.query_one("#auth-adapter", Input).value.strip()
            if adapter not in ADAPTERS:
                error.update(t("adapter_must", adapters=", ".join(sorted(ADAPTERS))))
                return
            payload["adapter"] = adapter
        if self.mode == "push":
            host = self.query_one("#auth-host", Input).value.strip()
            if not host:
                error.update(t("enter_host"))
                return
            payload["host"] = host
        if self.mode != "sources":
            identity = self.query_one("#auth-identity", Input).value.strip()
            shared = self.query_one("#auth-shared", Checkbox).value
            if not shared and (not identity or not PROFILE_NAME.fullmatch(identity)):
                error.update(t("enter_identity"))
                return
            payload["identity"] = identity or None
            payload["shared"] = shared
            payload["allow_oauth"] = self.query_one("#allow-oauth", Checkbox).value
            provider = self.query_one("#auth-provider", Input).value.strip()
            if self.mode == "push":
                payload["providers"] = [
                    item.strip() for item in provider.split(",") if item.strip()
                ]
            else:
                payload["provider"] = provider or None
        if self.mode in {"import", "export"}:
            source_profile = self.query_one("#auth-source-profile", Input).value.strip()
            path = self.query_one("#auth-path", Input).value.strip()
            payload["source_profile"] = source_profile or None
            payload["path"] = Path(path).expanduser() if path else None
        self.dismiss(payload)


class BackupScreen(ModalScreen[tuple[str, str | None] | None]):
    CSS = """
    BackupScreen { align: center middle; background: $background 70%; }
    #backup-dialog {
        width: 70; height: auto; max-height: 28; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #backup-title { text-style: bold; color: $text-primary; }
    #backup-hint { color: $text-muted; margin-bottom: 1; }
    #backup-list {
        height: 10; border: round $secondary; background: $background; padding: 0 1;
    }
    #backup-empty { color: $text-muted; }
    Button {
        margin-top: 1; margin-right: 1;
        border: round $secondary; text-style: bold;
    }
    #backup-create { color: $text-success; }
    #backup-restore { color: $text-warning; }
    """

    def __init__(self, backups: list[str]) -> None:
        super().__init__()
        self.backups = backups
        self.selected: str | None = backups[0] if backups else None

    def compose(self) -> ComposeResult:
        with Vertical(id="backup-dialog"):
            yield Label(t("backup_title"), id="backup-title")
            yield Label(t("backup_hint"), id="backup-hint")
            if self.backups:
                yield ListView(
                    *[ListItem(Label(name)) for name in self.backups],
                    id="backup-list",
                )
            else:
                yield Static(t("no_backups"), id="backup-empty")
            with Horizontal():
                yield Button(t("create_snapshot"), id="backup-create")
                yield Button(
                    t("restore_selected"),
                    id="backup-restore",
                    disabled=not self.backups,
                )
                yield Button(t("cancel"), id="backup-cancel")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        index = event.list_view.index
        if index is not None and 0 <= index < len(self.backups):
            self.selected = self.backups[index]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "backup-create":
            self.dismiss(("create", None))
        elif event.button.id == "backup-restore":
            self.dismiss(("restore", self.selected))
        else:
            self.dismiss(None)
