"""Create new projects and boards with the same content LibrePCB 2.x's
new-project wizard produces (Project::create + Board::addDefaultContent)."""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from . import geometry as g
from .errors import LibrePCBError
from .project import Project, check_inside_roots
from .sexpr import parse
from .transaction import atomic_write

GITIGNORE = """\
# LibrePCB files
user/
*.user.lp
.lock
*~

# Files created by operating systems or file managers
.DS_Store
Thumbs.db

# Files created by other applications
*.autosave
*.swp
"""

BOARD_TEMPLATE = """\
(librepcb_board {uuid}
 (name "{name}")
 (default_font "newstroke.bene")
 (grid (interval 2.54) (unit millimeters))
 (layers (inner 0))
 (thickness 1.6)
 (solder_resist green)
 (silkscreen white)
 (silkscreen_layers_top top_legend top_names)
 (silkscreen_layers_bot bot_legend bot_names)
 (design_rules
  (default_trace_width 0.5)
  (default_via_drill_diameter 0.3)
  (stopmask_max_via_drill_diameter 0.5)
  (stopmask_clearance (ratio 0.0) (min 0.1) (max 0.1))
  (solderpaste_clearance (ratio 0.1) (min 0.0) (max 1.0))
  (pad_annular_ring (outer full) (inner full) (ratio 0.25) (min 0.25) (max 2.0))
  (via_annular_ring (ratio 0.25) (min 0.2) (max 2.0))
 )
 (design_rule_check
  (min_pcb_size 0.0 0.0)
  (max_pcb_size (double_sided 0.0 0.0) (multilayer 0.0 0.0))
  (pcb_thickness)
  (max_layers 0)
  (solder_resist)
  (silkscreen)
  (min_copper_copper_clearance 0.2)
  (min_copper_board_clearance 0.3)
  (min_copper_npth_clearance 0.2)
  (min_drill_drill_clearance 0.35)
  (min_drill_board_clearance 0.5)
  (min_silkscreen_stopmask_clearance 0.127)
  (min_copper_width 0.2)
  (min_annular_ring 0.15)
  (min_npth_drill_diameter 0.25)
  (min_pth_drill_diameter 0.25)
  (min_npth_slot_width 1.0)
  (min_pth_slot_width 0.7)
  (max_tented_via_drill_diameter 0.5)
  (min_silkscreen_width 0.15)
  (min_silkscreen_text_height 0.8)
  (min_outline_tool_diameter 2.0)
  (blind_vias_allowed false)
  (buried_vias_allowed false)
  (allowed_npth_slots single_segment_straight)
  (allowed_pth_slots single_segment_straight)
  (approvals_version "0.2")
 )
 (fabrication_output_settings
  (base_path "./output/{{{{VERSION}}}}/gerber/{{{{PROJECT}}}}")
  (outlines (suffix "_OUTLINES.gbr"))
  (copper_top (suffix "_COPPER-TOP.gbr"))
  (copper_inner (suffix "_COPPER-IN{{{{CU_LAYER}}}}.gbr"))
  (copper_bot (suffix "_COPPER-BOTTOM.gbr"))
  (soldermask_top (suffix "_SOLDERMASK-TOP.gbr"))
  (soldermask_bot (suffix "_SOLDERMASK-BOTTOM.gbr"))
  (silkscreen_top (suffix "_SILKSCREEN-TOP.gbr"))
  (silkscreen_bot (suffix "_SILKSCREEN-BOTTOM.gbr"))
  (drills (merge false)
   (suffix_pth "_DRILLS-PTH.drl")
   (suffix_npth "_DRILLS-NPTH.drl")
   (suffix_merged "_DRILLS.drl")
   (suffix_buried "_DRILLS-PLATED-{{{{START_LAYER}}}}-{{{{END_LAYER}}}}.drl")
   (g85_slots false)
  )
  (solderpaste_top (create false) (suffix "_SOLDERPASTE-TOP.gbr"))
  (solderpaste_bot (create false) (suffix "_SOLDERPASTE-BOTTOM.gbr"))
 )
 (preferred_footprint_tags
  (tht_top)
  (tht_bot)
  (smt_top)
  (smt_bot)
  (common)
 )
 (polygon {outline} (layer brd_outlines)
  (width 0.0) (fill false) (grab_area false) (lock false)
  (vertex (position 0.0 0.0) (angle 0.0))
  (vertex (position {w} 0.0) (angle 0.0))
  (vertex (position {w} {h}) (angle 0.0))
  (vertex (position 0.0 {h}) (angle 0.0))
  (vertex (position 0.0 0.0) (angle 0.0))
 )
)
"""


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def board_content(name: str, width_mm: float = 100.0, height_mm: float = 80.0) -> bytes:
    from .sexpr import fmt_mm
    text = BOARD_TEMPLATE.format(uuid=g.new_uuid(), name=_esc(name), outline=g.new_uuid(),
                                 w=fmt_mm(width_mm), h=fmt_mm(height_mm))
    return parse(text).to_bytes()  # round-trip guarantees canonical layout


