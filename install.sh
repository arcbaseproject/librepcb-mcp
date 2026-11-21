#!/usr/bin/env bash
# librepcb-mcp installer: installs the server and registers it with your coding agents.
#
#   curl -fsSL https://raw.githubusercontent.com/arcbaseproject/librepcb-mcp/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping):
#   --yes             accept all defaults, no questions
#   --dir DIR         install directory (default ~/.local/share/librepcb-mcp)
#   --roots PATHS     ':'-separated project directories the server may modify
#   --no-libs         do not download LibrePCB libraries
#   --uninstall       remove the server and its agent registrations
# Environment:
#   LIBREPCB_MCP_SOURCE  pip source (default: GitHub main branch tarball)
set -euo pipefail
export GIT_TERMINAL_PROMPT=0  # unknown repos must fail, not ask for a password

REPO="arcbaseproject/librepcb-mcp"
SOURCE="${LIBREPCB_MCP_SOURCE:-https://github.com/$REPO/archive/refs/heads/main.tar.gz}"
NAME="librepcb"
INSTALL_DIR="$HOME/.local/share/librepcb-mcp"
LIB_DIR="$HOME/librepcb-libs"
ROOTS=""
YES=0
NO_LIBS=0
UNINSTALL=0
SERVER_ENV_JSON="{}"

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --dir) INSTALL_DIR="$2"; shift ;;
    --roots) ROOTS="$2"; shift ;;
    --no-libs) NO_LIBS=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ---- terminal ui ---------------------------------------------------------------
if [ -t 1 ]; then
  B=$'\e[1m'; D=$'\e[2m'; R=$'\e[0m'; G=$'\e[32m'; Y=$'\e[33m'; E=$'\e[31m'; C=$'\e[36m'
else
  B=""; D=""; R=""; G=""; Y=""; E=""; C=""
