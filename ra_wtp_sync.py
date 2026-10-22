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
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

__version__ = "1.0.0"

API = "https://retroachievements.org/API/"
USER_AGENT = f"ra-wtp-sync/{__version__} (+https://github.com/)"

# RA console id -> (short key, hash style, extra folder names looked up below `src`).
# Hash styles: plain | nes | fds | snes | n64 | lynx | a7800 | arduboy describe how RA hashes the ROM file;
# "name" is used for discs and other complex formats that RA hashes in its own way: those games
# are matched by title (or an "[RA 1234]" tag in the file name) instead, which is less strict.
# Consoles not listed here are still available under a key made from their RA name.
KNOWN_CONSOLES: dict[int, tuple[str, str, list[str]]] = {
    1: ("megadrive", "plain", ["genesis", "smd", "mdrive"]),
    2: ("n64", "n64", ["64dd"]),
    3: ("snes", "snes", ["sfc", "sfam", "superfamicom"]),
    4: ("gb", "plain", ["gameboy"]),
    5: ("gba", "plain", ["gameboyadvance"]),
    6: ("gbc", "plain", ["gameboycolor"]),
    7: ("nes", "nes", ["famicom"]),
    8: ("pcengine", "snes", ["turbografx", "tg16", "pce"]),
    9: ("segacd", "name", ["megacd", "scd"]),
    10: ("32x", "plain", ["sega32x"]),
    11: ("mastersystem", "plain", ["sms"]),
    12: ("psx", "name", ["ps1", "playstation", "psone"]),
    13: ("lynx", "lynx", ["atarilynx"]),
    14: ("ngp", "plain", ["ngpc", "neogeopocket", "neogeopocketcolor"]),
    15: ("gamegear", "plain", ["gg"]),
    16: ("gamecube", "name", ["gc", "ngc"]),
    17: ("jaguar", "plain", ["atarijaguar"]),
    18: ("nds", "name", ["ds"]),
    19: ("wii", "name", []),
    21: ("ps2", "name", ["playstation2"]),
    23: ("odyssey2", "plain", ["videopac"]),
    24: ("pokemini", "plain", []),
    25: ("atari2600", "plain", ["a2600"]),
    27: ("arcade", "name", ["mame", "fba", "fbneo"]),
    28: ("virtualboy", "plain", ["vb"]),
    33: ("sg1000", "plain", ["sg"]),
    39: ("saturn", "name", ["segasaturn"]),
    40: ("dreamcast", "name", ["dc"]),
    41: ("psp", "name", ["playstationportable"]),
    43: ("3do", "name", []),
    44: ("colecovision", "plain", ["coleco"]),
    49: ("pcfx", "name", []),
    51: ("atari7800", "a7800", []),
    53: ("wonderswan", "plain", ["ws", "wsc", "wonderswancolor"]),
    56: ("neogeocd", "name", []),
    57: ("channelf", "plain", []),
    62: ("3ds", "name", ["n3ds"]),
    63: ("supervision", "plain", []),
    71: ("arduboy", "arduboy", []),
    76: ("pcenginecd", "name", ["tgcd", "turbografxcd"]),
    77: ("jaguarcd", "name", []),
    78: ("dsi", "name", ["nintendodsi"]),
    81: ("fds", "fds", ["famicomdisksystem"]),
}
# Used when the RA console list cannot be fetched (offline).
FALLBACK_CONSOLES = {
    1: "Genesis/Mega Drive", 2: "Nintendo 64", 3: "SNES/Super Famicom", 4: "Game Boy",
    5: "Game Boy Advance", 6: "Game Boy Color", 7: "NES/Famicom", 8: "PC Engine/TurboGrafx-16",
    9: "Sega CD", 10: "32X", 11: "Master System", 12: "PlayStation", 13: "Atari Lynx",
    14: "Neo Geo Pocket", 15: "Game Gear", 16: "GameCube", 17: "Atari Jaguar", 18: "Nintendo DS",
    19: "Wii", 21: "PlayStation 2", 23: "Magnavox Odyssey 2", 24: "Pokemon Mini", 25: "Atari 2600",
    27: "Arcade", 28: "Virtual Boy", 29: "MSX", 33: "SG-1000", 37: "Amstrad CPC", 38: "Apple II",
    39: "Saturn", 40: "Dreamcast", 41: "PlayStation Portable", 43: "3DO Interactive Multiplayer",
    44: "ColecoVision", 45: "Intellivision", 46: "Vectrex", 47: "PC-8000/8800", 49: "PC-FX",
    51: "Atari 7800", 53: "WonderSwan", 56: "Neo Geo CD", 57: "Fairchild Channel F",
    63: "Watara Supervision", 69: "Mega Duck", 71: "Arduboy", 72: "WASM-4", 73: "Arcadia 2001",
    74: "Interton VC 4000", 75: "Elektor TV Games Computer", 76: "PC Engine CD/TurboGrafx-CD",
    77: "Atari Jaguar CD", 78: "Nintendo DSi", 80: "Uzebox", 81: "Famicom Disk System",
}
# Selected when the config does not say otherwise: cartridge based systems with small ROMs.
DEFAULT_CONSOLES = ["n64", "snes", "nes", "gb", "gbc", "gba", "megadrive", "mastersystem",
                    "gamegear", "pcengine", "pokemini", "virtualboy", "channelf", "atari2600"]

