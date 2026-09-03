from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from hermes_profile.helptext import help_text
from hermes_profile.i18n import t


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen { align: center middle; background: $background 80%; }
    #help-dialog {
        width: 88;
        height: 90%;
        padding: 1 2;
        border: tall $primary;
        background: $surface;
        color: $foreground;
    }
    #help-title { text-style: bold; color: $text-primary; height: 1; margin-bottom: 1; }
    #help-body { height: 1fr; color: $foreground; }
    #close-help {
        margin-top: 1;
        border: tall $success;
        color: $text-success;
        text-style: bold;
        background: $panel;
    }
    #close-help:hover { background: $success; color: $text; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", key_display="esc"),
        Binding("q", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(t("help"), id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(help_text())
            yield Button(t("close"), id="close-help")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
