"""Circuit + schematic modifications, mirroring the LibrePCB editor commands
(CmdAddComponentToCircuit, CmdAddSymbolToSchematic, net segment editing)."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from . import geometry as g
from .errors import LibrePCBError
from .library import LibraryIndex
from .project import LIB_KINDS, Project
from .sexpr import NL, L, SList, Str, Token, fmt_nm, fmt_udeg, mm_to_nm

IDENT_RE = re.compile(r"^[-a-zA-Z0-9._+/!?&@#$()]{1,32}$")
SCH_WIRE_WIDTH = "0.15875"


def check_identifier(name: str, what: str) -> None:
    if not IDENT_RE.match(name):
        raise LibrePCBError(f"Invalid {what} '{name}': 1-32 chars of A-Z a-z 0-9 . _ + - / ! ? & @ # $ ( )")


# ---- library elements -------------------------------------------------------

def ensure_lib(prj: Project, lib: LibraryIndex, kind: str, uuid: str) -> SList:
    """Return the element from the project library, scheduling a copy from the
    workspace libraries if the project does not have it yet."""
    if prj.has_lib(kind, uuid):
        return prj.lib(kind, uuid)
    rel = f"library/{kind}/{uuid}/{LIB_KINDS[kind]}.lp"
    if rel in prj._files:  # already scheduled in this transaction
        return prj._files[rel]
    el = lib.locate(kind, uuid)
    root = lib.read(el)
    prj._files[rel] = root
    prj.new_dirs.append((el.dir, prj.dir / "library" / kind / uuid))
    return root


# ---- pins -----------------------------------------------------------------

@dataclass
class PinRef:
    comp: SList
    sch_rel: str
    symbol: SList
    pin: str  # symbol pin uuid
    signal: str | None
    label: str
    pos: g.Point

    @property
    def designator(self) -> str:
        return self.comp.child_arg("name")


def symbol_lib(prj: Project, comp: SList, symbol: SList) -> tuple[SList, SList]:
    """(lib symbol, gate) for a placed symbol."""
    lib_cmp = prj.lib("cmp", comp.child_arg("lib_component"))
    var = lib_cmp.find_uuid("variant", comp.child_arg("lib_variant"))
    gate = var.find_uuid("gate", symbol.child_arg("lib_gate")) if var else None
    if gate is None:
        raise LibrePCBError(f"{comp.child_arg('name')}: gate missing in project library")
    return prj.lib("sym", gate.child_arg("symbol")), gate


def symbol_pins(prj: Project, comp: SList, symbol: SList) -> list[dict]:
    lib_sym, gate = symbol_lib(prj, comp, symbol)
    sig_names = prj.signal_names(comp)
    pos, rot = g.read_pos(symbol), g.read_rot(symbol)
    mirror = symbol.child_arg("mirror") == "true"
    out = []
    for gp in gate.lists("pin"):
        pin_node = lib_sym.find_uuid("pin", gp.arg(0))
        if pin_node is None:
            continue
        sig = gp.child_arg("signal")
        sig = None if sig == "none" else sig
        out.append({"pin": gp.arg(0), "name": pin_node.child_arg("name"), "signal": sig,
                    "signal_name": sig_names.get(sig) if sig else None,
                    "pos": g.transform(g.read_pos(pin_node), pos, rot, mirror)})
    return out


def resolve_pin(prj: Project, ref: str) -> PinRef:
    if ":" not in ref:
        raise LibrePCBError(f"Pin reference '{ref}' must be 'DESIGNATOR:PIN', e.g. 'R1:1' or 'U1:VCC'")
    des, pname = ref.split(":", 1)
    comp = prj.component(des)
    hits = []
    for rel, sym in prj.symbols_of(comp.arg(0)):
        for p in symbol_pins(prj, comp, sym):
            if pname in (p["name"], p["signal_name"], p["pin"]):
                hits.append(PinRef(comp, rel, sym, p["pin"], p["signal"], f"{des}:{p['name']}", p["pos"]))
    if not hits:
        names = [p["name"] + (f" ({p['signal_name']})" if p["signal_name"] else "")
                 for _, s in prj.symbols_of(comp.arg(0)) for p in symbol_pins(prj, comp, s)]
        if not prj.symbols_of(comp.arg(0)):
            raise LibrePCBError(f"{des} has no symbol in any schematic")
        raise LibrePCBError(f"{des} has no pin '{pname}'. Pins: {names}")
    if len(hits) > 1:
        raise LibrePCBError(f"Pin '{ref}' is ambiguous ({len(hits)} matches); use the pin UUID: "
                            f"{[h.pin for h in hits]}")
    return hits[0]


def signal_node(comp: SList, sig: str) -> SList:
    node = comp.find_uuid("signal", sig)
    if node is None:
        raise LibrePCBError(f"{comp.child_arg('name')}: signal {sig} missing in circuit")
    return node


def signal_net(comp: SList, sig: str | None) -> str | None:
    if sig is None:
        return None
    n = signal_node(comp, sig).child_arg("net")
    return None if n == "none" else n


# ---- nets -------------------------------------------------------------------

def create_net(prj: Project, name: str | None = None) -> str:
    circuit = prj.circuit
    netclass = circuit.get("netclass")
    if netclass is None:
        raise LibrePCBError("Circuit has no net class")
    names = {n.child_arg("name") for n in circuit.lists("net")}
    auto = name is None
    if auto:
        i = 1
        while f"N{i}" in names:
            i += 1
        name = f"N{i}"
    else:
        check_identifier(name, "net name")
        if name in names:
            raise LibrePCBError(f"Net '{name}' already exists")
    uuid = g.new_uuid()
    node = L("net", uuid, L("auto", auto), L("name", Str(name)), NL,
             L("netclass", netclass.arg(0)), NL)
    g.add_child(circuit, node)
    prj.touch("circuit/circuit.lp")
    return uuid


def net_by_name_or_new(prj: Project, name: str) -> str:
    for n in prj.circuit.lists("net"):
        if n.child_arg("name") == name or n.arg(0) == name:
            return n.arg(0)
    return create_net(prj, name)


def remove_unused_nets(prj: Project, candidates: set[str]) -> list[str]:
    used: set[str] = set()
    for c in prj.circuit.lists("component"):
        used |= {s.child_arg("net") for s in c.lists("signal")}
    for rel in prj.schematic_paths():
        used |= {s.child_arg("net") for s in prj.file(rel).lists("netsegment")}
    for rel in prj.board_paths():
        brd = prj.file(rel)
        used |= {s.child_arg("net") for s in brd.lists("netsegment")}
        used |= {p.child_arg("net") for p in brd.lists("plane")}
    removed = []
    for uuid in candidates - used:
        n = prj.circuit.find_uuid("net", uuid)
        if n is not None:
            removed.append(n.child_arg("name"))
            prj.circuit.remove(n)
            prj.touch("circuit/circuit.lp")
    return removed


# ---- schematic net segments -------------------------------------------------

def anchor_of(end: SList) -> tuple:
    for k in ("junction", "via"):
        if end.get(k) is not None:
            return (k, end.child_arg(k))
    if end.get("symbol") is not None:
        return ("p", end.child_arg("symbol"), end.child_arg("pin"))
    return ("pad", end.child_arg("device"), end.child_arg("pad"))


def pin_end(sym_uuid: str, pin_uuid: str) -> list:
    return [L("symbol", sym_uuid), L("pin", pin_uuid)]


def segment_of_pin(sch: SList, sym_uuid: str, pin_uuid: str) -> SList | None:
    key = ("p", sym_uuid, pin_uuid)
    for seg in sch.lists("netsegment"):
        for line in seg.lists("line"):
            if key in (anchor_of(line.req("from")), anchor_of(line.req("to"))):
                return seg
    return None


def new_sch_segment(sch: SList, net: str) -> SList:
    seg = SList("netsegment", [Token(g.new_uuid()), NL, L("net", net), NL])
    g.add_child(sch, seg)
    return seg


_ANCHOR_RANK = {"p": 0, "pad": 0, "bus_junction": 1, "via": 2, "junction": 3}


def anchor_sort_key(end_items: list) -> tuple:
    """NetLineAnchor/TraceAnchor::operator<: pins/pads < vias < junctions,
    then by UUID(s). LibrePCB stores the smaller anchor as 'from'."""
    a = anchor_of(SList("from", end_items))
    return (_ANCHOR_RANK[a[0]],) + a[1:]


def sch_line(a: list, b: list, width: str = SCH_WIRE_WIDTH) -> SList:
    if anchor_sort_key(b) < anchor_sort_key(a):
        a, b = b, a
    return L("line", g.new_uuid(), L("width", width), NL, L("from", *a), NL, L("to", *b), NL)


def merge_segments(parent: SList, keep: SList, other: SList, kinds: list[str]) -> None:
    for kind in kinds:
        for node in list(other.lists(kind)):
            g.add_child(keep, node, "sch_netsegment" if parent.name == "librepcb_schematic"
                        else "brd_netsegment")
    parent.remove(other)


def cleanup_segment(parent: SList, seg: SList, board: bool) -> None:
    """Drop dangling junctions, delete empty segments, split disconnected ones."""
    edge_kind = "trace" if board else "line"
    order = "brd_netsegment" if board else "sch_netsegment"
    edges = list(seg.lists(edge_kind))
    used = {a for e in edges for a in (anchor_of(e.req("from")), anchor_of(e.req("to")))}
    for j in list(seg.lists("junction")):
        if ("junction", j.arg(0)) not in used:
            seg.remove(j)
    vias = list(seg.lists("via")) if board else []
    if not edges and not vias:
        parent.remove(seg)
        return
    # union-find over anchors
    parent_of: dict[tuple, tuple] = {}

    def find(x: tuple) -> tuple:
        parent_of.setdefault(x, x)
        while parent_of[x] != x:
            parent_of[x] = parent_of[parent_of[x]]
            x = parent_of[x]
        return x

    for e in edges:
        a, b = anchor_of(e.req("from")), anchor_of(e.req("to"))
        parent_of[find(a)] = find(b)
    for v in vias:
        find(("via", v.arg(0)))
    groups: dict[tuple, list[SList]] = {}
    for e in edges:
        groups.setdefault(find(anchor_of(e.req("from"))), []).append(e)
    for v in vias:
        groups.setdefault(find(("via", v.arg(0))), []).append(v)
    if len(groups) <= 1:
        return
    net = seg.child_arg("net")
    for members in list(groups.values())[1:]:
        anchors = {a for m in members if m.name == edge_kind
                   for a in (anchor_of(m.req("from")), anchor_of(m.req("to")))}
        anchors |= {("via", m.arg(0)) for m in members if m.name == "via"}
        new = SList("netsegment", [Token(g.new_uuid()), NL, L("net", net), NL])
        for m in members:
            seg.remove(m)
            g.add_child(new, m, order)
        for j in list(seg.lists("junction")):
            if ("junction", j.arg(0)) in anchors:
                seg.remove(j)
                g.add_child(new, j, order)
        g.add_child(parent, new)


# ---- component instances --------------------------------------------------

def _symbol_texts(lib_sym: SList, pos: g.Point, rot: int, mirror: bool) -> list[SList]:
    texts = []
    for t in sorted(lib_sym.lists("text"), key=lambda n: n.arg(0)):
        t = copy.deepcopy(t)
        g.set_pos(t, g.transform(g.read_pos(t), pos, rot, mirror))
        trot = g.read_rot(t)
        g.set_rot(t, g.add_angles(rot, (180_000_000 - trot) if mirror else trot))
        if mirror:
            align = t.req("align")
            v = align.arg(1)
            align.set_arg(1, {"top": "bottom", "bottom": "top"}.get(v, v))
        texts.append(t)
    return texts


def _symbol_node(uuid: str, comp_uuid: str, gate: str, pos: g.Point, rot: int,
                 texts: list[SList]) -> SList:
    items: list = [Token(uuid), NL, L("component", comp_uuid), NL, L("lib_gate", gate), NL,
                   g.pos_node(pos), L("rotation", fmt_udeg(rot)), L("mirror", False), NL]
    for t in texts:
        items += [t, NL]
    return SList("symbol", items)


def add_component(prj: Project, lib: LibraryIndex, device: str, x_mm: float, y_mm: float,
                  rotation_deg: float = 0, schematic: str | None = None, name: str | None = None,
                  value: str | None = None) -> dict:
    dev_el = lib.resolve_device(device)
    dev = lib.read(dev_el)
    cmp_uuid, pkg_uuid = dev.child_arg("component"), dev.child_arg("package")
    cmp = ensure_lib(prj, lib, "cmp", cmp_uuid)
    variant = cmp.get("variant")
    if variant is None:
        raise LibrePCBError(f"Component {cmp.child_arg('name')} has no symbol variant")
    gates = list(variant.lists("gate"))
    if not gates:
        raise LibrePCBError(f"Component {cmp.child_arg('name')} has no symbols (schematic-less)")
    sch_rel, sch = prj.schematic(schematic)
    circuit = prj.circuit

    existing = {c.child_arg("name") for c in circuit.lists("component")}
    if name is None:
        prefix = cmp.child_arg("prefix") or "?"
        i = 1
        while f"{prefix}{i}" in existing:
            i += 1
        name = f"{prefix}{i}"
    check_identifier(name, "component name")
    if name in existing:
        raise LibrePCBError(f"Component name '{name}' already used")

    comp_uuid = g.new_uuid()
    pkg = lib.read(lib.locate("pkg", pkg_uuid)) if not prj.has_lib("pkg", pkg_uuid) \
        else prj.lib("pkg", pkg_uuid)
    # shortcut: 'auto' assembly type treated as requiring assembly (true for any package with pads)
    needs_assembly = (pkg.get("assembly_type") is None
                      or pkg.child_arg("assembly_type") != "none")
    variants = sorted(v.arg(0) for v in circuit.lists("variant")) if needs_assembly else []
    option = g.block("device", [Token(dev_el.uuid)],
                     [copy.deepcopy(a) for a in dev.lists("attribute")]
                     + [L("variant", v) for v in variants])
    default_lock = prj.file("project/settings.lp").get("default_lock_component_assembly")
    lock = default_lock is not None and default_lock.arg(0) == "true"
    items: list = [Token(comp_uuid), NL, L("lib_component", cmp_uuid), NL,
                   L("lib_variant", variant.arg(0)), NL,
                   L("name", Str(name)), L("value", Str(value if value is not None
                                                        else cmp.child_arg("default_value"))), NL,
                   L("lock_assembly", lock), NL]
    for a in cmp.lists("attribute"):
        items += [copy.deepcopy(a), NL]
    items += [option, NL]
    for s in sorted(cmp.lists("signal"), key=lambda n: n.arg(0)):
        items += [L("signal", s.arg(0), L("net", "none")), NL]
    g.add_child(circuit, SList("component", items))
    prj.touch("circuit/circuit.lp")

    base = (mm_to_nm(x_mm), mm_to_nm(y_mm))
    rot = g.deg_to_udeg(rotation_deg)
    placed = []
    for gate in gates:
        lib_sym = ensure_lib(prj, lib, "sym", gate.child_arg("symbol"))
        pos = g.transform(g.read_pos(gate), base, rot)
        srot = g.add_angles(rot, g.read_rot(gate))
        sym_uuid = g.new_uuid()
        g.add_child(sch, _symbol_node(sym_uuid, comp_uuid, gate.arg(0), pos, srot,
                                      _symbol_texts(lib_sym, pos, srot, False)))
        placed.append({"symbol_id": sym_uuid, "gate": gate.arg(0),
                       "position": {"x_mm": float(fmt_nm(pos[0])), "y_mm": float(fmt_nm(pos[1]))},
                       "rotation_deg": float(fmt_udeg(srot))})
    prj.touch(sch_rel)
    return {"component_id": comp_uuid, "name": name, "device_id": dev_el.uuid,
            "device_name": dev_el.name, "component_name": cmp.child_arg("name"),
            "schematic": sch.child_arg("name"), "symbols": placed,
            "signals": sorted(s.child_arg("name") for s in cmp.lists("signal"))}


def move_component(prj: Project, ident: str, x_mm: float, y_mm: float,
                   rotation_deg: float | None = None, gate: str | None = None) -> dict:
    comp = prj.component(ident)
    syms = prj.symbols_of(comp.arg(0))
    if not syms:
        raise LibrePCBError(f"{ident} has no symbol in any schematic")
    if gate is not None:
        syms = [(r, s) for r, s in syms if gate in (s.arg(0), s.child_arg("lib_gate"))]
    if len(syms) != 1:
        raise LibrePCBError(f"{ident} has {len(syms)} matching symbols; pass gate (symbol or gate UUID)")
    rel, sym = syms[0]
    old_pos, old_rot = g.read_pos(sym), g.read_rot(sym)
    mirror = sym.child_arg("mirror") == "true"
    new_pos = (mm_to_nm(x_mm), mm_to_nm(y_mm))
    new_rot = old_rot if rotation_deg is None else g.deg_to_udeg(rotation_deg)
    for t in sym.lists("text"):
        local = g.untransform(g.read_pos(t), old_pos, old_rot, mirror)
        g.set_pos(t, g.transform(local, new_pos, new_rot, mirror))
        g.set_rot(t, g.add_angles(g.read_rot(t), new_rot - old_rot))
    g.set_pos(sym, new_pos)
    g.set_rot(sym, new_rot)
    prj.touch(rel)
    return {"component_id": comp.arg(0), "name": comp.child_arg("name"), "symbol_id": sym.arg(0),
            "position": {"x_mm": float(fmt_nm(new_pos[0])), "y_mm": float(fmt_nm(new_pos[1]))},
            "rotation_deg": float(fmt_udeg(new_rot))}


def set_component_value(prj: Project, ident: str, value: str | None,
                        attributes: dict[str, str] | None) -> dict:
    comp = prj.component(ident)
    if value is not None:
        comp.req("value").set_arg(0, value)
    for key, val in (attributes or {}).items():
        attr = next((a for a in comp.lists("attribute") if a.arg(0) == key), None)
        if attr is None:
            raise LibrePCBError(f"{ident} has no attribute '{key}'. Existing: "
                                f"{[a.arg(0) for a in comp.lists('attribute')]}")
        attr.req("value").set_arg(0, val)
    prj.touch("circuit/circuit.lp")
    return {"component_id": comp.arg(0), "name": comp.child_arg("name"),
            "value": comp.child_arg("value"),
            "attributes": {a.arg(0): a.child_arg("value") for a in comp.lists("attribute")}}


def _set_signal_net(prj: Project, comp: SList, sig: str, net: str | None) -> None:
    signal_node(comp, sig).req("net").set_arg(0, net or "none")
    prj.touch("circuit/circuit.lp")


def connect_pins(prj: Project, a_ref: str, b_ref: str, net_name: str | None = None) -> dict:
    a, b = resolve_pin(prj, a_ref), resolve_pin(prj, b_ref)
    if (a.symbol.arg(0), a.pin) == (b.symbol.arg(0), b.pin):
        raise LibrePCBError("Cannot connect a pin to itself")
    if a.sch_rel != b.sch_rel:
        raise LibrePCBError(f"{a.label} and {b.label} are on different schematics; connect each "
                            "to the same net with add_net_label instead")
    for p in (a, b):
        if p.signal is None:
            raise LibrePCBError(f"{p.label} is not connected to any component signal")
    na, nb = signal_net(a.comp, a.signal), signal_net(b.comp, b.signal)
    if na and nb and na != nb:
        raise LibrePCBError(f"{a.label} is on net '{prj.net_name(na)}' and {b.label} on "
                            f"'{prj.net_name(nb)}'. Disconnect one first (nets are not merged).")
    net = na or nb
    if net_name is not None:
        wanted = net_by_name_or_new(prj, net_name)
        if net and wanted != net:
            raise LibrePCBError(f"Pins are already on net '{prj.net_name(net)}', not '{net_name}'")
        net = wanted
    if net is None:
        net = create_net(prj)
    sch = prj.file(a.sch_rel)
    sa = segment_of_pin(sch, a.symbol.arg(0), a.pin)
    sb = segment_of_pin(sch, b.symbol.arg(0), b.pin)
    if sa is not None and sa is sb:
        for line in sa.lists("line"):
            ends = {anchor_of(line.req("from")), anchor_of(line.req("to"))}
            if ends == {("p", a.symbol.arg(0), a.pin), ("p", b.symbol.arg(0), b.pin)}:
                raise LibrePCBError(f"{a.label} and {b.label} are already wired together")
    seg = sa or sb or new_sch_segment(sch, net)
    if sa is not None and sb is not None and sa is not sb:
        merge_segments(sch, sa, sb, ["junction", "line", "label"])
    line = sch_line(pin_end(a.symbol.arg(0), a.pin), pin_end(b.symbol.arg(0), b.pin))
    g.add_child(seg, line, "sch_netsegment")
    _set_signal_net(prj, a.comp, a.signal, net)  # type: ignore[arg-type]
    _set_signal_net(prj, b.comp, b.signal, net)  # type: ignore[arg-type]
    prj.touch(a.sch_rel)
    return {"net": prj.net_name(net), "net_id": net, "line_id": line.arg(0),
            "pins": [a.label, b.label], "schematic": sch.child_arg("name")}


def add_net_label(prj: Project, pin_ref: str, net_name: str | None, dx_mm: float = 2.54,
                  dy_mm: float = 0.0, rotation_deg: float = 0.0) -> dict:
    """Stub wire from a pin to a junction carrying a net label; this is how
    LibrePCB connects pins by net name (also across schematics)."""
    p = resolve_pin(prj, pin_ref)
    if p.signal is None:
        raise LibrePCBError(f"{p.label} is not connected to any component signal")
    current = signal_net(p.comp, p.signal)
    if net_name is None:
        if current is None:
            raise LibrePCBError(f"{p.label} has no net yet; pass net_name")
        net = current
    else:
        net = net_by_name_or_new(prj, net_name)
        if current and current != net:
            raise LibrePCBError(f"{p.label} is already on net '{prj.net_name(current)}'")
    sch = prj.file(p.sch_rel)
    seg = segment_of_pin(sch, p.symbol.arg(0), p.pin) or new_sch_segment(sch, net)
    pos = (p.pos[0] + mm_to_nm(dx_mm), p.pos[1] + mm_to_nm(dy_mm))
    if pos == p.pos:
        raise LibrePCBError("dx_mm/dy_mm must not both be 0 (the label needs a wire stub)")
    j = L("junction", g.new_uuid(), g.pos_node(pos))
    label = L("label", g.new_uuid(), NL, g.pos_node(pos),
              L("rotation", fmt_udeg(g.deg_to_udeg(rotation_deg))), L("mirror", False), NL)
    line = sch_line(pin_end(p.symbol.arg(0), p.pin), [L("junction", j.arg(0))])
    for node in (j, line, label):
        g.add_child(seg, node, "sch_netsegment")
    _set_signal_net(prj, p.comp, p.signal, net)
    prj.touch(p.sch_rel)
    return {"net": prj.net_name(net), "net_id": net, "pin": p.label, "label_id": label.arg(0),
            "position": {"x_mm": float(fmt_nm(pos[0])), "y_mm": float(fmt_nm(pos[1]))}}


def _pads_of_signal(prj: Project, comp: SList, sig: str, device: SList) -> set[str]:
    """Footprint pad UUIDs of a board device connected to a component signal."""
    lib_dev = prj.lib("dev", device.child_arg("lib_device"))
    pkg_pads = {p.arg(0) for p in lib_dev.lists("pad") if p.child_arg("signal") == sig}
    pkg = prj.lib("pkg", lib_dev.child_arg("package"))
    fpt = pkg.find_uuid("footprint", device.child_arg("lib_footprint"))
    if fpt is None:
        return set()
    return {fp.arg(0) for fp in fpt.lists("pad") if fp.child_arg("package_pad") in pkg_pads}


def detach_board_pads(prj: Project, comp: SList, pads_by_board: dict[str, set[str]]) -> int:
    """Remove traces attached to the given pads of ``comp``'s board devices."""
    removed = 0
    for rel, pads in pads_by_board.items():
        brd = prj.file(rel)
        for seg in list(brd.lists("netsegment")):
            hit = False
            for tr in list(seg.lists("trace")):
                for end in (tr.req("from"), tr.req("to")):
                    if end.get("device") is not None and end.child_arg("device") == comp.arg(0) \
                            and end.child_arg("pad") in pads:
                        seg.remove(tr)
                        removed += 1
                        hit = True
                        break
            if hit:
                cleanup_segment(brd, seg, board=True)
                prj.touch(rel)
    return removed


