"""Regression tests for export, backup, and library failures."""

import shutil
from uuid import uuid4

import pytest

from librepcb_mcp.config import Config
from librepcb_mcp.librepcb.cli import CliResult
from librepcb_mcp.librepcb.transaction import edit, make_backup
from librepcb_mcp.service import Service

from .conftest import EXAMPLE, EXAMPLE_LPP


def test_export_preserves_existing_jobs_file(tmp_path, monkeypatch):
    svc = Service(Config((EXAMPLE, tmp_path), (), None, tmp_path / "backups", 10))
    existing = tmp_path / ".librepcb-mcp-jobs.lp"
    existing.write_text("existing content")

    def fail(_project, _jobs_flag, jobs_path, *_args):
        assert jobs_path.is_file()
        assert existing.read_text() == "existing content"
        raise RuntimeError("simulated CLI failure")

    monkeypatch.setattr(svc.cli, "open_project", fail)
    with pytest.raises(RuntimeError, match="simulated CLI failure"):
        svc.export(str(EXAMPLE_LPP), ["bom"], output_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == [existing]


def test_backup_root_inside_project(tmp_path):
    (tmp_path / "project.lp").write_text("project content")
    root = tmp_path / "backups"
    for _ in range(2):
        backup = make_backup(tmp_path, root, "manual")
        assert (backup / "project.lp").read_text() == "project content"
        assert not (backup / "backups").exists()


def test_library_copy_failure_can_be_retried(tmp_path, monkeypatch):
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project)
    src = tmp_path / "element"
    src.mkdir()
    (src / "element.lp").write_text("library content")
    missing = src / "missing"
    missing.symlink_to("nonexistent")
    dst = project / "library" / "pkg" / str(uuid4())
    svc = Service(Config((tmp_path,), (), None, tmp_path / "backups", 10))
    monkeypatch.setattr(svc.cli, "require", lambda: [])
    monkeypatch.setattr(svc.cli, "verify_project", lambda _path: CliResult([], 0, "", ""))

    with pytest.raises(shutil.Error):
        with edit(project, svc.cfg.roots, svc.cli, svc.cfg.backup_dir, "test") as (prj, _):
            prj.new_dirs.append((src, dst))
    assert not dst.exists()

    missing.unlink()
    with edit(project, svc.cfg.roots, svc.cli, svc.cfg.backup_dir, "test") as (prj, _):
        prj.new_dirs.append((src, dst))
    assert (dst / "element.lp").read_text() == "library content"
