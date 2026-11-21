"""Safe, verified project modifications.

edit() yields a fresh Project; on exit, if anything changed:
  1. refuse if LibrePCB holds the project lock (.lock) or files changed on
     disk since they were read (conflicting edit);
  2. back up the project (outside the project dir);
  3. copy new library elements, write changed files atomically;
  4. re-open the project with ``librepcb-cli open-project --strict``;
  5. on any failure restore the original bytes and delete new files.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .cli import LibrePCBCli
from .errors import LibrePCBError
from .project import Project

log = logging.getLogger(__name__)

BACKUP_KEEP = 20
_BACKUP_IGNORE = shutil.ignore_patterns("output", ".git", ".lock", "*.autosave")


def check_not_locked(project_dir: Path) -> None:
    lock = project_dir / ".lock"
    if lock.exists():
        lines = lock.read_text(errors="replace").split("\n")
        who = f"{lines[1] if len(lines) > 1 else '?'}@{lines[2] if len(lines) > 2 else '?'}"
        raise LibrePCBError(
            f"Project is open in LibrePCB ({who}, lock file {lock}). Close it in LibrePCB before "
            "editing to avoid lost changes. If LibrePCB crashed, delete the stale .lock file.")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _project_key(project_dir: Path) -> str:
    h = hashlib.sha256(str(project_dir).encode()).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_dir.name)[:40]
    return f"{safe}-{h}"


def make_backup(project_dir: Path, backup_root: Path, reason: str) -> Path:
    backup_root = backup_root.resolve()
    base = backup_root / _project_key(project_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = base / f"{stamp}-{reason}"
    excluded = backup_root if backup_root.is_relative_to(project_dir) and backup_root != project_dir else base

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = _BACKUP_IGNORE(directory, names)
        if Path(directory) == excluded.parent:
            ignored.add(excluded.name)
        return ignored

    dest.mkdir()
    try:
        shutil.copytree(project_dir, dest, ignore=ignore, symlinks=True, dirs_exist_ok=True)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    for old in sorted(p for p in base.iterdir() if p.is_dir())[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


@contextlib.contextmanager
def _edit_lock(backup_root: Path, project_dir: Path) -> Iterator[None]:
    backup_root.mkdir(parents=True, exist_ok=True)
    with open(backup_root / f".{_project_key(project_dir)}.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class EditResult:
    def __init__(self) -> None:
        self.backup: Path | None = None
        self.changed_files: list[str] = []


@contextlib.contextmanager
def edit(path: Path, roots: tuple[Path, ...], cli: LibrePCBCli, backup_root: Path,
         reason: str) -> Iterator[tuple[Project, EditResult]]:
    cli.require()  # never write what we cannot verify
    project = Project(path, roots)
    result = EditResult()
    with _edit_lock(backup_root, project.dir):
        check_not_locked(project.dir)
        yield project, result  # caller mutates the project
        if not project.dirty and not project.new_dirs:
            return
        check_not_locked(project.dir)
        project.check_unchanged_on_disk()
        result.backup = make_backup(project.dir, backup_root, reason)
        rendered = project.rendered()
        created_dirs: list[Path] = []
        written: dict[Path, bytes | None] = {}
        try:
            for src, dst in project.new_dirs:
                if dst.exists():
                    raise LibrePCBError(f"refusing to overwrite existing {dst}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=dst.parent, prefix=".librepcb-mcp-") as temp:
                    staged = Path(temp) / "element"
                    shutil.copytree(src, staged)
                    staged.rename(dst)
                    created_dirs.append(dst)
            for rel, data in rendered.items():
                target = project.dir / rel
                written[target] = target.read_bytes() if target.exists() else None
                atomic_write(target, data)
            r = cli.verify_project(project.lpp)
            if not r.ok:
                raise LibrePCBError(
                    "LibrePCB rejected the modified project, changes were rolled back. "
                    f"librepcb-cli output:\n{(r.stdout + r.stderr).strip()}")
        except BaseException:
            for target, orig in written.items():
                try:
                    if orig is None:
                        target.unlink(missing_ok=True)
                        parent = target.parent
                        while parent != project.dir and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                    else:
                        atomic_write(target, orig)
                except OSError:
                    log.exception("rollback failed for %s (backup: %s)", target, result.backup)
            for d in created_dirs:
                shutil.rmtree(d, ignore_errors=True)
            raise
        result.changed_files = sorted(rendered) + [str(d.relative_to(project.dir)) for d in created_dirs]
