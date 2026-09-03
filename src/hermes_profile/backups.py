import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from hermes_profile.models import Settings
from hermes_profile.paths import PROFILE_NAME, write_private

BACKUP_SUFFIX = ".tar.gz"
PROFILE_FILES = {"profile.yaml", "runtime-config.yaml", "runtime.env"}


def create_backup(settings: Settings) -> dict[str, object]:
    """Archive manager-owned setup files without runtime state or credentials."""
    directory = settings.managed_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    name = f"setup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}{BACKUP_SUFFIX}"
    path = directory / name
    files = list(_backup_files(settings))
    with tarfile.open(path, "x:gz") as archive:
        for source, member_name in files:
            data = source.read_bytes()
            member = tarfile.TarInfo(member_name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
    path.chmod(0o600)
    return {"created": name, "files": len(files), "path": str(path)}


def list_backups(settings: Settings) -> dict[str, list[str]]:
    directory = settings.managed_dir / "backups"
    backups = (
        sorted(path.name for path in directory.glob(f"*{BACKUP_SUFFIX}"))
        if directory.is_dir()
        else []
    )
    return {"backups": backups}


def restore_backup(settings: Settings, name: str) -> dict[str, object]:
    path = _backup_path(settings, name)
    if not path.is_file():
        raise ValueError(f"backup not found: {name}")
    restored = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            target = _restore_target(settings, member)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{name}: unreadable backup member: {member.name}")
            try:
                content = source.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(
                    f"{name}: non-text backup member: {member.name}"
                ) from error
            write_private(target, content)
            restored += 1
    return {"restored": name, "files": restored}


def _backup_files(settings: Settings) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    if settings.fragments_dir.is_dir():
        files.extend(
            (path, str(Path("fragments") / path.relative_to(settings.fragments_dir)))
            for path in sorted(settings.fragments_dir.rglob("*"))
            if path.is_file()
        )
    if settings.profiles_dir.is_dir():
        for directory in sorted(settings.profiles_dir.iterdir()):
            if not directory.is_dir() or not PROFILE_NAME.fullmatch(directory.name):
                continue
            for filename in PROFILE_FILES:
                path = directory / filename
                if path.is_file():
                    files.append(
                        (path, str(Path("profiles") / directory.name / filename))
                    )
    return files


def _backup_path(settings: Settings, name: str) -> Path:
    if Path(name).name != name or not name.endswith(BACKUP_SUFFIX):
        raise ValueError("backup name must be a .tar.gz file in the backup directory")
    return settings.managed_dir / "backups" / name


def _restore_target(settings: Settings, member: tarfile.TarInfo) -> Path:
    if (
        not member.isfile()
        or member.name.startswith("/")
        or ".." in Path(member.name).parts
    ):
        raise ValueError(f"unsafe backup member: {member.name}")
    parts = Path(member.name).parts
    if len(parts) >= 2 and parts[0] == "fragments":
        return settings.fragments_dir.joinpath(*parts[1:])
    if (
        len(parts) == 3
        and parts[0] == "profiles"
        and PROFILE_NAME.fullmatch(parts[1])
        and parts[2] in PROFILE_FILES
    ):
        return settings.profiles_dir / parts[1] / parts[2]
    raise ValueError(f"unsupported backup member: {member.name}")
