import re
from collections.abc import Mapping

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env(content: str, source: str) -> dict[str, str]:
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
        values[name] = value
    return values


def render_env(values: Mapping[str, str]) -> str:
    return "".join(f"{name}={values[name]}\n" for name in sorted(values))
