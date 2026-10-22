<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Changelog

## Unreleased

* Explainer diagram (`docs/how-it-works.svg`) in the README and the GUI, including the optional Syncthing add-on.

* Scheduled runs: every 10 minutes by default with new `--if-changed`: one cheap API call, and a full pass only when the RA list or settings changed (or after `full_run_hours`, default 24).

* Official RA Fix/Improvement versions (e.g. Pokémon Emerald's RNG fix) are now built and preferred by default (`build_variants`); a library ROM whose hash RA registers as a fix counts as the QoL version.
* New `installed-roms.txt` and log/manifest info: which RA version of each game was chosen (GUI: *Show chosen versions*).

* New `dest_mode = "merge"` (`--merge`, GUI option): add games straight into an existing ROM folder, reusing its system folders and never overwriting or deleting files of your own.

* GUI is now a guided four-step page: API key help with an example and a Test login button, folder pickers, an Advanced section for the rest.
* Double-click launchers: `start-gui.sh`, `start-gui.bat`, `start-gui.command`.
* README starts with a beginner guide; CONTRIBUTING describes the code layout.

## 0.1.0 - 2026-10-22

* GUI: Schedule section with tabs for Linux, Windows and macOS (shared code in `ra_wtp_schedule.py`).
* Syncthing instructions moved into an optional add-on section.
* CI: current action versions, least-privilege permissions, timeouts, concurrency, GUI smoke test.