def _config_dir() -> Path:
    """Per-user config folder: XDG on Linux, Application Support on macOS, %APPDATA% on Windows."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ra-wtp-sync"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ra-wtp-sync"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ra-wtp-sync"


def _state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ra-wtp-sync"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ra-wtp-sync" / "state"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ra-wtp-sync"


CONFIG_DIR, STATE_DIR = _config_dir(), _state_dir()

DEFAULTS = {
    "username": "",
    "api_key_file": str(CONFIG_DIR / "api_key"),
    "src": "",
    "dest": "",
    "state_dir": str(STATE_DIR),
    "saves": [],
    "include_started": False,
    "consoles": list(DEFAULT_CONSOLES),   # keys, RA ids, or ["all"]
    "exclude_consoles": [],
    "report_file": "",       # missing-ROMs report (default: <state_dir>/missing-roms.txt)
    "max_rom_mb": 200,
    "hash_cache_days": 7,
    "min_list_fraction": 0.5,
    "build_patches": True,   # build missing hacks from RA's official patches
    "patched_dir": "",       # where built ROMs are kept (default: <state_dir>/patched)
    # RA also registers patched versions of games you may own (fixes, improvements, translations...).
    # List the patch categories to build for games already in your library, e.g. ["Fix", "Improvement"].
    "build_variants": [],
    "sources_file": "",      # extra JSON with free-download links (default: sources.json next to the script)
    "preferences": {
        # Earlier entries win. Choose from: save, qol, language, region, revision
        "order": ["save", "qol", "language", "region", "revision"],
        "languages": [],   # e.g. ["de", "en"]  (first = most wanted)
        "regions": [],     # e.g. ["europe", "usa"]
    },
}

SKIP_EXT = (".json", ".txt", ".nfo", ".xml", ".png", ".jpg", ".srm", ".sav", ".state",
            ".cfg", ".ips", ".bps", ".ups", ".part", ".tmp", ".ini", ".url", ".md", ".gif")
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


class FileLock:
    """Exclusive lock so two runs (cron + manual) never overlap; works on Linux, macOS and Windows."""

    def __init__(self, path: Path):
        self.path, self.f = path, None

    def acquire(self) -> bool:
        self.f = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.f.seek(0)
                if not self.f.read(1):
                    self.f.write("0")
                    self.f.flush()
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.release()
            return False
        return True

    def release(self) -> None:
        if self.f:
            self.f.close()   # closing drops the lock on every platform
            self.f = None


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


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
        CONFIG_DIR / "config.toml",
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
    if args.exclude:
        cfg["exclude_consoles"] = [c.strip() for c in args.exclude.split(",") if c.strip()]
    if args.no_patches:
        cfg["build_patches"] = False
    return cfg


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def build_console_table(names: dict[int, str]) -> dict[str, dict]:
    """{key: {id, hash, dirs, label}} for every RA console."""
    table: dict[str, dict] = {}
    for cid, label in sorted(names.items()):
        key, style, alias = KNOWN_CONSOLES.get(cid, (slug(label), "plain", []))
        table[key] = {"id": cid, "hash": style, "label": label,
                      "dirs": list(dict.fromkeys([key, slug(label), *alias]))}
    return table


def select_consoles(table: dict[str, dict], cfg: dict) -> dict[str, dict]:
    """Apply [console_overrides], `consoles` (keys, ids or "all") and `exclude_consoles`."""
    table = json.loads(json.dumps(table))
    for name, over in (cfg.get("console_overrides") or {}).items():
        table[name] = deep_merge(table.get(name, {"hash": "plain", "dirs": [name], "label": name}), over)
    by_id = {str(v["id"]): k for k, v in table.items() if "id" in v}

    def resolve(item) -> list[str]:
        item = str(item)
        if item.lower() == "all":
            return list(table)
        if item in table:
            return [item]
        if item in by_id:
            return [by_id[item]]
        sys.exit(f"unknown console '{item}' (see --list-consoles, or define [console_overrides.{item}])")

    chosen = [k for item in cfg["consoles"] for k in resolve(item)]
    skip = {k for item in cfg.get("exclude_consoles", []) for k in resolve(item)}
    return {k: table[k] for k in dict.fromkeys(chosen) if k not in skip and "id" in table[k]}


def console_names(key: str | None, state: Path | None, ttl_days: float = 30) -> dict[int, str]:
    """RA console id -> name: live from the API (cached), else the built-in list."""
    cache = (state / "consoles.json") if state else None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < ttl_days * 86400:
        return {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    if key:
        try:
            r = api("API_GetConsoleIDs.php", key, a=1, g=1)
            names = {int(c["ID"]): c["Name"] for c in r if c.get("IsGameSystem", True)}
            if names and cache:
                cache.write_text(json.dumps(names), encoding="utf-8")
            if names:
                return names
        except (RuntimeError, TypeError, KeyError):
            pass
    return dict(FALLBACK_CONSOLES)


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
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                sys.exit("TLS certificate check failed. On macOS run 'Install Certificates.command' "
                         "from your Python folder (or: pip install certifi), then retry.")
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
def n64_to_z64(data: bytes) -> bytes:
    """N64 dumps come in three byte orders; RA hashes (and patches expect) big-endian .z64."""
    head = data[:4]
    if head == b"\x37\x80\x40\x12":        # .v64: byte-swapped pairs
        a = bytearray(data)
        a[0::2], a[1::2] = data[1::2], data[0::2]
        return bytes(a)
    if head == b"\x40\x12\x37\x80":        # .n64: little-endian words
        a = bytearray(data)
        a[0::4], a[1::4], a[2::4], a[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
        return bytes(a)
    return data


HASH_LIMIT = 64 * 1024 * 1024   # RetroAchievements hashes at most the first 64 MiB of a ROM


def _md5(data: bytes) -> str:
    return hashlib.md5(data[:HASH_LIMIT]).hexdigest()


def md5s(data: bytes, style: str) -> set[str]:
    """MD5s RetroAchievements could have registered for this ROM image."""
    out = {_md5(data)}
    if style in ("nes", "fds") and data[:4] in (b"NES\x1a", b"FDS\x1a"):
        out.add(_md5(data[16:]))
    elif style == "lynx" and data[:4] == b"LYNX":
        out.add(_md5(data[64:]))
    elif style == "a7800" and data[1:10] == b"ATARI7800":
        out.add(_md5(data[128:]))
    elif style == "snes" and len(data) % 1024 == 512:
        out.add(_md5(data[512:]))
    elif style == "n64":
        out.add(_md5(n64_to_z64(data)))
    elif style == "arduboy":      # .hex text: RA normalises to Unix line endings first
        out.add(_md5(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")))
        out.add(_md5(data.replace(b"\r", b"")))
    return out


# --------------------------------------------------------------------------- archives
SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"
_tool_cache: list = []


def archive_tool() -> list[str] | None:
    """Command prefix for a 7z-capable extractor: 7-Zip, or bsdtar (macOS/Windows `tar`)."""
    if _tool_cache:
        return _tool_cache[0]
    found = None
    for exe in ("7zz", "7z", "7za", "7zr"):
        if shutil.which(exe):
            found = [shutil.which(exe), "x", "-y", "-bso0", "-bsp0"]
            break
    if not found:
        for exe in ("bsdtar", "tar"):
            path = shutil.which(exe)
            if path:
                try:
                    out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
                except (OSError, subprocess.SubprocessError):
                    continue
                if "bsdtar" in out or "libarchive" in out:
                    found = [path, "-x"]
                    break
    _tool_cache.append(found)
    return found


def extract_7z(path: str, max_bytes: int):
    """Yield (member name, bytes) from a .7z file using py7zr, 7-Zip or bsdtar."""
    try:
        import py7zr  # optional: pip install py7zr
        with py7zr.SevenZipFile(path) as z:
            for name, bio in z.readall().items():
                data = bio.read()
                if 0 < len(data) < max_bytes:
                    yield name, data
        return
    except ImportError:
        pass
    except Exception as e:
        log("  7z read failed", path, f"({e})")
        return
    tool = archive_tool()
    if not tool:
        if not getattr(extract_7z, "warned", False):
            log("  .7z archive found but no extractor: install 7-Zip / bsdtar or `pip install py7zr`")
            extract_7z.warned = True
        return
    with tempfile.TemporaryDirectory(prefix="ra-wtp-") as tmp:
        cmd = tool + ([f"-o{tmp}", path] if tool[1] == "x" else ["-f", path, "-C", tmp])
        try:
            subprocess.run(cmd, capture_output=True, timeout=300, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            log("  7z extract failed", path, f"({e})")
            return
        root = os.path.realpath(tmp)
        for dp, _, fns in os.walk(tmp):
            for fn in fns:
                p = os.path.join(dp, fn)
                if os.path.islink(p) or not os.path.realpath(p).startswith(root):
                    continue
                if 0 < os.path.getsize(p) < max_bytes:
                    yield os.path.relpath(p, tmp), Path(p).read_bytes()


def archive_members(path: str, max_bytes: int):
    """(name, bytes) of ROM-ish members of a .zip or .7z file."""
    low = path.lower()
    if low.endswith((".zip", ".arduboy")):
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if not i.is_dir() and not i.filename.lower().endswith(SKIP_EXT) and 1000 < i.file_size < max_bytes:
                    yield i.filename, z.read(i)
    elif low.endswith(".7z"):
        for name, data in extract_7z(path, max_bytes):
            if not name.lower().endswith(SKIP_EXT) and len(data) > 1000:
                yield name, data


def file_hashes(path: str, style: str, max_bytes: int) -> set[str]:
    res: set[str] = set()
    try:
        if path.lower().endswith((".zip", ".7z", ".arduboy")):
            for _, data in archive_members(path, max_bytes):
                res |= md5s(data, style)
        elif os.path.getsize(path) < max_bytes:
            with open(path, "rb") as f:
                res = md5s(f.read(), style)
    except Exception as e:  # corrupt archive, permission problem, ...
        log("  skipped", path, f"({e})")
    return res


def open_db(state: Path) -> sqlite3.Connection:
    db = sqlite3.connect(state / "cache.db")
    db.execute("create table if not exists files(path text primary key, size int, mtime real, hashes text)")
    db.execute("create table if not exists ghash2(id int primary key, fetched real, entries text)")
    return db


def find_dirs(src: Path, names: list[str]) -> list[Path]:
    wanted = {slug(n) for n in names}
    return [p for p in sorted(src.iterdir()) if p.is_dir() and slug(p.name) in wanted]


def index_console(db, src: Path, spec: dict, max_bytes: int, extra_roots=()) -> dict[str, list[str]]:
    """{md5: [paths]} for every ROM of one system; hashes are cached by size+mtime."""
    idx: dict[str, list[str]] = {}
    fresh = 0
    for root in [*find_dirs(src, spec["dirs"]), *[r for r in extra_roots if Path(r).is_dir()]]:
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


def game_entries(db, key, gid: int, ttl_days: float) -> list[dict]:
    """RA hash entries of a game: [{'MD5': ..., 'PatchUrl': ...}, ...] (cached)."""
    row = db.execute("select fetched,entries from ghash2 where id=?", (gid,)).fetchone()
    if row and time.time() - row[0] < ttl_days * 86400:
        return json.loads(row[1])
    r = api("API_GetGameHashes.php", key, i=gid)
    ents = [{"MD5": h["MD5"].lower(), "PatchUrl": h.get("PatchUrl"), "Name": h.get("Name")}
            for h in r.get("Results", [])]
    db.execute("insert or replace into ghash2 values(?,?,?)", (gid, time.time(), json.dumps(ents)))
    db.commit()
    time.sleep(0.3)  # be polite to the API
    return ents


NAME_SKIP_EXT = (".bin", ".cue", ".sub", ".ccd", ".img", ".mds", ".m3u")


def norm_title(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"^(~[^~]*~\s*)+", "", text)
    text = re.sub(r"\(.*?\)|\[.*?\]", "", text)
    text = re.sub(r"\b(the|a)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def index_names(src: Path, spec: dict, extra_roots=()) -> dict[str, list[str]]:
    """{normalised title: [paths]} for systems matched by name (no content hashing)."""
    idx: dict[str, list[str]] = {}
    for root in [*find_dirs(src, spec["dirs"]), *[r for r in extra_roots if Path(r).is_dir()]]:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.startswith(".") or fn.lower().endswith(SKIP_EXT + NAME_SKIP_EXT):
                    continue
                p = os.path.join(dp, fn)
                idx.setdefault(norm_title(stem(fn)), []).append(p)
                m = re.search(r"\[RA (\d+)\]", fn)
                if m:
                    idx.setdefault(f"ra:{m.group(1)}", []).append(p)
    return idx


# --------------------------------------------------------------------------- choosing a ROM
def stem(name: str) -> str:
    return unicodedata.normalize("NFC", os.path.splitext(name)[0])


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


# --------------------------------------------------------------------------- patching
PATCH_HOST = "https://github.com/RetroAchievements/RAPatches/"
MAX_PATCH_BYTES = 100 * 1024 * 1024
PATCH_EXT = (".ips", ".bps", ".ups")


def _varint(p: bytes, i: int) -> tuple[int, int]:
    """BPS/UPS variable length number."""
    v, shift = 0, 1
    while True:
        x = p[i]
        i += 1
        v += (x & 0x7F) * shift
        if x & 0x80:
            return v, i
        shift <<= 7
        v += shift


def apply_ips(src: bytes, p: bytes) -> bytes:
    if p[:5] != b"PATCH":
        raise ValueError("not an IPS patch")
    out = bytearray(src)
    i = 5
    while p[i:i + 3] != b"EOF":
        off = int.from_bytes(p[i:i + 3], "big")
        size = int.from_bytes(p[i + 3:i + 5], "big")
        i += 5
        if size == 0:  # RLE record
            size = int.from_bytes(p[i:i + 2], "big")
            data = p[i + 2:i + 3] * size
            i += 3
        else:
            data = p[i:i + size]
            i += size
        if off + len(data) > len(out):
            out.extend(b"\0" * (off + len(data) - len(out)))
        out[off:off + len(data)] = data
    i += 3
    if len(p) - i == 3:  # optional truncate length
        del out[int.from_bytes(p[i:i + 3], "big"):]
    return bytes(out)


def bps_header(p: bytes) -> tuple[int, int, int]:
    """(source size, source crc32, header end offset) of a BPS patch."""
    if p[:4] != b"BPS1":
        raise ValueError("not a BPS patch")
    ssz, i = _varint(p, 4)
    return ssz, int.from_bytes(p[-12:-8], "little"), i


def apply_bps(src: bytes, p: bytes) -> bytes:
    ssz, scrc, i = bps_header(p)
    tsz, i = _varint(p, i)
    msz, i = _varint(p, i)
    i += msz
    if len(src) != ssz or zlib.crc32(src) != scrc:
        raise ValueError("patch is for a different base ROM")
    out = bytearray(tsz)
    o = srel = trel = 0
    end = len(p) - 12
    while i < end:
        d, i = _varint(p, i)
        act, n = d & 3, (d >> 2) + 1
        if act == 0:
            out[o:o + n] = src[o:o + n]
        elif act == 1:
            out[o:o + n] = p[i:i + n]
            i += n
        elif act == 2:
            v, i = _varint(p, i)
            srel += -(v >> 1) if v & 1 else v >> 1
            out[o:o + n] = src[srel:srel + n]
            srel += n
        else:
            v, i = _varint(p, i)
            trel += -(v >> 1) if v & 1 else v >> 1
            if trel + n <= o:                 # no overlap: one slice copy
                out[o:o + n] = out[trel:trel + n]
            else:                             # overlapping run: must copy byte by byte
                for k in range(n):
                    out[o + k] = out[trel + k]
            trel += n
        o += n
    return bytes(out)


def apply_ups(src: bytes, p: bytes) -> bytes:
    if p[:4] != b"UPS1":
        raise ValueError("not a UPS patch")
    isz, i = _varint(p, 4)
    osz, i = _varint(p, i)
    if zlib.crc32(src) != int.from_bytes(p[-12:-8], "little"):
        raise ValueError("patch is for a different base ROM")
    out = bytearray(src[:osz].ljust(osz, b"\0"))
    pos, end = 0, len(p) - 12
    while i < end:
        skip, i = _varint(p, i)
        pos += skip
        while p[i]:
            out[pos] ^= p[i]
            pos += 1
            i += 1
        i += 1
        pos += 1
    return bytes(out)


APPLY = {".ips": apply_ips, ".bps": apply_bps, ".ups": apply_ups}


def fetch_patch_zip(url: str, cache: Path) -> bytes | None:
    """Download an official RAPatches zip (cached). Only the RAPatches repo is accepted."""
    if not url.startswith(PATCH_HOST):
        log("  refusing patch URL outside RAPatches:", url)
        return None
    f = cache / re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1])
    if f.exists():
        return f.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read(MAX_PATCH_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as e:
        log("  patch download failed:", url, e)
        return None
    if len(data) > MAX_PATCH_BYTES:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return data


def patches_in_zip(data: bytes) -> list[tuple[str, bytes]]:
    """ips/bps/ups files inside a downloaded .zip or .7z patch archive."""
    if data[:6] == SEVENZ_MAGIC:
        with tempfile.NamedTemporaryFile(suffix=".7z", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            return [(n, d) for n, d in extract_7z(tmp, MAX_PATCH_BYTES) if n.lower().endswith(PATCH_EXT)]
        finally:
            os.unlink(tmp)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return [(i.filename, z.read(i)) for i in z.infolist()
                    if i.filename.lower().endswith(PATCH_EXT) and i.file_size < MAX_PATCH_BYTES]
    except zipfile.BadZipFile:
        return []


def rom_blobs(path: str, max_bytes: int):
    """Yield (display name, bytes) for the ROM image(s) in a library file."""
    try:
        if path.lower().endswith((".zip", ".7z", ".arduboy")):
            yield from archive_members(path, max_bytes)
        elif os.path.getsize(path) < max_bytes:
            yield os.path.basename(path), Path(path).read_bytes()
    except Exception:
        return


def base_candidates(paths: list[str], hint: str) -> list[str]:
    """Library files ordered by how well their name matches the patch's base game."""
    toks = [t for t in re.split(r"[^a-z0-9]+", hint.lower()) if t]
    def score(p):
        n = os.path.basename(p).lower()
        return -sum(t in n for t in toks)
    return sorted(paths, key=lambda p: (score(p), len(p)))


