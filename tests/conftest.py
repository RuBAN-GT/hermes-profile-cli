import os

import pytest

from hermes_profile.i18n import set_language

os.environ["TEXTUAL_ANIMATIONS"] = "none"


@pytest.fixture(autouse=True)
def _reset_language() -> None:
    set_language("en")
    yield
    set_language("en")
