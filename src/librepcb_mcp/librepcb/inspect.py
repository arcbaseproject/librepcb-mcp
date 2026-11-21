"""Read-only views of a project as plain dicts (units: mm, degrees)."""

from __future__ import annotations

import math
import re

from . import geometry as g
from .board_ops import device_pads
from .circuit_ops import anchor_of, symbol_pins
from .project import Project
from .sexpr import SList, fmt_nm, fmt_udeg

_ATTR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)(?:\s+or\s+([A-Za-z0-9_]+))*\s*\}\}")


def _mm(nm: int) -> float:
    return float(fmt_nm(nm))


def _xy(p: g.Point) -> dict:
    return {"x_mm": _mm(p[0]), "y_mm": _mm(p[1])}


def _attr_text(attr: SList) -> str:
    v, unit = attr.child_arg("value"), attr.child_arg("unit")
    return f"{v} {unit}" if v and unit not in ("none", "") else v


def resolved_value(comp: SList) -> str:
    """Best-effort substitution of {{ATTR}} / {{A or B}} placeholders."""
    attrs = {a.arg(0): _attr_text(a) for a in comp.lists("attribute")}
    for opt in comp.lists("device"):
        for a in opt.lists("attribute"):
            attrs.setdefault(a.arg(0), _attr_text(a))

    def sub(m: re.Match) -> str:
        keys = re.findall(r"[A-Za-z0-9_]+", m.group(0).replace(" or ", " "))
        for k in keys:
            if attrs.get(k):
                return attrs[k]
        return m.group(0)

    return _ATTR_RE.sub(sub, comp.child_arg("value"))


def project_info(prj: Project) -> dict:
    md = prj.metadata
    return {
        "project_file": str(prj.lpp), "directory": str(prj.dir), "file_format": prj.file_format,
        "uuid": md.arg(0), "name": md.child_arg("name"), "author": md.child_arg("author"),
        "version": md.child_arg("version"), "created": md.child_arg("created"),
        "schematics": list_schematics(prj), "boards": list_boards(prj),
        "assembly_variants": [v.child_arg("name") for v in prj.circuit.lists("variant")],
        "component_count": len(prj.components()), "net_count": len(prj.nets()),
        "project_library": {k: len(list((prj.dir / "library" / k).glob("*")))
                            if (prj.dir / "library" / k).is_dir() else 0
                            for k in ("cmp", "dev", "pkg", "sym")},
        "locked_by_librepcb": (prj.dir / ".lock").exists(),
    }


def list_schematics(prj: Project) -> list[dict]:
    out = []
    for i, rel in enumerate(prj.schematic_paths()):
        s = prj.file(rel)
        out.append({"index": i, "id": s.arg(0), "name": s.child_arg("name"), "path": rel,
                    "symbols": len(list(s.lists("symbol"))),
                    "net_segments": len(list(s.lists("netsegment")))})
    return out


def list_boards(prj: Project) -> list[dict]:
    out = []
    for i, rel in enumerate(prj.board_paths()):
        b = prj.file(rel)
        out.append({"index": i, "id": b.arg(0), "name": b.child_arg("name"), "path": rel,
                    "devices": len(list(b.lists("device")))})
    return out


