#!/bin/bash
# Start the dashboard on an Ubuntu desktop (14.04/16.04 or newer) with Firefox, and keep it running.
#
#   bash start.sh                 start now (Ctrl+C to stop)
#   bash start.sh --install       start automatically every time this user logs in
#   bash start.sh --uninstall     stop starting automatically
#
# Options for server.py go after "--" and are remembered by --install:
#   bash start.sh --install -- --carto-key YOUR_KEY
#
# It finds a Python for server.py (3.5 or newer - Ubuntu 16.04's own python3 is fine), starts the server,
# opens Firefox full-screen on the dashboard, and watches both: a crashed server is restarted, a closed
# Firefox is reopened. Set OVERHEAD_PYTHON=/path/to/python3 to choose the Python yourself. Logs: ./logs

set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOGS="$HERE/logs"
OPTS_FILE="$HERE/launch-options"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/overhead-dashboard.desktop"
PROFILE="$HERE/cache/firefox-kiosk"
mkdir -p "$LOGS"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOGS/launcher.log"; }
rotate() { [ -f "$1" ] && [ "$(stat -c %s "$1")" -gt 5242880 ] && mv -f "$1" "$1.1"; true; }

# ---------------------------------------------------------------- arguments
MODE=run; BOOT_DELAY=0; SERVER_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE=install ;;
    --uninstall) MODE=uninstall ;;
    --autostart) MODE=run; BOOT_DELAY=25
                 if [ -f "$OPTS_FILE" ]; then while IFS= read -r l; do SERVER_ARGS+=("$l"); done < "$OPTS_FILE"; fi ;;
    --boot-delay) shift; BOOT_DELAY="$1" ;;
    --) shift; SERVER_ARGS+=("$@"); break ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------- find a Python for server.py
