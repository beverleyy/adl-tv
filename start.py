#!/usr/bin/env python3
"""
Start the dashboard and keep it running: the server, then a full-screen kiosk browser.

    python3 start.py                      start now (Ctrl+C to stop)
    python3 start.py --install            start automatically every time this user logs in
    python3 start.py --uninstall          stop starting automatically

Anything after "--" is passed to server.py, and is remembered by --install, e.g.
    python3 start.py --install -- --carto-key YOUR_KEY

While running it is a watchdog: if the server crashes it is restarted, and if the browser is closed it is
reopened. Logs go to ./logs. Standard library only, works on Windows, macOS and Linux.
"""
import argparse
import ctypes
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
LOGS = os.path.join(HERE, "logs")
SYSTEM = platform.system()          # "Windows", "Darwin" (macOS) or "Linux"
APP = "Overhead Dashboard"


# ---------------------------------------------------------------- logging
def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with open(os.path.join(LOGS, "launcher.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def logfile(name):
    """Append-mode log file, rotated once it passes 5 MB so the disk never fills up."""
    os.makedirs(LOGS, exist_ok=True)
    path = os.path.join(LOGS, name)
    if os.path.isfile(path) and os.path.getsize(path) > 5 * 1024 * 1024:
        os.replace(path, path + ".1")
    return open(path, "a", encoding="utf-8")


# ---------------------------------------------------------------- the server
def port_from(server_args):
    for i, a in enumerate(server_args):
        if a == "--port" and i + 1 < len(server_args):
            return int(server_args[i + 1])
        if a.startswith("--port="):
            return int(a.split("=", 1)[1])
    return int(os.environ.get("PORT", 8081))


def start_server(server_args):
    out = logfile("server.log")
    out.write(f"\n===== starting {time.ctime()} =====\n"); out.flush()
    flags = subprocess.CREATE_NO_WINDOW if SYSTEM == "Windows" else 0
    return subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "server.py")] + server_args,
                            cwd=HERE, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)


def wait_until_up(url, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


# ---------------------------------------------------------------- the browser
def find_browser(override=None):
    """Chrome / Chromium / Edge (best kiosk support), else Firefox. Returns (path, kind)."""
    if override:
        return override, ("firefox" if "firefox" in override.lower() else "chrome")
    cands = []
    if SYSTEM == "Windows":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for r in roots:
            cands += [(os.path.join(r, "Google", "Chrome", "Application", "chrome.exe"), "chrome"),
                      (os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe"), "chrome"),
                      (os.path.join(r, "Mozilla Firefox", "firefox.exe"), "firefox")]
    elif SYSTEM == "Darwin":
        cands = [("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chrome"),
                 ("/Applications/Chromium.app/Contents/MacOS/Chromium", "chrome"),
                 ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "chrome"),
                 ("/Applications/Firefox.app/Contents/MacOS/firefox", "firefox")]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            if shutil.which(name):
                cands.append((shutil.which(name), "chrome"))
        if shutil.which("firefox"):
            cands.append((shutil.which("firefox"), "firefox"))
    for path, kind in cands:
        if path and os.path.isfile(path):
            return path, kind
    return None, None


def start_browser(path, kind, url):
    # A profile of its own, inside this folder: no "restore pages?" prompts after a power cut, no profile-lock
    # fights with a normal browser window, and it works with snap-packaged Chromium on Ubuntu.
    profile = os.path.join(HERE, "cache", "kiosk-profile-" + kind)
    os.makedirs(profile, exist_ok=True)
    if kind == "chrome":
        args = [path, "--kiosk", url, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--noerrdialogs", "--disable-session-crashed-bubble", "--disable-infobars", "--disable-features=Translate",
                "--overscroll-history-navigation=0", "--check-for-update-interval=31536000", "--password-store=basic"]
    else:
        args = [path, "-new-instance", "-profile", profile, "--kiosk", url]
    return subprocess.Popen(args, stdout=logfile("browser.log"), stderr=subprocess.STDOUT)


def keep_screen_awake():
    """Best effort: stop the display sleeping while the dashboard runs."""
    try:
        if SYSTEM == "Windows":
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        elif SYSTEM == "Darwin":
            return subprocess.Popen(["caffeinate", "-d", "-i"])
        elif os.environ.get("DISPLAY") and shutil.which("xset"):
            subprocess.run(["xset", "s", "off", "-dpms", "s", "noblank"], check=False)
    except Exception as e:
        log(f"couldn't stop the screen sleeping ({e}); set the power settings to 'never sleep' instead")
    return None


# ---------------------------------------------------------------- run
def run(server_args, browser_override, no_browser, boot_delay):
    if boot_delay:
        log(f"waiting {boot_delay}s for the desktop and network to settle")
        time.sleep(boot_delay)
    url = f"http://localhost:{port_from(server_args)}"
    stopping = {"now": False}

    def stop(*_):
        stopping["now"] = True
    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    awake = keep_screen_awake()
    server, browser, crashes = None, None, 0
    bpath, bkind = (None, None) if no_browser else find_browser(browser_override)
    if not no_browser and not bpath:
        log("no Chrome, Chromium, Edge or Firefox found - only the server will run (use --browser PATH)")
    while not stopping["now"]:
        if server is None or server.poll() is not None:
            if server is not None:
                crashes += 1
                wait = min(60, 2 ** min(crashes, 6))
                log(f"server stopped (exit code {server.returncode}); restarting in {wait}s - see logs/server.log")
                time.sleep(wait)
            server = start_server(server_args)
            log(f"server started (pid {server.pid})")
            if wait_until_up(url, 90):
                log(f"server is answering at {url}")
                crashes = 0 if crashes < 3 else crashes
            else:
                log("server didn't answer within 90s; will keep trying")
                continue
        if bpath and (browser is None or browser.poll() is not None):
            if browser is not None:
                log("browser closed; reopening in 5s")
                time.sleep(5)
            browser = start_browser(bpath, bkind, url)
            log(f"browser started: {os.path.basename(bpath)} in kiosk mode")
        time.sleep(2)
    log("stopping")
    for p in (browser, server, awake):
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


# ---------------------------------------------------------------- install / uninstall
def python_for_autostart():
    """On Windows prefer pythonw.exe so no console window sits on top of the TV."""
    if SYSTEM == "Windows":
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.isfile(w):
            return w
    return sys.executable


def autostart_paths():
    home = os.path.expanduser("~")
    if SYSTEM == "Windows":
        return [os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup", APP + ".bat")]
    if SYSTEM == "Darwin":
        return [os.path.join(home, "Library", "LaunchAgents", "io.github.overhead-dashboard.plist")]
    return [os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), "autostart", "overhead-dashboard.desktop")]


