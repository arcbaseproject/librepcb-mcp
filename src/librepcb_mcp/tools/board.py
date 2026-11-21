from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..librepcb import board_ops as bo
from ..librepcb import inspect
from ..librepcb.errors import LibrePCBError
from ..service import Service
from . import Board, ComponentRef, ProjectPath, registrar

Layer = Annotated[str, Field(description="Copper layer: top_cu, bot_cu, or in1_cu... (inner)")]
Endpoint = Annotated[str, Field(description="Pad as 'DESIGNATOR:PAD' (pad name or signal name) "
                                            "or 'via:<uuid>'")]


def register(mcp, svc: Service) -> None:  # noqa: ANN001
    tool = registrar(mcp)

    @tool(read_only=True)
    def list_boards(project_path: ProjectPath) -> dict:
        """List boards (id, name, device count)."""
        return {"boards": inspect.list_boards(svc.open(project_path))}

    @tool(read_only=True)
    def get_board_info(project_path: ProjectPath, board: Board = None) -> dict:
        """Board outline, layers, design-rule defaults, placed devices, unplaced components,
        and per-net routing status (unrouted connections, trace length)."""
        prj = svc.open(project_path)
        return inspect.board(prj, prj.board(board)[0])

    @tool()
    def create_board(project_path: ProjectPath, name: str, width_mm: float = 100.0,
                     height_mm: float = 80.0) -> dict:
        """Add a board with LibrePCB's default settings and a rectangular outline."""
        return svc.edit(project_path, "create_board",
                        lambda p: bo.create_board(p, name, width_mm, height_mm))

    @tool()
    def add_device_to_board(
        project_path: ProjectPath, component: ComponentRef, x_mm: float, y_mm: float,
        rotation_deg: float = 0.0,
        flip: Annotated[bool, Field(description="Place on the bottom side")] = False,
        board: Board = None,
        device: Annotated[str | None, Field(description="Device UUID/name; default: the "
                                                        "component's assembly option")] = None,
        footprint: Annotated[str | None, Field(description="Footprint name/UUID; default: first")] = None,
    ) -> dict:
        """Place a schematic component's footprint on a board (copies device and package
        into the project library)."""
        return svc.edit(project_path, "add_device", lambda p: bo.add_device_to_board(
            p, svc.lib, component, x_mm, y_mm, rotation_deg, flip, board, device, footprint))

    @tool()
    def place_component(
        project_path: ProjectPath, component: ComponentRef,
        x_mm: Annotated[float | None, Field(description="default: keep")] = None,
        y_mm: Annotated[float | None, Field(description="default: keep")] = None,
        rotation_deg: Annotated[float | None, Field(description="Absolute; default: keep")] = None,
        flip: Annotated[bool | None, Field(description="Bottom side; default: keep")] = None,
        board: Board = None,
    ) -> dict:
        """Move/rotate/flip a device on a board. Traces stay attached to its pads."""
        return svc.edit(project_path, "place_component", lambda p: bo.place_component(
            p, component, x_mm, y_mm, rotation_deg, flip, board))

    @tool()
    def rotate_component(project_path: ProjectPath, component: ComponentRef,
                         rotation_deg: Annotated[float, Field(description="Absolute rotation")],
                         board: Board = None) -> dict:
        """Set a board device's rotation, keeping its position."""
        return svc.edit(project_path, "rotate_component", lambda p: bo.place_component(
            p, component, None, None, rotation_deg, None, board))

    @tool()
    def set_board_outline(
        project_path: ProjectPath,
        points: Annotated[list[tuple[float, float]] | None, Field(
            description="Polygon vertices [[x_mm, y_mm], ...]; closed automatically")] = None,
        width_mm: Annotated[float | None, Field(description="Rectangle from (0,0) if no points")] = None,
        height_mm: float | None = None,
        board: Board = None,
    ) -> dict:
        """Replace the board outline with a polygon or a width x height rectangle at the origin."""
        if points is None:
            if width_mm is None or height_mm is None:
                raise LibrePCBError("Pass points, or width_mm and height_mm")
            points = [(0, 0), (width_mm, 0), (width_mm, height_mm), (0, height_mm)]
        pts = points
        return svc.edit(project_path, "set_outline", lambda p: bo.set_board_outline(p, pts, board))

    @tool()
    def create_trace(
        project_path: ProjectPath, start: Endpoint, end: Endpoint, layer: Layer = "top_cu",
        width_mm: Annotated[float | None, Field(description="default: board default trace width")] = None,
        waypoints: Annotated[list[tuple[float, float]] | None, Field(
            description="Intermediate corner points [[x_mm, y_mm], ...]")] = None,
        board: Board = None,
    ) -> dict:
        """Draw a copper trace between two pads/vias of the same net, optionally via corner
        points. Electrical rules are not checked here: run run_drc afterwards."""
        return svc.edit(project_path, "create_trace", lambda p: bo.create_trace(
            p, start, end, layer, width_mm, waypoints, board))

    @tool()
    def create_via(
        project_path: ProjectPath, net: Annotated[str, Field(description="Net name or UUID")],
        x_mm: float, y_mm: float,
        drill_mm: Annotated[float | None, Field(description="default: board default")] = None,
        size_mm: Annotated[float | None, Field(description="Outer diameter; default drill+0.4")] = None,
        board: Board = None,
    ) -> dict:
        """Add a through-hole via (top to bottom copper) on a net. Connect it with
        create_trace using the returned 'via:<uuid>' endpoint."""
        return svc.edit(project_path, "create_via", lambda p: bo.create_via(
            p, net, x_mm, y_mm, drill_mm, size_mm, board))

    @tool()
    def route_net(
        project_path: ProjectPath, net: Annotated[str, Field(description="Net name or UUID")],
        layer: Layer = "top_cu",
        width_mm: Annotated[float | None, Field(description="default: board default")] = None,
        board: Board = None,
    ) -> dict:
        """Connect all not-yet-connected pads of a net with straight traces (shortest
        spanning tree). NOT an autorouter: no obstacle avoidance. Run run_drc afterwards
        and fix violations by moving parts or using create_trace with waypoints."""
        return svc.edit(project_path, "route_net", lambda p: bo.route_net(p, net, layer, width_mm, board))
