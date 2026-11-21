"""Read access to a LibrePCB 2.x project directory, plus change tracking.

A ``Project`` is loaded fresh for every tool call (files are parsed lazily),
so there is no stale cache to invalidate after edits.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import sexpr
from .errors import LibrePCBError
from .sexpr import SList

SUPPORTED_FILE_FORMATS = {"2"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LIB_KINDS = {"cmp": "component", "dev": "device", "pkg": "package", "sym": "symbol"}


def is_uuid(s: str) -> bool:
    return bool(UUID_RE.match(s))


def check_inside_roots(path: Path, roots: tuple[Path, ...]) -> Path:
    real = path.expanduser().resolve()
    if not roots:
        raise LibrePCBError(
            "No project directories configured. Set LIBREPCB_MCP_ROOTS to the directories "
            "the server may access (os.pathsep-separated).")
    if not any(real == r or real.is_relative_to(r) for r in roots):
        raise LibrePCBError(
            f"Path {real} is outside the configured project roots "
            f"({', '.join(map(str, roots))}). Adjust LIBREPCB_MCP_ROOTS.")
    return real


def find_lpp(path: Path) -> Path:
    if path.is_file() and path.suffix == ".lpp":
        return path
    if path.suffix == ".lppz":
        raise LibrePCBError("*.lppz archives are read-only bundles; extract them first.")
    if path.is_dir():
        lpps = sorted(path.glob("*.lpp"))
        if len(lpps) == 1:
            return lpps[0]
        if not lpps:
            raise LibrePCBError(f"No *.lpp project file in {path}")
        raise LibrePCBError(f"Multiple *.lpp files in {path}: {[p.name for p in lpps]}")
    raise LibrePCBError(f"Project not found: {path}")


class Project:
    def __init__(self, path: Path, roots: tuple[Path, ...]) -> None:
        self.lpp = find_lpp(check_inside_roots(path, roots))
        self.dir = self.lpp.parent
        self.roots = roots
        version_file = self.dir / ".librepcb-project"
        if not version_file.is_file():
            raise LibrePCBError(f"{self.dir} is not a LibrePCB project (no .librepcb-project)")
        self.file_format = version_file.read_text().strip()
        if self.file_format not in SUPPORTED_FILE_FORMATS:
            raise LibrePCBError(
                f"Unsupported project file format '{self.file_format}' (supported: "
                f"{sorted(SUPPORTED_FILE_FORMATS)}). Upgrade it with LibrePCB 2.x: "
                f"librepcb-cli open-project --save '{self.lpp}'")
        self._files: dict[str, SList] = {}
        self._digests: dict[str, str | None] = {}  # rel -> sha256 at read time
        self.dirty: set[str] = set()
        self.new_dirs: list[tuple[Path, Path]] = []  # (src, dst) library dirs to copy

    # ---- file access -----------------------------------------------------
    def file(self, rel: str) -> SList:
        if rel not in self._files:
            p = self.dir / rel
            try:
                data = p.read_bytes()
                self._digests[rel] = hashlib.sha256(data).hexdigest()
                self._files[rel] = sexpr.parse(data)
            except FileNotFoundError as e:
                raise LibrePCBError(f"Missing project file: {rel}") from e
            except sexpr.SExprError as e:
                raise LibrePCBError(f"Cannot parse {rel}: {e}") from e
        return self._files[rel]

    def set_file(self, rel: str, root: SList) -> None:
        if rel not in self._digests:
            p = self.dir / rel
            self._digests[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        self._files[rel] = root
        self.dirty.add(rel)

    def check_unchanged_on_disk(self) -> None:
        for rel in self.dirty:
            p = self.dir / rel
            now = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
            if now != self._digests.get(rel):
                raise LibrePCBError(f"{rel} was modified by another program during the edit; "
                                    "nothing was written. Retry the operation.")

    def touch(self, rel: str) -> None:
        self.file(rel)
        self.dirty.add(rel)

    def rendered(self) -> dict[str, bytes]:
        return {rel: self._files[rel].to_bytes() for rel in sorted(self.dirty)}

    @property
    def circuit(self) -> SList:
        return self.file("circuit/circuit.lp")

    @property
    def metadata(self) -> SList:
        return self.file("project/metadata.lp")

    # ---- schematics / boards --------------------------------------------
    def schematic_paths(self) -> list[str]:
        return [n.arg(0) for n in self.file("schematics/schematics.lp").lists("schematic")]

    def board_paths(self) -> list[str]:
        return [n.arg(0) for n in self.file("boards/boards.lp").lists("board")]

    def _pick(self, paths: list[str], ident: str | None, kind: str) -> str:
        if not paths:
            raise LibrePCBError(f"Project has no {kind}s. Create one first (create_{kind}).")
        if ident is None or ident == "":
            return paths[0]
        for p in paths:
            root = self.file(p)
            if ident in (root.arg(0), root.child_arg("name"), p):
                return p
        if ident.isdigit() and int(ident) < len(paths):
            return paths[int(ident)]
        names = [self.file(p).child_arg("name") for p in paths]
        raise LibrePCBError(f"No {kind} '{ident}'. Available: {names}")

    def schematic(self, ident: str | None = None) -> tuple[str, SList]:
        rel = self._pick(self.schematic_paths(), ident, "schematic")
        return rel, self.file(rel)

    def board(self, ident: str | None = None) -> tuple[str, SList]:
        rel = self._pick(self.board_paths(), ident, "board")
        return rel, self.file(rel)

    # ---- library ------------------------------------------------------------
    def lib(self, kind: str, uuid: str) -> SList:
        rel = f"library/{kind}/{uuid}/{LIB_KINDS[kind]}.lp"
        return self.file(rel)

    def has_lib(self, kind: str, uuid: str) -> bool:
        return (self.dir / "library" / kind / uuid).is_dir()

    # ---- circuit lookups --------------------------------------------------
    def components(self) -> list[SList]:
        return list(self.circuit.lists("component"))

    def component(self, ident: str) -> SList:
        for c in self.circuit.lists("component"):
            if ident in (c.arg(0), c.child_arg("name")):
                return c
        raise LibrePCBError(f"No component '{ident}' (use a designator like 'R1' or a UUID). "
                            f"Available: {[c.child_arg('name') for c in self.components()][:50]}")

    def nets(self) -> list[SList]:
        return list(self.circuit.lists("net"))

    def net(self, ident: str) -> SList:
        for n in self.circuit.lists("net"):
            if ident in (n.arg(0), n.child_arg("name")):
                return n
        raise LibrePCBError(f"No net '{ident}'. Available: {[n.child_arg('name') for n in self.nets()][:80]}")

    def net_name(self, uuid: str) -> str | None:
        n = self.circuit.find_uuid("net", uuid)
        return n.child_arg("name") if n else None

    def signal_names(self, comp: SList) -> dict[str, str]:
        lib_cmp = self.lib("cmp", comp.child_arg("lib_component"))
        return {s.arg(0): s.child_arg("name") for s in lib_cmp.lists("signal")}

    def gate_pins(self, comp: SList, gate_uuid: str) -> dict[str, str | None]:
        """pin uuid -> signal uuid (None if unconnected) for one gate."""
        lib_cmp = self.lib("cmp", comp.child_arg("lib_component"))
        var = lib_cmp.find_uuid("variant", comp.child_arg("lib_variant"))
        if var is None:
            raise LibrePCBError(f"Component {comp.child_arg('name')}: symbol variant missing in library")
        gate = var.find_uuid("gate", gate_uuid)
        if gate is None:
            raise LibrePCBError(f"Component {comp.child_arg('name')}: gate {gate_uuid} missing")
        out: dict[str, str | None] = {}
        for p in gate.lists("pin"):
            sig = p.child_arg("signal")
            out[p.arg(0)] = None if sig == "none" else sig
        return out

    def symbols_of(self, comp_uuid: str) -> list[tuple[str, SList]]:
        out = []
        for rel in self.schematic_paths():
            for s in self.file(rel).lists("symbol"):
                if s.child_arg("component") == comp_uuid:
                    out.append((rel, s))
        return out

    def devices_of(self, comp_uuid: str) -> list[tuple[str, SList]]:
        out = []
        for rel in self.board_paths():
            d = self.file(rel).find_uuid("device", comp_uuid)
            if d is not None:
                out.append((rel, d))
        return out
