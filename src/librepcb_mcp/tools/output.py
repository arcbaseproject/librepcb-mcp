"""Validation and export tools (all executed by librepcb-cli)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..service import Service
from . import Board, ProjectPath, registrar

OutputDir = Annotated[str | None, Field(description="Output directory inside a configured root; "
                                                    "default: <project>/output/mcp")]


def register(mcp, svc: Service) -> None:  # noqa: ANN001
    tool = registrar(mcp)

    @tool(read_only=True)
    def run_erc(project_path: ProjectPath) -> dict:
        """Run LibrePCB's electrical rule check. Returns non-approved messages."""
        return svc.check(project_path, erc=True, drc=False)

    @tool(read_only=True)
    def run_drc(project_path: ProjectPath,
                board: Annotated[str | None, Field(description="Board; default: all boards")] = None) -> dict:
        """Run LibrePCB's design rule check with each board's own DRC settings."""
        return svc.check(project_path, erc=False, drc=True, board=board)

    @tool()
    def export_schematic_pdf(project_path: ProjectPath, output_dir: OutputDir = None) -> dict:
        """Export all schematic pages to one PDF."""
        return svc.export(project_path, ["schematic_pdf"], None, output_dir)

    @tool()
    def export_board_pdf(project_path: ProjectPath, board: Board = None,
                         output_dir: OutputDir = None) -> dict:
        """Export a board assembly drawing (top and bottom) to PDF."""
        return svc.export(project_path, ["board_pdf"], board, output_dir)

    @tool()
    def export_gerbers(project_path: ProjectPath, board: Board = None,
                       output_dir: OutputDir = None) -> dict:
        """Export PCB fabrication data: Gerber X2 layers (copper, mask, silkscreen, paste,
        outline) AND Excellon drill files (PTH/NPTH), into gerber/<board>/."""
        return svc.export(project_path, ["gerber"], board, output_dir)

    @tool()
    def export_bom(project_path: ProjectPath,
                   board: Annotated[str | None, Field(description="Board for a board-specific "
                                                                  "BOM; default: generic BOM")] = None,
                   output_dir: OutputDir = None) -> dict:
        """Export the bill of materials as CSV (one file per assembly variant)."""
        return svc.export(project_path, ["bom"], board, output_dir)

    @tool()
    def export_project(project_path: ProjectPath, output_dir: OutputDir = None) -> dict:
        """Export the whole project as a single *.lppz archive (openable in LibrePCB)."""
        return svc.export(project_path, ["lppz"], None, output_dir)

    @tool()
    def run_output_jobs(project_path: ProjectPath,
                        jobs: Annotated[list[str] | None, Field(
                            description="Job names; default: all jobs defined in the project")] = None,
                        output_dir: OutputDir = None) -> dict:
        """Run the output jobs configured in the project itself (project/jobs.lp)."""
        return svc.run_project_jobs(project_path, jobs, output_dir)