def dir_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_") or "board"


def find_fontobene(cli_cmd: tuple[str, ...] | None) -> Path | None:
    """Locate LibrePCB's bundled newstroke.bene (copied into new projects)."""
    cands = [Path("/usr/share/librepcb/fontobene"), Path("/usr/local/share/librepcb/fontobene"),
             Path.home() / ".local/share/flatpak/app/org.librepcb.LibrePCB/current/active/files/share/librepcb/fontobene",
             Path("/var/lib/flatpak/app/org.librepcb.LibrePCB/current/active/files/share/librepcb/fontobene")]
    if cli_cmd and cli_cmd[0].startswith("/"):
        cands.insert(0, Path(cli_cmd[0]).resolve().parent.parent / "share/librepcb/fontobene")
    return next((c for c in cands if (c / "newstroke.bene").is_file()), None)


def create_project(directory: Path, name: str, author: str, roots: tuple[Path, ...],
                   cli_cmd: tuple[str, ...] | None, with_board: bool = True,
                   board_width_mm: float = 100.0, board_height_mm: float = 80.0) -> Path:
    directory = check_inside_roots(directory, roots)
    if directory.exists() and any(directory.iterdir()):
        raise LibrePCBError(f"{directory} exists and is not empty")
    if not name.strip():
        raise LibrePCBError("Project name must not be empty")
    filename = re.sub(r"[^\w\- ]+", "_", name).strip() or "project"
    project_uuid = g.new_uuid()
    created = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    files: dict[str, str] = {
        ".librepcb-project": "2\n",
        f"{filename}.lpp": "LIBREPCB-PROJECT",
        ".gitignore": GITIGNORE,
        "README.md": f"# {name}\n\nLibrePCB project.\n",
        "project/metadata.lp": (
            f"(librepcb_project_metadata {project_uuid}\n (name \"{_esc(name)}\")\n"
            f" (author \"{_esc(author)}\")\n (version \"v1\")\n (created {created})\n)\n"),
        "project/settings.lp": (
            "(librepcb_project_settings\n (library_locale_order\n )\n (library_norm_order\n )\n"
            " (custom_bom_attributes\n )\n (default_lock_component_assembly false)\n)\n"),
        "project/jobs.lp": "(librepcb_jobs\n)\n",
        "circuit/circuit.lp": (
            f"(librepcb_circuit\n (variant {g.new_uuid()} (name \"Std\")\n"
            "  (description \"Standard assembly\")\n )\n"
            f" (netclass {g.new_uuid()} (name \"default\")\n  (default_trace_width inherit)\n"
            "  (default_via_drill_diameter inherit)\n  (min_copper_copper_clearance 0.0)\n"
            "  (min_copper_width 0.0)\n  (min_via_drill_diameter 0.0)\n )\n)\n"),
        "circuit/erc.lp": "(librepcb_erc\n)\n",
        "schematics/schematics.lp": "(librepcb_schematics\n (schematic \"schematics/main/schematic.lp\")\n)\n",
        "schematics/main/schematic.lp": (
            f"(librepcb_schematic {g.new_uuid()}\n (name \"Main\")\n"
            " (grid (interval 2.54) (unit millimeters))\n)\n"),
        "boards/boards.lp": ("(librepcb_boards\n (board \"boards/default/board.lp\")\n)\n"
                             if with_board else "(librepcb_boards\n)\n"),
    }
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for rel, text in files.items():
            atomic_write(directory / rel, text.encode())
        if with_board:
            atomic_write(directory / "boards/default/board.lp",
                         board_content("default", board_width_mm, board_height_mm))
        font = find_fontobene(cli_cmd)
        if font:
            dst = directory / "resources/fontobene"
            dst.mkdir(parents=True, exist_ok=True)
            for f in font.glob("*.bene"):
                shutil.copy2(f, dst / f.name)
        (directory / "library").mkdir(exist_ok=True)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    Project(directory, roots)  # sanity: loads
    return directory / f"{filename}.lpp"