def readme_hint(data: bytes) -> str:
    """The 'Use with: ...' text RAPatches ships next to a patch (the base ROM it needs)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                if i.filename.lower().endswith((".txt", ".md")) and i.file_size < 4096:
                    return " ".join(z.read(i).decode("latin1").split())[:240]
    except zipfile.BadZipFile:
        pass
    return ""


def build_patched(entries: list[dict], wanted_md5: set[str], style: str, paths: list[str],
                  max_bytes: int, cache: Path, max_tries: int = 60):
    """Apply the official RA patch to a base ROM from the library.
    Returns (rom bytes, extension, base path, "", []) on success,
    else (None, None, None, reason, [base ROMs the patches need])."""
    reasons: list[str] = []
    needs: list[str] = []
    for e in entries:
        url = e.get("PatchUrl")
        if not url:
            continue
        pname_url = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        if not pname_url.lower().endswith((".zip", ".7z")):
            reasons.append(f"patch format not supported ({pname_url})")
            continue
        zdata = fetch_patch_zip(url, cache)
        if not zdata:
            reasons.append(f"patch download failed ({pname_url})")
            continue
        patches = patches_in_zip(zdata)
        if not patches:
            reasons.append(f"patch format not supported (no ips/bps/ups in {pname_url})")
            continue
        hint = urllib.parse.unquote(url).split("/RAPatches/")[-1].rsplit("/", 1)[0].replace("/", " ")
        for pname, pbytes in patches:
            fn = APPLY[os.path.splitext(pname)[1].lower()]
            tries, verified_base = 0, False
            for cand in base_candidates(paths, hint):
                if verified_base or tries > max_tries:
                    break
                for bname, blob in rom_blobs(cand, max_bytes):
                    tries += 1
                    if tries > max_tries:
                        break
                    variants = [blob]
                    if style in ("snes", "nes") and len(blob) % 1024 == 512:
                        variants.append(blob[512:])   # some patches expect the unheadered ROM
                    if style == "nes" and blob[:4] == b"NES\x1a":
                        variants.append(blob[16:])
                    if style == "n64":
                        variants = [n64_to_z64(blob)]   # patches are made against big-endian .z64
                    for v in variants:
                        try:
                            res = fn(v, pbytes)
                        except Exception:
                            continue
                        if md5s(res, style) & wanted_md5:
                            ext = ".z64" if style == "n64" else (os.path.splitext(bname)[1] or ".bin")
                            return res, ext, cand, "", []
                        # BPS/UPS check the base's CRC, so a patch that applied has the right base:
                        # this patch is simply a different version than the one RA registered.
                        if fn is not apply_ips:
                            verified_base = True
        need = readme_hint(zdata) or str(e.get("Name") or pname_url)
        needs.append(need)
        reasons.append(f"base ROM missing in the library - patch needs: {need}")
    return None, None, None, "; ".join(dict.fromkeys(reasons)) or "no patch published", list(dict.fromkeys(needs))


def patch_category(url: str | None) -> str:
    """'Fix', 'Improvement', 'Hacks', ... from a RAPatches URL (<Console>/<Category>/...)."""
    m = re.search(r"/RAPatches/raw/(?:refs/heads/)?main/[^/]+/([^/]+)/", url or "")
    return urllib.parse.unquote(m.group(1)) if m else ""


def load_sources(cfg: dict) -> dict[str, dict]:
    """RA game id -> {'url', 'note'}: free downloads from the authors (sources.json + optional own file)."""
    out: dict[str, dict] = {}
    for p in (Path(__file__).resolve().parent / "sources.json", Path(cfg.get("sources_file") or "").expanduser()):
        if p.is_file():
            try:
                out.update({k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                            if k.isdigit() and isinstance(v, dict)})
            except ValueError:
                log("  ignoring unreadable sources file", p)
    return out



# --------------------------------------------------------------------------- homebrew catalogs
# console key -> machine readable catalog maintained by the community. repo.json is meant to be used by
# other tools ("as a source to build your own application"); it is only read to show author, license and
# links in the report. Nothing is downloaded from it - import the collection into your library yourself.
CATALOGS = {"arduboy": "https://arduboy.ried.cl/repo.json"}
_NONFREE = re.compile(r"proprietary|unspecified|unknown|none|\bnc\b|-nc\b|noncommercial", re.I)
_FREE = re.compile(r"^(mit|bsd|apache|gpl|lgpl|agpl|mpl|cc0|cc-by|unlicense|isc|zlib|wtfpl|public domain)", re.I)


def http_get(url: str, max_bytes: int, timeout: int = 60) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log("  download failed:", url, e)
        return None
    return data if len(data) <= max_bytes else None


def is_permissive(license_: str | None) -> bool:
    """True for open-source style licenses; 'Proprietary', 'Unspecified' and NC licenses are not."""
    lic = (license_ or "").strip()
    return bool(lic) and not _NONFREE.search(lic) and bool(_FREE.match(lic))


def load_catalog(key: str, state: Path, ttl_days: float) -> list[dict]:
    url = CATALOGS.get(key)
    if not url:
        return []
    cache = state / f"catalog-{key}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl_days * 86400:
        raw = cache.read_bytes()
    else:
        raw = http_get(url, 20 * 1024 * 1024)
        if raw is None:
            return json.loads(cache.read_text(encoding="utf-8")).get("items", []) if cache.exists() else []
        cache.write_bytes(raw)
    try:
        return json.loads(raw).get("items", [])
    except ValueError:
        return []


def catalog_lookup(items: list[dict], title: str, rom_names: list[str]) -> dict | None:
    """Catalog entry whose title equals the RA game title or one of RA's registered ROM names."""
    wanted = {norm_title(title)} | {norm_title(stem(n)) for n in rom_names if n}
    wanted.discard("")
    return next((i for i in items if norm_title(i.get("title", "")) in wanted), None)


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


