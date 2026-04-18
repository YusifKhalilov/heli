#!/usr/bin/env bash
set -euo pipefail

# ── colors ────────────────────────────────────────────────────────────────────
R='\033[0;31m'  G='\033[0;32m'  Y='\033[1;33m'
B='\033[0;34m'  C='\033[0;36m'  W='\033[1;37m'  D='\033[2m'  N='\033[0m'

# ── helpers ───────────────────────────────────────────────────────────────────
ok()   { printf "  ${G}✓${N}  %s\n" "$1"; }
err()  { printf "  ${R}✗${N}  %s\n" "$1"; }
info() { printf "  ${D}·${N}  %s\n" "$1"; }
warn() { printf "  ${Y}!${N}  %s\n" "$1"; }
die()  { err "$1"; exit 1; }

spinner() {
    local pid=$1 msg=$2 i=0 frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${C}%s${N}  %s" "${frames[i % ${#frames[@]}]}" "$msg"
        ((i++)) && true
        sleep 0.08
    done
    printf "\r"
}

# ── banner ────────────────────────────────────────────────────────────────────
clear
printf "\n${C}"
cat << 'EOF'
    __         ___
   / /_  ___  / (_)
  / __ \/ _ \/ / /
 / / / /  __/ / /
/_/ /_/\___/_/_/

EOF
printf "${N}"
printf "  ${W}Hyprland gamma control — one-command installer${N}\n"
printf "  ${D}────────────────────────────────────────────────${N}\n\n"

# ── preflight: hard requirements ──────────────────────────────────────────────
# These cannot be auto-installed — they are compositor/system-level binaries.
# heli is built specifically for Hyprland (omarchy / hyprland-based setups).
printf "${W}  Checking requirements…${N}\n"

FAIL=0

if command -v python3 &>/dev/null; then
    ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"
else
    err "python3 not found — install with: pacman -S python"
    FAIL=1
fi

if command -v hyprsunset &>/dev/null; then
    ok "hyprsunset"
else
    err "hyprsunset not found — install with: pacman -S hyprsunset"
    FAIL=1
fi

if command -v hyprctl &>/dev/null; then
    ok "hyprctl (Hyprland)"
else
    err "hyprctl not found — heli requires Hyprland compositor"
    FAIL=1
fi

[[ $FAIL -eq 1 ]] && die "Missing system requirements above — install them and re-run"

# ── pip install strategy ──────────────────────────────────────────────────────
# Arch Linux and other distros enforce PEP 668 (externally-managed-environment),
# blocking plain `pip install --user`. We detect this automatically and add
# --break-system-packages so the user never has to intervene.

pip_install() {
    local pkg=$1
    local pip_err
    pip_err=$(python3 -m pip install --user --quiet "$pkg" 2>&1) && return 0
    if echo "$pip_err" | grep -q "externally-managed-environment"; then
        python3 -m pip install --user --quiet --break-system-packages "$pkg"
    else
        printf "%s\n" "$pip_err" >&2
        return 1
    fi
}

# ── python dependencies ───────────────────────────────────────────────────────
printf "\n${W}  Installing Python dependencies…${N}\n"

for pkg in textual astral; do
    if python3 -c "import $pkg" 2>/dev/null; then
        ok "$pkg already installed"
    else
        info "Installing $pkg…"
        pip_install "$pkg" &
        PID=$!
        spinner $PID "Installing $pkg…"
        wait $PID && ok "$pkg installed" || die "Failed to install $pkg — check your Python environment"
    fi
done

# ── copy files ────────────────────────────────────────────────────────────────
printf "\n${W}  Installing heli…${N}\n"

SHARE="$HOME/.local/share/heli"
BIN="$HOME/.local/bin"
RAW="https://raw.githubusercontent.com/YusifKhalilov/heli/master"

mkdir -p "$SHARE" "$BIN"

curl -fsSL "$RAW/heli.py"          -o "$SHARE/heli.py"
curl -fsSL "$RAW/restore-gamma.sh" -o "$SHARE/restore-gamma.sh"
chmod +x "$SHARE/restore-gamma.sh"
ok "downloaded app files → $SHARE"

# ── launcher ──────────────────────────────────────────────────────────────────
cat > "$BIN/heli" << 'LAUNCHER'
#!/usr/bin/env bash
exec python3 "$HOME/.local/share/heli/heli.py" "$@"
LAUNCHER
chmod +x "$BIN/heli"
ok "created launcher → $BIN/heli"

# ── autostart (Hyprland via uwsm) ─────────────────────────────────────────────
AUTOSTART="$HOME/.config/hypr/autostart.conf"
AUTOSTART_LINE="exec-once = uwsm-app -- $SHARE/restore-gamma.sh"

if [[ -f "$AUTOSTART" ]]; then
    if grep -qF "restore-gamma.sh" "$AUTOSTART"; then
        info "autostart entry already present — skipping"
    else
        printf "\n%s\n" "$AUTOSTART_LINE" >> "$AUTOSTART"
        ok "added autostart entry → $AUTOSTART"
    fi
else
    warn "~/.config/hypr/autostart.conf not found — creating it"
    mkdir -p "$(dirname "$AUTOSTART")"
    printf "%s\n" "$AUTOSTART_LINE" > "$AUTOSTART"
    ok "created $AUTOSTART with autostart entry"
fi

# ── PATH check ────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN:"* ]]; then
    printf "\n"
    warn "$BIN is not in your PATH"
    info "Add this to your ~/.bashrc or ~/.zshrc:"
    printf "\n    ${Y}export PATH=\"\$HOME/.local/bin:\$PATH\"${N}\n\n"
    info "Then run:  source ~/.bashrc"
fi

# ── done ──────────────────────────────────────────────────────────────────────
printf "\n${D}  ────────────────────────────────────────────────${N}\n"
printf "  ${G}${W}heli installed successfully!${N}\n"
printf "  ${D}Type${N} ${C}heli${N} ${D}to launch.${N}\n\n"
