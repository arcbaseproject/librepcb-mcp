import copy

import pytest

from librepcb_mcp.librepcb import geometry as g
from librepcb_mcp.librepcb.sexpr import (NL, L, SExprError, SList, Str, Token, deg_to_udeg,
                                         fmt_deg, fmt_mm, parse)

from .conftest import EXAMPLE


def test_roundtrip_example_files_byte_identical():
    files = list(EXAMPLE.rglob("*.lp"))
    assert len(files) > 10
    for f in files:
        data = f.read_bytes()
        assert parse(data).to_bytes() == data, f


def test_string_escaping_roundtrip():
    root = L("x", Str('a "quoted" \\ back\nnewline\ttab'))
    out = root.to_bytes()
    assert out == b'(x "a \\"quoted\\" \\\\ back\\nnewline\\ttab")\n'
    assert parse(out).arg(0) == 'a "quoted" \\ back\nnewline\ttab'


def test_layout_rules_match_librepcb():
    root = SList("root", [Token("u"), NL, L("a", "1"), NL, L("b", L("c", "2"), NL), NL])
    assert root.to_bytes() == b"(root u\n (a 1)\n (b (c 2)\n )\n)\n"
    assert SList("librepcb_jobs", [NL]).to_bytes() == b"(librepcb_jobs\n)\n"


@pytest.mark.parametrize("bad", ["", "(a", "(a))", "(a) (b)", '(a "x)', r'(a "\q")', "x", "(a b"])
def test_parse_errors(bad):
    with pytest.raises(SExprError):
        parse(bad)


def test_comments_are_skipped():
    assert parse("; c\n(a 1) ; tail\n").arg(0) == "1"


def test_invalid_token_rejected_on_write():
    with pytest.raises(SExprError):
        L("a", "has space").to_bytes()


@pytest.mark.parametrize("mm,s", [(0, "0.0"), (2.54, "2.54"), (-1.5, "-1.5"), (0.15875, "0.15875"),
                                  (1e-6, "0.000001"), (100, "100.0"), (0.0000004, "0.0")])
def test_fmt_mm(mm, s):
    assert fmt_mm(mm) == s


def test_angles_wrap_like_librepcb():
    assert fmt_deg(450) == "90.0"
    assert fmt_deg(-90) == "-90.0"
    assert deg_to_udeg(360) == 0
    assert g.add_angles(270_000_000, 180_000_000) == 90_000_000


def test_nl_sentinel_survives_deepcopy():
    root = L("a", NL, L("b"), NL)
    c = copy.deepcopy(root)
    assert c.items[0] is NL and c.to_bytes() == root.to_bytes()


def test_rotate_exact_and_transform():
    assert g.rotate((1000, 0), 90_000_000) == (0, 1000)
    assert g.rotate((1000, 0), 180_000_000) == (-1000, 0)
    assert g.rotate((1000, 0), 45_000_000) == (707, 707)
    p = g.transform((1000, 2000), (5, 5), 90_000_000, mirror=True)
    assert g.untransform(p, (5, 5), 90_000_000, mirror=True) == (1000, 2000)


def test_add_child_keeps_type_order_and_uuid_sort():
    root = parse("(librepcb_circuit\n (variant v (name \"Std\")\n )\n (net b)\n (component z)\n)\n")
    g.add_child(root, L("net", "a"))
    g.add_child(root, L("net", "c"))
    g.add_child(root, L("component", "y"))
    names = [(n.name, n.arg(0)) for n in root.lists()]
    assert names == [("variant", "v"), ("net", "a"), ("net", "b"), ("net", "c"),
                     ("component", "y"), ("component", "z")]
