#!/usr/bin/env python3
# ra-wtp-sync - mirror a RetroAchievements "Want to Play" list into a folder
# Copyright (C) 2026 ra-wtp-sync contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
"""ra-wtp-sync: mirror a RetroAchievements "Want to Play" list into a folder.

Point it at a (read-only) ROM library and an output folder, e.g. one that
Syncthing shares with your handhelds. It copies one RA-compatible ROM per game
on your Want to Play list and removes the copy again when you take the game off
the list.

* The library is only ever opened for reading.
* ROMs are matched by RetroAchievements hash, never by file name, so every copied
  file is one that actually earns achievements.
* Only files this tool copied (tracked in a manifest) are ever deleted.

Standard library only; Python 3.11+.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

__version__ = "1.0.0"

API = "https://retroachievements.org/API/"
USER_AGENT = f"ra-wtp-sync/{__version__} (+https://github.com/)"

# name -> RA console id, folder names to look for below `src` (case-insensitive),
# and how RA hashes that system's ROMs ("plain", "nes", "snes", "n64").
DEFAULT_CONSOLES: dict[str, dict] = {
    "megadrive":  {"id": 1,  "hash": "plain", "dirs": ["megadrive", "genesis", "smd", "mdrive"]},
    "n64":        {"id": 2,  "hash": "n64",   "dirs": ["n64", "64dd"]},
    "snes":       {"id": 3,  "hash": "snes",  "dirs": ["snes", "sfc", "sfam", "superfamicom"]},
    "gb":         {"id": 4,  "hash": "plain", "dirs": ["gb", "gameboy"]},
    "gba":        {"id": 5,  "hash": "plain", "dirs": ["gba", "gameboyadvance"]},
    "gbc":        {"id": 6,  "hash": "plain", "dirs": ["gbc", "gameboycolor"]},
    "nes":        {"id": 7,  "hash": "nes",   "dirs": ["nes", "famicom", "fds"]},
    "pcengine":   {"id": 8,  "hash": "snes",  "dirs": ["pcengine", "turbografx", "tg16", "pce"]},
    "mastersystem": {"id": 11, "hash": "plain", "dirs": ["mastersystem", "sms"]},
    "gamegear":   {"id": 15, "hash": "plain", "dirs": ["gamegear", "gg"]},
    "pokemini":   {"id": 24, "hash": "plain", "dirs": ["pokemini", "pokemon-mini"]},
    "virtualboy": {"id": 28, "hash": "plain", "dirs": ["virtualboy", "vb"]},
    "channelf":   {"id": 57, "hash": "plain", "dirs": ["fairchild-channel-f", "channelf"]},
    "atari2600":  {"id": 25, "hash": "plain", "dirs": ["atari2600", "a2600"]},
}

DEFAULTS = {
    "username": "",
    "api_key_file": "~/.config/ra-wtp-sync/api_key",
    "src": "",
    "dest": "",
    "state_dir": "~/.local/state/ra-wtp-sync",
    "saves": [],
    "include_started": False,
    "consoles": list(DEFAULT_CONSOLES),
    "max_rom_mb": 200,
    "hash_cache_days": 7,
    "min_list_fraction": 0.5,
    "preferences": {
        # Earlier entries win. Choose from: save, qol, language, region, revision
        "order": ["save", "qol", "language", "region", "revision"],
        "languages": [],   # e.g. ["de", "en"]  (first = most wanted)
        "regions": [],     # e.g. ["europe", "usa"]
    },
}

SKIP_EXT = (".json", ".txt", ".nfo", ".xml", ".png", ".jpg", ".srm", ".sav", ".state",
            ".cfg", ".ips", ".bps", ".ups", ".part", ".tmp")
SAVE_EXT = (".srm", ".sav", ".eep", ".dsv", ".fla", ".mpk", ".rtc", ".sra")

# Heuristics on file names (No-Intro / GoodTools / hack conventions).
QOL_RE = re.compile(r"qol|quality.of.life|enhanc|improv|restor|bug.?fix|\bfix(ed|es)?\b|"
                    r"widescreen|remaster|uncensor|no.glitch|patched", re.I)
LANGUAGES = {
    "de": r"germany|[(,]\s?de\b|\((g|d)\)",
    "fr": r"france|[(,]\s?fr\b|\(f\)",
    "es": r"spain|[(,]\s?es\b|\(s\)",
    "it": r"italy|[(,]\s?it\b|\(i\)",
    "ja": r"japan|[(,]\s?ja\b|\(j\)",
    "en": r"usa|world|[(,]\s?en\b|\((u|ue|jue)\)",
}
REGIONS = {
    "europe": r"europe|\((e|eu|eur|ue|pal)\)",
    "usa": r"usa|\((u|ue|jue)\)",
    "japan": r"japan|\(j\)",
    "world": r"world|\(w\)",
}


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


# --------------------------------------------------------------------------- config
def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(args) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    explicit = args.config or os.environ.get("RA_WTP_CONFIG")
    # first existing wins: explicit, config.toml next to the script (git-ignored), user config dir
    candidates = [Path(explicit).expanduser()] if explicit else [
        Path(__file__).resolve().parent / "config.toml",
        Path("~/.config/ra-wtp-sync/config.toml").expanduser(),
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path:
        with open(path, "rb") as f:
            cfg = deep_merge(cfg, tomllib.load(f))
    elif explicit:
        sys.exit(f"config file not found: {candidates[0]}")
    # environment, then CLI, override the file
    if os.environ.get("RA_USER"):
        cfg["username"] = os.environ["RA_USER"]
    for key in ("username", "src", "dest", "state_dir", "api_key_file"):
        val = getattr(args, key, None)
        if val:
            cfg[key] = val
    if args.saves:
        cfg["saves"] = args.saves
    if args.consoles:
        cfg["consoles"] = [c.strip() for c in args.consoles.split(",") if c.strip()]
    if args.include_started:
        cfg["include_started"] = True
    return cfg


def console_table(cfg) -> dict:
    """Default consoles overlaid with [consoles.<name>] tables from the config."""
    table = json.loads(json.dumps(DEFAULT_CONSOLES))
    for name, over in (cfg.get("console_overrides") or {}).items():
        table[name] = deep_merge(table.get(name, {"hash": "plain", "dirs": [name]}), over)
    for name in cfg["consoles"]:
        if name not in table or "id" not in table[name]:
            sys.exit(f"unknown console '{name}' - define it as [console_overrides.{name}] with id/dirs/hash")
    return {n: table[n] for n in cfg["consoles"]}


# --------------------------------------------------------------------------- RA API
def api(endpoint: str, key: str, **params):
    q = urllib.parse.urlencode({"y": key, **params})
    req = urllib.request.Request(f"{API}{endpoint}?{q}", headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"RetroAchievements rejected the request (HTTP {e.code}): check username / API key")
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, ValueError, TimeoutError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"RA API call failed: {endpoint}")


def paged(endpoint, key, user, **extra):
    off, results = 0, []
    while True:
        r = api(endpoint, key, z=user, u=user, c=500, o=off, **extra)
        results += r.get("Results", [])
        off += 500
        if off >= r.get("Total", 0):
            return results


def fetch_games(key, user, include_started) -> dict[int, tuple[str, int]]:
    games = {}
    for g in paged("API_GetUserWantToPlayList.php", key, user):
        games[g["ID"]] = (g["Title"], g["ConsoleID"])
    if include_started:
        for g in paged("API_GetUserCompletionProgress.php", key, user):
            if not g.get("HighestAwardKind"):
                games[g["GameID"]] = (g["Title"], g["ConsoleID"])
    return {i: v for i, v in games.items() if "[Subset" not in v[0]}


# --------------------------------------------------------------------------- hashing
def md5s(data: bytes, style: str) -> set[str]:
    """MD5s RetroAchievements could have registered for this ROM image."""
    out = {hashlib.md5(data).hexdigest()}
    if style == "nes" and data[:4] == b"NES\x1a":
        out.add(hashlib.md5(data[16:]).hexdigest())
    elif style == "snes" and len(data) % 1024 == 512:
        out.add(hashlib.md5(data[512:]).hexdigest())
    elif style == "n64":
        head = data[:4]
        if head == b"\x37\x80\x40\x12":        # .v64 byte-swapped pairs
            a = bytearray(data)
            a[0::2], a[1::2] = data[1::2], data[0::2]
            out.add(hashlib.md5(a).hexdigest())
        elif head == b"\x40\x12\x37\x80":      # .n64 little-endian words
            a = bytearray(data)
            a[0::4], a[1::4], a[2::4], a[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
            out.add(hashlib.md5(a).hexdigest())
    return out


def file_hashes(path: str, style: str, max_bytes: int) -> set[str]:
    res: set[str] = set()
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    if i.is_dir() or i.filename.lower().endswith(SKIP_EXT) or not 1000 < i.file_size < max_bytes:
                        continue
                    res |= md5s(z.read(i), style)
        elif os.path.getsize(path) < max_bytes:
            with open(path, "rb") as f:
                res = md5s(f.read(), style)
    except Exception as e:  # corrupt archive, permission problem, ...
        log("  skipped", path, f"({e})")
    return res


def open_db(state: Path) -> sqlite3.Connection:
    db = sqlite3.connect(state / "cache.db")
    db.execute("create table if not exists files(path text primary key, size int, mtime real, hashes text)")
    db.execute("create table if not exists ghash(id int primary key, fetched real, hashes text)")
    return db


def find_dirs(src: Path, names: list[str]) -> list[Path]:
    wanted = {n.lower() for n in names}
    return [p for p in sorted(src.iterdir()) if p.is_dir() and p.name.lower() in wanted]


def index_console(db, src: Path, spec: dict, max_bytes: int) -> dict[str, list[str]]:
    """{md5: [paths]} for every ROM of one system; hashes are cached by size+mtime."""
    idx: dict[str, list[str]] = {}
    fresh = 0
    for root in find_dirs(src, spec["dirs"]):
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.startswith(".") or fn.lower().endswith(SKIP_EXT):
                    continue
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if not os.path.isfile(p):
                    continue
                row = db.execute("select size,mtime,hashes from files where path=?", (p,)).fetchone()
                if row and row[0] == st.st_size and row[1] == st.st_mtime:
                    hs = json.loads(row[2])
                else:
                    hs = sorted(file_hashes(p, spec["hash"], max_bytes))
                    db.execute("insert or replace into files values(?,?,?,?)",
                               (p, st.st_size, st.st_mtime, json.dumps(hs)))
                    fresh += 1
                    if fresh % 50 == 0:
                        db.commit()
                for h in hs:
                    idx.setdefault(h, []).append(p)
    db.commit()
    return idx


def game_hashes(db, key, gid: int, ttl_days: float) -> set[str]:
    row = db.execute("select fetched,hashes from ghash where id=?", (gid,)).fetchone()
    if row and time.time() - row[0] < ttl_days * 86400:
        return set(json.loads(row[1]))
    r = api("API_GetGameHashes.php", key, i=gid)
    hs = [h["MD5"].lower() for h in r.get("Results", [])]
    db.execute("insert or replace into ghash values(?,?,?)", (gid, time.time(), json.dumps(hs)))
    db.commit()
    time.sleep(0.3)  # be polite to the API
    return set(hs)


# --------------------------------------------------------------------------- choosing a ROM
def stem(name: str) -> str:
    return os.path.splitext(name)[0]


def revision(name: str) -> float:
    best = 0.0
    m = re.search(r"Rev\s?([A-Z]|\d+)", name, re.I)
    if m:
        v = m.group(1)
        best = max(best, (ord(v.upper()) - 64) if v.isalpha() else int(v))
    m = re.search(r"\bv-?\s?(\d+(?:\.\d+)?)", name, re.I)
    if m:
        best = max(best, float(m.group(1)))
    m = re.search(r"PRG(\d)", name)
    if m:
        best = max(best, int(m.group(1)))
    return best


def match_rank(name: str, wanted: list[str], table: dict[str, str]) -> int:
    """0 = best. Index of the first preferred language/region found in the name."""
    for i, w in enumerate(wanted):
        if w in table and re.search(table[w], name, re.I):
            return i
    return len(wanted) + 1


def rank(path: str, saved: set[str], prefs: dict) -> tuple:
    """Sort key (lower = preferred) following prefs['order']."""
    n = os.path.basename(path)
    crit = {
        "save": not (stem(n) in saved or stem(stem(n)) in saved),
        "qol": not QOL_RE.search(n),
        "language": match_rank(n, [x.lower() for x in prefs["languages"]], LANGUAGES),
        "region": match_rank(n, [x.lower() for x in prefs["regions"]], REGIONS),
        "revision": -revision(n),
    }
    return tuple(crit[c] for c in prefs["order"] if c in crit) + (len(n), n)


def load_saves(dirs: list[str]) -> set[str]:
    names: set[str] = set()
    for d in dirs:
        for dp, _, fns in os.walk(os.path.expanduser(d)):
            names.update(stem(fn) for fn in fns if fn.lower().endswith(SAVE_EXT))
    return names


# --------------------------------------------------------------------------- file ops
def safe_remove(dest: Path, rel: str) -> None:
    p = Path(os.path.realpath(dest / rel))
    if dest not in p.parents:
        return
    if p.exists():
        p.unlink()
    try:
        if p.parent != dest and not any(p.parent.iterdir()):
            p.parent.rmdir()
    except OSError:
        pass


def copy_atomic(src: str, tgt: Path) -> None:
    tgt.parent.mkdir(parents=True, exist_ok=True)
    # Syncthing ignores .syncthing.*.tmp, so peers never see a half-written file.
    tmp = tgt.parent / f".syncthing.{tgt.name}.tmp"
    shutil.copyfile(src, tmp)  # src is opened read-only
    os.replace(tmp, tgt)


# --------------------------------------------------------------------------- main
def sync(cfg: dict, dry_run: bool) -> int:
    if not cfg["username"]:
        sys.exit("no RetroAchievements username (config 'username', --username or $RA_USER)")
    if not cfg["src"] or not cfg["dest"]:
        sys.exit("'src' (ROM library) and 'dest' (output folder) are required")
    key = os.environ.get("RA_API_KEY")
    if not key:
        kf = Path(cfg["api_key_file"]).expanduser()
        if not kf.is_absolute():
            kf = Path(__file__).resolve().parent / kf
        if not kf.exists():
            sys.exit(f"no API key: set $RA_API_KEY or put it in {kf}")
        key = kf.read_text().strip()

    src, dest = Path(cfg["src"]).expanduser().resolve(), Path(cfg["dest"]).expanduser().resolve()
    if dest == src or src in dest.parents or dest in src.parents:
        sys.exit("'src' and 'dest' must be separate folders (src is never written to)")
    if not src.is_dir():
        sys.exit(f"src is not a directory: {src}")
    state = Path(cfg["state_dir"]).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    lock = open(state / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another run is in progress")

    table = console_table(cfg)
    by_id = {spec["id"]: name for name, spec in table.items()}
    max_bytes = int(cfg["max_rom_mb"]) * 1024 * 1024
    mf_path = state / "manifest.json"
    manifest: dict = json.loads(mf_path.read_text()) if mf_path.exists() else {}

    games = fetch_games(key, cfg["username"], cfg["include_started"])
    wanted = {i: v for i, v in games.items() if v[1] in by_id}
    log(f"RA list: {len(games)} games, {len(wanted)} on selected consoles")
    # Outage guard: an empty/short answer must never wipe the library.
    if not games or (len(manifest) > 10 and len(wanted) < cfg["min_list_fraction"] * len(manifest)):
        sys.exit("RA list empty or suspiciously small - refusing to change anything")

    saved = load_saves(cfg["saves"])
    if cfg["saves"]:
        log(f"{len(saved)} save files found for ROM preference")
    db = open_db(state)
    indexes: dict[int, dict] = {}
    plan: dict[str, tuple[str, str, str]] = {}
    missing: list[tuple[int, str, str]] = []
    for gid, (title, cid) in sorted(wanted.items()):
        name = by_id[cid]
        hs = game_hashes(db, key, gid, cfg["hash_cache_days"])
        if cid not in indexes:
            log("indexing", name)
            indexes[cid] = index_console(db, src, table[name], max_bytes)
        cands = {p for h in hs for p in indexes[cid].get(h, [])}
        if not cands:
            missing.append((gid, title, name))
            continue
        plan[str(gid)] = (title, name, min(cands, key=lambda p: rank(p, saved, cfg["preferences"])))

    for gid, (title, name, p) in plan.items():
        rel = f"{name}/{os.path.basename(p)}"
        tgt = dest / rel
        old = manifest.get(gid)
        if old and old["file"] == rel and tgt.exists() and tgt.stat().st_size == os.path.getsize(p):
            continue
        log("ADD", rel)
        if not dry_run:
            copy_atomic(p, tgt)
            if old and old["file"] != rel:
                safe_remove(dest, old["file"])
        manifest[gid] = {"title": title, "file": rel}

    for gid in [g for g in manifest if g not in plan and int(g) not in wanted]:
        log("DEL", manifest[gid]["file"], "-", manifest[gid]["title"])
        if not dry_run:
            safe_remove(dest, manifest[gid]["file"])
        manifest.pop(gid)

    if not dry_run:
        mf_path.write_text(json.dumps(manifest, indent=1))
        (state / "not-in-library.txt").write_text(
            "".join(f"{gid}\t{c}\t{t}\n" for gid, t, c in missing))
    log(f"done: {len(plan)} matched, {len(missing)} not in library"
        + ("" if dry_run else f" (list: {state / 'not-in-library.txt'})"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ra-wtp-sync", description=__doc__.split("\n\n")[0])
    ap.add_argument("-c", "--config", help="TOML config (default: config.toml next to the script, else ~/.config/ra-wtp-sync/config.toml)")
    ap.add_argument("--username", help="RetroAchievements user name")
    ap.add_argument("--api-key-file", dest="api_key_file", help="file holding the RA Web API key (or set $RA_API_KEY)")
    ap.add_argument("--src", help="ROM library (read-only)")
    ap.add_argument("--dest", help="output folder (e.g. your Syncthing folder)")
    ap.add_argument("--state-dir", dest="state_dir", help="cache + manifest location")
    ap.add_argument("--saves", nargs="*", help="save folders; a ROM that already has a save here is preferred")
    ap.add_argument("--consoles", help="comma separated console names, e.g. n64,snes,gba")
    ap.add_argument("--include-started", action="store_true", help="also keep games you started but have not beaten")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--version", action="version", version=__version__)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args)
    return sync(cfg, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
