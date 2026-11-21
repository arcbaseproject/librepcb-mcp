"""MCP tool groups. Units everywhere: millimeters and degrees (counter-clockwise),
LibrePCB's coordinate system (y axis pointing up)."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..librepcb.errors import LibrePCBError
from ..librepcb.sexpr import SExprError

log = logging.getLogger(__name__)

ProjectPath = Annotated[str, Field(description="Path to the project's *.lpp file or its directory")]
Schematic = Annotated[str | None, Field(description="Schematic name, UUID or index; default: first")]
Board = Annotated[str | None, Field(description="Board name, UUID or index; default: first")]
ComponentRef = Annotated[str, Field(description="Component designator (e.g. 'R1') or UUID")]


def registrar(mcp: MCPServer) -> Callable[..., Callable]:
    def tool(read_only: bool = False, destructive: bool = False) -> Callable:
        def deco(fn: Callable[..., Any]) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except (LibrePCBError, SExprError) as e:
                    raise ToolError(str(e)) from e
                except OSError as e:
                    raise ToolError(f"File system error: {e}") from e

            mcp.tool(annotations=ToolAnnotations(readOnlyHint=read_only,
                                                 destructiveHint=destructive,
                                                 idempotentHint=read_only))(wrapper)
            return fn
        return deco
    return tool