def find_links(m: dict, sources: dict) -> list[str]:
    """Where to look for a missing ROM: the author's page if known, else sensible search pages."""
    src = sources.get(str(m["id"]))
    if src:
        return [src["url"] + (f"   ({src['note']})" if src.get("note") else "")]
    links = []
    cat = m.get("catalog")
    if cat:   # the author's own page, from the community catalog
        terms = "" if is_permissive(cat.get("license")) else " - not an open license: check the author's terms"
        links.append(f"{cat.get('sourceUrl') or cat.get('url')}   (by {cat.get('author')}, license "
                     f"{cat.get('license')}{terms}; listed in {m['key']} catalog)")
        if cat.get("url") and cat.get("url") != cat.get("sourceUrl"):
            links.append(f"{cat['url']}   (project / forum page)")
        return links
    clean = re.sub(r"\[.*?\]", "", re.sub(r"^(~[^~]*~\s*)+", "", m["title"])).strip()
    if re.match(r"~(Homebrew|Demo)~", m["title"]):
        links.append("https://itch.io/search?q=" + urllib.parse.quote(clean))
    if m["key"] == "pokemini":
        links.append("https://pokemon-mini.net/   (community hub with Pokemon Mini homebrew downloads)")
    if m["key"] == "arduboy":   # not in the community catalog: point at the places authors publish
        q = urllib.parse.quote(clean)
        links.append(f"https://github.com/search?q={q}+arduboy&type=repositories   (authors usually publish on GitHub)")
        links.append("https://arduboy.com/   (official site; the community forum links most games)")
    return links


