#!/usr/bin/env python3
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
"""Guided setup (Linux, macOS, Windows): asks a few questions, writes config.toml and
api_key next to the script (both git-ignored), runs a dry run and can schedule the sync."""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC = HERE / "ra_wtp_sync.py"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def yes(prompt: str, default: bool) -> bool:
    ans = ask(f"{prompt} ({'Y/n' if default else 'y/N'})", "").lower()
    return default if not ans else ans.startswith("y")


def default_saves() -> str:
    home = Path.home()
    if sys.platform == "darwin":
        return str(home / "Library/Application Support/RetroArch/saves")
    if sys.platform == "win32":
        return ""   # RetroArch is usually portable on Windows; enter its saves folder if you have one
    return str(home / ".config/retroarch/saves")


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)   # JSON strings are valid TOML basic strings


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join(toml_str(i) for i in items) + "]"


def schedule(minutes: int = 30) -> None:
    """Install a recurring job with the OS's own scheduler."""
    py = sys.executable
    if sys.platform == "win32":
        pyw = Path(py).with_name("pythonw.exe")           # no console window
        exe = str(pyw if pyw.exists() else py)
        cmd = f'"{exe}" "{SYNC}"'
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", "ra-wtp-sync", "/SC", "MINUTE",
                            "/MO", str(minutes), "/TR", cmd], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
    elif sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/ra-wtp-sync.plist"
        log = Path.home() / "Library/Logs/ra-wtp-sync.log"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ra-wtp-sync</string>
<key>ProgramArguments</key><array><string>{py}</string><string>{SYNC}</string></array>
<key>StartInterval</key><integer>{minutes * 60}</integer>
<key>RunAtLoad</key><true/>
<key>Nice</key><integer>10</integer>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
        print(f"LaunchAgent installed: {plist}" if r.returncode == 0 else r.stderr.strip())
    else:
        if not shutil.which("crontab"):
            print("No crontab found. Use the systemd timer from examples/ instead.")
            return
        line = f'7,37 * * * * nice -n 10 "{py}" "{SYNC}" >> "{HERE / "cron.log"}" 2>&1'
        old = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
        new = [l for l in old if str(SYNC) not in l] + [line]
        subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, check=True)
        print("Cron entry installed:\n  " + line)


def main() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required")
        return 1
    cfg_path, key_path = HERE / "config.toml", HERE / "api_key"
    if cfg_path.exists() and not yes("config.toml already exists - overwrite?", False):
        print("Keeping existing config.")
        return 0

    print("== RetroAchievements account ==")
    user = ask("Your RetroAchievements username")
    if not user:
        print("username is required")
        return 1
    print("Web API key: https://retroachievements.org/settings  ->  Keys")
    key = getpass.getpass("Paste your API key (input hidden), then press Enter: ").strip()
    if not key:
        print("API key is required")
        return 1
    key_path.touch(mode=0o600)
    key_path.chmod(0o600)
    key_path.write_text(key, encoding="utf-8")

    print("\n== Folders ==")
    src = ask("ROM library to read from (never modified)", str(Path.home() / "roms"))
    if not Path(src).expanduser().is_dir():
        print(f"  (note: {src} does not exist yet)")
    dest = ask("Output folder, e.g. inside your Syncthing folder", str(Path.home() / "Sync" / "roms-wtp"))
    saves = ask("RetroArch saves folder, to prefer ROMs that already have a save (empty = none)", default_saves())

    print("\n== Choices ==")
    print("Available systems (* = selected by default; 'title' = disc based, big files, matched by title):")
    subprocess.run([sys.executable, str(SYNC), "--api-key-file", str(key_path), "--list-consoles"], check=False)
    consoles = ask("Systems to handle: keys or ids separated by spaces, or 'all'",
                   "n64 snes nes gb gbc gba megadrive").split()
    langs = ask("Preferred languages, in order (de fr es it ja en; empty = none)", "en").split()
    regions = ask("Preferred regions, in order (europe usa japan world; empty = none)", "europe usa").split()
    patch = yes("Build missing hacks from RetroAchievements' official patches?", True)
    started = yes("Also keep games you started but have not beaten?", False)

    cfg_path.touch(mode=0o600)
    cfg_path.write_text(f"""# Generated by setup.py - git-ignored, edit freely (see examples/config.example.toml)
username = {toml_str(user)}
api_key_file = "api_key"
src = {toml_str(src)}
dest = {toml_str(dest)}
saves = {toml_list([saves] if saves else [])}
consoles = {toml_list(consoles)}
include_started = {str(started).lower()}
build_patches = {str(patch).lower()}

[preferences]
order = ["save", "qol", "language", "region", "revision"]
languages = {toml_list(langs)}
regions = {toml_list(regions)}
""", encoding="utf-8")

    print(f"\nConfig written to {cfg_path} - running a dry run (changes nothing)...")
    if subprocess.run([sys.executable, str(SYNC), "--dry-run"]).returncode != 0:
        print("Dry run failed - check config.toml")
        return 1
    if yes("\nLooks right? Run it for real now?", True):
        subprocess.run([sys.executable, str(SYNC)])
    if yes("\nRun it automatically every 30 minutes?", False):
        schedule()
    print("Done. Re-run ra_wtp_sync.py any time (--dry-run to preview), or open ra_wtp_gui.py to change settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
