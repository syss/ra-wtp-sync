<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# ra-wtp-sync

Keep a folder of ROMs in sync with your [RetroAchievements](https://retroachievements.org)
**Want to Play** list.

Add a game to your list on the site and the matching ROM from *your own* library shows up
in an output folder; remove it and the copy is deleted again. Point any sync tool at that
folder (see the optional [Syncthing add-on](#add-on-syncthing)) and your handhelds and PCs
always carry exactly the games you want to play, with RA-compatible dumps.

It comes as a **script** (`ra_wtp_sync.py`, for the command line and schedulers) and a **GUI**
(`ra_wtp_gui.py`, a settings page in your browser). Both use the same config file.

* **Hash matching, not file names.** ROMs are matched against the hashes RetroAchievements
  registers for each game, so every file copied is one that earns achievements.
  Zips, headered NES/SNES dumps and byte-swapped N64 dumps are handled.
* **One ROM per game.** If several of your files are supported, one is chosen in an order you
  configure: existing save file → quality-of-life version → preferred language → preferred
  region → highest revision.
* **Your library stays untouched.** The source is only opened for reading. The output folder
  is owned by the tool, and only files it copied itself (tracked in a manifest) are ever deleted.
* **Safe by default.** An empty or suspiciously short answer from the API never deletes
  anything; copies are atomic (a sync tool never sees half-written files); runs are locked.
* **Every RetroAchievements console is available.** Select and deselect them in the config
  (`consoles = ["all"]`, `exclude_consoles`), on the command line, or in the settings GUI.
* **Auto-patching.** Hacks, fixes and translations that RetroAchievements publishes as official
  patches are built from base ROMs in your library when they are missing, and verified by hash.
* **Missing-ROMs list.** Every run writes `missing-roms.txt`: each wanted game that could not be
  provided, with the reason and a link.
* Standard library only, Python 3.11+. No ROMs are downloaded or distributed - you bring your own.

## Requirements

Python 3.11 or newer on **Linux, Windows or macOS** (tested on all three by CI). Nothing else to
install. Below, `python` means `python3` on Linux/macOS and `py` (or `python`) on Windows.

## Start here (no experience needed)

1. **Get the files.** On this page click the green **Code** button → **Download ZIP**, and unzip it
   somewhere (or `git clone` it).
2. **Install Python 3.11 or newer** if you do not have it yet (free, from
   [python.org/downloads](https://www.python.org/downloads/); on Windows tick *Add python.exe to PATH*).
3. **Open the settings page** by starting the launcher for your system:

   | System | Do this |
   | --- | --- |
   | Linux | run `./start-gui.sh` (or `python3 ra_wtp_gui.py`) |
   | Windows | double-click `start-gui.bat` |
   | macOS | double-click `start-gui.command` (first time: right-click → Open) |

4. In the page that opens in your browser, follow the four numbered steps: account, folders, systems,
   *Save & preview*. The page explains where to find your API key and has a **Test login** button.
   Then press **Save & run now**, and optionally set up the **Schedule** at the bottom (one tab per
   system, in the order Linux, Windows, macOS; your own system's tab installs the job with one click).

The page is served on 127.0.0.1 only and needs no extra packages.

**Your API key**, in short: log in at [retroachievements.org](https://retroachievements.org) →
[Settings](https://retroachievements.org/settings) → **Keys** → copy the **Web API Key**. It is 32 letters
and digits, for example `Ab3dE5fGh7Ij9kLm1nOp3qRs5tUv7wXy` (an example; yours is different). The tool
only uses it to read your Want to Play list and stores it in a file only your user can read.

## Command line

Guided setup in the terminal (asks the same questions, writes `config.toml`, does a dry run and can
schedule the sync): `python setup.py`. Or by hand:

```
git clone <this repo>
cd ra-wtp-sync
cp examples/config.example.toml config.toml     # Windows: copy examples\config.example.toml config.toml
# edit config.toml: username, src, dest, ...
python ra_wtp_sync.py --dry-run                  # see what would happen
python ra_wtp_sync.py                            # do it
```

API key: either `export RA_API_KEY=...` (Windows: `set RA_API_KEY=...`) or save it, and nothing else, in
a file named by `api_key_file` (default: `api_key` in your user config folder, see below; a relative
path is resolved next to the script). `setup.py` and the GUI do this for you and restrict the file to
your user.

The first run hashes your library (cached afterwards, keyed by size and mtime), so it can take a
few minutes for a big collection; later runs only look at new files.

### New folder or your existing ROM folder

* **New folder** (default, recommended): `dest` is owned by the tool, one sub-folder per system.
* **Merge** (`dest_mode = "merge"`, `--merge`, or the second option in the GUI / `setup.py`): `dest` is your
  *existing* ROM folder, for example your frontend's or a handheld's SD card. A system folder that already
  exists is reused whatever its spelling (`GBA`, `Game Boy Advance`, `gba` ...), otherwise one is created.
  A file that is already there is never overwritten (it is left alone and never deleted), and only files this
  tool added itself are removed when a game leaves your list; folders are never removed. The library
  (`src`) may live inside that folder, but must not be the same folder. Try it with `--dry-run` first.

### Where things live

| | Linux | Windows | macOS |
| --- | --- | --- | --- |
| config (after `config.toml` next to the script) | `~/.config/ra-wtp-sync/` | `%APPDATA%\ra-wtp-sync\` | `~/Library/Application Support/ra-wtp-sync/` |
| cache, manifest, report | `~/.local/state/ra-wtp-sync/` | `%LOCALAPPDATA%\ra-wtp-sync\` | `~/Library/Application Support/ra-wtp-sync/state/` |

(`XDG_CONFIG_HOME` / `XDG_STATE_HOME` are honoured on Linux; override any of it with `state_dir`,
`api_key_file`, `--config`.)

### Run it regularly

`python setup.py` and the GUI's *Schedule* section do this for you. By hand:

* **Linux** - cron: `*/10 * * * * python3 /path/to/ra-wtp-sync/ra_wtp_sync.py`, or the systemd user
  timer in `examples/` (`ra-wtp-sync.service` + `ra-wtp-sync.timer`, copy to `~/.config/systemd/user/`,
  then `systemctl --user enable --now ra-wtp-sync.timer`).
* **Windows** - Task Scheduler:
  `schtasks /Create /TN ra-wtp-sync /SC MINUTE /MO 10 /TR "pythonw C:\path\to\ra_wtp_sync.py --if-changed"`.
* **macOS** - a LaunchAgent (`setup.py` writes `~/Library/LaunchAgents/ra-wtp-sync.plist` and loads it).

**How the schedule stays cheap.** The jobs run every 10 minutes with `--if-changed`: each run asks RetroAchievements
for your Want to Play list (one API call), compares it and your settings with the last full run, and **stops
right there if nothing changed**. A full pass (library scan, copying, deleting, patching) happens when the list
or a setting changed, after a failed run, and at least every `full_run_hours` (default 24) so that new files in
your library are found. After adding ROMs to your library, run `python ra_wtp_sync.py` once by hand (or press
*Save & run now* in the GUI) to pick them up immediately.

Overlapping runs are prevented by a lock, so a slow first run never collides with the next one.

### Platform notes

* **Windows:** enable long paths if your library has very deep folders. File names that are not
  valid on Windows (for example containing `:`) cannot exist in the library there; patched ROMs
  are named safely. The output folder is plain files, so any sync tool works.
* **macOS:** if you get a TLS certificate error with a python.org Python, run *Install
  Certificates.command* from the Python folder in Applications (or `pip install certifi`).
  File names are compared Unicode-normalised, so accented names match on macOS volumes.
* **All:** source and output must be different folders; the library is only opened for reading.

## Configuration

Settings come from (highest first) command line, `$RA_USER` / `$RA_API_KEY`, the config file
(`--config`, `$RA_WTP_CONFIG`, a `config.toml` next to the script - git-ignored - or one in your user config folder), then defaults.
`examples/config.example.toml` documents every option:

| option | meaning |
| --- | --- |
| `username` | your RetroAchievements user name |
| `api_key_file` / `$RA_API_KEY` | Web API key |
| `src` | ROM library (read-only) |
| `dest` | output folder, one sub-folder per system |
| `state_dir` | hash cache, manifest, report |
| `saves` | folders searched for existing save files |
| `include_started` | also keep games you started but have not beaten |
| `consoles`, `exclude_consoles` | which systems to handle: keys, ids or `"all"` |
| `build_patches`, `patched_dir` | build missing hacks from official RA patches |
| `report_file` | where the missing-ROMs list is written |
| `preferences` | `order`, `languages`, `regions` used to pick one ROM per game |
| `console_overrides.<name>` | other folder names / extra systems (`id`, `dirs`, `hash`) |
| `max_rom_mb`, `hash_cache_days`, `min_list_fraction` | limits and safety net |

### Consoles

`python ra_wtp_sync.py --list-consoles` shows every console RetroAchievements supports (about 55),
which ones are selected, and how each is matched:

* **hash** - cartridge based systems: the file's content hash must equal one RA registered, so only
  ROMs that earn achievements are copied (zip members, NES/SNES/Lynx/7800 headers and byte-swapped
  N64 dumps are handled).
* **hash**, special cases handled: NES/FDS/Lynx/7800/SNES/PC Engine headers, byte-swapped N64, and Arduboy `.hex`
  files (RA hashes them after converting to Unix line endings; `.arduboy` packages are read via their hex).
* **title** - discs and other formats RA hashes in its own way (PlayStation, PS2, PSP, GameCube,
  Wii, DS/DSi/3DS, Saturn, Dreamcast, Sega CD, arcade, ...): matched by game title or an
  `[RA 1234]` tag in the file name. Less strict, and the files can be huge. Use single-file
  formats (CHD, ISO, RVZ, WBFS, zip, NDS, ...); `.cue/.bin` sets are not supported.

By default only small cartridge systems are selected. Choose differently with
`consoles = [...]` / `["all"]` / `exclude_consoles = [...]` in the config, `--consoles` /
`--exclude` on the command line, or the GUI. Source folders are found by name below `src`
(case and punctuation are ignored; aliases like `sfc`/`snes`/`super-nintendo` are built in);
map yours with `console_overrides`.

### Arduboy

Arduboy games are `.hex` files (RetroAchievements hashes them with Unix line endings) and are matched by
hash like any cartridge system. The easy way to get a library: import the community collection
[Erwin's Arduboy Collection](https://github.com/eried/ArduboyCollection) into your `arduboy` folder, e.g.

```
git clone --depth 1 https://github.com/eried/ArduboyCollection.git /path/to/library/arduboy/ArduboyCollection
```

(per-game licenses vary, see the collection). The tool finds the right `.hex` files inside by hash; nothing is
ever downloaded by the tool itself. Its catalog (`repo.json`, offered by the maintainer as a data source) is
only read to show the **author's page and license** next to a missing game in the report.

### Which version was chosen

Besides `missing-roms.txt`, every run writes `installed-roms.txt` (same folder): one entry per game with the
file that was copied and the version RetroAchievements registers for it, e.g. `RA Fix: Pokemon Emerald [32-bit
RTC+TIMER RNG fix]`, `RA base ROM (no fix or improvement applied) - RA also registers: Fix`, or *matched by title*
for disc systems. The run log shows the same after each `ADD`, and the info is kept in the manifest.
A plain ROM can still win over the fix when it already has a save file (rule order above), and a built fix
is named `... [QoL fix] [RA <id>]`.

### Missing ROMs, links and patches

Each run writes `missing-roms.txt` (in the state folder, or `report_file`):

1. **ROMs to look for** - at the top, one line per wanted game that was not provided: the MD5 and
   file name RetroAchievements registered for it, so you can recognise the right dump.
2. **Base ROMs the official patches need** - when a patch exists but its base ROM is not in your
   library, the exact dump (name and hash from the patch's readme).
3. **Details** - the reason for each game (not in the library; patch needs a base ROM you lack; patch
   format unsupported; no patch published), the RetroAchievements page, and where to get it:
   the author's page for homebrew from `sources.json`, otherwise an itch.io search link for
   homebrew/demos and the community hub for Pokémon Mini. `sources.json` ships with the author pages
   found so far; add yours (`{"<RA game id>": {"url": "...", "note": "..."}}`) or use `sources_file`.

Missing hacks and fixes are built from RA's official patches (RAPatches repo; IPS/BPS/UPS, also inside
.7z) using base ROMs from your library, and verified against RA's hash before they are used.

**Variants of games you own.** RA also registers patched versions of some games (fixes,
improvements, translations). By default (`build_variants = ["Fix", "Improvement"]`; `[]` turns it off) those are built from your
own ROM too and preferred over the plain one (rule order: save file, QoL, language, region,
revision), e.g. Pokémon Emerald's *32-bit RTC+TIMER RNG fix*. A ROM already in your library whose hash RA
registers as a fix or improvement counts as the QoL version too, whatever its file name. Only patches RA registered keep
achievements working: any other patch changes the hash and RA will not recognise the game.

## Limitations

* Disc based systems are available but off by default: their files are big.
* Only ROMs that exist in your library can be copied. Hacks and homebrew need the patched ROM, or
  the base ROM plus an official RA patch in IPS, BPS or UPS format (also inside .7z); other patch formats (xdelta, ppf) are
  reported as unsupported.
* `.zip` is handled natively; `.7z` archives (ROMs in your library and RA patches) need an extractor:
  `pip install py7zr`, or 7-Zip / bsdtar on the PATH (macOS and Windows ship `tar`=bsdtar; most Linux
  distributions have `7z`). Other archive types (rar, ...) are not inspected.
* Language/region/QoL detection is based on file-name conventions (No-Intro, GoodTools).

## Add-ons

Optional; nothing above depends on them.

### Add-on: Syncthing

[Syncthing](https://syncthing.net) (or Resilio, OneDrive, rsync, ...) can carry the output folder to your
handhelds and other PCs. This tool only fills the folder.

* Share `dest` as a normal send & receive folder (or send-only from the machine running this).
  When a game leaves your list, the file is deleted in `dest` and Syncthing propagates it.
* Keep the folder's file versioning setting in mind if you want deletions to be final.
* Copies are written through a `.syncthing.<name>.tmp` file, which Syncthing ignores, so peers never
  see a half-written ROM.
* Keep `state_dir` (hash cache, manifest) outside the shared folder; it is not meant to be synced.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the code layout and how to add a console, a language or a platform.

## Development

```bash
python3 tests/run_ci.py      # unit tests
python3 tests/gui_smoke.py   # starts the GUI and queries it
```

## License

GPL-3.0-or-later - see [LICENSE](LICENSE). Every source file carries an SPDX header.
This project is not affiliated with RetroAchievements.
