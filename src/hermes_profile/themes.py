from textual.theme import Theme

THEMES = (
    Theme(
        name="hermes-dracula",
        primary="#bd93f9",
        secondary="#6272a4",
        accent="#8be9fd",
        foreground="#f8f8f2",
        background="#282a36",
        surface="#21222c",
        panel="#44475a",
        success="#50fa7b",
        warning="#ffb86c",
        error="#ff5555",
        dark=True,
        variables={"footer-key-foreground": "#f8f8f2"},
    ),
    Theme(
        name="hermes-nord",
        primary="#88c0d0",
        secondary="#4c566a",
        accent="#b48ead",
        foreground="#eceff4",
        background="#2e3440",
        surface="#3b4252",
        panel="#434c5e",
        success="#a3be8c",
        warning="#ebcb8b",
        error="#bf616a",
        dark=True,
        variables={"footer-key-foreground": "#eceff4"},
    ),
    Theme(
        name="hermes-gruvbox",
        primary="#83a598",
        secondary="#665c54",
        accent="#d3869b",
        foreground="#ebdbb2",
        background="#282828",
        surface="#3c3836",
        panel="#504945",
        success="#b8bb26",
        warning="#fabd2f",
        error="#fb4934",
        dark=True,
        variables={"footer-key-foreground": "#ebdbb2"},
    ),
)

THEME_NAMES = frozenset(theme.name for theme in THEMES)
DEFAULT_THEME = "hermes-dracula"


def apply_hermes_themes(app: object, preferred: str | None = None) -> str:
    """Register only Hermes themes so the palette cannot pick builtin colors."""
    from textual.app import App

    if not isinstance(app, App):
        raise TypeError("apply_hermes_themes requires a Textual App")
    for theme in THEMES:
        app.register_theme(theme)
    name = preferred if preferred in THEME_NAMES else DEFAULT_THEME
    app.theme = name
    for registered in list(app.available_themes):
        if registered not in THEME_NAMES:
            app.unregister_theme(registered)
    return name


def next_theme(current: str) -> str:
    names = [theme.name for theme in THEMES]
    try:
        index = names.index(current)
    except ValueError:
        return names[0]
    return names[(index + 1) % len(names)]
