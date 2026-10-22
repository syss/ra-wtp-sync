#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
# macOS: double-click this file to open the settings page.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  exec python3 ra_wtp_gui.py "$@"
fi
echo "Python 3.11 or newer is required. Download it from https://www.python.org/downloads/ and run this file again."
read -r _
