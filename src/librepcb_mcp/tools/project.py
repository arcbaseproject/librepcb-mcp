from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..librepcb import create, inspect, transaction
from ..librepcb.errors import LibrePCBError
from ..service import Service
from . import ProjectPath, registrar


def register(mcp, svc: Service) -> None:  # noqa: ANN001
    tool = registrar(mcp)

    @tool(read_only=True)
    def server_status() -> dict:
        """Show configuration: allowed project roots, detected librepcb-cli and version,
        library directories, backup directory. Call this first if anything fails."""
        return svc.status()

    @tool()
    def create_project(
        directory: Annotated[str, Field(description="New (or empty) directory inside a configured root")],
        name: Annotated[str, Field(description="Project name")],
        author: Annotated[str, Field(description="Author metadata")] = "",
        with_board: Annotated[bool, Field(description="Also create board 'default'")] = True,
        board_width_mm: float = 100.0,
        board_height_mm: float = 80.0,
    ) -> dict:
        """Create a new, empty LibrePCB 2.x project (schematic 'Main', optional board
        with a rectangular outline). The result is verified by opening it with librepcb-cli."""
        lpp = create.create_project(Path(directory), name, author, svc.cfg.roots, svc.cfg.cli,
                                    with_board, board_width_mm, board_height_mm)
        r = svc.cli.verify_project(lpp)
        if not r.ok:
            shutil.rmtree(lpp.parent, ignore_errors=True)
            raise LibrePCBError(f"LibrePCB rejected the new project (removed):\n{r.stdout}{r.stderr}")
        return {"success": True, "project_file": str(lpp),
                **inspect.project_info(svc.open(str(lpp)))}

    @tool(read_only=True)
    def list_projects(
        root: Annotated[str | None, Field(description="Directory to scan; default: all roots")] = None,
    ) -> dict:
        """Find LibrePCB projects (*.lpp) below the configured roots."""
        return {"projects": svc.list_projects(root)}

    @tool(read_only=True)
    def get_project_info(project_path: ProjectPath) -> dict:
        """Project metadata, schematics, boards, component/net counts, file format,
        and whether LibrePCB currently has the project open (lock file)."""
        return inspect.project_info(svc.open(project_path))

    @tool(read_only=True)
    def validate_project(project_path: ProjectPath) -> dict:
        """Open the project with librepcb-cli in strict mode (parse errors, broken
        references, non-canonical files) and run ERC plus DRC on all boards."""
        strict = svc.check(project_path, erc=False, drc=False, strict=True)
        checks = svc.check(project_path, erc=True, drc=bool(svc.open(project_path).board_paths()))
        return {**strict, **checks}

    @tool()
    def backup_project(project_path: ProjectPath) -> dict:
        """Copy the project (without output/) to the backup directory. Edits make
        automatic backups too; this is for an explicit checkpoint."""
        prj = svc.open(project_path)
        dest = transaction.make_backup(prj.dir, svc.cfg.backup_dir, "manual")
        return {"success": True, "backup": str(dest)}