def disconnect_pins(prj: Project, refs: list[str]) -> dict:
    touched_nets: set[str] = set()
    report = []
    for ref in refs:
        p = resolve_pin(prj, ref)
        sch = prj.file(p.sch_rel)
        key = ("p", p.symbol.arg(0), p.pin)
        removed_lines = 0
        for seg in list(sch.lists("netsegment")):
            lines = [ln for ln in seg.lists("line")
                     if key in (anchor_of(ln.req("from")), anchor_of(ln.req("to")))]
            for ln in lines:
                seg.remove(ln)
            if lines:
                removed_lines += len(lines)
                cleanup_segment(sch, seg, board=False)
        prj.touch(p.sch_rel)
        net = signal_net(p.comp, p.signal)
        traces = 0
        still = False
        if p.signal is not None:
            # signal stays on its net if another pin of the same signal is still wired
            for rel, sym in prj.symbols_of(p.comp.arg(0)):
                for sp in symbol_pins(prj, p.comp, sym):
                    if sp["signal"] == p.signal and segment_of_pin(prj.file(rel), sym.arg(0), sp["pin"]):
                        still = True
            if not still and net:
                _set_signal_net(prj, p.comp, p.signal, None)
                touched_nets.add(net)
                pads = {rel: _pads_of_signal(prj, p.comp, p.signal, d)
                        for rel, d in prj.devices_of(p.comp.arg(0))}
                traces = detach_board_pads(prj, p.comp, pads)
        report.append({"pin": p.label, "removed_wires": removed_lines,
                       "removed_traces": traces,
                       "net_after": prj.net_name(net) if (still and net) else None})
    return {"pins": report, "removed_nets": remove_unused_nets(prj, touched_nets)}


