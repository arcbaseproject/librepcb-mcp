# Architecture and LibrePCB research notes

Findings below were verified against LibrePCB **2.1.1** (Flatpak, file format `2`), the
LibrePCB source tree (`master`, Nov 2026), the `librepcb-test-data` repository and the
`LibrePCB_Base.lplib` library. Source references are paths in the LibrePCB repository.

## 1. Project structure

A project is a directory marked by `.librepcb-project` (content: file format major
version, `2`) plus a `*.lpp` file whose only content is `LIBREPCB-PROJECT`.

```
MyProject.lpp                   marker file
.librepcb-project               "2"
project/metadata.lp             uuid, name, author, version, created
project/settings.lp             locale/norm order, custom BOM attributes
project/jobs.lp                 output jobs (PDF, Gerber, BOM, ...)
circuit/circuit.lp              assembly variants, net classes, nets, component instances
circuit/erc.lp                  approved ERC messages
schematics/schematics.lp        ordered list of schematic files
schematics/<dir>/schematic.lp   symbols, net segments, polygons, texts
boards/boards.lp                ordered list of board files
boards/<dir>/board.lp           settings, design rules, devices, net segments, planes, polygons
library/{cmp,dev,pkg,sym}/<uuid>/   copies of all used library elements
resources/fontobene/*.bene      stroke font (copied by the new-project wizard)
*.user.lp, .lock                per-user state / lock file while open in the GUI
```

`*.lppz` is a zip of the same tree (read-only for this server).

## 2. S-expression format

`libs/librepcb/core/serialization/sexpression.cpp`. Lists, tokens (`[A-Za-z0-9\\._:-]`),
quoted strings (escapes `\" \\ \b \f \n \r \t \v`), `;` line comments. **Line breaks are
explicit child nodes** chosen by each `serialize()` (`ensureLineBreak()`), and indentation
is derived from them. Numbers are fixed-point: lengths in nm, angles in µdeg, printed with
up to 6 decimals and at least one (`decimalFixedPointToString`). Angles are reduced with C
`%` (sign kept), e.g. 270°+180° → 90°.

LibrePCB defines a canonical form: saving a file must reproduce it byte for byte.
`librepcb-cli open-project --strict` fails on any non-canonical file. This server's parser
keeps line breaks as nodes, so canonical files round-trip byte-identically (checked on 1350
real `.lp` files), and new nodes are built with the same break placement as the C++
`serialize()` functions.

Canonical ordering rules found in the source and enforced here:

* UUID-keyed collections are sorted by UUID (nets, components, symbols, devices, net
  segments, junctions, lines, traces, vias, texts ...), grouped by type in the order the
  parent's `serialize()` writes them.
* Wire/trace endpoints are normalized: `NetLine::normalizeAnchors` / `Trace::normalizeAnchors`
  store the smaller anchor as `from` (pins/pads < vias < junctions, then UUID).
* UUIDs must be RFC 4122 version 4 (other versions are rejected on load).

## 3. Components, symbols, devices, packages

* **Symbol** (`sym`): pins with positions, graphics, texts (`{{NAME}}`, `{{VALUE}}`).
* **Component** (`cmp`): signals, attributes, prefix, default value, one or more
  *symbol variants*, each with *gates* (symbol + position offset + pin→signal map).
* **Package** (`pkg`): package pads, 3D models, one or more footprints (footprint pads
  reference package pads; `(side)`, optional `(hole)` for THT).
* **Device** (`dev`): binds one component to one package: package pad → component signal.

## 4. Library references

Project files only reference elements by UUID. Every element used by the project is copied
into `library/<kind>/<uuid>/` (whole directory, including STEP files). The editor copies the
component and symbols when a component is added (`CmdAddComponentToCircuit`,
`CmdAddSymbolToSchematic`) and the device and package when it is placed on a board
(`CmdAddDeviceToBoard`). Workspace libraries live in `<workspace>/data/libraries/{local,remote}/*.lplib`.

## 5. UUIDs and nets

`circuit.lp` holds nets (`(net uuid (auto bool) (name ...) (netclass uuid))`) and
component instances with `(signal <signal-uuid> (net <net-uuid>|none))`. Net membership is
defined there; schematic net segments and board net segments must agree with it (a wire on
a pin whose signal has another net fails to load). Auto nets are named `N<i>`.
Component instances also hold attributes and *assembly options* (`(device uuid ...
(variant ...))`) used for BOM/assembly.

## 6. Schematics

`schematic.lp`: `symbol` (component, gate, position, rotation, mirror, texts in absolute
coordinates) and `netsegment` (net, junctions, lines between pins/junctions, labels).
Pins carry no coordinates in the file: lines reference `(symbol)(pin)`. Net labels connect
segments of the same net by name, also across pages.

