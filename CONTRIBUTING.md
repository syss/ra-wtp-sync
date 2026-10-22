<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Contributing

* Python 3.11+, standard library only - please do not add dependencies.
* Must work on Linux, Windows and macOS (CI runs all three).
* Run before opening a PR:

  ```
  python tests/run_ci.py
  python tests/gui_smoke.py
  ```
* Never add code that downloads or ships ROMs; the tool only works with the user's own library.
* Every source file carries the SPDX header (`GPL-3.0-or-later`).
* Platform lists and tabs use the order **Linux, Windows, macOS**.
