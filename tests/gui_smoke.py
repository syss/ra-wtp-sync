# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
"""Start the settings GUI on a free port and check that the page and the token-guarded API answer.

    python tests/gui_smoke.py             # python ra_wtp_gui.py
    python tests/gui_smoke.py launcher    # through the double-click launcher of this OS
"""
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as tmp:
    cfg = Path(tmp) / "config.toml"
    cfg.write_text(f'state_dir = "{Path(tmp).as_posix()}/state"\n', encoding="utf-8")
    args = ["--no-browser", "-c", str(cfg)]
    if sys.argv[1:] == ["launcher"]:
        cmd = (["cmd", "/c", str(root / "start-gui.bat")] if sys.platform == "win32"
               else ["sh", str(root / ("start-gui.command" if sys.platform == "darwin" else "start-gui.sh"))])
    else:
        cmd = [sys.executable, str(root / "ra_wtp_gui.py")]
    p = subprocess.Popen(cmd + args, stdout=subprocess.PIPE, text=True)
    try:
        url = re.search(r"http://\S+/", p.stdout.readline()).group(0)
        html = urllib.request.urlopen(url, timeout=10).read().decode()
        token = re.search(r'const T="([^"]+)"', html).group(1)
        req = urllib.request.Request(url + "api/state", headers={"X-Token": token})
        state = json.load(urllib.request.urlopen(req, timeout=30))
        assert [x["label"] for x in state["schedule"]["platforms"]] == ["Linux", "Windows", "macOS"], state["schedule"]
        req = urllib.request.Request(url + "api/browse?path=" + urllib.parse.quote(tmp), headers={"X-Token": token})
        assert json.load(urllib.request.urlopen(req, timeout=10))["path"], "browse"
        print("GUI OK")
    finally:
        if sys.platform == "win32":     # the launcher is cmd.exe -> python: end the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.terminate()
        p.wait(timeout=10)
