from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from hermes_profile.i18n import t
from hermes_profile.tui.help import HelpScreen

if TYPE_CHECKING:
    from hermes_profile.tui.app import ProfileApp


class LocationHomeScreen(Screen[None]):
    """Pick a location before opening its profile workspace."""

    CSS = """
    LocationHomeScreen { background: $background; color: $foreground; }
    #location-home { height: 1fr; padding: 0 1; }
    #location-header { height: auto; margin: 1 0; }
    #location-title { color: $primary; text-style: bold; }
    #location-subtitle { color: $secondary; }
    #location-body { height: 1fr; layout: horizontal; }
    #location-sidebar { width: 42; height: 100%; margin-right: 1; }
    #location-list {
        height: 1fr;
        border: round $secondary;
        background: $surface;
        padding: 0 1;
    }
    #location-list:focus { border: round $primary; }
    #location-list ListItem { height: auto; padding: 0 1; }
    #location-list ListItem:hover { background: $panel; }
    #location-list ListItem.--highlight { background: $panel; text-style: bold; }
    #location-actions { height: 1; margin-top: 1; }
    #add-location-home, #edit-location-home, #delete-location-home, #open-location {
        min-height: 1;
        height: 1;
        border: none;
        padding: 0 1;
        text-style: bold;
    }
    #add-location-home { width: 1fr; margin-right: 1; color: $primary; }
    #edit-location-home { width: 1fr; margin-right: 1; color: $accent; }
    #delete-location-home { width: 1fr; color: $error; }
    #location-detail {
        width: 1fr;
        height: 100%;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #selected-location { text-style: bold; color: $primary; }
    #selected-location-kind { color: $accent; margin-bottom: 1; }
    #selected-location-detail { height: 1fr; color: $foreground; }
    #open-location {
        dock: bottom;
        width: 100%;
        color: $success;
        background: $panel;
    }
    #add-location-home:hover, #open-location:hover {
        background: $primary;
        color: $text;
    }
    #delete-location-home:hover { background: $error; color: $text; }
    Button:disabled { color: $secondary; }
    """
    BINDINGS = [
        Binding("enter", "open", "Open", priority=True),
        Binding("a", "add", "Add"),
        Binding("d", "delete", "Remove"),
        Binding("e", "edit", "Edit"),
        Binding("i", "init", "Init remote"),
        Binding("ctrl+t", "cycle_theme", "Theme"),
        Binding("ctrl+l", "cycle_language", "Lang"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("f1", "help", "Help", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, app: "ProfileApp") -> None:
        super().__init__()
        self.profile_app = app
        self.selected = "local"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="location-home"):
            with Vertical(id="location-header"):
                yield Label(t("where_work"), id="location-title")
                yield Label(t("location_keys"), id="location-subtitle")
            with Horizontal(id="location-body"):
                with Vertical(id="location-sidebar"):
                    yield ListView(*self._location_items(), id="location-list")
                    with Horizontal(id="location-actions"):
                        yield Button(t("add_location"), id="add-location-home")
                        yield Button(t("edit"), id="edit-location-home", disabled=True)
                        yield Button(
                            t("remove"), id="delete-location-home", disabled=True
                        )
                with Vertical(id="location-detail"):
                    yield Label(id="selected-location")
                    yield Label(id="selected-location-kind")
                    yield Static(id="selected-location-detail")
                    yield Button(
                        t("open_profiles"), id="open-location", variant="success"
                    )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#location-list", ListView).focus()
        self._show_selected()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "location-list" and event.item is not None:
            if event.item.id is not None:
                self.selected = event.item.id
                self._show_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-location":
            self.action_open()
        elif event.button.id == "add-location-home":
            self.action_add()
        elif event.button.id == "edit-location-home":
            self.action_edit()
        elif event.button.id == "delete-location-home":
            self.action_delete()

    def action_open(self) -> None:
        self.profile_app.open_location(self.selected)
        self.app.pop_screen()

    def action_add(self) -> None:
        self.profile_app.push_location_type(self._refresh_after_location_change)

    def action_delete(self) -> None:
        self.profile_app.confirm_delete_location(
            self.selected, self._refresh_after_location_change
        )

    def action_edit(self) -> None:
        self.profile_app.edit_location(
            self.selected, self._refresh_after_location_change
        )

    def action_init(self) -> None:
        self.profile_app.confirm_init_remote(self.selected)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "init" and not self.selected.startswith("ssh--"):
            return False
        return True

    def action_cycle_theme(self) -> None:
        self.profile_app.action_cycle_theme()

    def action_cycle_language(self) -> None:
        self.profile_app.action_cycle_language()

    def relabel(self) -> None:
        self.query_one("#location-title", Label).update(t("where_work"))
        self.query_one("#location-subtitle", Label).update(t("location_keys"))
        self.query_one("#add-location-home", Button).label = t("add_location")
        self.query_one("#edit-location-home", Button).label = t("edit")
        self.query_one("#delete-location-home", Button).label = t("remove")
        self.query_one("#open-location", Button).label = t("open_profiles")
        self.refresh_locations()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self.app.exit()

    def _refresh_after_location_change(self, saved: bool | None) -> None:
        if not saved:
            return
        if self.selected not in {item[0] for item in self.profile_app.location_items()}:
            self.selected = "local"
        self.refresh_locations()

    @work(exclusive=True, group="home-locations")
    async def refresh_locations(self) -> None:
        listing = self.query_one("#location-list", ListView)
        await listing.clear()
        for item in self._location_items():
            listing.append(item)
        if listing.children:
            listing.index = 0
            first = listing.children[0]
            if first.id is not None:
                self.selected = first.id
        self._show_selected()

    def _location_items(self) -> list[ListItem]:
        return [
            ListItem(
                Label(f"{title}\n[dim]{kind}[/]"),
                id=identifier,
            )
            for identifier, title, kind in self.profile_app.location_items()
        ]

    def _show_selected(self) -> None:
        title, kind, detail = self.profile_app.location_summary(self.selected)
        self.query_one("#selected-location", Label).update(title)
        self.query_one("#selected-location-kind", Label).update(kind)
        self.query_one("#selected-location-detail", Static).update(detail)
        delete = self.query_one("#delete-location-home", Button)
        delete.disabled = self.selected == "local"
        self.query_one("#edit-location-home", Button).disabled = False
        self.refresh_bindings()
