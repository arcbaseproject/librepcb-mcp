from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..librepcb.errors import LibrePCBError
from ..librepcb.project import is_uuid
from ..service import Service
from . import registrar


def register(mcp, svc: Service) -> None:  # noqa: ANN001
    tool = registrar(mcp)
    lib = svc.lib

    def _need_libs() -> None:
        if not lib.libraries():
            raise LibrePCBError("No libraries found. Install libraries in LibrePCB (Library "
                                "Manager) or set LIBREPCB_MCP_LIBRARIES to *.lplib directories.")

    @tool(read_only=True)
    def list_libraries() -> dict:
        """List installed LibrePCB libraries used for component search."""
        idx = lib.index()
        out = []
        for p in lib.libraries():
            out.append({"name": p.name, "path": str(p),
                        "components": sum(1 for e in idx["cmp"].values() if e.library == p.name),
                        "devices": sum(1 for e in idx["dev"].values() if e.library == p.name)})
        return {"libraries": out}

    @tool(read_only=True)
    def search_components(
        query: Annotated[str, Field(description="Words matched against name, keywords, "
                                                "description, e.g. 'resistor 0603', 'led red'")],
        limit: int = 30,
    ) -> dict:
        """Search library DEVICES (a component + a package/footprint, what add_component
        needs). Returns device id, component and package names."""
        _need_libs()
        out = []
        for el in lib.search(query, "dev", limit):
            d = el.summary()
            cmp_el = lib.index()["cmp"].get(el.refs["component"])
            d["component"] = {"id": el.refs["component"], "name": cmp_el.name if cmp_el else None}
            d["package_id"] = el.refs["package"]
            out.append(d)
        if not out:  # components without devices (e.g. schematic-only) are still useful
            for el in lib.search(query, "cmp", limit):
                d = el.summary()
                d["note"] = "component without matching device query; see get_component_details"
                out.append(d)
        return {"results": out}

    @tool(read_only=True)
    def get_component_details(component_id: Annotated[str, Field(description="Library component UUID")]) -> dict:
        """Library component: signals (pins), symbol variants/gates, attributes, prefix,
        default value, and all devices implementing it."""
        _need_libs()
        el = lib.locate("cmp", component_id)
        root = lib.read(el)
        return {**el.summary(), "prefix": root.child_arg("prefix"),
                "default_value": root.child_arg("default_value"),
                "schematic_only": root.child_arg("schematic_only") == "true",
                "attributes": {a.arg(0): a.child_arg("value") for a in root.lists("attribute")},
                "signals": [{"id": s.arg(0), "name": s.child_arg("name"),
                             "required": s.child_arg("required") == "true"} for s in root.lists("signal")],
                "symbol_variants": [{"id": v.arg(0), "name": v.child_arg("name"),
                                     "gates": len(list(v.lists("gate")))} for v in root.lists("variant")],
                "devices": [{"id": d.uuid, "name": d.name, "library": d.library}
                            for d in lib.index()["dev"].values() if d.refs["component"] == component_id]}

    @tool(read_only=True)
    def get_device_details(device: Annotated[str, Field(description="Device UUID or exact name")]) -> dict:
        """Library device: its component, package, footprints and pad -> signal mapping."""
        _need_libs()
        el = lib.resolve_device(device)
        dev = lib.read(el)
        cmp = lib.read(lib.locate("cmp", el.refs["component"]))
        pkg = lib.read(lib.locate("pkg", el.refs["package"]))
        sigs = {s.arg(0): s.child_arg("name") for s in cmp.lists("signal")}
        pads = {p.arg(0): p.child_arg("name") for p in pkg.lists("pad")}
        return {**el.summary(), "component": {"id": cmp.arg(0), "name": cmp.child_arg("name")},
                "package": {"id": pkg.arg(0), "name": pkg.child_arg("name"),
                            "assembly_type": pkg.child_arg("assembly_type") if pkg.get("assembly_type") else None},
                "footprints": [f.child_arg("name") for f in pkg.lists("footprint")],
                "pads": [{"pad": pads.get(p.arg(0), p.arg(0)),
                          "signal": sigs.get(p.child_arg("signal")) if p.child_arg("signal") != "none" else None}
                         for p in dev.lists("pad")]}

    @tool(read_only=True)
    def list_footprints(
        package_or_device: Annotated[str, Field(description="Package UUID, device UUID, or device name")],
    ) -> dict:
        """Footprints of a package (resolved from a device if given), with pad counts."""
        _need_libs()
        if is_uuid(package_or_device) and package_or_device not in lib.index()["dev"]:
            pkg_uuid = package_or_device
        else:
            pkg_uuid = lib.resolve_device(package_or_device).refs["package"]
        pkg = lib.read(lib.locate("pkg", pkg_uuid))
        return {"package": {"id": pkg_uuid, "name": pkg.child_arg("name")},
                "footprints": [{"id": f.arg(0), "name": f.child_arg("name"),
                                "pads": len(list(f.lists("pad"))),
                                "has_3d_model": f.get("3d_model") is not None}
                               for f in pkg.lists("footprint")]}
