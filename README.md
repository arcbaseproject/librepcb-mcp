# librepcb-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets AI agents such
as Claude Code inspect, edit, check and export **real LibrePCB projects** on Linux. It runs
natively (no Wine, Docker, browser or HTTP service) over STDIO.

* Reads LibrePCB's native project files with a parser that reproduces LibrePCB's canonical
  S-expression output byte for byte.
* Edits mirror what the LibrePCB editor writes. Every edit is backed up, written atomically,
  then re-opened with `librepcb-cli open-project --strict`. If LibrePCB rejects it, the
  edit is rolled back and the CLI output comes back as the error.
* ERC, DRC and all exports are run by `librepcb-cli`, never emulated.

Design notes and research results: [docs/architecture.md](docs/architecture.md).

## Requirements

| | |
|---|---|
| LibrePCB | **2.x** (project file format `2`). Tested with **2.1.1** (Flatpak). Older projects must be upgraded first: `librepcb-cli open-project --save project.lpp` |
| Python | 3.12+ (tested 3.14) |
| Linux | any distribution with the above; tested on Void Linux (x86_64). Uses `librepcb-cli` from `PATH` (distro package, AppImage, official tarball) or the Flatpak `org.librepcb.LibrePCB`. |
| Python packages | `mcp>=2.3,<3` (official SDK, `MCPServer` API) |

## Feature status

| Group | Tools | Status |
|-------|-------|--------|
| Project | `create_project`, `list_projects`, `get_project_info`, `validate_project`, `backup_project`, `server_status` | working |
| Schematic inspection | `list_schematics`, `list_components`, `get_component`, `list_nets`, `get_net_connections`, `inspect_schematic` | working |
| Schematic editing | `create_schematic`, `add_component`, `remove_component`, `move_component`, `set_component_value`, `connect_pins`, `disconnect_pins`, `create_net`, `add_net_label` | working |
| Board | `list_boards`, `get_board_info`, `create_board`, `add_device_to_board`, `place_component`, `rotate_component`, `set_board_outline`, `create_trace`, `create_via` | working |
| Routing | `route_net` | **straight-line only**: shortest spanning tree, no obstacle avoidance. Not an autorouter. |
| Validation | `run_erc`, `run_drc` | working (librepcb-cli) |
| Export | `export_schematic_pdf`, `export_board_pdf`, `export_gerbers` (Gerber **and** Excellon drills), `export_bom`, `export_project` (`.lppz`), `run_output_jobs` | working (librepcb-cli) |
| Library | `list_libraries`, `search_components`, `get_component_details`, `get_device_details`, `list_footprints` | working |

Intentionally not provided: `open_project` / `save_project` (the server is stateless; each
edit is saved and verified immediately), a separate `export_drill_files` (drills come with
`export_gerbers`), and `get_validation_results` (call `run_erc` / `run_drc` /
`validate_project`, which return the results).

