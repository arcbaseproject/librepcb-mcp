"""Board modifications, mirroring CmdAddDeviceToBoard and the board editor."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from . import geometry as g
from .circuit_ops import (anchor_of, anchor_sort_key, ensure_lib, merge_segments,
                          signal_net)
from .errors import LibrePCBError
from .library import LibraryIndex
from .project import Project
from .sexpr import NL, L, SList, Token, fmt_mm, fmt_nm, fmt_udeg, mm_to_nm

COPPER_OUTER = ("top_cu", "bot_cu")


def mirror_layer(layer: str) -> str:
    if layer.startswith("top_"):
        return "bot_" + layer[4:]
    if layer.startswith("bot_"):
        return "top_" + layer[4:]
    return layer


def _xy(p: g.Point) -> dict:
    return {"x_mm": float(fmt_nm(p[0])), "y_mm": float(fmt_nm(p[1]))}


# ---- stroke texts -------------------------------------------------------------

def _map_text(t: SList, pos: g.Point, rot: int, flip: bool) -> None:
    """BI_Device::getDefaultStrokeTexts transform, applied in place."""
    g.set_pos(t, g.transform(g.read_pos(t), pos, rot, flip))
    trot = g.read_rot(t)
    g.set_rot(t, g.add_angles(rot, -trot) if flip else g.add_angles(rot, trot))
    if flip:
        m = t.req("mirror")
        m.set_arg(0, "false" if m.arg(0) == "true" else "true")
        layer = t.req("layer")
        layer.set_arg(0, mirror_layer(layer.arg(0)))


def _board_text(fp_text: SList) -> SList:
    """Footprint (stroke_text) -> board layout (BoardStrokeTextData::serialize)."""
    a = fp_text
    return L("stroke_text", a.arg(0), copy.deepcopy(a.req("layer")), NL,
             copy.deepcopy(a.req("height")), copy.deepcopy(a.req("stroke_width")),
             copy.deepcopy(a.req("letter_spacing")), copy.deepcopy(a.req("line_spacing")), NL,
             copy.deepcopy(a.req("align")), copy.deepcopy(a.req("position")),
             copy.deepcopy(a.req("rotation")), NL,
             copy.deepcopy(a.req("auto_rotate")), copy.deepcopy(a.req("mirror")),
             copy.deepcopy(a.req("lock")), copy.deepcopy(a.req("value")), NL)


def _default_texts(fpt: SList, pos: g.Point, rot: int, flip: bool) -> list[SList]:
    out = []
    for t in sorted(fpt.lists("stroke_text"), key=lambda n: n.arg(0)):
        bt = _board_text(t)
        _map_text(bt, pos, rot, flip)
        out.append(bt)
    return out


# ---- devices ------------------------------------------------------------------

def _pick_footprint(pkg: SList, footprint: str | None) -> SList:
    fpts = list(pkg.lists("footprint"))
    if not fpts:
        raise LibrePCBError(f"Package {pkg.child_arg('name')} has no footprints")
    if footprint is None:
        return fpts[0]
    for f in fpts:
        if footprint in (f.arg(0), f.child_arg("name")):
            return f
    raise LibrePCBError(f"No footprint '{footprint}'. Available: {[f.child_arg('name') for f in fpts]}")


def add_device_to_board(prj: Project, lib: LibraryIndex, ident: str, x_mm: float, y_mm: float,
                        rotation_deg: float = 0, flip: bool = False, board: str | None = None,
                        device: str | None = None, footprint: str | None = None) -> dict:
    comp = prj.component(ident)
    brd_rel, brd = prj.board(board)
    if brd.find_uuid("device", comp.arg(0)) is not None:
        raise LibrePCBError(f"{ident} is already on board '{brd.child_arg('name')}'; use place_component")
    lib_cmp = prj.lib("cmp", comp.child_arg("lib_component"))
    if lib_cmp.get("schematic_only") is not None and lib_cmp.child_arg("schematic_only") == "true":
        raise LibrePCBError(f"{ident} is schematic-only and cannot be placed on a board")
    options = [o.arg(0) for o in comp.lists("device")]
    if device is None:
        if not options:
            raise LibrePCBError(f"{ident} has no assembly option with a device; pass device "
                                "(see get_component_details for compatible devices)")
        dev_uuid = options[0]
    else:
        dev_uuid = device if prj.has_lib("dev", device) else lib.resolve_device(device).uuid
    dev = ensure_lib(prj, lib, "dev", dev_uuid)
    if dev.child_arg("component") != comp.child_arg("lib_component"):
        raise LibrePCBError(f"Device {dev.child_arg('name')} does not belong to component "
                            f"{lib_cmp.child_arg('name')}")
    pkg = ensure_lib(prj, lib, "pkg", dev.child_arg("package"))
    fpt = _pick_footprint(pkg, footprint)
    fpt_models = {m.arg(0) for m in fpt.lists("3d_model")}
    model = next((m.arg(0) for m in pkg.lists("3d_model") if m.arg(0) in fpt_models), "none")
    pos, rot = (mm_to_nm(x_mm), mm_to_nm(y_mm)), g.deg_to_udeg(rotation_deg)
    items: list = [Token(comp.arg(0)), NL, L("lib_device", dev_uuid), NL,
                   L("lib_footprint", fpt.arg(0)), NL, L("lib_3d_model", model), NL,
                   g.pos_node(pos), L("rotation", fmt_udeg(rot)), L("flip", flip),
                   L("lock", False), L("glue", True), NL]
    for t in _default_texts(fpt, pos, rot, flip):
        items += [t, NL]
    g.add_child(brd, SList("device", items))
    prj.touch(brd_rel)
    if dev_uuid not in options:  # like CmdAddDeviceToBoard: add a compatible assembly option
        if comp.child_arg("lock_assembly") == "true":
            raise LibrePCBError(f"{ident} has locked assembly options without this device")
        assembly = pkg.get("assembly_type") is None or pkg.child_arg("assembly_type") != "none"
        variants = sorted(v.arg(0) for v in prj.circuit.lists("variant")) if assembly else []
        opt = g.block("device", [Token(dev_uuid)], [copy.deepcopy(a) for a in dev.lists("attribute")]
                      + [L("variant", v) for v in variants])
        last = [i for i, it in enumerate(comp.items)
                if isinstance(it, SList) and it.name in ("device", "attribute", "lock_assembly")][-1]
        comp.items[last + 2:last + 2] = [opt, NL]
        prj.touch("circuit/circuit.lp")
    return {"component_id": comp.arg(0), "name": comp.child_arg("name"),
            "board": brd.child_arg("name"), "device": dev.child_arg("name"),
            "package": pkg.child_arg("name"), "footprint": fpt.child_arg("name"),
            "position": _xy(pos), "rotation_deg": float(fmt_udeg(rot)), "flip": flip}


def place_component(prj: Project, ident: str, x_mm: float | None, y_mm: float | None,
                    rotation_deg: float | None = None, flip: bool | None = None,
                    board: str | None = None) -> dict:
    comp = prj.component(ident)
    brd_rel, brd = prj.board(board)
    dev = brd.find_uuid("device", comp.arg(0))
    if dev is None:
        raise LibrePCBError(f"{ident} is not on board '{brd.child_arg('name')}'; use add_device_to_board")
    if dev.child_arg("lock") == "true":
        raise LibrePCBError(f"{ident} is locked on the board")
    old_pos, old_rot = g.read_pos(dev), g.read_rot(dev)
    old_flip = dev.child_arg("flip") == "true"
    pos = (mm_to_nm(x_mm) if x_mm is not None else old_pos[0],
           mm_to_nm(y_mm) if y_mm is not None else old_pos[1])
    rot = old_rot if rotation_deg is None else g.deg_to_udeg(rotation_deg)
    new_flip = old_flip if flip is None else flip
    if new_flip != old_flip:
        # flipping changes text layers: regenerate default texts like the editor does
        lib_dev = prj.lib("dev", dev.child_arg("lib_device"))
        pkg = prj.lib("pkg", lib_dev.child_arg("package"))
        fpt = _pick_footprint(pkg, dev.child_arg("lib_footprint"))
        for t in list(dev.lists("stroke_text")):
            dev.remove(t)
        for t in _default_texts(fpt, pos, rot, new_flip):
            dev.items += [t, NL]  # device ends with "... (glue ..) NL" after removal
    else:
        for t in dev.lists("stroke_text"):
            local = g.untransform(g.read_pos(t), old_pos, old_rot, old_flip)
            g.set_pos(t, g.transform(local, pos, rot, old_flip))
            trot = g.read_rot(t)
            g.set_rot(t, g.add_angles(trot, (old_rot - rot) if old_flip else (rot - old_rot)))
    g.set_pos(dev, pos)
    g.set_rot(dev, rot)
    dev.req("flip").set_arg(0, "true" if new_flip else "false")
    prj.touch(brd_rel)
    return {"component_id": comp.arg(0), "name": comp.child_arg("name"),
            "board": brd.child_arg("name"), "position": _xy(pos),
            "rotation_deg": float(fmt_udeg(rot)), "flip": new_flip}


# ---- outline ----------------------------------------------------------------------

def set_board_outline(prj: Project, points_mm: list[tuple[float, float]],
                      board: str | None = None) -> dict:
    if len(points_mm) < 3:
        raise LibrePCBError("Outline needs at least 3 points")
    brd_rel, brd = prj.board(board)
    for poly in list(brd.lists("polygon")):
        if poly.child_arg("layer") == "brd_outlines":
            brd.remove(poly)
    pts = [(mm_to_nm(x), mm_to_nm(y)) for x, y in points_mm]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    items: list = [Token(g.new_uuid()), L("layer", "brd_outlines"), NL,
                   L("width", "0.0"), L("fill", False), L("grab_area", False), L("lock", False), NL]
    for p in pts:
        items += [L("vertex", g.pos_node(p), L("angle", "0.0")), NL]
    g.add_child(brd, SList("polygon", items))
    prj.touch(brd_rel)
    return {"board": brd.child_arg("name"), "vertices": [_xy(p) for p in pts]}


# ---- pads ---------------------------------------------------------------------------

@dataclass
class PadRef:
    comp: SList
    device: SList
    pad: str  # footprint pad uuid
    name: str
    net: str | None
    pos: g.Point
    layers: tuple[str, ...]  # copper layers where a trace may attach
    label: str


def device_pads(prj: Project, comp: SList, dev: SList) -> list[PadRef]:
    lib_dev = prj.lib("dev", dev.child_arg("lib_device"))
    pkg = prj.lib("pkg", lib_dev.child_arg("package"))
    fpt = _pick_footprint(pkg, dev.child_arg("lib_footprint"))
    pad_names = {p.arg(0): p.child_arg("name") for p in pkg.lists("pad")}
    pad_sig = {p.arg(0): p.child_arg("signal") for p in lib_dev.lists("pad")}
    sig_names = prj.signal_names(comp)
    pos, rot = g.read_pos(dev), g.read_rot(dev)
    flip = dev.child_arg("flip") == "true"
    out = []
    for fp in fpt.lists("pad"):
        pkg_pad = fp.child_arg("package_pad")
        sig = pad_sig.get(pkg_pad)
        sig = None if sig in (None, "none") else sig
        name = pad_names.get(pkg_pad, "?") if pkg_pad != "none" else "(unnamed)"
        if fp.get("hole") is not None:
            layers: tuple[str, ...] = ("*",)
        else:
            side = fp.child_arg("side")
            layer = "top_cu" if side == "top" else "bot_cu" if side == "bottom" else side
            layers = (mirror_layer(layer) if flip else layer,)
        out.append(PadRef(comp, dev, fp.arg(0), name, signal_net(comp, sig),
                          g.transform(g.read_pos(fp), pos, rot, flip), layers,
                          f"{comp.child_arg('name')}:{name}" + (f" ({sig_names[sig]})" if sig else "")))
    return out


def resolve_pad(prj: Project, brd: SList, ref: str) -> PadRef:
    if ":" not in ref:
        raise LibrePCBError(f"Pad reference '{ref}' must be 'DESIGNATOR:PAD', e.g. 'R1:1'")
    des, pname = ref.split(":", 1)
    comp = prj.component(des)
    dev = brd.find_uuid("device", comp.arg(0))
    if dev is None:
        raise LibrePCBError(f"{des} is not placed on board '{brd.child_arg('name')}'")
    pads = device_pads(prj, comp, dev)
    hits = [p for p in pads if pname in (p.name, p.pad)]
    if not hits:  # fall back to component signal name
        sigs = prj.signal_names(comp)
        hits = [p for p in pads if p.label.endswith(f"({pname})") and pname in sigs.values()]
    if len(hits) != 1:
        raise LibrePCBError(f"{'Ambiguous' if hits else 'Unknown'} pad '{ref}'. Pads: "
                            f"{[p.label for p in pads]}")
    return hits[0]


def _pad_end(p: PadRef) -> list:
    return [L("device", p.comp.arg(0)), L("pad", p.pad)]


def _check_layer(p: PadRef, layer: str) -> None:
    if "*" not in p.layers and layer not in p.layers:
        raise LibrePCBError(f"Pad {p.label} is an SMD pad on {p.layers[0]}; a trace on {layer} "
                            "cannot attach to it")


def _segment_with(brd: SList, anchor: tuple) -> SList | None:

    for seg in brd.lists("netsegment"):
        if anchor[0] == "via" and seg.find_uuid("via", anchor[1]) is not None:
            return seg
        for tr in seg.lists("trace"):
            if anchor in (anchor_of(tr.req("from")), anchor_of(tr.req("to"))):
                return seg
    return None


def _new_segment(brd: SList, net: str) -> SList:
    seg = SList("netsegment", [Token(g.new_uuid()), NL, L("net", net), NL])
    g.add_child(brd, seg)
    return seg


def _trace(a: list, b: list, layer: str, width: str) -> SList:
    if anchor_sort_key(b) < anchor_sort_key(a):
        a, b = b, a
    return L("trace", g.new_uuid(), L("layer", layer), L("width", width), NL,
             L("from", *a), NL, L("to", *b), NL)


def _default_width(brd: SList) -> str:
    return brd.req("design_rules").child_arg("default_trace_width")


def _check_copper(brd: SList, layer: str) -> None:
    inner = int(brd.req("layers").child_arg("inner"))
    valid = list(COPPER_OUTER) + [f"in{i}_cu" for i in range(1, inner + 1)]
    if layer not in valid:
        raise LibrePCBError(f"Invalid copper layer '{layer}'. Valid: {valid}")


def _endpoint(prj: Project, brd: SList, ref: str) -> tuple[list, str | None, tuple, PadRef | None]:
    """-> (anchor items, net, anchor key, pad)"""
    if ref.startswith("via:"):
        vid = ref[4:]
        for seg in brd.lists("netsegment"):
            if seg.find_uuid("via", vid) is not None:
                net = seg.child_arg("net")
                return [L("via", vid)], (None if net == "none" else net), ("via", vid), None
        raise LibrePCBError(f"No via {vid} on board")
    p = resolve_pad(prj, brd, ref)
    return _pad_end(p), p.net, ("pad", p.comp.arg(0), p.pad), p


def create_trace(prj: Project, start: str, end: str, layer: str = "top_cu",
                 width_mm: float | None = None, waypoints_mm: list[tuple[float, float]] | None = None,
                 board: str | None = None) -> dict:
    brd_rel, brd = prj.board(board)
    _check_copper(brd, layer)
    a_items, na, a_key, pa = _endpoint(prj, brd, start)
    b_items, nb, b_key, pb = _endpoint(prj, brd, end)
    if a_key == b_key:
        raise LibrePCBError("Trace start and end are identical")
    for p in (pa, pb):
        if p is not None:
            if p.net is None:
                raise LibrePCBError(f"Pad {p.label} is not connected to a net in the schematic; "
                                    "connect it first (connect_pins / add_net_label)")
            _check_layer(p, layer)
    if na != nb:
        raise LibrePCBError(f"Endpoints are on different nets ({prj.net_name(na or '')} vs "
                            f"{prj.net_name(nb or '')}); traces may only join same-net copper")
    net = na
    width = fmt_mm(width_mm) if width_mm is not None else _default_width(brd)
    if mm_to_nm(width) <= 0:
        raise LibrePCBError("Trace width must be > 0")
    sa, sb = _segment_with(brd, a_key), _segment_with(brd, b_key)
    seg = sa or sb or _new_segment(brd, net)  # type: ignore[arg-type]
    if sa is not None and sb is not None and sa is not sb:
        merge_segments(brd, sa, sb, ["via", "junction", "trace"])
    chain = [a_items]
    for x, y in waypoints_mm or []:
        j = L("junction", g.new_uuid(), g.pos_node((mm_to_nm(x), mm_to_nm(y))))
        g.add_child(seg, j, "brd_netsegment")
        chain.append([L("junction", j.arg(0))])
    chain.append(b_items)
    ids = []
    for u, v in zip(chain, chain[1:], strict=False):
        t = _trace(u, v, layer, width)
        g.add_child(seg, t, "brd_netsegment")
        ids.append(t.arg(0))
    prj.touch(brd_rel)
    return {"board": brd.child_arg("name"), "net": prj.net_name(net) if net else None,
            "layer": layer, "width_mm": float(width), "trace_ids": ids}


def create_via(prj: Project, net: str, x_mm: float, y_mm: float, drill_mm: float | None = None,
               size_mm: float | None = None, board: str | None = None) -> dict:
    brd_rel, brd = prj.board(board)
    net_uuid = prj.net(net).arg(0)
    rules = brd.req("design_rules")
    drill = fmt_mm(drill_mm) if drill_mm is not None else rules.child_arg("default_via_drill_diameter")
    size = fmt_mm(size_mm) if size_mm is not None else fmt_mm(float(drill) + 0.4)
    if not 0 < mm_to_nm(drill) < mm_to_nm(size):
        raise LibrePCBError("Need 0 < drill_mm < size_mm")
    vid = g.new_uuid()
    via = L("via", vid, L("from", "top_cu"), L("to", "bot_cu"), NL,
            g.pos_node((mm_to_nm(x_mm), mm_to_nm(y_mm))), L("drill", drill), L("size", size),
            L("exposure", "off"), NL)
    seg = _new_segment(brd, net_uuid)
    g.add_child(seg, via, "brd_netsegment")
    prj.touch(brd_rel)
    return {"board": brd.child_arg("name"), "via_id": vid, "net": prj.net_name(net_uuid),
            "position": {"x_mm": x_mm, "y_mm": y_mm}, "drill_mm": float(drill),
            "size_mm": float(size), "connect_with": f"via:{vid}"}


def route_net(prj: Project, net: str, layer: str = "top_cu", width_mm: float | None = None,
              board: str | None = None) -> dict:
    """Connect all pads of a net with straight traces (minimum spanning tree).
    No obstacle avoidance: DRC must be run afterwards."""

    brd_rel, brd = prj.board(board)
    _check_copper(brd, layer)
    net_uuid = prj.net(net).arg(0)
    pads: list[PadRef] = []
    for dev in brd.lists("device"):
        comp = prj.circuit.find_uuid("component", dev.arg(0))
        if comp is None:
            continue
        pads += [p for p in device_pads(prj, comp, dev) if p.net == net_uuid]
    if len(pads) < 2:
        raise LibrePCBError(f"Net '{net}' has {len(pads)} pad(s) on this board; nothing to route")
    for p in pads:
        _check_layer(p, layer)
    # existing copper connectivity: pads joined through one segment count as connected
    group = {("pad", p.comp.arg(0), p.pad): i for i, p in enumerate(pads)}
    parent = list(range(len(pads)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for seg in brd.lists("netsegment"):
        idx = {group[a] for tr in seg.lists("trace")
               for a in (anchor_of(tr.req("from")), anchor_of(tr.req("to"))) if a in group}
        idx_l = sorted(idx)
        for i in idx_l[1:]:
            parent[find(i)] = find(idx_l[0])
    edges = sorted((math.dist(pads[i].pos, pads[j].pos), i, j)
                   for i in range(len(pads)) for j in range(i + 1, len(pads)))
    created = []
    for _, i, j in edges:
        if find(i) != find(j):
            parent[find(i)] = find(j)
            r = create_trace(prj, f"{pads[i].comp.child_arg('name')}:{pads[i].pad}",
                             f"{pads[j].comp.child_arg('name')}:{pads[j].pad}", layer, width_mm,
                             None, board)
            created.append({"from": pads[i].label, "to": pads[j].label, "trace_ids": r["trace_ids"]})
    return {"board": brd.child_arg("name"), "net": prj.net_name(net_uuid), "layer": layer,
            "traces_created": created,
            "warning": "Straight-line traces without obstacle avoidance. Run run_drc and fix "
                       "clearance violations; this is not an autorouter."}


def create_board(prj: Project, name: str, width_mm: float = 100.0, height_mm: float = 80.0) -> dict:
    from .create import board_content, dir_name
    from .sexpr import Str, parse
    existing = [prj.file(p).child_arg("name") for p in prj.board_paths()]
    if name in existing:
        raise LibrePCBError(f"Board '{name}' already exists")
    d, base, i = dir_name(name), dir_name(name), 2
    while (prj.dir / "boards" / d).exists():
        d, i = f"{base}_{i}", i + 1
    rel = f"boards/{d}/board.lp"
    root = parse(board_content(name, width_mm, height_mm))
    prj.set_file(rel, root)
    lst = prj.file("boards/boards.lp")
    lst.items += ([L("board", Str(rel)), NL] if lst.items and lst.items[-1] is NL
                  else [NL, L("board", Str(rel)), NL])
    prj.touch("boards/boards.lp")
    return {"board_id": root.arg(0), "name": name, "path": rel,
            "outline_mm": {"width": width_mm, "height": height_mm}}
