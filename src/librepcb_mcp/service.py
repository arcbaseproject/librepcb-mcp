"""Glue between MCP tools and the LibrePCB integration layer."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .librepcb import jobs, transaction
from .librepcb.cli import CheckReport, LibrePCBCli
from .librepcb.errors import LibrePCBError
from .librepcb.library import LibraryIndex
from .librepcb.project import Project, check_inside_roots

log = logging.getLogger(__name__)

SKIP_DIRS = {"output", ".git", "library", "node_modules", ".venv"}


class Service:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cli = LibrePCBCli(cfg.cli, cfg.cli_timeout)
        self.lib = LibraryIndex(cfg.libraries)

    # ---- projects ---------------------------------------------------------------
    def open(self, path: str) -> Project:
        return Project(Path(path), self.cfg.roots)

    def edit(self, path: str, reason: str, fn: Callable[[Project], dict]) -> dict:
        with transaction.edit(Path(path), self.cfg.roots, self.cli, self.cfg.backup_dir,
                              reason) as (prj, res):
            out = fn(prj)
        return {"success": True, **out, "changed_files": res.changed_files,
                "backup": str(res.backup) if res.backup else None,
                "verified": "project re-opened with 'librepcb-cli open-project --strict'"}

    def list_projects(self, root: str | None) -> list[dict]:
        roots = [check_inside_roots(Path(root), self.cfg.roots)] if root else list(self.cfg.roots)
        found = []
        for r in roots:
            stack = [(r, 0)]
            while stack:
                d, depth = stack.pop()
                try:
                    entries = sorted(d.iterdir())
                except OSError:
                    continue
                if (d / ".librepcb-project").is_file():
                    for lpp in d.glob("*.lpp"):
                        found.append({"project_file": str(lpp), "name": lpp.stem,
                                      "file_format": (d / ".librepcb-project").read_text().strip()})
                    continue
                if depth < 6:
                    stack += [(e, depth + 1) for e in entries
                              if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".")]
        return sorted(found, key=lambda x: x["project_file"])

    # ---- validation -------------------------------------------------------------
    def check(self, path: str, erc: bool, drc: bool, strict: bool = False,
              board: str | None = None) -> dict:
        prj = self.open(path)
        flags: list[str] = []
        if strict:
            flags.append("--strict")
        if erc:
            flags.append("--erc")
        if drc:
            flags.append("--drc")
            if board:
                flags += ["--board", prj.board(board)[1].child_arg("name")]
        r, rep = self.cli.open_project(prj.lpp, *flags)
        noncanon = [ln.split("'", 1)[1].rstrip("'") for ln in r.stdout.splitlines()
                    if "Non-canonical file:" in ln]
        failed = r.returncode not in (0, 1) or (erc and not rep.ran_erc) or \
            (drc and not rep.ran_drc) or (r.returncode == 1 and not (rep.erc or rep.drc or noncanon))
        if failed:
            raise LibrePCBError(f"librepcb-cli failed (exit {r.returncode}):\n{(r.stdout + r.stderr).strip()}")
        out: dict = {"project_file": str(prj.lpp)}
        if strict:
            out["opens_in_librepcb"] = True
            out["non_canonical_files"] = noncanon
        if erc:
            out["erc"] = self._section(rep, "erc")
        if drc:
            out["drc"] = self._section(rep, "drc")
        if r.stderr.strip():
            out["cli_stderr"] = r.stderr.strip()
        out["note"] = ("Passing ERC/DRC does not mean the design is electrically correct or "
                       "ready for fabrication.")
        return out

    @staticmethod
    def _section(rep: CheckReport, which: str) -> dict:
        msgs = rep.erc if which == "erc" else rep.drc
        counts: dict[str, int] = {}
        for m in msgs:
            counts[m.severity.lower()] = counts.get(m.severity.lower(), 0) + 1
        return {"passed": not msgs, "non_approved_messages": len(msgs), "by_severity": counts,
                "approved_messages": rep.erc_approved if which == "erc" else rep.drc_approved,
                "messages": [m.as_dict() for m in msgs]}

    # ---- exports ------------------------------------------------------------------
    def export(self, path: str, kinds: list[str], board: str | None = None,
               output_dir: str | None = None) -> dict:
        prj = self.open(path)
        out = Path(output_dir) if output_dir else prj.dir / "output" / "mcp"
        out = check_inside_roots(out, self.cfg.roots)
        board_uuid = None
        if any(k in ("board_pdf", "gerber") for k in kinds) or (board and "bom" in kinds):
            board_uuid = prj.board(board)[1].arg(0)
        builders = {"schematic_pdf": jobs.schematic_pdf,
                    "board_pdf": lambda: jobs.board_pdf(board_uuid),  # type: ignore[arg-type]
                    "gerber": lambda: jobs.gerber_excellon(board_uuid),  # type: ignore[arg-type]
                    "bom": lambda: jobs.bom(board_uuid), "lppz": jobs.lppz}
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out, prefix=".librepcb-mcp-") as temp:
            jobs_path = Path(temp) / "jobs.lp"
            jobs_path.write_bytes(jobs.jobs_file(*(builders[k]() for k in kinds)))
            r, rep = self.cli.open_project(prj.lpp, "--jobs", jobs_path, "--run-jobs",
                                           "--outdir", out)
        if not r.ok:
            raise LibrePCBError(f"Export failed (exit {r.returncode}):\n{(r.stdout + r.stderr).strip()}")
        missing = [f for f in rep.output_files if not Path(f).is_file()]
        if missing or not rep.output_files:
            raise LibrePCBError(f"librepcb-cli reported success but files are missing: {missing}\n{r.stdout}")
        return {"success": True, "output_dir": str(out), "files": rep.output_files}

    def run_project_jobs(self, path: str, names: list[str] | None,
                         output_dir: str | None) -> dict:
        prj = self.open(path)
        flags: list[str | Path] = []
        for n in names or []:
            flags += ["--run-job", n]
        if not names:
            flags.append("--run-jobs")
        if output_dir:
            flags += ["--outdir", check_inside_roots(Path(output_dir), self.cfg.roots)]
        r, rep = self.cli.open_project(prj.lpp, *flags)
        if not r.ok:
            raise LibrePCBError(f"Output jobs failed (exit {r.returncode}):\n{(r.stdout + r.stderr).strip()}")
        return {"success": True, "files": rep.output_files}

    # ---- environment --------------------------------------------------------------
    def status(self) -> dict:
        d = {"roots": [str(r) for r in self.cfg.roots],
             "libraries": [str(p) for p in self.lib.libraries()],
             "backup_dir": str(self.cfg.backup_dir),
             "cli_command": self.cli.command, "cli_available": self.cli.available}
        if self.cli.available:
            try:
                d["cli_version"] = self.cli.version()
            except LibrePCBError as e:
                d["cli_error"] = str(e)
        return d
