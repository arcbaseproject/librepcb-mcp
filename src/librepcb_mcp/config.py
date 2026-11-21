"""Runtime configuration, read from environment variables.

LIBREPCB_MCP_ROOTS      os.pathsep-separated directories the server may read
                        and modify. Required for any project access.
LIBREPCB_MCP_LIBRARIES  os.pathsep-separated paths: *.lplib directories or
                        directories containing them. Optional; if unset the
                        LibrePCB workspace libraries are auto-detected.
LIBREPCB_CLI            librepcb-cli command (e.g. "/usr/bin/librepcb-cli" or
                        "flatpak run --command=librepcb-cli org.librepcb.LibrePCB").
                        Optional; auto-detected.
LIBREPCB_MCP_BACKUP_DIR Where automatic backups go. Default:
                        $XDG_STATE_HOME/librepcb-mcp/backups.
LIBREPCB_MCP_CLI_TIMEOUT Seconds before a CLI call is killed (default 300).
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


def _paths(var: str) -> list[Path]:
    raw = os.environ.get(var, "")
    return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]


def detect_cli() -> list[str] | None:
    env = os.environ.get("LIBREPCB_CLI")
    if env:
        return shlex.split(env)
    exe = shutil.which("librepcb-cli")
    if exe:
        return [exe]
    flatpak = shutil.which("flatpak")
    if flatpak and any(p.exists() for p in (
            Path.home() / ".local/share/flatpak/app/org.librepcb.LibrePCB",
            Path("/var/lib/flatpak/app/org.librepcb.LibrePCB"))):
        return [flatpak, "run", "--command=librepcb-cli", "org.librepcb.LibrePCB"]
    return None


def detect_workspace_libraries() -> list[Path]:
    """Library dirs of the LibrePCB workspace(s) found in the usual places."""
    candidates = [Path.home() / "LibrePCB-Workspace"]
    for conf in (Path.home() / ".config/LibrePCB/LibrePCB.conf",
                 Path.home() / ".var/app/org.librepcb.LibrePCB/config/LibrePCB/LibrePCB.conf"):
        if conf.is_file():
            for line in conf.read_text(errors="replace").splitlines():
                if line.startswith("workspace_path="):
                    candidates.insert(0, Path(line.split("=", 1)[1].strip()).expanduser())
    out = []
    for ws in candidates:
        libs = ws / "data" / "libraries"
        if libs.is_dir():
            out.append(libs)
    return out


@dataclass(frozen=True)
class Config:
    roots: tuple[Path, ...]
    libraries: tuple[Path, ...]
    cli: tuple[str, ...] | None
    backup_dir: Path
    cli_timeout: float

    @classmethod
    def from_env(cls) -> Config:
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        backup = os.environ.get("LIBREPCB_MCP_BACKUP_DIR")
        libs = _paths("LIBREPCB_MCP_LIBRARIES") or detect_workspace_libraries()
        cli = detect_cli()
        return cls(
            roots=tuple(_paths("LIBREPCB_MCP_ROOTS")),
            libraries=tuple(libs),
            cli=tuple(cli) if cli else None,
            backup_dir=Path(backup).expanduser().resolve() if backup
            else state / "librepcb-mcp" / "backups",
            cli_timeout=float(os.environ.get("LIBREPCB_MCP_CLI_TIMEOUT", "300")),
        )
