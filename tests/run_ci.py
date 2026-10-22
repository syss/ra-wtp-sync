# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
"""Run the unit tests; on GitHub Actions also emit every failure as an error annotation."""
import os
import sys
import unittest
from pathlib import Path

here = Path(__file__).resolve().parent
suite = unittest.defaultTestLoader.discover(str(here))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if os.environ.get("GITHUB_ACTIONS"):
    for test, tb in result.failures + result.errors:
        msg = f"{test.id()}: {tb.strip().splitlines()[-1]}"
        print("::error title=test failed::" + msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
sys.exit(not result.wasSuccessful())
