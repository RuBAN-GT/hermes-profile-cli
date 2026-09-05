import subprocess
import sys
from pathlib import Path

from hermes_profile import __version__
from hermes_profile.transport import SOURCE_REPO

SHARE_SRC = Path("~/.local/share/hermes-profile/src").expanduser()
UPDATE_TIMEOUT_SECONDS = 180


def self_update() -> dict[str, str]:
    src = _source_checkout()
    dirty = _run(["git", "-C", str(src), "status", "--porcelain"])
    if dirty.strip():
        raise ValueError(
            "source checkout has local changes; commit or stash them first"
        )
    shallow = _run(["git", "-C", str(src), "rev-parse", "--is-shallow-repository"])
    fetch_options = ["--unshallow"] if shallow.strip() == "true" else []
    _run(["git", "-C", str(src), "fetch", *fetch_options, "origin", "main"])
    _run(["git", "-C", str(src), "merge", "--ff-only", "origin/main"])
    _run([sys.executable, "-m", "pip", "install", "-U", str(src)])
    return {
        "ok": "true",
        "previous_version": __version__,
        "version": _installed_version(),
        "source": str(src),
        "python": sys.executable,
    }


def _source_checkout() -> Path:
    package_repo = Path(__file__).resolve().parents[2]
    if (package_repo / ".git").exists() and (package_repo / "pyproject.toml").is_file():
        return package_repo
    if (SHARE_SRC / ".git").exists():
        return SHARE_SRC
    SHARE_SRC.parent.mkdir(parents=True, exist_ok=True)
    if SHARE_SRC.exists():
        raise ValueError(f"expected a git checkout at {SHARE_SRC}")
    _run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "main",
            SOURCE_REPO,
            str(SHARE_SRC),
        ]
    )
    return SHARE_SRC


def _installed_version() -> str:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from hermes_profile import __version__; print(__version__)",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=UPDATE_TIMEOUT_SECONDS,
    )
    version = completed.stdout.strip()
    return version or __version__


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=UPDATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"update timed out after {UPDATE_TIMEOUT_SECONDS}s") from error
    except FileNotFoundError as error:
        raise ValueError(f"missing command: {command[0]}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(detail or f"command failed: {' '.join(command)}")
    return completed.stdout
