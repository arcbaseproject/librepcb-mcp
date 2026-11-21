"""Integration tests against the real librepcb-cli (and LibrePCB_Base library)."""

from pathlib import Path

import pytest

from librepcb_mcp.config import Config
from librepcb_mcp.librepcb import board_ops as bo
from librepcb_mcp.librepcb import create as create_mod
from librepcb_mcp.librepcb import circuit_ops as co
from librepcb_mcp.librepcb import inspect
from librepcb_mcp.librepcb.errors import LibrePCBError
from librepcb_mcp.librepcb.library import LibraryIndex
from librepcb_mcp.librepcb.project import Project
from librepcb_mcp.librepcb.sexpr import NL
from librepcb_mcp.service import Service

from .conftest import needs_cli, needs_lib, snapshot

pytestmark = needs_cli


def edit(cfg: Config, svc: Service, lpp: Path, fn):
    return svc.edit(str(lpp), "test", fn)


def test_example_is_valid_and_clean(example: Path, cfg: Config):
    svc = Service(cfg)
    strict = svc.check(str(example), erc=False, drc=False, strict=True)
    assert strict["non_canonical_files"] == []
    res = svc.check(str(example), erc=True, drc=True)
    assert res["erc"]["passed"] and res["drc"]["passed"]


def test_workflow_edit_reopen_validate_export(example: Path, cfg: Config):
    svc = Service(cfg)
    prj = Project(example, cfg.roots)
    assert [c.child_arg("name") for c in prj.components()]  # 1. load, 2. inspect
    assert svc.check(str(example), erc=True, drc=False)["erc"]["passed"]  # 3. ERC
    out = edit(cfg, svc, example, lambda p: co.set_component_value(p, "R1", None, {"RESISTANCE": "470"}))
    assert out["success"] and out["changed_files"] == ["circuit/circuit.lp"]  # 4./5. edit+save
    r = svc.cli.verify_project(example)  # 6. reopen with CLI
    assert r.ok, r.stdout
    assert Project(example, cfg.roots).component("R1").req("attribute").child_arg("value") == "470"
    v = svc.check(str(example), erc=True, drc=True)  # 7. validate
    assert v["erc"]["passed"] and v["drc"]["passed"]
    exp = svc.export(str(example), ["schematic_pdf", "board_pdf", "gerber", "bom", "lppz"])  # 8.
    names = [Path(f).name for f in exp["files"]]
    assert any(n.endswith("_Schematic.pdf") for n in names)
    assert any(n.endswith("_COPPER-TOP.gbr") for n in names)
    assert any(n.endswith("_DRILLS-PTH.drl") for n in names)
    assert any(n.endswith(".lppz") for n in names)
    bom = next(f for f in exp["files"] if f.endswith(".csv"))
    assert "470Ω" in Path(bom).read_text()
    assert all(Path(f).stat().st_size > 0 for f in exp["files"])
    # re-export replaces own files and leaves foreign files alone
    foreign = Path(exp["output_dir"]) / "keep.txt"
    foreign.write_text("x")
    svc.export(str(example), ["bom"])
    assert foreign.exists()


def test_real_cli_rejection_rolls_back(example: Path, cfg: Config):
    svc = Service(cfg)
    before = snapshot(example.parent)

    def corrupt(p: Project) -> dict:
        r1 = p.component("R1")
        sig = next(r1.lists("signal"))
        sig.req("net").set_arg(0, "00000000-0000-4000-8000-000000000000")  # dangling net ref
        p.touch("circuit/circuit.lp")
        return {}

    with pytest.raises(LibrePCBError, match="rejected"):
        edit(cfg, svc, example, corrupt)
    assert snapshot(example.parent) == before


def test_non_canonical_output_is_rejected(example: Path, cfg: Config):
    svc = Service(cfg)

    def reorder(p: Project) -> dict:
        net = next(p.circuit.lists("net"))
        p.circuit.remove(net)
        p.circuit.items += [net, NL]  # nets after components: loads, but not canonical
        p.touch("circuit/circuit.lp")
        return {}

    with pytest.raises(LibrePCBError, match="rejected"):
        edit(cfg, svc, example, reorder)


