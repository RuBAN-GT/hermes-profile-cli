from copy import deepcopy
from typing import Any


def merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override)
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = merge(result[key], value) if key in result else deepcopy(value)
    return result


NO_CHANGE = object()


def changed_values(before: Any, after: Any) -> Any:
    """Return additive or changed values; deletion is intentionally unsupported."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = {}
        for key, value in after.items():
            previous = before.get(key, NO_CHANGE)
            changed = (
                value if previous is NO_CHANGE else changed_values(previous, value)
            )
            if changed is not NO_CHANGE:
                result[key] = changed
        return result if result else NO_CHANGE
    return NO_CHANGE if before == after else deepcopy(after)