def write_report(path: Path, user: str, missing: list[dict], matched: int, sources: dict | None = None) -> None:
    """Human readable list of wanted games that could not be provided: first the ROMs to look for
    (name + MD5 as registered on RetroAchievements), then each game with its reason and links."""
    sources = sources or {}
    ordered = sorted(missing, key=lambda m: (m["console"], m["title"]))
    lines = [f"# Games on {user}'s RetroAchievements list that could not be provided",
             f"# {time.strftime('%Y-%m-%d %H:%M:%S')}  -  {matched} provided, {len(missing)} missing", "",
             "## ROMs to look for (name and MD5 as registered on RetroAchievements)", ""]
    for m in ordered:
        if m["roms"]:
            for name, md5 in m["roms"][:4]:
                lines.append(f"{md5}  {name}   [{m['console']}] {m['title']}")
        else:
            lines.append(f"{'-' * 32}  (any dump; matched by title)   [{m['console']}] {m['title']}")
    needs = {n: m["title"] for m in ordered for n in m["needs"]}
    if needs:
        lines += ["", "## Base ROMs the official patches need", ""]
        lines += [f"- {n}   (for: {t})" for n, t in needs.items()]
    lines += ["", "## Details", ""]
    for m in ordered:
        lines += [f"[{m['console']}] {m['title']}", f"    why:  {m['reason']}",
                  f"    link: https://retroachievements.org/game/{m['id']}"]
        lines += [f"    get:  {link}" for link in find_links(m, sources)]
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- main
def sync(cfg: dict, dry_run: bool) -> int:
    if not cfg["username"]:
        sys.exit("no RetroAchievements username (config 'username', --username or $RA_USER)")
    if not cfg["src"] or not cfg["dest"]:
        sys.exit("'src' (ROM library) and 'dest' (output folder) are required")
    key = get_api_key(cfg)
    if not key:
        sys.exit(f"no API key: set $RA_API_KEY or put it in {cfg['api_key_file']}")

    src, dest = Path(cfg["src"]).expanduser().resolve(), Path(cfg["dest"]).expanduser().resolve()
    if dest == src or src in dest.parents or dest in src.parents:
        sys.exit("'src' and 'dest' must be separate folders (src is never written to)")
    if not src.is_dir():
        sys.exit(f"src is not a directory: {src}")
    state = Path(cfg["state_dir"]).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    lock = FileLock(state / "lock")
    if not lock.acquire():
        sys.exit("another run is in progress")

    table = select_consoles(build_console_table(console_names(key, state)), cfg)
    by_id = {spec["id"]: name for name, spec in table.items()}
    max_bytes = int(cfg["max_rom_mb"]) * 1024 * 1024
    mf_path = state / "manifest.json"
    manifest: dict = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() else {}

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
    patched_dir = Path(cfg["patched_dir"] or state / "patched").expanduser()
    fail_path = state / "patch-failed.json"
    failed: dict = json.loads(fail_path.read_text(encoding="utf-8")) if fail_path.exists() else {}
    failed = {k: v for k, v in failed.items() if isinstance(v, dict)}   # drop pre-1.0 float entries
    sources = load_sources(cfg)
    _catalogs: dict[str, list] = {}

    def catalog_items(key_: str) -> list[dict]:
        if key_ not in _catalogs:
            _catalogs[key_] = load_catalog(key_, state, cfg["hash_cache_days"])
        return _catalogs[key_]

    variant_cats = {c.lower() for c in cfg["build_variants"]}
    indexes: dict[int, dict] = {}
    plan: dict[str, tuple[str, str, str]] = {}
    missing: list[dict] = []

    def library_paths(cid) -> list[str]:
        paths = [p for lst in indexes[cid].values() for p in lst]
        return list(dict.fromkeys(p for p in paths if not p.startswith(str(patched_dir))))

    def store(name, label, ext, data) -> Path:
        safe = re.sub(r'[\\/:*?"<>|]+', " -", label).strip()
        out = patched_dir / name / f"{safe}{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return out

    for gid, (title, cid) in sorted(wanted.items()):
        name, spec = by_id[cid], table[by_id[cid]]
        reason, ents, needs, catalog = "not in the library", [], [], None
        if spec["hash"] == "name":                  # discs etc.: match by title
            if cid not in indexes:
                log("indexing", name, "(by title)")
                indexes[cid] = index_names(src, spec, [patched_dir / name])
            cands = set(indexes[cid].get(norm_title(title), [])) | set(indexes[cid].get(f"ra:{gid}", []))
        else:
            ents = game_entries(db, key, gid, cfg["hash_cache_days"])
            hs = {e["MD5"] for e in ents}
            if cid not in indexes:
                log("indexing", name)
                indexes[cid] = index_console(db, src, spec, max_bytes, [patched_dir / name])
            cands = {p for h in hs for p in indexes[cid].get(h, [])}
            if not cands and name in CATALOGS:       # author page + license for the report (read-only)
                catalog = catalog_lookup(catalog_items(name), title, [e.get("Name") for e in ents])
            if not cands and cfg["build_patches"] and any(e["PatchUrl"] for e in ents):
                old = failed.get(str(gid), {})
                if time.time() - old.get("t", 0) < cfg["hash_cache_days"] * 86400:
                    reason, needs = old["why"] + " (retrying later)", old.get("needs", [])
                elif dry_run:
                    reason = "an official patch exists (not tried in a dry run)"
                    log("WOULD BUILD from patch:", title)
                else:
                    log("BUILD", title)
                    data, ext, base, why, needs = build_patched(
                        ents, hs, spec["hash"], library_paths(cid), max_bytes, state / "patch-cache")
                    if data:
                        out = store(name, f"{title} [RA {gid}]", ext, data)
                        log(f"  built {out.name} from {os.path.basename(base)}")
                        cands = {str(out)}
                        indexes[cid].setdefault(next(iter(hs & md5s(data, spec["hash"]))), []).append(str(out))
                    else:
                        log("  cannot build:", why)
                        reason = why
                        failed[str(gid)] = {"t": time.time(), "why": why, "needs": needs}
            elif not cands and not any(e["PatchUrl"] for e in ents):
                reason = "not in the library, and RA publishes no patch for it"
            # RA-registered variants (fixes, improvements, ...) of a game that is in the library
            if cands and variant_cats:
                # one variant per category (best by language/region/revision), not every registered one
                groups: dict[str, list[dict]] = {}
                for e in ents:
                    cat = patch_category(e.get("PatchUrl"))
                    if cat.lower() in variant_cats:
                        groups.setdefault(cat, []).append(e)
                for cat, group in groups.items():
                    if any(e["MD5"] in indexes[cid] for e in group):
                        continue                                  # already built / present
                    e = min(group, key=lambda x: rank(x.get("Name") or "", saved, cfg["preferences"]))
                    fkey = f"{gid}:{e['MD5'][:8]}"
                    if time.time() - failed.get(fkey, {}).get("t", 0) < cfg["hash_cache_days"] * 86400:
                        continue
                    if dry_run:
                        log(f"WOULD BUILD {cat} variant:", e.get("Name"))
                        continue
                    data, ext, base, why, _ = build_patched([e], {e["MD5"]}, spec["hash"], library_paths(cid),
                                                            max_bytes, state / "patch-cache")
                    if data:
                        label = f"{stem(e.get('Name') or title)} [QoL {cat.lower()}] [RA {gid}]"
                        out = store(name, label, ext, data)
                        log(f"  built {cat} variant {out.name}")
                        cands.add(str(out))
                        indexes[cid][e["MD5"]] = [str(out)]
                    else:
                        log(f"  {cat} variant not built:", why)
                        failed[fkey] = {"t": time.time(), "why": why}
        if not cands:
            missing.append({"id": gid, "title": title, "console": spec["label"], "key": name, "reason": reason,
                            "roms": [(e.get("Name"), e["MD5"]) for e in ents], "needs": needs, "catalog": catalog})
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

    report = Path(cfg["report_file"] or state / "missing-roms.txt").expanduser()
    write_report(report, cfg["username"], missing, len(plan), sources)
    if not dry_run:
        fail_path.write_text(json.dumps(failed), encoding="utf-8")
        mf_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log(f"done: {len(plan)} matched, {len(missing)} missing - list with reasons: {report}")
    return 0


