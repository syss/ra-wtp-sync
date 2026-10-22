<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Changelog

## Unreleased

* New `dest_mode = "merge"` (`--merge`, GUI option): add games straight into an existing ROM folder, reusing its system folders and never overwriting or deleting files of your own.

* GUI is now a guided four-step page: API key help with an example and a Test login button, folder pickers, an Advanced section for the rest.
* Double-click launchers: `start-gui.sh`, `start-gui.bat`, `start-gui.command`.
* README starts with a beginner guide; CONTRIBUTING describes the code layout.

## 0.1.0 - 2026-10-22

* GUI: Schedule section with tabs for Linux, Windows and macOS (shared code in `ra_wtp_schedule.py`).
* Syncthing instructions moved into an optional add-on section.
* CI: current action versions, least-privilege permissions, timeouts, concurrency, GUI smoke test.
