#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ra-wtp-sync contributors
# Linux: run this file (or double-click it and choose "Run") to open the settings page.
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$py" ra_wtp_gui.py "$@"
  fi
done
echo "Python 3.11 or newer is required. Install it with your package manager, e.g.: sudo apt install python3"
exit 1