def get_api_key(cfg: dict) -> str | None:
    key = os.environ.get("RA_API_KEY")
    if not key:
        kf = Path(cfg["api_key_file"]).expanduser()
        kf = kf if kf.is_absolute() else Path(__file__).resolve().parent / kf
        key = kf.read_text(encoding="utf-8").strip() if kf.exists() else None
    return key


def list_consoles(cfg: dict) -> int:
    state = Path(cfg["state_dir"]).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    table = build_console_table(console_names(get_api_key(cfg), state))
    chosen = select_consoles(table, cfg)
    print(f"{'':2}{'key':<16}{'id':>4}  {'matching':<9} name")
    for k, v in table.items():
        how = "title" if v["hash"] == "name" else "hash"
        print(f"{'*' if k in chosen else ' ':2}{k:<16}{v['id']:>4}  {how:<9} {v['label']}")
    print("\n* = selected by the current configuration")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ra-wtp-sync", description=__doc__.split("\n\n")[0])
    ap.add_argument("-c", "--config", help="TOML config (default: config.toml next to the script, else <user config dir>/ra-wtp-sync/config.toml)")
    ap.add_argument("--username", help="RetroAchievements user name")
    ap.add_argument("--api-key-file", dest="api_key_file", help="file holding the RA Web API key (or set $RA_API_KEY)")
    ap.add_argument("--src", help="ROM library (read-only)")
    ap.add_argument("--dest", help="output folder (e.g. your Syncthing folder)")
    ap.add_argument("--state-dir", dest="state_dir", help="cache + manifest location")
    ap.add_argument("--saves", nargs="*", help="save folders; a ROM that already has a save here is preferred")
    ap.add_argument("--consoles", help="comma separated console keys/ids, or \"all\" (see --list-consoles)")
    ap.add_argument("--exclude", help="comma separated consoles to leave out (after --consoles/config)")
    ap.add_argument("--list-consoles", action="store_true", help="show all available consoles and exit")
    ap.add_argument("--include-started", action="store_true", help="also keep games you started but have not beaten")
    ap.add_argument("--no-patches", action="store_true", help="do not build missing hacks from RA patches")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would change, change nothing")
    ap.add_argument("--version", action="version", version=__version__)
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    cfg = load_config(args)
    if args.list_consoles:
        return list_consoles(cfg)
    return sync(cfg, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
