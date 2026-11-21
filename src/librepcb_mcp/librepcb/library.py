"""Index of installed LibrePCB libraries (*.lplib directories)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import sexpr
from .errors import LibrePCBError
from .project import LIB_KINDS, is_uuid
from .sexpr import SList

log = logging.getLogger(__name__)


def _version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


@dataclass
class Element:
    kind: str  # cmp | dev | pkg | sym
    uuid: str
    name: str
    description: str
    keywords: str
    version: str
    deprecated: bool
    dir: Path
    library: str
    refs: dict[str, str]  # dev: component/package uuids

    def summary(self) -> dict:
        d = {"id": self.uuid, "name": self.name, "library": self.library,
             "version": self.version}
        if self.description:
            d["description"] = self.description[:300]
        if self.keywords:
            d["keywords"] = self.keywords
        if self.deprecated:
            d["deprecated"] = True
        return d


def find_lplibs(paths: tuple[Path, ...]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.suffix == ".lplib" and p.is_dir():
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(x for x in p.glob("*.lplib") if x.is_dir()))
            out.extend(sorted(x for x in p.glob("*/*.lplib") if x.is_dir()))
    return out


class LibraryIndex:
    """Lazy index of components and devices (packages/symbols on demand)."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        self._index: dict[str, dict[str, Element]] | None = None
        self._stamp: tuple | None = None

    def _current_stamp(self, libs: list[Path]) -> tuple:
        return tuple((str(lib), (lib / k).stat().st_mtime_ns if (lib / k).exists() else 0)
                     for lib in libs for k in ("cmp", "dev"))

    def libraries(self) -> list[Path]:
        return find_lplibs(self.paths)

    def _load_element(self, kind: str, d: Path, lib: Path) -> Element | None:
        f = d / f"{LIB_KINDS[kind]}.lp"
        try:
            root = sexpr.parse(f.read_bytes())
            uuid = root.arg(0)
            if not is_uuid(uuid):
                raise sexpr.SExprError(f"invalid element UUID {uuid!r}")
        except (OSError, UnicodeDecodeError, sexpr.SExprError) as e:
            log.warning("skip unreadable %s: %s", f, e)
            return None

        def opt(name: str) -> str:
            n = root.get(name)
            return n.arg(0) if n is not None and n.atoms() else ""

        refs = {}
        if kind == "dev":
            refs = {"component": opt("component"), "package": opt("package")}
        return Element(kind, uuid, opt("name"), opt("description"), opt("keywords"),
                       opt("version"), opt("deprecated") == "true", d, lib.name, refs)

    def index(self) -> dict[str, dict[str, Element]]:
        libs = self.libraries()
        stamp = self._current_stamp(libs)
        if self._index is None or stamp != self._stamp:
            idx: dict[str, dict[str, Element]] = {"cmp": {}, "dev": {}}
            for lib in libs:
                for kind in ("cmp", "dev"):
                    for d in sorted((lib / kind).glob("*")):
                        if not is_uuid(d.name) or not d.is_dir():
                            continue
                        el = self._load_element(kind, d, lib)
                        if el is None:
                            continue
                        prev = idx[kind].get(el.uuid)
                        if prev is None or _version_key(el.version) > _version_key(prev.version):
                            idx[kind][el.uuid] = el
            self._index, self._stamp = idx, stamp
        return self._index

    def locate(self, kind: str, uuid: str) -> Element:
        """Newest copy of an element across all libraries."""
        if kind in ("cmp", "dev"):
            el = self.index()[kind].get(uuid)
            if el:
                return el
        else:
            best: Element | None = None
            for lib in self.libraries():
                d = lib / kind / uuid
                if d.is_dir():
                    el = self._load_element(kind, d, lib)
                    if el and (best is None or _version_key(el.version) > _version_key(best.version)):
                        best = el
            if best:
                return best
        raise LibrePCBError(
            f"{LIB_KINDS[kind]} {uuid} not found in configured libraries "
            f"({[str(p) for p in self.paths] or 'none configured: set LIBREPCB_MCP_LIBRARIES'})")

    def read(self, el: Element) -> SList:
        return sexpr.parse((el.dir / f"{LIB_KINDS[el.kind]}.lp").read_bytes())

    def search(self, query: str, kind: str = "dev", limit: int = 30,
               include_deprecated: bool = False) -> list[Element]:
        words = [w.lower() for w in query.split() if w]
        scored = []
        for el in self.index()[kind].values():
            if el.deprecated and not include_deprecated:
                continue
            name, hay = el.name.lower(), f"{el.name} {el.keywords} {el.description}".lower()
            if all(w in hay for w in words):
                score = sum(3 if w in name else 1 for w in words) + (5 if query.lower() == name else 0)
                scored.append((-score, el.name, el))
        return [el for _, _, el in sorted(scored, key=lambda t: (t[0], t[1]))[:limit]]

    def resolve_device(self, ident: str) -> Element:
        devs = self.index()["dev"]
        if is_uuid(ident):
            if ident in devs:
                return devs[ident]
            raise LibrePCBError(f"Device {ident} not found in libraries. Use search_components.")
        exact = [d for d in devs.values() if d.name.lower() == ident.lower()]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise LibrePCBError(f"Device name '{ident}' is ambiguous; pass a device UUID: "
                                f"{[(d.uuid, d.library) for d in exact]}")
        raise LibrePCBError(f"No device named '{ident}'. Use search_components to find a device UUID.")
