"""LibrePCB S-expression parser and serializer.

Mirrors ``libs/librepcb/core/serialization/sexpression.cpp`` (LibrePCB 2.x):
line breaks are explicit child nodes, so a parsed canonical file serializes
back byte-identical. New nodes control their layout by inserting ``NL``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from collections.abc import Iterator
from typing import Union


class SExprError(ValueError):
    """Raised for malformed S-expression input."""


class _LineBreak:
    __slots__ = ()

    def __repr__(self) -> str:
        return "NL"

    def __copy__(self) -> _LineBreak:
        return self

    def __deepcopy__(self, memo: dict) -> _LineBreak:
        return self

    def __reduce__(self) -> str:
        return "NL"


NL = _LineBreak()


@dataclass(eq=True)
class Token:
    value: str


@dataclass(eq=True)
class Str:
    value: str


Item = Union["SList", Token, Str, _LineBreak]

_TOKEN_SPECIAL = set("\\.:_-")
_ESCAPE_OUT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n",
               "\r": "\\r", "\t": "\\t", "\v": "\\v"}
_ESCAPE_IN = {"'": "'", '"': '"', "\\": "\\", "b": "\b", "f": "\f", "n": "\n",
              "r": "\r", "t": "\t", "v": "\v", "a": "\a", "?": "?"}


def _is_token_char(c: str) -> bool:
    return c.isascii() and (c.isalnum() or c in _TOKEN_SPECIAL)


@dataclass(eq=True)
class SList:
    name: str
    items: list[Item] = field(default_factory=list)

    # ---- query helpers -------------------------------------------------
    def lists(self, name: str | None = None) -> Iterator[SList]:
        for it in self.items:
            if isinstance(it, SList) and (name is None or it.name == name):
                yield it

    def get(self, name: str) -> SList | None:
        return next(self.lists(name), None)

    def req(self, name: str) -> SList:
        node = self.get(name)
        if node is None:
            raise SExprError(f"missing ({name} ...) inside ({self.name} ...)")
        return node

    def atoms(self) -> list[Token | Str]:
        return [it for it in self.items if isinstance(it, (Token, Str))]

    def arg(self, index: int = 0) -> str:
        """Value of the index-th atom (token or string)."""
        atoms = self.atoms()
        if index >= len(atoms):
            raise SExprError(f"({self.name} ...) has no argument #{index}")
        return atoms[index].value

    def set_arg(self, index: int, value: str) -> None:
        n = -1
        for it in self.items:
            if isinstance(it, (Token, Str)):
                n += 1
                if n == index:
                    it.value = value
                    return
        raise SExprError(f"({self.name} ...) has no argument #{index}")

    def child_arg(self, name: str, index: int = 0) -> str:
        return self.req(name).arg(index)

    def find_uuid(self, name: str, uuid: str) -> SList | None:
        return next((n for n in self.lists(name) if n.arg(0) == uuid), None)

    def remove(self, node: SList) -> None:
        """Remove a child list together with the line break following it."""
        idx = next(i for i, it in enumerate(self.items) if it is node)
        del self.items[idx]
        if idx < len(self.items) and self.items[idx] is NL:
            del self.items[idx]

    def insert_sorted(self, node: SList, key: str | None = None) -> None:
        """Insert ``node`` (followed by NL) among same-named siblings, sorted
        by first argument, as LibrePCB does for UUID-keyed lists."""
        key = node.arg(0) if key is None else key
        siblings = [(i, it) for i, it in enumerate(self.items)
                    if isinstance(it, SList) and it.name == node.name]
        pos = None
        for i, sib in siblings:
            if sib.arg(0) > key:
                pos = i
                break
        if pos is None:
            if siblings:
                pos = siblings[-1][0] + 1
                if pos < len(self.items) and self.items[pos] is NL:
                    pos += 1
            else:
                raise SExprError(f"no sibling ({node.name} ...) to anchor insertion; "
                                 "use insert_after")
        self.items[pos:pos] = [node, NL]

    def insert_after(self, anchor_names: list[str], node: SList) -> None:
        """Insert node + NL after the last child whose name is in anchor_names
        (or after the first argument line if none present)."""
        last = None
        for i, it in enumerate(self.items):
            if isinstance(it, SList) and it.name in anchor_names:
                last = i
        if last is None:
            raise SExprError(f"no anchor {anchor_names} in ({self.name} ...)")
        pos = last + 1
        if pos < len(self.items) and self.items[pos] is NL:
            pos += 1
        self.items[pos:pos] = [node, NL]

    # ---- serialization -------------------------------------------------
    def to_string(self, indent: int = 0) -> str:
        return _to_string(self, indent)

    def to_bytes(self) -> bytes:
        s = self.to_string(0)
        if not s.endswith("\n"):
            s += "\n"
        return s.encode("utf-8")


def L(name: str, *items: Item | str | float | int) -> SList:
    """Build a list; python ``str``/numbers become tokens, use ``Str`` for quoted."""
    out: list[Item] = []
    for it in items:
        if isinstance(it, (SList, Token, Str, _LineBreak)):
            out.append(it)
        elif isinstance(it, bool):
            out.append(Token("true" if it else "false"))
        elif isinstance(it, str):
            out.append(Token(it))
        else:
            raise TypeError(f"use fmt_mm/fmt_deg for numbers, got {it!r}")
    return SList(name, out)


def _to_string(node: Item, indent: int) -> str:
    if isinstance(node, SList):
        if not node.name or not all(_is_token_char(c) for c in node.name):
            raise SExprError(f"invalid list name {node.name!r}")
        parts = ["(", node.name]
        last_space = False
        last = len(node.items) - 1
        for i, child in enumerate(node.items):
            if not last_space and child is not NL:
                parts.append(" ")
            next_nl = i < last and node.items[i + 1] is NL
            cur = 0 if (child is NL and next_nl) else indent + 1
            last_space = child is NL and cur > 0
            if last_space and i == last:
                cur -= 1
            parts.append(_to_string(child, cur))
        parts.append(")")
        return "".join(parts)
    if isinstance(node, Token):
        if not node.value or not all(_is_token_char(c) for c in node.value):
            raise SExprError(f"invalid token {node.value!r}")
        return node.value
    if isinstance(node, Str):
        return '"' + "".join(_ESCAPE_OUT.get(c, c) for c in node.value) + '"'
    if node is NL:
        return "\n" + " " * indent
    raise SExprError(f"cannot serialize {node!r}")


# ---- parser --------------------------------------------------------------

class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def skip_ws(self, newlines: bool = False) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c == ";":  # Lisp line comment, as LibrePCB accepts
                while self.i < n and s[self.i] != "\n":
                    self.i += 1
            elif c == "\n" and not newlines:
                return
            elif c in " \f\r\t\v" or c == "\n":
                self.i += 1
            else:
                return

    def node(self) -> Item:
        c = self.s[self.i]
        if c == "\n":
            self.i += 1
            self.skip_ws()
            return NL
        if c == "(":
            return self.list_()
        if c == '"':
            return Str(self.string())
        return Token(self.token())

    def list_(self) -> SList:
        self.i += 1
        lst = SList(self.token())
        while True:
            if self.i >= len(self.s):
                raise SExprError("S-expression node ended without closing ')'")
            if self.s[self.i] == ")":
                self.i += 1
                self.skip_ws()
                return lst
            lst.items.append(self.node())

    def token(self) -> str:
        start = self.i
        while self.i < len(self.s) and _is_token_char(self.s[self.i]):
            self.i += 1
        tok = self.s[start:self.i]
        if not tok:
            ch = self.s[self.i] if self.i < len(self.s) else "EOF"
            raise SExprError(f"invalid token character {ch!r} at offset {self.i}")
        self.skip_ws()
        return tok

    def string(self) -> str:
        self.i += 1
        out: list[str] = []
        while True:
            if self.i >= len(self.s):
                raise SExprError("string not terminated")
            c = self.s[self.i]
            self.i += 1
            if c == "\\":
                e = self.s[self.i] if self.i < len(self.s) else ""
                if e not in _ESCAPE_IN:
                    raise SExprError(f"illegal escape sequence '\\{e}'")
                out.append(_ESCAPE_IN[e])
                self.i += 1
            elif c == '"':
                break
            else:
                out.append(c)
        self.skip_ws()
        return "".join(out)


def parse(data: bytes | str) -> SList:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    p = _Parser(text)
    p.skip_ws(newlines=True)
    if p.i >= len(text):
        raise SExprError("no S-expression node found")
    if text[p.i] != "(":
        raise SExprError("root node is not a list")
    root = p.list_()
    p.skip_ws(newlines=True)
    if p.i < len(text):
        raise SExprError("file contains more than one root node")
    return root


# ---- number formatting (Toolbox::decimalFixedPointToString, 6 decimals) ---

def _fixed6(units: int) -> str:
    sign = "-" if units < 0 else ""
    units = abs(units)
    whole, frac = divmod(units, 1_000_000)
    frac_s = f"{frac:06d}".rstrip("0") or "0"
    return f"{sign}{whole}.{frac_s}"


def mm_to_nm(mm: float | str | Decimal) -> int:
    return int((Decimal(str(mm)) * 1_000_000).quantize(Decimal(1), ROUND_HALF_UP))


def fmt_mm(mm: float | str | Decimal) -> str:
    return _fixed6(mm_to_nm(mm))


def fmt_nm(nm: int) -> str:
    return _fixed6(nm)


def deg_to_udeg(deg: float | str | Decimal) -> int:
    """Degrees to LibrePCB microdegrees, C-style modulo 360 like ``Angle``."""
    u = int((Decimal(str(deg)) * 1_000_000).quantize(Decimal(1), ROUND_HALF_UP))
    r = abs(u) % 360_000_000
    return -r if u < 0 else r


def fmt_deg(deg: float | str | Decimal) -> str:
    return _fixed6(deg_to_udeg(deg))


def fmt_udeg(udeg: int) -> str:
    r = abs(udeg) % 360_000_000
    return _fixed6(-r if udeg < 0 else r)


def parse_mm(s: str) -> float:
    return float(Decimal(s))
