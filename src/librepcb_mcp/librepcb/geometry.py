"""Coordinates (nanometers / microdegrees, as LibrePCB stores them) and
canonical child ordering helpers."""

from __future__ import annotations

import math
import uuid as _uuid

from .sexpr import NL, SList, Token, deg_to_udeg, fmt_nm, fmt_udeg, mm_to_nm

Point = tuple[int, int]


def new_uuid() -> str:
    return str(_uuid.uuid4())


def read_pos(node: SList) -> Point:
    p = node.req("position")
    return mm_to_nm(p.arg(0)), mm_to_nm(p.arg(1))


def read_rot(node: SList, name: str = "rotation") -> int:
    return deg_to_udeg(node.child_arg(name))


def pos_node(p: Point) -> SList:
    return SList("position", [Token(fmt_nm(p[0])), Token(fmt_nm(p[1]))])


def set_pos(node: SList, p: Point) -> None:
    pn = node.req("position")
    pn.set_arg(0, fmt_nm(p[0]))
    pn.set_arg(1, fmt_nm(p[1]))


def set_rot(node: SList, udeg: int, name: str = "rotation") -> None:
    node.req(name).set_arg(0, fmt_udeg(udeg))


def rotate(p: Point, udeg: int) -> Point:
    """Point::rotate() around origin: exact for multiples of 90 degrees."""
    a = udeg % 360_000_000
    x, y = p
    if a == 0:
        return p
    if a == 90_000_000:
        return -y, x
    if a == 180_000_000:
        return -x, -y
    if a == 270_000_000:
        return y, -x
    r = math.radians(a / 1e6)
    return (round(x * math.cos(r) - y * math.sin(r)), round(x * math.sin(r) + y * math.cos(r)))


def transform(p: Point, origin: Point, udeg: int, mirror: bool = False) -> Point:
    """Transform::map(): mirror horizontally, rotate, translate."""
    if mirror:
        p = (-p[0], p[1])
    q = rotate(p, udeg)
    return q[0] + origin[0], q[1] + origin[1]


def untransform(p: Point, origin: Point, udeg: int, mirror: bool = False) -> Point:
    q = rotate((p[0] - origin[0], p[1] - origin[1]), -udeg)
    return (-q[0], q[1]) if mirror else q


def add_angles(a: int, b: int) -> int:
    """Angle + Angle (C++ % semantics keep the sign of the sum)."""
    s = a + b
    r = abs(s) % 360_000_000
    return -r if s < 0 else r


def norm360(a: int) -> int:
    return a % 360_000_000


def block(name: str, head: list, children: list[SList]) -> SList:
    """``(name head...\\n child\\n child\\n)`` - the usual ensureLineBreak layout."""
    items: list = list(head) + [NL]
    for c in children:
        items += [c, NL]
    return SList(name, items)


ORDER = {
    "librepcb_circuit": ["variant", "netclass", "net", "component"],
    "librepcb_schematic": ["name", "grid", "symbol", "bussegment", "netsegment", "polygon",
                           "text", "image"],
    "librepcb_board": ["name", "default_font", "grid", "layers", "thickness", "solder_resist",
                       "silkscreen", "silkscreen_layers_top", "silkscreen_layers_bot",
                       "design_rules", "design_rule_check", "fabrication_output_settings",
                       "preferred_footprint_tags", "device", "netsegment", "plane", "zone",
                       "polygon", "stroke_text", "hole"],
    "sch_netsegment": ["net", "junction", "line", "label"],
    "brd_netsegment": ["net", "pad", "via", "junction", "trace"],
}


def add_child(parent: SList, node: SList, order_key: str | None = None) -> None:
    """Insert node at its canonical place: grouped by type in ``ORDER`` and
    sorted by UUID within a type (LibrePCB stores UUID-keyed maps sorted)."""
    order = ORDER[order_key or parent.name]
    rank = order.index(node.name)
    key = node.arg(0)
    for idx, it in enumerate(parent.items):
        if not isinstance(it, SList) or it.name not in order:
            continue
        r = order.index(it.name)
        if r > rank or (r == rank and it.arg(0) > key):
            parent.items[idx:idx] = [node, NL]
            return
    if parent.items and parent.items[-1] is NL:
        parent.items += [node, NL]
    else:
        parent.items += [NL, node, NL]


def sort_children(parent: SList, name: str) -> None:
    """Re-sort same-named children by UUID in place (after merges)."""
    slots = [i for i, it in enumerate(parent.items) if isinstance(it, SList) and it.name == name]
    nodes = sorted((parent.items[i] for i in slots), key=lambda n: n.arg(0))
    for i, n in zip(slots, nodes, strict=True):
        parent.items[i] = n