## 7. Boards

`board.lp`: settings, design rules, DRC settings, legacy fabrication settings, `device`
(component, device, footprint, 3D model, position/rotation/flip/lock/glue, texts),
`netsegment` (net, vias, junctions, traces between pads/vias/junctions), `plane`, `zone`,
`polygon` (`brd_outlines` = board outline), `stroke_text`, `hole`.

## 8. librepcb-cli (2.1.1)

* `open-project [--strict] [--erc] [--drc] [--board NAME] [--save] PROJECT`
* `--run-jobs` / `--run-job NAME` with `--jobs FILE` (custom `jobs.lp`) and `--outdir DIR`:
  graphics (PDF/SVG/PNG), Gerber/Excellon, Gerber X3, pick&place, IPC-D-356 netlist, BOM,
  interactive BOM, STEP, project JSON, `.lppz`, copy, zip.
* `open-library [--all] [--check] [--save]`, `open-package`, `open-symbol`, `open-step`.
* Rule-check messages go to **stderr**, headers to **stdout**; each line is flushed, so the
  server merges both into one pipe to attribute messages to ERC/DRC and boards.
* Output jobs only delete files they generated earlier for the same job UUID
  (`OutputDirectoryWriter::removeObsoleteFiles`); unknown files are left alone.
* Deprecated `--export-*` options still exist but exit non-zero with a warning.

## 9. What needs more than the CLI

The CLI has no editing commands (only `--save` for upgrades and board removal). All
creation/editing in this server is a native reader/writer that mirrors the editor commands
listed above, followed by verification with the CLI.

## 10. Official programming interfaces

No documented, stable programming API was found: the C++ core (`libs/librepcb/core`) is
built as an internal static library of the application, not published as an SDK, and has
no Python bindings. The CLI plus the documented file format are the usable interfaces. A
C++ integration layer was not needed.

## Server design

```
MCP client (Claude Code) ── stdio ── server.py (MCPServer, mcp SDK 2.x)
                                        tools/*.py   typed tool definitions, error mapping
                                        service.py   check/export orchestration
                                        librepcb/
                                          sexpr.py        canonical parser/serializer
                                          project.py      project model, path policy
                                          inspect.py      read-only views
                                          circuit_ops.py  circuit + schematic edits
                                          board_ops.py    board edits
                                          create.py       new project/board
                                          library.py      *.lplib index and search
                                          jobs.py         output job files
                                          cli.py          librepcb-cli wrapper
                                          transaction.py  backup/atomic write/verify/rollback
```

Edit transaction (`transaction.edit`):

1. Require librepcb-cli (never write what cannot be verified).
2. Serialize the server's own edits per project (`flock`).
3. Refuse if `.lock` exists (project open in LibrePCB).
4. Apply the change in memory; before writing, compare each file's SHA-256 with the value
   read earlier (concurrent external edit → abort, nothing written).
5. Back up the project (minus `output/`, `.git`) to `$XDG_STATE_HOME/librepcb-mcp/backups`
   (last 20 kept).
6. Copy new library element directories; write files via temp file + `fsync` + `rename`.
7. `librepcb-cli open-project --strict`. On failure restore every original byte, delete new
   files/dirs, return the CLI output.

Every path is resolved (symlinks included) and must lie under `LIBREPCB_MCP_ROOTS`.
Subprocesses get argument lists, never a shell. Logs go to stderr only.

## Supported vs. unsupported

| Area | Status |
|------|--------|
| Read project, schematics, components, nets, boards | supported |
| ERC, DRC, strict validation | supported (librepcb-cli) |
| PDF, Gerber/Excellon, BOM, `.lppz`, project output jobs | supported (librepcb-cli) |
| New project / schematic page / board | supported |
| Add/remove/move components, set value/attributes | supported (single-gate and multi-gate) |
| Wires between pins, net labels, named nets | supported |
| Place/rotate/flip devices, outline, traces with corners, vias | supported |
| `route_net` | straight-line MST connections only, no obstacle avoidance |
| Mirroring schematic symbols, buses, polygons/texts in schematics | not implemented |
| Copper planes/zones, inner-layer setup, design-rule editing | not implemented |
| Merging two different nets, renaming nets | not implemented |
| Real autorouting | not implemented (would need an external router; LibrePCB 2.1.1's CLI offers no routing) |
| Editing `.lppz` | not supported (extract first) |
| Projects with file format < 2 | read refused; upgrade with `librepcb-cli open-project --save` |
| Creating library elements | not implemented |
