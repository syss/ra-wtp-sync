# ra-wtp-sync - mirror a RetroAchievements "Want to Play" list into a folder
# Copyright (C) 2026 ra-wtp-sync contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
"""Run the sync regularly with the OS's own scheduler (Linux: cron, Windows: Task Scheduler,
macOS: LaunchAgent). Shared by setup.py and the GUI. Platform order everywhere: linux, win, mac."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC = HERE / "ra_wtp_sync.py"
PLATFORMS = ("linux", "win", "mac")
LABELS = {"linux": "Linux", "win": "Windows", "mac": "macOS"}
TASK = "ra-wtp-sync"
FLAG = "--if-changed"   # every scheduled run is a cheap check; the full pass runs when RA changed (or daily)


def current() -> str:
    return "win" if sys.platform == "win32" else "mac" if sys.platform == "darwin" else "linux"


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents/ra-wtp-sync.plist"


def _win_python() -> str:
    pyw = Path(sys.executable).with_name("pythonw.exe")   # no console window
    return str(pyw if pyw.exists() else sys.executable)


def cron_line(minutes: int = 10) -> str:
    step = max(1, min(minutes, 60))
    spec = "7,37 * * * *" if step == 30 else f"*/{step} * * * *" if step < 60 else "7 * * * *"
    return f'{spec} nice -n 10 "{sys.executable}" "{SYNC}" {FLAG} >> "{HERE / "cron.log"}" 2>&1'


def plist_text(minutes: int = 10) -> str:
    log = Path.home() / "Library/Logs/ra-wtp-sync.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ra-wtp-sync</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{SYNC}</string><string>{FLAG}</string></array>
<key>StartInterval</key><integer>{minutes * 60}</integer>
<key>RunAtLoad</key><true/>
<key>Nice</key><integer>10</integer>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def manual(plat: str, minutes: int = 10) -> str:
    """The exact command / entry to set up by hand on `plat` (shown in the GUI)."""
    if plat == "linux":
        return f"crontab -e   # add this line:\n{cron_line(minutes)}"
    if plat == "win":
        return (f'schtasks /Create /F /TN {TASK} /SC MINUTE /MO {minutes} '
                f'/TR "\\"{_win_python()}\\" \\"{SYNC}\\" {FLAG}"')
    return f"# save as {plist_path()}, then: launchctl load {plist_path()}\n{plist_text(minutes)}"


def install(minutes: int = 10) -> tuple[bool, str]:
    """Install the recurring job for the current OS. Returns (ok, message)."""
    plat = current()
    if plat == "win":
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "MINUTE", "/MO", str(minutes),
                            "/TR", f'"{_win_python()}" "{SYNC}" {FLAG}'], capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if plat == "mac":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist_text(minutes), encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(p)], capture_output=True, text=True)
        return r.returncode == 0, f"LaunchAgent installed: {p}" if r.returncode == 0 else r.stderr.strip()
    if not shutil.which("crontab"):
        return False, "No crontab found. Use the systemd timer from examples/ instead."
    old = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
    new = [l for l in old if str(SYNC) not in l] + [cron_line(minutes)]
    r = subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, capture_output=True)
    return r.returncode == 0, "Cron entry installed:\n  " + cron_line(minutes) if r.returncode == 0 else r.stderr.strip()


def remove() -> tuple[bool, str]:
    plat = current()
    if plat == "win":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True, text=True)
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())
    if plat == "mac":
        p = plist_path()
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink(missing_ok=True)
        return True, "LaunchAgent removed."
    if not shutil.which("crontab"):
        return False, "No crontab found."
    old = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
    new = [l for l in old if str(SYNC) not in l]
    r = subprocess.run(["crontab", "-"], input="\n".join(new) + "\n" if new else "", text=True, capture_output=True)
    return r.returncode == 0, "Cron entry removed." if r.returncode == 0 else r.stderr.strip()


def installed() -> bool:
    plat = current()
    if plat == "win":
        return subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True).returncode == 0
    if plat == "mac":
        return plist_path().exists()
    if not shutil.which("crontab"):
        return False
    return str(SYNC) in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