fi
ok()   { printf '  %s✔%s %s\n' "$G" "$R" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$R" "$*"; }
err()  { printf '  %s✖%s %s\n' "$E" "$R" "$*" >&2; }
info() { printf '  %s•%s %s\n' "$C" "$R" "$*"; }
step() { printf '\n%s%s%s\n' "$B" "$*" "$R"; }
die()  { err "$*"; exit 1; }

# Questions read from the terminal, so they work under `curl ... | bash`.
TTY=""
if [ "$YES" -eq 0 ]; then
  if { exec 3</dev/tty; } 2>/dev/null; then TTY=1; else
    warn "No terminal available: using defaults (same as --yes)."; YES=1
  fi
fi
ask() {  # ask "question" default -> REPLY
  local q="$1" def="${2-}"
  if [ "$YES" -eq 1 ]; then REPLY="$def"; return; fi
  printf '  %s?%s %s %s[%s]%s ' "$C" "$R" "$q" "$D" "$def" "$R"
  IFS= read -r REPLY <&3 || REPLY=""
  REPLY="${REPLY:-$def}"
}
confirm() {  # confirm "question" Y|N
  local def="${2:-Y}" hint="Y/n"
  [ "$def" = "N" ] && hint="y/N"
  if [ "$YES" -eq 1 ]; then [ "$def" = "Y" ]; return; fi
  printf '  %s?%s %s %s[%s]%s ' "$C" "$R" "$1" "$D" "$hint" "$R"
  local a; IFS= read -r a <&3 || a=""
  a="${a:-$def}"
  case "$a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
spin() {  # spin "label" cmd... (output goes to the log)
  local label="$1"; shift
  if [ -t 1 ]; then
    "$@" >>"$LOG" 2>&1 & local pid=$! i=0 frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s%s%s %s' "$C" "${frames:i++%10:1}" "$R" "$label"; sleep 0.1
    done
    printf '\r\e[K'
    wait "$pid"
  else
    "$@" >>"$LOG" 2>&1
  fi
}

LOG="$(mktemp -t librepcb-mcp-install.XXXXXX.log)"
PY="$INSTALL_DIR/venv/bin/python"

printf '\n%s  LibrePCB MCP installer%s  %shttps://github.com/%s%s\n' "$B" "$R" "$D" "$REPO" "$R"

[ "$(uname -s)" = "Linux" ] || die "This installer supports Linux only."

# ---- agent config helpers (python does the JSON/TOML edits) ----------------------
# edit_config KIND FILE ACTION: KIND json-mcpServers|json-vscode|toml-codex, ACTION add|remove.
# Existing files are backed up; files that cannot be parsed are left untouched.
edit_config() {
  local kind="$1" file="$2" action="$3" pybin
  pybin="$PY"; [ -x "$pybin" ] || pybin="$(command -v python3)"
  "$pybin" - "$kind" "$file" "$action" "$NAME" "$PY" "$SERVER_ENV_JSON" <<'PYEOF'
import json, os, re, shutil, sys, time
kind, path, action, name, py, env_json = sys.argv[1:7]
env = json.loads(env_json)
backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
exists = os.path.exists(path)
text = open(path, encoding="utf-8").read() if exists else ""

def write(new):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if exists:
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    print("backup " + backup if exists else "created")

if kind == "toml-codex":
    # drop [mcp_servers.<name>] and its sub-tables, then append ours
    out, skip = [], False
    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            skip = m.group(1).strip().strip('"') in (f"mcp_servers.{name}",) or \
                m.group(1).strip().startswith(f"mcp_servers.{name}.")
        if not skip:
            out.append(line)
    new = "".join(out).rstrip("\n")
    if action == "add":
        q = lambda s: json.dumps(s)
        block = (f"\n\n[mcp_servers.{name}]\ncommand = {q(py)}\nargs = [\"-m\", \"librepcb_mcp\"]\n"
                 f"env = {{ {', '.join(f'{k} = {q(v)}' for k, v in env.items())} }}\n")
        new = (new + block) if new else block.lstrip("\n")
    else:
        new += "\n" if new else ""
    if new != text:
        write(new)
    else:
        print("unchanged")
    sys.exit(0)

try:
    data = json.loads(text) if text.strip() else {}
except json.JSONDecodeError as e:
    print(f"unparseable: {e}")  # JSONC with comments etc.: do not touch
    sys.exit(3)
key = "servers" if kind == "json-vscode" else "mcpServers"
servers = data.setdefault(key, {})
if action == "add":
    entry = {"command": py, "args": ["-m", "librepcb_mcp"], "env": env}
    if kind == "json-vscode":
        entry = {"type": "stdio", **entry}
    servers[name] = entry
else:
    if name not in servers:
        print("unchanged"); sys.exit(0)
    del servers[name]
write(json.dumps(data, indent=2) + "\n")
PYEOF
}

manual_snippet() {
  local kind="$1" file="$2"
  warn "Could not edit $file automatically (comments or invalid JSON). Add this yourself:"
  local key="mcpServers" extra=""
  [ "$kind" = "json-vscode" ] && { key="servers"; extra='"type": "stdio", '; }
  printf '%s    "%s": { "%s": { %s"command": "%s", "args": ["-m", "librepcb_mcp"], "env": %s } }%s\n' \
    "$D" "$key" "$NAME" "$extra" "$PY" "$SERVER_ENV_JSON" "$R"
}

# ---- agent detection ---------------------------------------------------------------
AGENT_IDS=(); AGENT_LABELS=(); AGENT_KINDS=(); AGENT_FILES=()
add_agent() { AGENT_IDS+=("$1"); AGENT_LABELS+=("$2"); AGENT_KINDS+=("$3"); AGENT_FILES+=("$4"); }
XDG="${XDG_CONFIG_HOME:-$HOME/.config}"
detect_agents() {
  if command -v claude >/dev/null 2>&1; then add_agent claude "Claude Code" cli-claude ""; fi
  if command -v codex >/dev/null 2>&1 || [ -d "$HOME/.codex" ]; then
    add_agent codex "Codex CLI" toml-codex "${CODEX_HOME:-$HOME/.codex}/config.toml"; fi
  if command -v gemini >/dev/null 2>&1 || [ -d "$HOME/.gemini" ]; then
    add_agent gemini "Gemini CLI" json-mcpServers "$HOME/.gemini/settings.json"; fi
  if command -v cursor >/dev/null 2>&1 || [ -d "$HOME/.cursor" ]; then
    add_agent cursor "Cursor" json-mcpServers "$HOME/.cursor/mcp.json"; fi
  if command -v windsurf >/dev/null 2>&1 || [ -d "$HOME/.codeium/windsurf" ]; then
    add_agent windsurf "Windsurf" json-mcpServers "$HOME/.codeium/windsurf/mcp_config.json"; fi
  local d label
  for d in "Code:VS Code" "Code - Insiders:VS Code Insiders" "VSCodium:VSCodium" "Code - OSS:Code - OSS"; do
    label="${d#*:}"; d="${d%%:*}"
    if [ -d "$XDG/$d/User" ]; then add_agent "vscode-$d" "$label (Copilot agent)" json-vscode "$XDG/$d/User/mcp.json"; fi
  done
}

register() {  # register INDEX
  local i="$1" kind="${AGENT_KINDS[$1]}" file="${AGENT_FILES[$1]}" label="${AGENT_LABELS[$1]}" out rc
  if [ "$kind" = "cli-claude" ]; then
    claude mcp remove "$NAME" -s user >>"$LOG" 2>&1 || true
    local args=(mcp add "$NAME" --transport stdio -s user)
    local kv
    while IFS= read -r kv; do [ -n "$kv" ] && args+=(-e "$kv"); done <<<"$SERVER_ENV_LINES"
    args+=(-- "$PY" -m librepcb_mcp)
    if claude "${args[@]}" >>"$LOG" 2>&1; then ok "$label  ${D}(claude mcp add -s user)${R}"
    else err "$label: 'claude mcp add' failed, see $LOG"; return 1; fi
    return 0
  fi
  set +e; out="$(edit_config "$kind" "$file" add 2>&1)"; rc=$?; set -e
  if [ $rc -eq 0 ]; then ok "$label  ${D}$file${out:+ ($out)}${R}"
  elif [ $rc -eq 3 ]; then manual_snippet "$kind" "$file"
  else err "$label: $out"; return 1; fi
}

unregister() {
  local i="$1" kind="${AGENT_KINDS[$1]}" file="${AGENT_FILES[$1]}" label="${AGENT_LABELS[$1]}"
  if [ "$kind" = "cli-claude" ]; then
    if claude mcp remove "$NAME" -s user >>"$LOG" 2>&1; then ok "$label"; else info "$label: not registered"; fi
    return 0
  fi
  [ -f "$file" ] || { info "$label: not registered"; return 0; }
  local out; out="$(edit_config "$kind" "$file" remove 2>&1)" && ok "$label  ${D}$out${R}" || warn "$label: $out"
}

# ---- uninstall ------------------------------------------------------------------------
if [ "$UNINSTALL" -eq 1 ]; then
  step "Uninstall"
  detect_agents
  for i in "${!AGENT_IDS[@]}"; do unregister "$i"; done
  if [ -d "$INSTALL_DIR" ] && confirm "Delete $INSTALL_DIR?" Y; then rm -rf "$INSTALL_DIR"; ok "Removed $INSTALL_DIR"; fi
  info "Libraries in $LIB_DIR and your projects were not touched."
  exit 0
fi

# ---- 1. requirements --------------------------------------------------------------------
step "1/5  Checking requirements"
PYTHON=""
for p in python3.14 python3.13 python3.12 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PYTHON="$(command -v "$p")"; break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.12+ is required (install it with your package manager)."
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null || die "Python venv/ensurepip missing (Debian/Ubuntu: sudo apt install python3-venv)."
ok "Python $("$PYTHON" -c 'import platform; print(platform.python_version())')  ${D}$PYTHON${R}"
command -v curl >/dev/null 2>&1 || command -v git >/dev/null 2>&1 || die "curl or git is required."

CLI_CMD=""
if command -v librepcb-cli >/dev/null 2>&1; then
  CLI_CMD="$(command -v librepcb-cli)"
elif command -v flatpak >/dev/null 2>&1 && flatpak info org.librepcb.LibrePCB >/dev/null 2>&1; then
  CLI_CMD="flatpak run --command=librepcb-cli org.librepcb.LibrePCB"
elif command -v flatpak >/dev/null 2>&1; then
  warn "LibrePCB is not installed. It is needed for ERC/DRC, exports and verified edits."
  if confirm "Install LibrePCB from Flathub now (flatpak install --user)?" Y; then
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo >>"$LOG" 2>&1 || true
    spin "Installing LibrePCB (Flatpak)…" flatpak install --user -y --noninteractive flathub org.librepcb.LibrePCB \
      && CLI_CMD="flatpak run --command=librepcb-cli org.librepcb.LibrePCB" \
      || warn "Flatpak install failed, see $LOG"
  fi
fi
if [ -n "$CLI_CMD" ]; then
  # shellcheck disable=SC2086
  ver="$($CLI_CMD --version 2>/dev/null | head -1 || true)"
  ok "${ver:-librepcb-cli}  ${D}$CLI_CMD${R}"
  case "$ver" in *"Version 0."*|*"Version 1."*) warn "LibrePCB 2.x is required; this version is too old." ;; esac
else
  warn "librepcb-cli not found: read-only tools will work, edits and checks will not. Get LibrePCB 2.x from https://librepcb.org/download/"
fi

# ---- 2. install server --------------------------------------------------------------------
step "2/5  Installing the server"
if [ "$YES" -eq 0 ]; then ask "Install directory" "$INSTALL_DIR"; INSTALL_DIR="${REPLY/#\~/$HOME}"; fi
PY="$INSTALL_DIR/venv/bin/python"
mkdir -p "$INSTALL_DIR"
[ -x "$PY" ] || spin "Creating virtual environment…" "$PYTHON" -m venv "$INSTALL_DIR/venv" || die "venv creation failed, see $LOG"
spin "Installing librepcb-mcp and dependencies…" "$PY" -m pip install --upgrade --quiet pip "$SOURCE" \
  || die "pip install failed, see $LOG"
"$PY" -c 'from librepcb_mcp.server import build_server; build_server()' >>"$LOG" 2>&1 \
  || die "Installed server does not start, see $LOG"
ok "librepcb-mcp $("$PY" -c 'import librepcb_mcp; print(librepcb_mcp.__version__)')  ${D}$INSTALL_DIR${R}"

# ---- 3. configuration ---------------------------------------------------------------------
step "3/5  Configuration"
info "The server only reads and writes projects inside the directories you allow."
[ -n "$ROOTS" ] || ROOTS="$HOME/pcb-projects"
if [ -d "$HOME/LibrePCB-Workspace/projects" ] && [[ ":$ROOTS:" != *":$HOME/LibrePCB-Workspace/projects:"* ]]; then
  ROOTS="$ROOTS:$HOME/LibrePCB-Workspace/projects"
fi
ask "Project directories (':'-separated)" "$ROOTS"; ROOTS="${REPLY//\~/$HOME}"
IFS=':' read -r -a _roots <<<"$ROOTS"
for r in "${_roots[@]}"; do
  mkdir -p "$r" 2>/dev/null || warn "Cannot create $r"
  case "$CLI_CMD" in flatpak*) [[ "$(cd "$r" 2>/dev/null && pwd -P)/" == "$HOME/"* ]] || \
    warn "$r is outside \$HOME: the LibrePCB Flatpak cannot access it." ;; esac
