"""MCP protocol tests: in-process client and a real STDIO subprocess."""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from librepcb_mcp.config import Config
from librepcb_mcp.server import build_server

from .conftest import needs_cli, needs_lib

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "server_status", "create_project", "list_projects", "get_project_info", "validate_project",
    "backup_project", "list_schematics", "list_components", "get_component", "list_nets",
    "get_net_connections", "inspect_schematic", "create_schematic", "add_component",
    "remove_component", "move_component", "set_component_value", "connect_pins",
    "disconnect_pins", "create_net", "add_net_label", "list_boards", "get_board_info",
    "create_board", "add_device_to_board", "place_component", "rotate_component",
    "set_board_outline", "create_trace", "create_via", "route_net", "run_erc", "run_drc",
    "export_schematic_pdf", "export_board_pdf", "export_gerbers", "export_bom", "export_project",
    "run_output_jobs", "list_libraries", "search_components", "get_component_details",
    "get_device_details", "list_footprints",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _json(result) -> object:
    assert result.content, result
    return json.loads(result.content[0].text)


async def test_tools_registered_with_schemas(cfg: Config):
    async with Client(build_server(cfg)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.description and len(t.description) > 20, t.name
        assert t.input_schema["type"] == "object"
    assert tools["place_component"].annotations.read_only_hint is False
    assert tools["get_project_info"].annotations.read_only_hint is True
    assert tools["remove_component"].annotations.destructive_hint is True
    props = tools["add_component"].input_schema["properties"]
    assert {"project_path", "device", "x_mm", "y_mm", "rotation_deg"} <= set(props)


async def test_read_tools_on_example(cfg: Config, example: Path):
    async with Client(build_server(cfg)) as c:
        info = _json(await c.call_tool("get_project_info", {"project_path": str(example)}))
        assert info["name"] == "LED Example"
        comp = _json(await c.call_tool("get_component", {"project_path": str(example),
                                                         "component": "LED1"}))
        assert comp["board_devices"][0]["rotation_deg"] == 90.0
        projects = _json(await c.call_tool("list_projects", {}))
        assert [Path(p["project_file"]) for p in projects["projects"]] == [example]


async def test_errors_are_tool_errors_not_crashes(cfg: Config, example: Path, tmp_path: Path):
    async with Client(build_server(cfg)) as c:
        r = await c.call_tool("get_project_info", {"project_path": str(tmp_path)})
        assert r.is_error and "outside the configured project roots" in r.content[0].text
        r = await c.call_tool("get_component", {"project_path": str(example), "component": "X9"})
        assert r.is_error and "No component 'X9'" in r.content[0].text
        r = await c.call_tool("place_component", {"project_path": str(example)})
        assert r.is_error  # schema validation: component missing


async def test_missing_cli_reported(cfg: Config, example: Path):
    nocli = Config(cfg.roots, cfg.libraries, None, cfg.backup_dir, 10)
    async with Client(build_server(nocli)) as c:
        status = _json(await c.call_tool("server_status", {}))
        assert status["cli_available"] is False
        r = await c.call_tool("run_erc", {"project_path": str(example)})
        assert r.is_error and "librepcb-cli not found" in r.content[0].text
        r = await c.call_tool("move_component", {"project_path": str(example), "component": "R1",
                                                 "x_mm": 1, "y_mm": 1})
        assert r.is_error and "librepcb-cli not found" in r.content[0].text


@needs_cli
async def test_edit_through_mcp(cfg: Config, example: Path):
    async with Client(build_server(cfg)) as c:
        r = _json(await c.call_tool("move_component", {"project_path": str(example),
                                                       "component": "R1", "x_mm": 45.72,
                                                       "y_mm": 50.8, "rotation_deg": 0}))
        assert r["success"] and r["position"] == {"x_mm": 45.72, "y_mm": 50.8}
        assert Path(r["backup"]).is_dir()
        erc = _json(await c.call_tool("run_erc", {"project_path": str(example)}))
        assert erc["erc"]["passed"]


async def test_stdio_subprocess(cfg: Config, example: Path):
    env = {**os.environ, "LIBREPCB_MCP_ROOTS": str(cfg.roots[0]), "LIBREPCB_MCP_LOG": "DEBUG"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "librepcb_mcp"], env=env)
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == EXPECTED_TOOLS
        info = _json(await c.call_tool("get_project_info", {"project_path": str(example)}))
        assert info["component_count"] == 4


@needs_lib
async def test_library_tools(cfg: Config):
    async with Client(build_server(cfg)) as c:
        libs = _json(await c.call_tool("list_libraries", {}))["libraries"]
        assert libs[0]["name"] == "LibrePCB_Base.lplib" and libs[0]["devices"] > 50
        res = _json(await c.call_tool("search_components", {"query": "LED red 3.0", "limit": 5}))
        dev = res["results"][0]
        assert "LED" in dev["name"] and dev["component"]["name"] == "LED"
        det = _json(await c.call_tool("get_device_details", {"device": dev["id"]}))
        assert {p["signal"] for p in det["pads"]} == {"A", "C"}
        fps = _json(await c.call_tool("list_footprints", {"package_or_device": dev["id"]}))
        assert fps["footprints"] and fps["footprints"][0]["pads"] == 2
        cmp = _json(await c.call_tool("get_component_details",
                                      {"component_id": dev["component"]["id"]}))
        assert cmp["prefix"] == "LED" and any(d["id"] == dev["id"] for d in cmp["devices"])
        r = await c.call_tool("get_device_details", {"device": "no such device"})
        assert r.is_error and "search_components" in r.content[0].text
