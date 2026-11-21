"""Project reading, path policy, transactions — no librepcb-cli required
(a fake CLI script stands in where verification is exercised)."""

import os
import stat
from pathlib import Path

import pytest

from librepcb_mcp.config import Config
from librepcb_mcp.librepcb import circuit_ops as co
from librepcb_mcp.librepcb import inspect, transaction
from librepcb_mcp.librepcb.cli import LibrePCBCli, parse_output
from librepcb_mcp.librepcb.errors import LibrePCBError
from librepcb_mcp.librepcb.project import Project

from .conftest import snapshot


def fake_cli(root: Path, exit_code: int, stdout: str = "") -> LibrePCBCli:
    script = root / f"fake-cli-{exit_code}.sh"
    script.write_text(f"#!/bin/sh\nprintf '%s' '{stdout}'\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return LibrePCBCli((str(script),), 10)


def test_inspect_example(example: Path, cfg: Config):
    prj = Project(example, cfg.roots)
    info = inspect.project_info(prj)
    assert info["name"] == "LED Example" and info["file_format"] == "2"
    assert info["component_count"] == 4
    comps = {c["name"]: c for c in (inspect.component(prj, c, True) for c in prj.components())}
    assert comps["R1"]["value_resolved"] == "330 ohm"
    assert {s["signal"]: s["net"] for s in comps["LED1"]["signals"]} == {"A": "LED_A", "C": "GND"}
    nets = {n["name"]: n for n in inspect.list_nets(prj)}
    assert nets["GND"]["component_signals"] == ["LED1:C", "TP2:1"]
    assert nets["GND"]["net_labels"] == 2
    brd = inspect.board(prj, prj.board_paths()[0])
    assert all(r["unrouted_connections"] == 0 for r in brd["routing"])
    assert brd["unplaced_components"] == []


def test_path_outside_roots_rejected(example: Path, tmp_path: Path):
    with pytest.raises(LibrePCBError, match="outside the configured project roots"):
        Project(example, (tmp_path,))
    with pytest.raises(LibrePCBError, match="No project directories configured"):
        Project(example, ())


def test_symlink_escape_rejected(example: Path, cfg: Config, tmp_path: Path):
    link = cfg.roots[0] / "escape"
    link.symlink_to(tmp_path)
    with pytest.raises(LibrePCBError, match="outside"):
        Project(link, cfg.roots)


def test_invalid_projects(cfg: Config):
    root = cfg.roots[0]
    with pytest.raises(LibrePCBError, match="not found"):
        Project(root / "missing", cfg.roots)
    (root / "empty").mkdir()
    with pytest.raises(LibrePCBError, match="No \\*.lpp"):
        Project(root / "empty", cfg.roots)
    old = root / "old"
    old.mkdir()
    (old / "x.lpp").write_text("LIBREPCB-PROJECT")
    (old / ".librepcb-project").write_text("0.1\n")
    with pytest.raises(LibrePCBError, match="Unsupported project file format '0.1'"):
        Project(old, cfg.roots)


def test_corrupt_file_reports_parse_error(example: Path, cfg: Config):
    (example.parent / "circuit/circuit.lp").write_text("(librepcb_circuit\n (net")
    with pytest.raises(LibrePCBError, match="Cannot parse circuit/circuit.lp"):
        _ = Project(example, cfg.roots).circuit


def test_edit_refused_without_cli(example: Path, cfg: Config):
    before = snapshot(example.parent)
    with pytest.raises(LibrePCBError, match="librepcb-cli not found"):
        with transaction.edit(example, cfg.roots, LibrePCBCli(None), cfg.backup_dir, "t") as (p, _):
            co.set_component_value(p, "R1", "1k", None)
    assert snapshot(example.parent) == before


def test_edit_refused_when_open_in_librepcb(example: Path, cfg: Config):
    (example.parent / ".lock").write_text("Full Name\nuser\nhost\n123\nlibrepcb\n2026-01-01T00:00:00Z")
    with pytest.raises(LibrePCBError, match="open in LibrePCB \\(user@host"):
        with transaction.edit(example, cfg.roots, fake_cli(cfg.roots[0], 0), cfg.backup_dir, "t") as (p, _):
            co.set_component_value(p, "R1", "1k", None)


def test_rollback_when_verification_fails(example: Path, cfg: Config):
    before = snapshot(example.parent)
    cli = fake_cli(cfg.roots[0], 1, "Finished with errors!")
    with pytest.raises(LibrePCBError, match="rolled back"):
        with transaction.edit(example, cfg.roots, cli, cfg.backup_dir, "t") as (p, _):
            co.create_schematic(p, "Second")  # creates new file + dir
            co.set_component_value(p, "R1", "1k", None)
    assert snapshot(example.parent) == before
    assert not (example.parent / "schematics" / "second").exists()


def test_success_writes_backup_and_atomic_files(example: Path, cfg: Config):
    cli = fake_cli(cfg.roots[0], 0, "SUCCESS")
    with transaction.edit(example, cfg.roots, cli, cfg.backup_dir, "t") as (p, res):
        co.set_component_value(p, "R1", "1k", None)
    assert res.changed_files == ["circuit/circuit.lp"]
    assert (res.backup / "circuit/circuit.lp").read_bytes() != (example.parent / "circuit/circuit.lp").read_bytes()
    assert Project(example, cfg.roots).component("R1").child_arg("value") == "1k"
    assert not list(example.parent.rglob(".*.tmp"))


def test_conflicting_external_edit_detected(example: Path, cfg: Config):
    cli = fake_cli(cfg.roots[0], 0)
    circuit = example.parent / "circuit/circuit.lp"
    with pytest.raises(LibrePCBError, match="modified by another program"):
        with transaction.edit(example, cfg.roots, cli, cfg.backup_dir, "t") as (p, _):
            co.set_component_value(p, "R1", "1k", None)
            circuit.write_bytes(circuit.read_bytes() + b"\n")
    assert circuit.read_bytes().endswith(b")\n\n")  # external change kept, ours not written


def test_parse_cli_output():
    out = """Open project 'x.lpp'...
Run ERC...
  Approved messages: 1
  Non-approved messages: 1
    - [WARNING] Net 'N1' is unused
Run DRC...
  Board 'default':
    Approved messages: 0
    Non-approved messages: 2
      - [ERROR] Clearance on 'Top Copper': 'A' trace <-> 'B' < 0.2 mm
      - [WARNING] Minimum width
Run output job 'BOM'...
  => '/out/a.csv'
Finished with errors!
"""
    rep = parse_output(out)
    assert rep.ran_erc and rep.ran_drc and rep.erc_approved == 1
    assert [m.as_dict() for m in rep.erc] == [{"severity": "warning", "message": "Net 'N1' is unused"}]
    assert [m.board for m in rep.drc] == ["default", "default"]
    assert rep.output_files == ["/out/a.csv"]


def test_cli_timeout_and_missing_binary(tmp_path: Path):
    with pytest.raises(LibrePCBError, match="cannot execute"):
        LibrePCBCli((str(tmp_path / "nope"),)).run("--version")
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    with pytest.raises(LibrePCBError, match="timed out"):
        LibrePCBCli((str(slow),), timeout=0.5).run("x")


def test_config_from_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LIBREPCB_MCP_ROOTS", f"{tmp_path}{os.pathsep}~")
    monkeypatch.setenv("LIBREPCB_CLI", "/opt/x/librepcb-cli --flag")
    c = Config.from_env()
    assert c.roots == (tmp_path.resolve(), Path.home().resolve())
    assert c.cli == ("/opt/x/librepcb-cli", "--flag")
