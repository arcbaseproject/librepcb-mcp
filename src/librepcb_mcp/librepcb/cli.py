"""Thin, injection-safe wrapper around ``librepcb-cli``."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import LibrePCBError

log = logging.getLogger(__name__)

_MSG_RE = re.compile(r"^\s*- \[(\w+)\] (.*)$")
_BOARD_RE = re.compile(r"^\s*Board '(.*)':\s*$")
_OUT_RE = re.compile(r"^\s*=> '(.*)'\s*$")


@dataclass
class CliResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class CheckMessage:
    severity: str
    message: str
    board: str | None = None  # None for ERC

    def as_dict(self) -> dict:
        d = {"severity": self.severity.lower(), "message": self.message}
        if self.board is not None:
            d["board"] = self.board
        return d


@dataclass
class CheckReport:
    erc: list[CheckMessage] = field(default_factory=list)
    drc: list[CheckMessage] = field(default_factory=list)
    erc_approved: int = 0
    drc_approved: dict[str, int] = field(default_factory=dict)
    ran_erc: bool = False
    ran_drc: bool = False
    output_files: list[str] = field(default_factory=list)


def parse_output(stdout: str) -> CheckReport:
    rep = CheckReport()
    section: str | None = None
    board: str | None = None
    for line in stdout.splitlines():
        s = line.strip()
        if s == "Run ERC...":
            section, rep.ran_erc = "erc", True
            continue
        if s == "Run DRC...":
            section, rep.ran_drc = "drc", True
            continue
        if s.startswith("Run "):
            section = None
        if m := _BOARD_RE.match(line):
            board = m.group(1)
            continue
        if m := _OUT_RE.match(line):
            rep.output_files.append(m.group(1))
            continue
        if s.startswith("Approved messages:"):
            n = int(s.split(":")[1])
            if section == "erc":
                rep.erc_approved = n
            elif section == "drc" and board is not None:
                rep.drc_approved[board] = n
            continue
        if (m := _MSG_RE.match(line)) and section:
            msg = CheckMessage(m.group(1), m.group(2), board if section == "drc" else None)
            (rep.erc if section == "erc" else rep.drc).append(msg)
    return rep


class LibrePCBCli:
    def __init__(self, command: tuple[str, ...] | None, timeout: float = 300) -> None:
        self.command = list(command) if command else None
        self.timeout = timeout
        self._version: str | None = None

    @property
    def available(self) -> bool:
        return self.command is not None

    def require(self) -> list[str]:
        if not self.command:
            raise LibrePCBError(
                "librepcb-cli not found. Install LibrePCB (>= 2.0) so that 'librepcb-cli' is on "
                "PATH, install the Flatpak 'org.librepcb.LibrePCB', or set LIBREPCB_CLI.")
        return self.command

    def run(self, *args: str | Path, timeout: float | None = None) -> CliResult:
        cmd = self.require() + [str(a) for a in args]
        log.debug("run %s", cmd)
        try:
            # stderr merged into stdout: the CLI prints rule-check messages to stderr
            # and section headers to stdout, flushing every line, so one pipe keeps
            # the order needed to attribute messages to ERC/DRC and boards.
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout or self.timeout,
                               check=False)
        except FileNotFoundError as e:
            raise LibrePCBError(f"cannot execute librepcb-cli ({cmd[0]}): {e}") from e
        except subprocess.TimeoutExpired as e:
            raise LibrePCBError(f"librepcb-cli timed out after {e.timeout}s: {' '.join(cmd)}") from e
        return CliResult(cmd, p.returncode, p.stdout, "")

    def version(self) -> str:
        if self._version is None:
            r = self.run("--version", timeout=60)
            if not r.ok:
                raise LibrePCBError(f"librepcb-cli --version failed: {r.stdout.strip()}")
            self._version = r.stdout.strip()
        return self._version

    def open_project(self, lpp: Path, *flags: str | Path) -> tuple[CliResult, CheckReport]:
        r = self.run("open-project", *flags, lpp)
        return r, parse_output(r.stdout)

    def verify_project(self, lpp: Path) -> CliResult:
        """Open strictly: fails on parse errors, broken references or files
        that LibrePCB would rewrite (non-canonical)."""
        r = self.run("open-project", "--strict", lpp)
        return r