def install(server_args, browser_override):
    cmd = [python_for_autostart(), os.path.join(HERE, "start.py"), "--boot-delay", "20"]
    if browser_override:
        cmd += ["--browser", browser_override]
    if server_args:
        cmd += ["--"] + server_args
    path = autostart_paths()[0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if SYSTEM == "Windows":
        q = lambda s: f'"{s}"'
        body = "@echo off\r\n" + f'start "" {" ".join(q(c) for c in cmd)}\r\n'
    elif SYSTEM == "Darwin":
        items = "".join(f"\n        <string>{c}</string>" for c in cmd)
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>io.github.overhead-dashboard</string>
    <key>ProgramArguments</key><array>{items}
    </array>
    <key>RunAtLoad</key><true/>
    <key>WorkingDirectory</key><string>{HERE}</string>
</dict></plist>
"""
    else:
        quoted = " ".join("'" + c.replace("'", "'\\''") + "'" for c in cmd)
        quoted = quoted.replace('"', '\\"')                     # (escaped outside the f-string: Python < 3.12 needs that)
        body = f"""[Desktop Entry]
Type=Application
Name={APP}
Comment=Starts the ADS-B dashboard in kiosk mode
Exec=sh -c "cd '{HERE}' && exec {quoted}"
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
Terminal=false
"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    print(f"Installed: it will start automatically when this user logs in.\n  {path}")
    if SYSTEM == "Darwin":
        subprocess.run(["launchctl", "load", "-w", path], check=False)
    print("\nFor it to come back by itself after a power cut, also set:")
    if SYSTEM == "Windows":
        print("  * automatic sign-in for this account (Settings > Accounts > Sign-in options, or run 'netplwiz')")
        print("  * Power & sleep: screen and sleep 'Never'")
    elif SYSTEM == "Darwin":
        print("  * System Settings > Users & Groups > Automatically log in as: this user")
        print("  * System Settings > Energy: 'Start up automatically after a power failure'")
    else:
        print("  * Settings > Users > Automatic Login: on   (or your display manager's autologin option)")
        print("  * Settings > Power: Blank Screen 'Never', Automatic Suspend 'Off'")
    print("  * the BIOS/UEFI setting 'Restore on AC power loss' (or 'AC Recovery') = Power On")


def uninstall():
    for path in autostart_paths():
        if os.path.isfile(path):
            if SYSTEM == "Darwin":
                subprocess.run(["launchctl", "unload", "-w", path], check=False)
            os.remove(path)
            print(f"Removed {path}")
            return
    print("It wasn't set to start automatically.")


# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[1:]
    server_args = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[:argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="start automatically at login")
    ap.add_argument("--uninstall", action="store_true", help="stop starting automatically")
    ap.add_argument("--browser", help="path to the browser to use (default: Chrome/Chromium/Edge, else Firefox)")
    ap.add_argument("--no-browser", action="store_true", help="only run (and watch) the server")
    ap.add_argument("--boot-delay", type=int, default=0, help="seconds to wait before starting (used at login)")
    a = ap.parse_args(own)
    if a.install:
        return install(server_args, a.browser)
    if a.uninstall:
        return uninstall()
    run(server_args, a.browser, a.no_browser, a.boot_delay)


if __name__ == "__main__":
    main()