Not implemented: copper planes/zones, net renaming/merging, buses, schematic polygons/texts,
mirrored schematic symbols, design-rule editing, creating library elements, autorouting.
See [docs/architecture.md](docs/architecture.md#supported-vs-unsupported).

## Installation

### Quick install (detects your coding agents)

```bash
curl -fsSL https://raw.githubusercontent.com/arcbaseproject/librepcb-mcp/main/install.sh | bash
```

The interactive installer checks Python and LibrePCB (offers the Flatpak if LibrePCB is missing), installs
the server into `~/.local/share/librepcb-mcp`, asks which project folders it may edit, optionally downloads
official LibrePCB libraries, and registers the server with the agents it finds: Claude Code, Codex CLI,
Gemini CLI, Cursor, Windsurf, VS Code / VSCodium. Config files are backed up first; files it cannot parse
(e.g. JSON with comments) are left alone and you get the snippet to paste. Re-run to update.
`bash -s -- --yes` accepts defaults, `bash -s -- --uninstall` removes it.

### Manual install

```bash
git clone https://github.com/arcbaseproject/librepcb-mcp.git
cd librepcb-mcp
python3 -m venv .venv
.venv/bin/pip install -e .          # or: .venv/bin/pip install -e '.[dev]' for tests
```

### LibrePCB CLI

Install LibrePCB 2.x by any official route, e.g.

```bash
flatpak install --user flathub org.librepcb.LibrePCB      # Flatpak
# or a distro package / the official tarball/AppImage providing `librepcb-cli`
```

Detection order: `$LIBREPCB_CLI` → `librepcb-cli` on `PATH` → the Flatpak. Check with the
`server_status` tool or:

```bash
LIBREPCB_MCP_ROOTS=$HOME .venv/bin/python -c "from librepcb_mcp.config import Config; print(Config.from_env().cli)"
```

**Flatpak note:** the Flatpak sandbox only sees your home directory, so keep projects (and
export directories) under `$HOME`.

### Libraries

`search_components` / `add_component` need installed LibrePCB libraries. Either install them
in LibrePCB (Library Manager; the workspace is auto-detected from LibrePCB's config or
`~/LibrePCB-Workspace`), or point the server at `*.lplib` directories:

```bash
git clone --depth 1 https://github.com/LibrePCB-Libraries/LibrePCB_Base.lplib ~/librepcb-libs/LibrePCB_Base.lplib
export LIBREPCB_MCP_LIBRARIES=~/librepcb-libs
```

### Configuration (environment variables)

| Variable | Meaning |
|----------|---------|
| `LIBREPCB_MCP_ROOTS` | **Required.** `:`-separated directories the server may read and modify. Everything else is refused (symlinks are resolved first). |
| `LIBREPCB_MCP_LIBRARIES` | `:`-separated `*.lplib` dirs or dirs containing them. Default: LibrePCB workspace libraries. |
| `LIBREPCB_CLI` | CLI command, e.g. `/usr/bin/librepcb-cli` or `flatpak run --command=librepcb-cli org.librepcb.LibrePCB`. |
| `LIBREPCB_MCP_BACKUP_DIR` | Backup location. Default `$XDG_STATE_HOME/librepcb-mcp/backups` (last 20 per project). |
| `LIBREPCB_MCP_CLI_TIMEOUT` | Seconds per CLI call, default 300. |
| `LIBREPCB_MCP_LOG` | Log level for stderr, default `WARNING`. |

## Claude Code integration

Verified with Claude Code 2.1.330 (`claude mcp list` reports `✔ Connected`, and a headless
`claude -p` session called `get_project_info` on the example project):

```bash
claude mcp add librepcb --transport stdio \
  -e LIBREPCB_MCP_ROOTS=$HOME/pcb-projects \
  -e LIBREPCB_MCP_LIBRARIES=$HOME/librepcb-libs \
  -- /absolute/path/to/librepcb-mcp/.venv/bin/python -m librepcb_mcp
```

Put the server name **before** `-e` (`-e` accepts several values and would otherwise swallow
the name). Add `-s user` to make it available in every directory. Check with
`claude mcp get librepcb`.

Other MCP clients: run `/absolute/path/.venv/bin/python -m librepcb_mcp` (or the installed
`librepcb-mcp` script) as a STDIO server with the environment variables above.

## Conventions

* Units: **millimeters** and **degrees** (counter-clockwise). LibrePCB's y axis points up.
  Schematic grid is 2.54 mm.
* Components: designator (`R1`) or UUID. Pins: `DESIGNATOR:PIN`, PIN = symbol pin name or
  signal name (`R1:1`, `LED1:A`, `U1:VCC`). Board pads: `DESIGNATOR:PAD`; vias: `via:<uuid>`; trace corners: `junction:<uuid>`.
* Schematics/boards: name, UUID or index; default is the first.
* Edit results include `success`, the changed files, the backup path and what verified the
  result. Errors come back as MCP tool errors with LibrePCB's own message where relevant.
* Close the project in the LibrePCB GUI before editing: the server refuses to write while
  LibrePCB's `.lock` file exists.

## Tool reference

| Tool | Purpose |
|------|---------|
| `server_status` | Roots, CLI command/version, libraries, backup dir |
| `create_project` | New project with schematic `Main` and optional board (outline size in mm) |
| `list_projects` | Find `*.lpp` below the roots |
| `get_project_info` | Metadata, schematics, boards, counts, lock state |
| `validate_project` | Strict open + ERC + DRC |
| `backup_project` | Manual backup |
| `list_schematics` / `inspect_schematic` | Pages; symbols and every wire/label per net segment |
| `list_components` / `get_component` | Components; details with pin and pad positions and nets |
| `list_nets` / `get_net_connections` | Nets with connected signals, labels, traces, vias |
| `create_schematic` | Add a page |
| `add_component` | Add a library device's component; places all gates |
| `remove_component` | Delete with attached wires/traces; drop unused nets |
| `move_component` | Move/rotate a schematic symbol |
| `set_component_value` | Value text and attribute values (e.g. `RESISTANCE`) |
| `connect_pins` | Wire two pins on one page into one net (named or auto) |
| `disconnect_pins` | Remove wires from pins, detach from net and traces |
| `create_net` | Named net |
| `add_net_label` | Stub wire + net label: connect by net name (GND, VCC), across pages |
| `list_boards` / `get_board_info` | Boards; outline, devices, unplaced parts, per-net unrouted count |
| `create_board` | Additional board |
| `add_device_to_board` | Place a component's footprint (optionally bottom side) |
| `place_component` / `rotate_component` | Move, rotate, flip devices |
| `set_board_outline` | Polygon or rectangle outline |
| `create_trace` | Trace between pads/vias/junctions of one net, optional corner points (returned as `junction:<uuid>` for T-joins) |
| `create_via` | Through via on a net |
| `route_net` | Straight traces joining all unconnected pads of a net |
| `run_erc` / `run_drc` | LibrePCB's checks; non-approved messages with severity |
| `export_schematic_pdf`, `export_board_pdf`, `export_gerbers`, `export_bom`, `export_project` | Exports into `<project>/output/mcp` (or `output_dir`) |
| `run_output_jobs` | Run the project's own output jobs |
| `list_libraries`, `search_components`, `get_component_details`, `get_device_details`, `list_footprints` | Library browsing |

## Example

`examples/led-circuit/` is a 5 V LED circuit (test pads for +5V/GND, 330 Ω 0603 resistor, red
3 mm THT LED) on a 30 × 20 mm board. It was generated entirely through this server's MCP
tools, opens in LibrePCB 2.1.1 in strict mode, and passes ERC and DRC with zero messages.
Prompts that work with Claude Code:

* "Show me the components and nets of examples/led-circuit."
* "Change R1 to 470 Ω, run ERC and DRC, then export Gerbers."
* "Create a new project in ~/pcb-projects/blinky with a resistor and a green LED in series
  between VCC and GND, place them on a 25×15 mm board and route it."

A typical tool sequence: `search_components` → `create_project` → `add_component` →
`connect_pins` / `add_net_label` → `run_erc` → `add_device_to_board` → `get_board_info` →
`create_trace` / `route_net` → `run_drc` → `export_gerbers`.

Passing ERC and DRC does **not** mean a design is electrically correct, safe, or ready to
manufacture. Review designs in LibrePCB before ordering boards.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No project directories configured` | Set `LIBREPCB_MCP_ROOTS` |
| `outside the configured project roots` | Add the directory to `LIBREPCB_MCP_ROOTS` |
| `librepcb-cli not found` | Install LibrePCB 2.x or set `LIBREPCB_CLI`; edits are refused without it |
| CLI cannot open files (Flatpak) | Keep projects and outputs under `$HOME` |
| `Project is open in LibrePCB` | Close it in LibrePCB, or delete a stale `.lock` after a crash |
| `Unsupported project file format` | `librepcb-cli open-project --save project.lpp` (makes a format-2 copy in place; back up first) |
| `No libraries found` | Install libraries in LibrePCB or set `LIBREPCB_MCP_LIBRARIES` |
| `LibrePCB rejected the modified project` | Nothing was changed; the message contains LibrePCB's reason. Please report it as a bug with that output. |
| Nothing in Claude Code | `claude mcp get librepcb`; run the command manually; set `LIBREPCB_MCP_LOG=DEBUG` (logs go to stderr) |

## Limitations

* `route_net` draws straight lines; DRC violations are expected on dense boards.
* No planes/zones, no net renaming or merging, no buses, no schematic graphics.
* Schematic symbols are placed unmirrored; boards: top or bottom side.
* `add_component` uses the component's first symbol variant and, on boards, the first
  footprint unless one is named.
* Components are added with an assembly option for the chosen device but no MPN/part.
* `.lppz` archives are not edited.
* Read results are computed from the files; DRC/ERC results come from LibrePCB only.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # unit, integration, MCP protocol tests
.venv/bin/pip install ruff && .venv/bin/ruff check src tests
```

Tests that need `librepcb-cli` or `LibrePCB_Base.lplib` are skipped with a reason when they
are missing (`LIBREPCB_CLI`, `LIBREPCB_MCP_TEST_LIBRARY` select them). Work directories are
created under `~/.cache/librepcb-mcp-tests` so the Flatpak CLI can read them.

Last run (2026-11-20, LibrePCB 2.1.1 Flatpak, Python 3.14, mcp 2.3.0): **49 passed, 0 skipped**.

## License

GPL-3.0-or-later (see `LICENSE`). The example project embeds elements from
`LibrePCB_Base.lplib` (CC0-1.0) and LibrePCB's `newstroke.bene` font.
