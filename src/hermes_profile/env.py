import re
from collections.abc import Mapping

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env(
    content: str, source: str, *, context: Mapping[str, str] | None = None
) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{source}:{number}: expected NAME=value")
        name, value = line.split("=", 1)
        if not NAME.fullmatch(name):
            raise ValueError(f"{source}:{number}: invalid environment variable name")
        if context is not None:
            value, _ = interpolate(value, {**context, **values})
        if any(char in value for char in "\r\n\0"):
            raise ValueError(f"invalid environment value: {name}")
        values[name] = value
    return values


VARIABLE = re.compile(
    r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
)


def interpolate(value: str, context: Mapping[str, str]) -> tuple[str, set[str]]:
    """Substitute once; replacement text is never interpreted as a template."""
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        escaped, name = match.groups()
        if escaped is not None:
            return "${" + escaped + "}"
        if name not in context:
            raise ValueError(f"missing environment variable: {name}")
        used.add(name)
        return context[name]

    return VARIABLE.sub(replace, value), used


def render_env(values: Mapping[str, str]) -> str:
    return "".join(f"{name}={values[name]}\n" for name in sorted(values))