find_python() {
  local c cands=()
  if [ -n "${OVERHEAD_PYTHON:-}" ]; then cands=("$OVERHEAD_PYTHON")
  else
    cands=(python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3
           "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" "$HOME/.pyenv/shims/python3"
           /usr/local/bin/python3.1? /usr/local/bin/python3.[789] /opt/*/bin/python3)
  fi
  for c in "${cands[@]}"; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 5) else 1)' 2>/dev/null; then
        command -v "$c" 2>/dev/null || echo "$c"; return 0
      fi
    fi
  done
  return 1
}

# ---------------------------------------------------------------- find the newest Firefox
# A current Firefox downloaded from Mozilla usually lives in ~/firefox or /opt/firefox, next to the distro's
# old one in /usr/bin. Use whichever is newest (or OVERHEAD_FIREFOX if set).
ff_version() { "$1" --version 2>/dev/null | grep -oE '[0-9]+' | head -1; }
find_firefox() {
  local c v best="" bestv=0
  if [ -n "${OVERHEAD_FIREFOX:-}" ]; then echo "$OVERHEAD_FIREFOX"; return 0; fi
  for c in "$HOME/firefox/firefox" /opt/firefox/firefox /usr/local/firefox/firefox "$(command -v firefox 2>/dev/null)"; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    v="$(ff_version "$c")"; v="${v:-0}"
    if [ "$v" -gt "$bestv" ]; then best="$c"; bestv="$v"; fi
  done
  [ -n "$best" ] && echo "$best"
}

# ---------------------------------------------------------------- install / uninstall
if [ "$MODE" = install ]; then
  mkdir -p "$(dirname "$AUTOSTART")"
  : > "$OPTS_FILE"; for a in "${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}"; do printf '%s\n' "$a" >> "$OPTS_FILE"; done
  cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Overhead Dashboard
Comment=Starts the ADS-B dashboard full-screen
Exec=/bin/bash "$HERE/start.sh" --autostart
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
  echo "Installed: the dashboard will start by itself every time $(whoami) logs in."
  echo "  $AUTOSTART"
  PY="$(find_python)" && echo "  Python for the server: $PY ($("$PY" -V 2>&1))" \
    || echo "  WARNING: no Python 3.5+ found - see the note printed by 'bash start.sh'."
  FF="$(find_firefox)"; FV="$( [ -n "$FF" ] && ff_version "$FF" )"
  [ -n "$FV" ] && [ "$FV" -lt 89 ] && echo "  WARNING: Firefox $FV is too old for the dashboard page (needs 89 or newer). Put a current Firefox in ~/firefox - see README."
  echo "  Firefox: ${FF:-not found} (version ${FV:-?})$( [ -n "$FV" ] && [ "$FV" -lt 71 ] && echo ' (older than 71: no kiosk mode; install xdotool for automatic full-screen:  sudo apt-get install xdotool)')"
  cat <<'EOF'

For it to come back by itself after a power cut, also set (once):
  * BIOS/UEFI: "Restore on AC power loss" / "AC Recovery" / "After power failure" = Power On
  * System Settings > User Accounts > (unlock) > Automatic Login: ON for this user
  * System Settings > Brightness & Lock: Turn screen off "Never", Lock "Off"
    (start.sh also turns screen blanking off each time it starts)
EOF
  exit 0
fi
if [ "$MODE" = uninstall ]; then
  rm -f "$AUTOSTART" "$OPTS_FILE" && echo "Removed: it won't start automatically any more."
  exit 0
fi

# ---------------------------------------------------------------- run
PY="$(find_python)" || {
  log "ERROR: server.py needs Python 3.5 or newer, and none was found."
  log "       This machine's python3 is: $(python3 -V 2>&1 || echo 'not installed')."
  log "       If you have a newer one somewhere, run:  OVERHEAD_PYTHON=/path/to/python3 bash start.sh"
  exit 1
}
PORT=8081
for ((i = 0; i < ${#SERVER_ARGS[@]}; i++)); do
  [ "${SERVER_ARGS[$i]}" = "--port" ] && PORT="${SERVER_ARGS[$((i + 1))]}"
  case "${SERVER_ARGS[$i]}" in --port=*) PORT="${SERVER_ARGS[$i]#--port=}" ;; esac
done
URL="http://localhost:$PORT"

if [ "$BOOT_DELAY" -gt 0 ]; then log "waiting ${BOOT_DELAY}s for the desktop and Wi-Fi to settle"; sleep "$BOOT_DELAY"; fi

# keep the TV awake (these stick as this user's settings, which is what a kiosk wants)
if [ -n "${DISPLAY:-}" ] && command -v xset >/dev/null; then xset s off -dpms s noblank 2>/dev/null; fi
if command -v gsettings >/dev/null; then
  gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
  gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
  gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
fi

SPID=""; FPID=""; CRASHES=0
stop() {
  log "stopping"
  [ -n "$FPID" ] && kill "$FPID" 2>/dev/null
  [ -n "$SPID" ] && kill "$SPID" 2>/dev/null
  wait 2>/dev/null; exit 0
}
trap stop INT TERM

is_up() { "$PY" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3)' "$URL/api/config" 2>/dev/null; }

start_server() {
  rotate "$LOGS/server.log"
  echo "===== starting $(date) with $PY =====" >> "$LOGS/server.log"
  ( cd "$HERE" && exec "$PY" -u server.py "${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}" ) >> "$LOGS/server.log" 2>&1 &
  SPID=$!
  log "server started with $("$PY" -V 2>&1) (pid $SPID)"
}

FIREFOX="$(find_firefox || true)"
FFVER="$( [ -n "$FIREFOX" ] && ff_version "$FIREFOX" )"
[ -n "$FIREFOX" ] && log "using Firefox $FFVER at $FIREFOX"
[ -z "$FIREFOX" ] && log "Firefox not found - only the server will run"
[ -n "$FFVER" ] && [ "$FFVER" -lt 89 ] && log "WARNING: Firefox $FFVER is too old for the dashboard page (it needs Firefox 89+); put a current Firefox in ~/firefox (see README)"

start_firefox() {
  # its own profile: no "restore session?" page after a power cut, no first-run tabs, no fights with a normal window
  mkdir -p "$PROFILE"
  cat > "$PROFILE/user.js" <<'EOF'
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", -1);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("full-screen-api.warning.timeout", 0);
user_pref("browser.fullscreen.autohide", true);
EOF
  rotate "$LOGS/browser.log"
  if [ -n "$FFVER" ] && [ "$FFVER" -ge 71 ]; then
    "$FIREFOX" -no-remote -profile "$PROFILE" --kiosk "$URL" >> "$LOGS/browser.log" 2>&1 &
    FPID=$!; log "Firefox $FFVER started in kiosk mode"
  else
    "$FIREFOX" -no-remote -profile "$PROFILE" "$URL" >> "$LOGS/browser.log" 2>&1 &
    FPID=$!
    if command -v xdotool >/dev/null; then                   # no kiosk mode before Firefox 71: press F11 for it
      ( sleep 10; xdotool search --sync --onlyvisible --pid "$FPID" 2>/dev/null | head -1 | xargs -r -I{} xdotool windowactivate --sync {} key F11 ) &
      log "Firefox ${FFVER:-?} started (no kiosk mode in this version; switching to full screen with xdotool)"
    else
      log "Firefox ${FFVER:-?} started, but it has no kiosk mode: press F11 once, or install xdotool (sudo apt-get install xdotool) to do it automatically"
    fi
  fi
}

log "launcher starting (dashboard at $URL)"
while true; do
  if [ -z "$SPID" ] || ! kill -0 "$SPID" 2>/dev/null; then
    if [ -n "$SPID" ]; then
      wait "$SPID" 2>/dev/null; CRASHES=$((CRASHES + 1)); W=$(( CRASHES > 5 ? 60 : 2 ** CRASHES ))
      log "server stopped; restarting in ${W}s (see logs/server.log)"; sleep "$W"
    fi
    start_server
    for _ in $(seq 1 90); do is_up && break; sleep 1; done
    if is_up; then log "server is answering at $URL"; [ "$CRASHES" -lt 3 ] && CRASHES=0
    else log "server isn't answering yet; will keep trying"; continue; fi
  fi
  if [ -n "$FIREFOX" ] && { [ -z "$FPID" ] || ! kill -0 "$FPID" 2>/dev/null; }; then
    if [ -n "$FPID" ]; then wait "$FPID" 2>/dev/null; log "Firefox closed; reopening in 5s"; sleep 5; fi
    start_firefox
  fi
  sleep 2
done