done
ok "Project roots: $ROOTS"

LIBS=""
WS_LIBS="$(ls -d "$HOME"/LibrePCB-Workspace/data/libraries 2>/dev/null || true)"
if [ -n "$WS_LIBS" ]; then
  ok "LibrePCB workspace libraries found: $WS_LIBS"; LIBS="$WS_LIBS"
fi
if [ "$NO_LIBS" -eq 0 ] && confirm "Download official LibrePCB libraries into $LIB_DIR (for component search)?" "$([ -n "$WS_LIBS" ] && echo N || echo Y)"; then
  ask "Libraries (space-separated, see github.com/LibrePCB-Libraries)" "LibrePCB_Base LibrePCB_Connectors"
  mkdir -p "$LIB_DIR"
  for l in $REPLY; do
    l="${l%.lplib}"
    if [ -d "$LIB_DIR/$l.lplib" ]; then
      if [ -d "$LIB_DIR/$l.lplib/.git" ] && command -v git >/dev/null 2>&1; then
        spin "Updating $l…" git -C "$LIB_DIR/$l.lplib" pull --ff-only -q || warn "Could not update $l"
      fi
      ok "$l"; continue
    fi
    if command -v git >/dev/null 2>&1; then
      spin "Downloading $l…" git clone -q --depth 1 "https://github.com/LibrePCB-Libraries/$l.lplib.git" "$LIB_DIR/$l.lplib" \
        && ok "$l" || warn "Could not download $l (check the name)"
    else
      tmp="$(mktemp -d)"
      if spin "Downloading $l…" bash -c "curl -fsSL 'https://github.com/LibrePCB-Libraries/$l.lplib/archive/HEAD.tar.gz' | tar -xz -C '$tmp'"; then
        mv "$tmp"/*/ "$LIB_DIR/$l.lplib" && ok "$l"
      else warn "Could not download $l (check the name)"; fi
      rm -rf "$tmp"
    fi
  done
  LIBS="${LIBS:+$LIBS:}$LIB_DIR"
fi
[ -n "$LIBS" ] || warn "No libraries configured: component search and add_component will not work."

SERVER_ENV_LINES="LIBREPCB_MCP_ROOTS=$ROOTS"
[ -n "$LIBS" ] && SERVER_ENV_LINES+=$'\n'"LIBREPCB_MCP_LIBRARIES=$LIBS"
[ -n "$CLI_CMD" ] && SERVER_ENV_LINES+=$'\n'"LIBREPCB_CLI=$CLI_CMD"
SERVER_ENV_JSON="$(printf '%s\n' "$SERVER_ENV_LINES" | "$PY" -c 'import json,sys; print(json.dumps(dict(l.rstrip("\n").split("=",1) for l in sys.stdin if "=" in l)))')"
export SERVER_ENV_JSON

# ---- 4. agents ------------------------------------------------------------------------------
step "4/5  Coding agents"
detect_agents
if [ ${#AGENT_IDS[@]} -eq 0 ]; then
  warn "No supported agent found (Claude Code, Codex CLI, Gemini CLI, Cursor, Windsurf, VS Code)."
  info "Register manually: command ${B}$PY -m librepcb_mcp${R}, environment:"
  printf '%s%s%s\n' "$D" "$(sed 's/^/      /' <<<"$SERVER_ENV_LINES")" "$R"
else
  for i in "${!AGENT_IDS[@]}"; do printf '    %s%d)%s %s\n' "$B" $((i + 1)) "$R" "${AGENT_LABELS[$i]}"; done
  ask "Install into which agents? (numbers, 'all' or 'none')" "all"
  sel="$REPLY"; chosen=()
  case "$sel" in
    all|ALL|a) chosen=("${!AGENT_IDS[@]}") ;;
    none|n|"") chosen=() ;;
    *) for n in ${sel//,/ }; do
         if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#AGENT_IDS[@]} ]; then chosen+=($((n - 1)))
         else warn "Ignoring '$n'"; fi
       done ;;
  esac
  failed=0
  for i in "${chosen[@]}"; do register "$i" || failed=1; done
  [ ${#chosen[@]} -eq 0 ] && info "Skipped agent registration."
  [ $failed -eq 0 ] || warn "Some registrations failed, see $LOG"
fi

# ---- 5. done -----------------------------------------------------------------------------------
step "5/5  Done"
ok "Server: ${B}$PY -m librepcb_mcp${R}"
info "Restart your agent, then try: ${B}\"Create a LibrePCB project in ${_roots[0]}/blinky with an LED and a resistor\"${R}"
info "Close a project in the LibrePCB app before letting an agent edit it."
info "Update: run this installer again.  Uninstall: ${D}curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- --uninstall${R}"
info "Log: $LOG"