def remove_component(prj: Project, ident: str) -> dict:
    comp = prj.component(ident)
    cu = comp.arg(0)
    nets = {s.child_arg("net") for s in comp.lists("signal")} - {"none"}
    removed_wires = 0
    for rel, sym in prj.symbols_of(cu):
        sch = prj.file(rel)
        for seg in list(sch.lists("netsegment")):
            lines = [ln for ln in seg.lists("line")
                     if any(a[0] == "p" and a[1] == sym.arg(0)
                            for a in (anchor_of(ln.req("from")), anchor_of(ln.req("to"))))]
            for ln in lines:
                seg.remove(ln)
            if lines:
                removed_wires += len(lines)
                cleanup_segment(sch, seg, board=False)
        sch.remove(sym)
        prj.touch(rel)
    removed_traces = 0
    for rel, dev in prj.devices_of(cu):
        brd = prj.file(rel)
        for seg in list(brd.lists("netsegment")):
            trs = [t for t in seg.lists("trace")
                   if any(e.get("device") is not None and e.child_arg("device") == cu
                          for e in (t.req("from"), t.req("to")))]
            for t in trs:
                seg.remove(t)
            if trs:
                removed_traces += len(trs)
                cleanup_segment(brd, seg, board=True)
        brd.remove(dev)
        prj.touch(rel)
    prj.circuit.remove(comp)
    prj.touch("circuit/circuit.lp")
    return {"removed": comp.child_arg("name"), "component_id": cu, "removed_wires": removed_wires,
            "removed_traces": removed_traces, "removed_nets": remove_unused_nets(prj, nets)}


def create_schematic(prj: Project, name: str) -> dict:
    if not name.strip():
        raise LibrePCBError("Schematic name must not be empty")
    existing = [prj.file(p).child_arg("name") for p in prj.schematic_paths()]
    if name in existing:
        raise LibrePCBError(f"Schematic '{name}' already exists")
    dirname = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_") or "schematic"
    base, i = dirname, 2
    while (prj.dir / "schematics" / dirname).exists():
        dirname, i = f"{base}_{i}", i + 1
    rel = f"schematics/{dirname}/schematic.lp"
    uuid = g.new_uuid()
    root = SList("librepcb_schematic", [Token(uuid), NL, L("name", Str(name)), NL,
                                        L("grid", L("interval", "2.54"), L("unit", "millimeters")),
                                        NL])
    prj.set_file(rel, root)
    lst = prj.file("schematics/schematics.lp")
    lst.items += ([L("schematic", Str(rel)), NL] if lst.items and lst.items[-1] is NL
                  else [NL, L("schematic", Str(rel)), NL])
    prj.touch("schematics/schematics.lp")
    return {"schematic_id": uuid, "name": name, "path": rel}
