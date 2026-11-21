"""MCP server entry point (STDIO transport)."""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Config
from .service import Service
from .tools import board, library, output, project, schematic

INSTRUCTIONS = """\
Work on real LibrePCB 2.x projects. Units: millimeters, degrees counter-clockwise, y axis up.
Typical flow: search_components -> create_project -> add_component -> connect_pins /
add_net_label -> run_erc -> add_device_to_board -> place_component -> create_trace /
route_net -> run_drc -> export_gerbers. Every edit is backed up, written atomically and
re-opened with librepcb-cli; failed edits are rolled back. Close the project in the
LibrePCB GUI before editing. Passing ERC/DRC does not make a design production-ready."""


def build_server(cfg: Config | None = None) -> MCPServer:
    svc = Service(cfg or Config.from_env())
    mcp = MCPServer("librepcb", instructions=INSTRUCTIONS, version=__version__)
    for group in (project, schematic, board, output, library):
        group.register(mcp, svc)
    return mcp


def main() -> None:
    # stdout belongs to the MCP protocol: all diagnostics go to stderr
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("LIBREPCB_MCP_LOG", "WARNING"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_server().run("stdio")