def component(prj: Project, comp: SList, detailed: bool = False) -> dict:
    lib_cmp = prj.lib("cmp", comp.child_arg("lib_component"))
    sig_names = prj.signal_names(comp)
    d: dict = {"id": comp.arg(0), "name": comp.child_arg("name"),
               "value": comp.child_arg("value"), "value_resolved": resolved_value(comp),
               "library_component": lib_cmp.child_arg("name")}
    syms = prj.symbols_of(comp.arg(0))
    devs = prj.devices_of(comp.arg(0))
    d["placed_in_schematic"] = bool(syms)
    d["placed_on_boards"] = [prj.file(r).child_arg("name") for r, _ in devs]
    if not detailed:
        return d
    d["attributes"] = {a.arg(0): _attr_text(a) for a in comp.lists("attribute")}
    d["signals"] = [{"signal": sig_names.get(s.arg(0), s.arg(0)),
                     "net": prj.net_name(s.child_arg("net")) if s.child_arg("net") != "none" else None}
                    for s in comp.lists("signal")]
    d["assembly_options"] = []
    for opt in comp.lists("device"):
        name = prj.lib("dev", opt.arg(0)).child_arg("name") if prj.has_lib("dev", opt.arg(0)) else None
        d["assembly_options"].append({"device_id": opt.arg(0), "device_name": name})
    d["symbols"] = []
    for rel, s in syms:
        d["symbols"].append({
            "symbol_id": s.arg(0), "schematic": prj.file(rel).child_arg("name"),
            "position": _xy(g.read_pos(s)), "rotation_deg": float(fmt_udeg(g.read_rot(s))),
            "mirror": s.child_arg("mirror") == "true",
            "pins": [{"pin": p["name"], "signal": p["signal_name"], "position": _xy(p["pos"])}
                     for p in symbol_pins(prj, comp, s)]})
    d["board_devices"] = []
    for rel, dev in devs:
        lib_dev = prj.lib("dev", dev.child_arg("lib_device"))
        d["board_devices"].append({
            "board": prj.file(rel).child_arg("name"), "device": lib_dev.child_arg("name"),
            "position": _xy(g.read_pos(dev)), "rotation_deg": float(fmt_udeg(g.read_rot(dev))),
            "flip": dev.child_arg("flip") == "true", "locked": dev.child_arg("lock") == "true",
            "pads": [{"pad": p.label, "net": prj.net_name(p.net) if p.net else None,
                      "position": _xy(p.pos)} for p in device_pads(prj, comp, dev)]})
    return d


def _pin_labels(prj: Project) -> dict[tuple[str, str], str]:
    """(symbol uuid, pin uuid) -> 'R1:1'"""
    out = {}
    for comp in prj.components():
        for _, s in prj.symbols_of(comp.arg(0)):
            for p in symbol_pins(prj, comp, s):
                out[(s.arg(0), p["pin"])] = f"{comp.child_arg('name')}:{p['name']}"
    return out


def net_connections(prj: Project, net: SList) -> dict:
    uuid = net.arg(0)
    pins = []
    for comp in prj.components():
        names = None
        for s in comp.lists("signal"):
            if s.child_arg("net") == uuid:
                names = names or prj.signal_names(comp)
                pins.append(f"{comp.child_arg('name')}:{names.get(s.arg(0), s.arg(0))}")
    seg_count = sum(1 for rel in prj.schematic_paths()
                    for s in prj.file(rel).lists("netsegment") if s.child_arg("net") == uuid)
    labels = sum(len(list(s.lists("label"))) for rel in prj.schematic_paths()
                 for s in prj.file(rel).lists("netsegment") if s.child_arg("net") == uuid)
    traces = vias = 0
    for rel in prj.board_paths():
        for s in prj.file(rel).lists("netsegment"):
            if s.child_arg("net") == uuid:
                traces += len(list(s.lists("trace")))
                vias += len(list(s.lists("via")))
    return {"id": uuid, "name": net.child_arg("name"), "auto_named": net.child_arg("auto") == "true",
            "component_signals": sorted(pins), "schematic_segments": seg_count,
            "net_labels": labels, "board_traces": traces, "board_vias": vias}


def list_nets(prj: Project) -> list[dict]:
    return [net_connections(prj, n) for n in prj.nets()]


def schematic(prj: Project, sch_rel: str) -> dict:
    sch = prj.file(sch_rel)
    labels = _pin_labels(prj)
    comps = {c.arg(0): c for c in prj.components()}

    def end(e: SList, juncs: dict) -> str | dict:
        a = anchor_of(e)
        if a[0] == "junction":
            return {"junction": a[1], **juncs.get(a[1], {})}
        return labels.get((a[1], a[2]), f"{a[1]}:{a[2]}")

    symbols = []
    for s in sch.lists("symbol"):
        c = comps.get(s.child_arg("component"))
        symbols.append({"component": c.child_arg("name") if c else s.child_arg("component"),
                        "symbol_id": s.arg(0), "position": _xy(g.read_pos(s)),
                        "rotation_deg": float(fmt_udeg(g.read_rot(s))),
                        "mirror": s.child_arg("mirror") == "true"})
    segments = []
    for seg in sch.lists("netsegment"):
        juncs = {j.arg(0): _xy(g.read_pos(j)) for j in seg.lists("junction")}
        segments.append({
            "id": seg.arg(0), "net": prj.net_name(seg.child_arg("net")),
            "wires": [{"from": end(ln.req("from"), juncs), "to": end(ln.req("to"), juncs)}
                      for ln in seg.lists("line")],
            "labels": [_xy(g.read_pos(lb)) for lb in seg.lists("label")]})
    return {"id": sch.arg(0), "name": sch.child_arg("name"), "path": sch_rel,
            "symbols": symbols, "net_segments": segments}


