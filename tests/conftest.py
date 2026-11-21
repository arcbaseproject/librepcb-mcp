from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from librepcb_mcp.config import Config, detect_cli
from librepcb_mcp.librepcb.cli import LibrePCBCli

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "led-circuit"
EXAMPLE_LPP = EXAMPLE / "LED Example.lpp"

_CLI = detect_cli()
_LIB_CANDIDATES = [os.environ.get("LIBREPCB_MCP_TEST_LIBRARY", ""),
                   str(Path.home() / ".cache/librepcb-mcp-research/LibrePCB_Base.lplib")]
BASE_LIB = next((Path(p) for p in _LIB_CANDIDATES if p and (Path(p) / "dev").is_dir()), None)

needs_cli = pytest.mark.skipif(_CLI is None, reason="librepcb-cli not installed (set LIBREPCB_CLI)")
needs_lib = pytest.mark.skipif(BASE_LIB is None,
                               reason="LibrePCB_Base.lplib not found (set LIBREPCB_MCP_TEST_LIBRARY)")


@pytest.fixture
def root() -> Path:
    """Work directory below $HOME: the LibrePCB Flatpak can only see the home dir."""
    base = Path.home() / ".cache" / "librepcb-mcp-tests"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=base))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cfg(root: Path) -> Config:
    return Config(roots=(root,), libraries=(BASE_LIB,) if BASE_LIB else (),
                  cli=tuple(_CLI) if _CLI else None, backup_dir=root / "_backups", cli_timeout=120)


@pytest.fixture
def cli(cfg: Config) -> LibrePCBCli:
    return LibrePCBCli(cfg.cli, 120)


@pytest.fixture
def example(root: Path) -> Path:
    dst = root / "led-circuit"
    shutil.copytree(EXAMPLE, dst, ignore=shutil.ignore_patterns("output"))
    return dst / "LED Example.lpp"


def snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}
