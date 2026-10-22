# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
"""Install, detect and remove the recurring job with the real scheduler of this OS.
Windows: Task Scheduler, Linux: cron (skipped without crontab). macOS is skipped: a LaunchAgent
needs a logged-in GUI session, which CI runners do not reliably have."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ra_wtp_schedule as sc

if sc.current() == "mac" or (sc.current() == "linux" and not shutil.which("crontab")):
    print("skipped: no suitable scheduler on this runner")
    sys.exit(0)
ok, msg = sc.install(30)
print(ok, msg)
assert ok, msg
try:
    assert sc.installed(), "installed() is False right after install()"
finally:
    ok, msg = sc.remove()
    print(ok, msg)
assert ok, msg
assert not sc.installed(), "installed() is True after remove()"
print("schedule OK")