def board(prj: Project, brd_rel: str) -> dict:
    brd = prj.file(brd_rel)
    comps = {c.arg(0): c for c in prj.components()}
    outline = [[_xy(g.read_pos(v)) for v in p.lists("vertex")]
               for p in brd.lists("polygon") if p.child_arg("layer") == "brd_outlines"]
    devices, pads_by_net = [], {}
    for dev in brd.lists("device"):
        c = comps.get(dev.arg(0))
        if c is None:
            continue
        lib_dev = prj.lib("dev", dev.child_arg("lib_device"))
        pads = device_pads(prj, c, dev)
        for p in pads:
            if p.net:
                pads_by_net.setdefault(p.net, []).append(p)
        devices.append({"component": c.child_arg("name"), "device": lib_dev.child_arg("name"),
                        "position": _xy(g.read_pos(dev)),
                        "rotation_deg": float(fmt_udeg(g.read_rot(dev))),
                        "flip": dev.child_arg("flip") == "true"})
    # connectivity: pads touched by the same segment are connected (planes ignored)
    routing = []
    seg_pads: dict[str, list[set]] = {}
    traces_total = vias_total = 0
    length: dict[str, float] = {}
    for seg in brd.lists("netsegment"):
        net = seg.child_arg("net")
        touched = {(a[1], a[2]) for tr in seg.lists("trace")
                   for a in (anchor_of(tr.req("from")), anchor_of(tr.req("to"))) if a[0] == "pad"}
        seg_pads.setdefault(net, []).append(touched)
        traces_total += len(list(seg.lists("trace")))
        vias_total += len(list(seg.lists("via")))
        pos = {j.arg(0): g.read_pos(j) for j in seg.lists("junction")}
        pos |= {v.arg(0): g.read_pos(v) for v in seg.lists("via")}
        for tr in seg.lists("trace"):
            pts = []
            for e in (tr.req("from"), tr.req("to")):
                a = anchor_of(e)
                if a[0] == "pad":
                    p = next((p for p in pads_by_net.get(net, []) if (p.comp.arg(0), p.pad) == a[1:]), None)
                    pts.append(p.pos if p else None)
                else:
                    pts.append(pos.get(a[1]))
            if None not in pts:
                length[net] = length.get(net, 0) + math.dist(pts[0], pts[1]) / 1e6
    has_plane = {p.child_arg("net") for p in brd.lists("plane")}
    for net, pads in sorted(pads_by_net.items(), key=lambda kv: prj.net_name(kv[0]) or ""):
        groups = [{(p.comp.arg(0), p.pad)} for p in pads]
        for touched in seg_pads.get(net, []):
            hit = [gr for gr in groups if gr & touched]
            if len(hit) > 1:
                groups = [gr for gr in groups if not gr & touched] + [set().union(*hit)]
        routing.append({"net": prj.net_name(net), "pads": [p.label for p in pads],
                        "unrouted_connections": len(groups) - 1,
                        "trace_length_mm": round(length.get(net, 0.0), 3),
                        "has_plane": net in has_plane})
    rules = brd.req("design_rules")
    return {"id": brd.arg(0), "name": brd.child_arg("name"), "path": brd_rel,
            "thickness_mm": float(brd.child_arg("thickness")),
            "inner_copper_layers": int(brd.req("layers").child_arg("inner")),
            "default_trace_width_mm": float(rules.child_arg("default_trace_width")),
            "default_via_drill_mm": float(rules.child_arg("default_via_drill_diameter")),
            "outline": outline, "devices": devices, "trace_count": traces_total,
            "via_count": vias_total, "routing": routing,
            "unplaced_components": sorted(c.child_arg("name") for u, c in comps.items()
                                          if brd.find_uuid("device", u) is None
                                          and prj.lib("cmp", c.child_arg("lib_component"))
                                          .child_arg("schematic_only") != "true"),
            "note": "unrouted_connections counts pad groups not joined by traces; copper planes "
                    "are not evaluated (see has_plane). Run run_drc for authoritative results."}
