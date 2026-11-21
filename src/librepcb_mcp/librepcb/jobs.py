"""Output jobs (LibrePCB 2.x ``jobs.lp`` format) run via ``--jobs FILE --run-jobs``.

Job UUIDs are derived from (kind, board) so re-running an export replaces the
previous files of that export only: librepcb-cli deletes files it generated
earlier for the same job UUID and never touches unknown files.
"""

from __future__ import annotations

import uuid as _uuid

_NS = _uuid.UUID("0b8f3f5e-6d0c-4a59-9f37-2a1c3e0d7c11")

SCHEMATIC_LAYERS = """\
   (layer schematic_bus_labels (color "#ff008eff"))
   (layer schematic_buses (color "#ff008eff"))
   (layer schematic_comments (color "#ff000080"))
   (layer schematic_documentation (color "#ff808080"))
   (layer schematic_frames (color "#ff000000"))
   (layer schematic_grab_areas (color "#ffffffe1"))
   (layer schematic_guide (color "#ff808000"))
   (layer schematic_image_borders (color "#ff808080"))
   (layer schematic_names (color "#ff202020"))
   (layer schematic_net_labels (color "#ff008000"))
   (layer schematic_outlines (color "#ff800000"))
   (layer schematic_pin_lines (color "#ff800000"))
   (layer schematic_pin_names (color "#ff404040"))
   (layer schematic_pin_numbers (color "#ff404040"))
   (layer schematic_values (color "#ff505050"))
   (layer schematic_wires (color "#ff008000"))
"""


def _board_layers(side: str) -> str:
    rows = [("board_comments", "#d9724a00"), ("board_documentation", "#ca707070"),
            (f"board_documentation_{side}", "#ca707070"), ("board_frames", "#ca707070"),
            (f"board_grab_areas_{side}", "#897f7f7f"), ("board_guide", "#fe404000"),
            ("board_holes", "#e37f7f7f"), (f"board_legend_{side}", "#dc7f7f7f"),
            ("board_measures", "#fe404000"), (f"board_names_{side}", "#ca767f6c"),
            ("board_outlines", "#e37f7f7f"), ("board_pads", "#ca365a0b"),
            ("board_plated_cutouts", "#e3006e7f"), (f"board_values_{side}", "#ca6c797f")]
    return "".join(f'   (layer {n} (color "{c}"))\n' for n, c in sorted(rows))


def job_uuid(kind: str, board: str | None) -> str:
    # deterministic, but stamped as version 4: LibrePCB rejects other versions
    raw = _uuid.uuid5(_NS, f"{kind}:{board or ''}").bytes
    return str(_uuid.UUID(bytes=raw, version=4))


def _graphics_content(kind: str, title: str, layers: str, board: str, mirror: bool) -> str:
    return (f'  (content (type {kind}) (title "{title}")\n'
            f"   (paper auto) (orientation auto) (rotate false) (mirror {'true' if mirror else 'false'}) (scale auto)\n"
            "   (margins (left 10.0) (top 10.0) (right 10.0) (bottom 10.0))\n"
            '   (dpi 600) (min_line_width 0.1) (monochrome false) (background "#00000000")\n'
            f"{layers}   (board {board})\n   (variant none)\n  )\n")


def schematic_pdf() -> str:
    return (f'(job {job_uuid("schematic_pdf", None)} (name "Schematic PDF")\n'
            '  (type graphics) (title "{{PROJECT}} - {{VERSION}}")\n'
            + _graphics_content("schematic", "Schematic", SCHEMATIC_LAYERS, "none", False)
            + '  (output "{{PROJECT}}_{{VERSION}}_Schematic.pdf")\n )')


def board_pdf(board: str) -> str:
    return (f'(job {job_uuid("board_pdf", board)} (name "Board Assembly PDF")\n'
            '  (type graphics) (title "{{PROJECT}} - {{VERSION}}")\n'
            + _graphics_content("board", "Assembly Top", _board_layers("top"), board, False)
            + _graphics_content("board", "Assembly Bottom", _board_layers("bottom"), board, True)
            + '  (output "{{PROJECT}}_{{VERSION}}_{{BOARD}}_Assembly.pdf")\n )')


def gerber_excellon(board: str, solderpaste: bool = True) -> str:
    sp = "true" if solderpaste else "false"
    return (f'(job {job_uuid("gerber", board)} (name "Gerber/Excellon")\n'
            "  (type gerber_excellon)\n"
            '  (outlines (suffix "_OUTLINES.gbr"))\n'
            '  (copper_top (suffix "_COPPER-TOP.gbr"))\n'
            '  (copper_inner (suffix "_COPPER-IN{{CU_LAYER}}.gbr"))\n'
            '  (copper_bot (suffix "_COPPER-BOTTOM.gbr"))\n'
            '  (soldermask_top (suffix "_SOLDERMASK-TOP.gbr"))\n'
            '  (soldermask_bot (suffix "_SOLDERMASK-BOTTOM.gbr"))\n'
            '  (silkscreen_top (suffix "_SILKSCREEN-TOP.gbr"))\n'
            '  (silkscreen_bot (suffix "_SILKSCREEN-BOTTOM.gbr"))\n'
            f'  (solderpaste_top (create {sp}) (suffix "_SOLDERPASTE-TOP.gbr"))\n'
            f'  (solderpaste_bot (create {sp}) (suffix "_SOLDERPASTE-BOTTOM.gbr"))\n'
            "  (drills (merge false)\n"
            '   (suffix_pth "_DRILLS-PTH.drl")\n'
            '   (suffix_npth "_DRILLS-NPTH.drl")\n'
            '   (suffix_merged "_DRILLS.drl")\n'
            '   (suffix_buried "_DRILLS-PLATED-{{START_LAYER}}-{{END_LAYER}}.drl")\n'
            "   (g85_slots false)\n  )\n"
            f"  (board {board})\n"
            '  (output "gerber/{{BOARD}}/{{PROJECT}}_{{VERSION}}")\n )')


def bom(board: str | None) -> str:
    return (f'(job {job_uuid("bom", board)} (name "BOM")\n  (type bom)\n'
            f"  (board {board or 'none'})\n  (variant all)\n"
            f'  (output "{{{{PROJECT}}}}_{{{{VERSION}}}}_{"{{BOARD}}_" if board else ""}BOM_{{{{VARIANT}}}}.csv")\n )')


def lppz() -> str:
    return (f'(job {job_uuid("lppz", None)} (name "Project Archive")\n  (type lppz)\n'
            '  (output "{{PROJECT}}_{{VERSION}}.lppz")\n )')


def jobs_file(*jobs: str) -> bytes:
    from .sexpr import parse
    text = "(librepcb_jobs\n " + "\n ".join(jobs) + "\n)\n"
    return parse(text).to_bytes()
