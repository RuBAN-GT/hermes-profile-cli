from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from hermes_profile.helptext import HELP_TEXT


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen { align: center middle; background: #282a36 80%; }
    #help-dialog {
        width: 88;
        height: 90%;
        padding: 1 2;
        border: tall #bd93f9;
        background: #21222c;
        color: #f8f8f2;
    }
    #help-title { text-style: bold; color: #bd93f9; height: 1; margin-bottom: 1; }
    #help-body { height: 1fr; color: #f8f8f2; }
    #close-help {
        margin-top: 1;
        border: tall #50fa7b;
        color: #50fa7b;
        text-style: bold;
        background: #44475a;
    }
    #close-help:hover { background: #50fa7b; color: #282a36; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close", key_display="esc"),
        Binding("q", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static("Help", id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(HELP_TEXT)
            yield Button("Close", id="close-help")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
