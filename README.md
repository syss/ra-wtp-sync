<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# ra-wtp-sync

Keep a folder of ROMs in sync with your [RetroAchievements](https://retroachievements.org)
**Want to Play** list.

Add a game to your list on the site and the matching ROM from *your own* library shows up
in an output folder; remove it and the copy is deleted again. Point
[Syncthing](https://syncthing.net) (or any other sync tool) at that folder and your
handhelds and PCs always carry exactly the games you want to play, with RA-compatible dumps.

* **Hash matching, not file names.** ROMs are matched against the hashes RetroAchievements
  registers for each game, so every file copied is one that earns achievements.
  Zips, headered NES/SNES dumps and byte-swapped N64 dumps are handled.
* **One ROM per game.** If several of your files are supported, one is chosen in an order you
  configure: existing save file → quality-of-life version → preferred language → preferred
  region → highest revision.
* **Your library stays untouched.** The source is only opened for reading. The output folder
  is owned by the tool, and only files it copied itself (tracked in a manifest) are ever deleted.
* **Safe by default.** An empty or suspiciously short answer from the API never deletes
  anything; copies are atomic (Syncthing never sees half-written files); runs are locked.
* Standard library only, Python 3.11+. No ROMs are downloaded or distributed - you bring your own.

## Quick start

```bash
git clone <this repo> && cd ra-wtp-sync
cp examples/config.example.toml config.toml          # git-ignored: your settings stay local
$EDITOR config.toml                                  # username, src, dest, ...

# API key from https://retroachievements.org/settings (Keys). Stored with mode 600:
umask 077 && read -rsp "RA API key: " k && printf %s "$k" > api_key     # git-ignored too
# (default api_key_file is ~/.config/ra-wtp-sync/api_key; a relative path is resolved next to the script)

./ra_wtp_sync.py --dry-run   # see what would happen
./ra_wtp_sync.py             # do it
```

The first run hashes your library (cached afterwards, keyed by size and mtime), so it can take a
few minutes for a big collection; later runs only look at new files.
Games on your list that are not in your library are written to `not-in-library.txt` in the
state directory.

### Run it regularly

systemd (user):

```bash
install -Dm644 examples/ra-wtp-sync.service ~/.config/systemd/user/ra-wtp-sync.service
install -Dm644 examples/ra-wtp-sync.timer   ~/.config/systemd/user/ra-wtp-sync.timer
systemctl --user enable --now ra-wtp-sync.timer
```

or cron: `*/30 * * * * /usr/bin/python3 /path/to/ra-wtp-sync/ra_wtp_sync.py >> ~/.local/state/ra-wtp-sync/log 2>&1`

## Configuration

Settings come from (highest first) command line, `$RA_USER` / `$RA_API_KEY`, the config file
(`--config`, `$RA_WTP_CONFIG`, a `config.toml` next to the script - git-ignored - or `~/.config/ra-wtp-sync/config.toml`), then defaults.
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
| `consoles` | which systems to handle - leave out the big disc based ones |
| `preferences` | `order`, `languages`, `regions` used to pick one ROM per game |
| `console_overrides.<name>` | other folder names / extra systems (`id`, `dirs`, `hash`) |
| `max_rom_mb`, `hash_cache_days`, `min_list_fraction` | limits and safety net |

Built-in systems: Mega Drive/Genesis, N64, SNES, Game Boy, Game Boy Color, GBA, NES,
PC Engine, Master System, Game Gear, Pokémon Mini, Virtual Boy, Channel F, Atari 2600.
Source folders are found by name below `src` (case-insensitive, aliases such as `sfc`/`snes`
are built in); map yours with `console_overrides`.

## Syncthing notes

* Share `dest` as a normal send & receive folder (or send-only from the machine running this).
  When a game leaves your list, the file is deleted in `dest` and Syncthing propagates it.
* Keep the file versioning setting of the folder in mind if you want deletions to be final.
* Disc based systems (PlayStation, GameCube, Wii, ...) are deliberately not built in.

## Limitations

* Only ROMs that exist in your library can be copied. Hacks and homebrew need the patched ROM
  to be present; RA's published patches are not applied by this tool.
* Compressed formats other than `.zip` (e.g. `.7z`) are not inspected.
* Language/region/QoL detection is based on file-name conventions (No-Intro, GoodTools).

## Development

```bash
python3 -m unittest discover -s tests
```

## License

GPL-3.0-or-later - see [LICENSE](LICENSE). Every source file carries an SPDX header.
This project is not affiliated with RetroAchievements.
