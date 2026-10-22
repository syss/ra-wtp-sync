# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
"""Start the settings GUI on a free port and check that the page and the token-guarded API answer."""
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as tmp:
    cfg = Path(tmp) / "config.toml"
    cfg.write_text(f'state_dir = "{Path(tmp).as_posix()}/state"\n', encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(root / "ra_wtp_gui.py"), "--no-browser", "-c", str(cfg)],
                         stdout=subprocess.PIPE, text=True)
    try:
        url = re.search(r"http://\S+/", p.stdout.readline()).group(0)
        html = urllib.request.urlopen(url, timeout=10).read().decode()
        token = re.search(r'const T="([^"]+)"', html).group(1)
        req = urllib.request.Request(url + "api/state", headers={"X-Token": token})
        state = json.load(urllib.request.urlopen(req, timeout=30))
        assert [x["label"] for x in state["schedule"]["platforms"]] == ["Linux", "Windows", "macOS"], state["schedule"]
        print("GUI OK")
    finally:
        p.terminate()
        p.wait(timeout=10)