@needs_lib
def test_build_circuit_and_board_from_scratch(cfg: Config):
    svc = Service(cfg)
    lpp = create_mod.create_project(cfg.roots[0] / "new", "Blinky", "t", cfg.roots, cfg.cli,
                                    board_width_mm=40, board_height_mm=20)
    assert svc.cli.verify_project(lpp).ok
    lib = LibraryIndex(cfg.libraries)
    dev = lib.search("resistor 1608", limit=1)[0]
    edit(cfg, svc, lpp, lambda p: co.add_component(p, lib, dev.uuid, 10.16, 10.16))
    edit(cfg, svc, lpp, lambda p: co.add_component(p, lib, dev.uuid, 30.48, 10.16, 90))
    edit(cfg, svc, lpp, lambda p: co.connect_pins(p, "R1:2", "R2:1"))
    edit(cfg, svc, lpp, lambda p: co.add_net_label(p, "R1:1", "VIN", -2.54, 0))
    edit(cfg, svc, lpp, lambda p: co.add_net_label(p, "R2:2", "GND", 0, -2.54))
    edit(cfg, svc, lpp, lambda p: co.move_component(p, "R2", 33.02, 12.7, 180))
    with pytest.raises(LibrePCBError, match="Disconnect one first"):
        edit(cfg, svc, lpp, lambda p: co.connect_pins(p, "R1:1", "R2:2"))
    with pytest.raises(LibrePCBError, match="no pin 'X'"):
        edit(cfg, svc, lpp, lambda p: co.connect_pins(p, "R1:X", "R2:2"))
    erc = svc.check(str(lpp), erc=True, drc=False)["erc"]
    assert {m["message"] for m in erc["messages"]} == {
        "Less than two pins in net: 'GND'", "Less than two pins in net: 'VIN'"}

    edit(cfg, svc, lpp, lambda p: bo.add_device_to_board(p, lib, "R1", 10, 10))
    edit(cfg, svc, lpp, lambda p: bo.add_device_to_board(p, lib, "R2", 25, 10, 90, flip=True))
    edit(cfg, svc, lpp, lambda p: bo.place_component(p, "R2", 25, 12, 0, False))
    with pytest.raises(LibrePCBError, match="SMD pad on top_cu"):
        edit(cfg, svc, lpp, lambda p: bo.route_net(p, "N1", "bot_cu"))
    r = edit(cfg, svc, lpp, lambda p: bo.route_net(p, "N1"))
    assert len(r["traces_created"]) == 1
    v = edit(cfg, svc, lpp, lambda p: bo.create_via(p, "GND", 30, 5))
    t = edit(cfg, svc, lpp, lambda p: bo.create_trace(p, "R2:2", v["connect_with"], "top_cu", 0.3,
                                                      [(28, 5), (29, 5)]))
    assert len(t["waypoint_junctions"]) == 2
    # T-join from an existing junction to another endpoint of the same net
    edit(cfg, svc, lpp, lambda p: bo.create_trace(p, t["waypoint_junctions"][0], v["connect_with"],
                                                  "top_cu", 0.3, [(28, 4)]))
    with pytest.raises(LibrePCBError, match="different nets"):
        edit(cfg, svc, lpp, lambda p: bo.create_trace(p, "R1:1", "R2:2"))
    edit(cfg, svc, lpp, lambda p: bo.create_board(p, "second", 20, 20))
    prj = Project(lpp, cfg.roots)
    info = inspect.board(prj, prj.board_paths()[0])
    assert {r["net"]: r["unrouted_connections"] for r in info["routing"]}["N1"] == 0
    assert len(prj.board_paths()) == 2
    drc = svc.check(str(lpp), erc=False, drc=True)["drc"]
    assert "messages" in drc  # DRC ran; result content depends on placement

    # disconnect removes wires, board traces and the now-unused auto net
    out = edit(cfg, svc, lpp, lambda p: co.disconnect_pins(p, ["R1:2", "R2:1"]))
    assert out["removed_nets"] == ["N1"]
    assert out["pins"][0]["removed_traces"] == 1
    out = edit(cfg, svc, lpp, lambda p: co.remove_component(p, "R2"))
    assert out["removed"] == "R2" and out["removed_traces"] == 1
    assert svc.cli.verify_project(lpp).ok
    assert svc.check(str(lpp), erc=True, drc=False)["erc"] is not None
