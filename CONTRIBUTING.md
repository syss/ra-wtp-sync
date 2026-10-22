<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Contributing

Thanks for helping! The project is deliberately **plain Python 3.11+, standard library only**: that
is the language almost everybody who wants to tweak it already has installed, it runs unchanged on
Linux, Windows and macOS, and there is no build step. Please do not add dependencies.

## Code layout

| File | What it does |
| --- | --- |
| `ra_wtp_sync.py` | the engine and command line: RA API, hashing, matching, copying, patching, report |
| `ra_wtp_gui.py` | the browser settings page: a tiny local web server plus one HTML/JS page (the `PAGE` string at the bottom) |
| `ra_wtp_schedule.py` | installs the recurring job: cron (Linux), Task Scheduler (Windows), LaunchAgent (macOS) |
| `setup.py` | guided terminal setup |
| `start-gui.sh` / `.bat` / `.command` | double-click launchers that check for Python and open the GUI |
| `sources.json` | author/download pages shown in the missing-ROMs report |
| `tests/` | unit tests (`run_ci.py`) and a GUI smoke test (`gui_smoke.py`) |
| `examples/` | example config and systemd units |

## Common changes

* **Add or fix a console's folder names:** the `KNOWN_CONSOLES` table in `ra_wtp_sync.py` (the last list in each entry holds folder aliases) and a test in `tests/test_ra_wtp_sync.py`.
* **Add a language or region:** `LANGS` / `REGIONS` at the top of `ra_wtp_gui.py` and `LANGUAGES` / `REGIONS` (detection patterns) in `ra_wtp_sync.py`.
* **Change the settings page:** edit `PAGE` in `ra_wtp_gui.py` (plain HTML/CSS/JS, no framework), then run `python ra_wtp_gui.py`.
* **Translate the UI:** not available yet; a good first contribution is moving the page text into a small dictionary.

## Before opening a PR

```
python tests/run_ci.py
python tests/gui_smoke.py
```

* Must work on Linux, Windows and macOS (CI runs all three).
* Never add code that downloads or ships ROMs; the tool only works with the user's own library.
* Every source file carries the SPDX header (`GPL-3.0-or-later`).
* Platform lists and tabs use the order **Linux, Windows, macOS**.
