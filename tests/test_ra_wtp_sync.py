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

import hashlib, json, os, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ra_wtp_sync as m

PREFS = {"order": ["save", "qol", "language", "region", "revision"], "languages": ["de"], "regions": ["europe"]}


def best(names, saved=frozenset(), prefs=PREFS):
    return min(names, key=lambda n: m.rank(n, set(saved), prefs))


class Rank(unittest.TestCase):
    def test_save_wins(self):
        self.assertEqual(best(["A (Europe).zip", "A (USA).zip"], {"A (USA)"}), "A (USA).zip")

    def test_qol_over_german(self):
        self.assertEqual(best(["G (Germany).gb", "G (USA) (QoL Patch).gb"]), "G (USA) (QoL Patch).gb")

    def test_german_over_europe(self):
        self.assertEqual(best(["G (Europe).gb", "G (Germany).gb"]), "G (Germany).gb")

    def test_europe_over_usa(self):
        self.assertEqual(best(["G (USA).gb", "G (Europe).gb"]), "G (Europe).gb")

    def test_highest_revision(self):
        prefs = dict(PREFS, languages=[], regions=[])
        self.assertEqual(best(["G (USA).z64", "G (USA) (Rev B).z64", "G (USA) (Rev A).z64"], prefs=prefs),
                         "G (USA) (Rev B).z64")

    def test_order_is_configurable(self):
        prefs = dict(PREFS, order=["region", "save"])
        self.assertEqual(best(["A (Europe).zip", "A (USA).zip"], {"A (USA)"}, prefs), "A (Europe).zip")


class Hashing(unittest.TestCase):
    def test_nes_header_stripped(self):
        body = b"x" * 4096
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"NES\x1a" + b"\0" * 12 + body, "nes"))

    def test_snes_copier_header_stripped(self):
        body = b"y" * 2048
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"\0" * 512 + body, "snes"))

    def test_n64_byte_orders(self):
        z64 = b"\x80\x37\x12\x40" + bytes(range(256)) * 4
        v64 = bytearray(z64); v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
        n64 = bytearray(z64); n64[0::4], n64[1::4], n64[2::4], n64[3::4] = z64[3::4], z64[2::4], z64[1::4], z64[0::4]
        want = hashlib.md5(z64).hexdigest()
        self.assertIn(want, m.md5s(bytes(v64), "n64"))
        self.assertIn(want, m.md5s(bytes(n64), "n64"))

    def test_zip_member(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.urandom(5000)
            p = os.path.join(d, "g.zip")
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("g.gb", data)
            self.assertIn(hashlib.md5(data).hexdigest(), m.file_hashes(p, "plain", 10**7))


class Safety(unittest.TestCase):
    def test_remove_stays_inside_dest(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d, "dest").resolve(); dest.mkdir()
            outside = Path(d, "outside.txt"); outside.write_text("keep")
            inner = dest / "n64" / "a.z64"; inner.parent.mkdir(); inner.write_text("x")
            m.safe_remove(dest, "../outside.txt")
            self.assertTrue(outside.exists())
            m.safe_remove(dest, "n64/a.z64")
            self.assertFalse(inner.exists())
            self.assertFalse(inner.parent.exists())  # empty system folder cleaned up

    def test_src_dest_must_differ(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(m.DEFAULTS, username="u", src=d, dest=os.path.join(d, "out"), state_dir=os.path.join(d, "s"))
            os.environ["RA_API_KEY"] = "x"
            with self.assertRaises(SystemExit):
                m.sync(cfg, True)


def _vi(n):  # BPS/UPS varint encoder
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n:
            out.append(x)
            n -= 1
        else:
            out.append(0x80 | x)
            return bytes(out)


class Patching(unittest.TestCase):
    base = bytes(range(256)) * 8

    def test_ips_with_rle_and_truncate(self):
        p = b"PATCH" + (10).to_bytes(3, "big") + (3).to_bytes(2, "big") + b"abc" \
            + (20).to_bytes(3, "big") + b"\0\0" + (5).to_bytes(2, "big") + b"Z" + b"EOF" + (100).to_bytes(3, "big")
        out = m.apply_ips(self.base, p)
        self.assertEqual(out[10:13], b"abc")
        self.assertEqual(out[20:25], b"ZZZZZ")
        self.assertEqual(len(out), 100)

    def test_bps_roundtrip_and_wrong_base(self):
        import zlib
        target = self.base[:100] + b"HACKED" + self.base[106:]
        body = b"BPS1" + _vi(len(self.base)) + _vi(len(target)) + _vi(0)
        body += _vi(((100 - 1) << 2) | 0)                 # SourceRead 100 bytes
        body += _vi(((6 - 1) << 2) | 1) + b"HACKED"       # TargetRead
        body += _vi(((len(target) - 106 - 1) << 2) | 0)   # SourceRead rest
        body += zlib.crc32(self.base).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
        patch = body + zlib.crc32(body).to_bytes(4, "little")
        self.assertEqual(m.apply_bps(self.base, patch), target)
        with self.assertRaises(ValueError):
            m.apply_bps(b"x" + self.base, patch)

    def test_ups_xor(self):
        import zlib
        target = bytearray(self.base); target[5] ^= 0x0F; target[6] ^= 0xF0
        body = b"UPS1" + _vi(len(self.base)) + _vi(len(target)) + _vi(5) + bytes([0x0F, 0xF0, 0])
        body += zlib.crc32(self.base).to_bytes(4, "little") + zlib.crc32(bytes(target)).to_bytes(4, "little")
        patch = body + zlib.crc32(body).to_bytes(4, "little")
        self.assertEqual(m.apply_ups(self.base, patch), bytes(target))

    def test_only_rapatches_urls_are_fetched(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(m.fetch_patch_zip("https://example.com/evil.zip", Path(d)))


class Consoles(unittest.TestCase):
    table = m.build_console_table(m.FALLBACK_CONSOLES)

    def sel(self, **kw):
        return set(m.select_consoles(self.table, dict(m.DEFAULTS, **kw)))

    def test_default_is_small_cartridge_set(self):
        s = self.sel()
        self.assertIn("n64", s)
        self.assertNotIn("wii", s)
        self.assertNotIn("psx", s)

    def test_all_and_exclude(self):
        everything = self.sel(consoles=["all"])
        self.assertGreater(len(everything), 40)
        self.assertEqual(self.sel(consoles=["all"], exclude_consoles=["wii", "gamecube", 12]),
                         everything - {"wii", "gamecube", "psx"})

    def test_select_by_id_and_unknown(self):
        self.assertEqual(self.sel(consoles=[2, "gba"]), {"n64", "gba"})
        with self.assertRaises(SystemExit):
            self.sel(consoles=["nope"])

    def test_every_ra_console_is_available(self):
        self.assertTrue(set(m.FALLBACK_CONSOLES) <= {v["id"] for v in self.table.values()})

    def test_disc_systems_match_by_title(self):
        self.assertEqual(self.table["psx"]["hash"], "name")
        self.assertEqual(self.table["snes"]["hash"], "snes")

    def test_folder_name_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("Super Nintendo", "N64", "game-boy-advance"):
                os.mkdir(os.path.join(d, n))
            self.assertEqual([p.name for p in m.find_dirs(Path(d), ["n64"])], ["N64"])
            self.assertEqual([p.name for p in m.find_dirs(Path(d), ["gameboyadvance"])], ["game-boy-advance"])


class TitleMatching(unittest.TestCase):
    def test_titles_normalise(self):
        n = m.norm_title
        self.assertEqual(n("The Legend of Zelda: Ocarina of Time"), n("Legend of Zelda, The - Ocarina of Time (USA) (Rev A)"))
        self.assertEqual(n("~Hack~ Super Mario 64: Chaos Edition"), n("Super Mario 64 - Chaos Edition"))
        self.assertEqual(n("Pok\u00e9mon Crystal Version"), n("Pokemon - Crystal Version (UE) [!]"))


class Hashing2(unittest.TestCase):
    def test_arduboy_hex_line_endings_are_normalised(self):
        lf = b":100000000C945D000C946E000C946E000C946E00A1\n:00000001FF\n"
        crlf = lf.replace(b"\n", b"\r\n")
        self.assertIn(hashlib.md5(lf).hexdigest(), m.md5s(crlf, "arduboy"))
        self.assertIn("arduboy", m.build_console_table(m.FALLBACK_CONSOLES)["arduboy"]["dirs"])
        self.assertEqual(m.build_console_table(m.FALLBACK_CONSOLES)["arduboy"]["hash"], "arduboy")

    def test_arduboy_package_is_hashed_via_its_hex(self):
        with tempfile.TemporaryDirectory() as d:
            hexdata = b":100000000C945D00\n" * 80
            p = os.path.join(d, "game.arduboy")
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("info.json", "{}")
                z.writestr("game.hex", hexdata)
            self.assertIn(hashlib.md5(hexdata).hexdigest(), m.file_hashes(p, "arduboy", 10**7))

    def test_only_first_64_mib_are_hashed(self):
        big = os.urandom(1024) * (65 * 1024)          # 65 MiB
        self.assertIn(hashlib.md5(big[:64 * 1024 * 1024]).hexdigest(), m.md5s(big, "n64"))


    def test_lynx_and_7800_headers(self):
        body = b"z" * 2048
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"LYNX" + b"\0" * 60 + body, "lynx"))
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"\1ATARI7800" + b"\0" * 118 + body, "a7800"))


class Report(unittest.TestCase):
    def test_report_has_rom_list_reasons_links_and_sources(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "r.txt")
            miss = [
                {"id": 42, "title": "~Hack~ Some Hack", "console": "Nintendo 64", "key": "n64",
                 "reason": "base ROM missing in the library", "roms": [("Some Hack.z64", "a" * 32)],
                 "needs": ["Super Mario 64 (USA).z64 md5 20b854b2"]},
                {"id": 7, "title": "~Homebrew~ Tiny Game", "console": "Pokemon Mini", "key": "pokemini",
                 "reason": "not in the library", "roms": [], "needs": []},
                {"id": 9, "title": "~Homebrew~ Known Game", "console": "Game Boy", "key": "gb",
                 "reason": "not in the library", "roms": [("Known.gb", "b" * 32)], "needs": []},
            ]
            m.write_report(p, "me", miss, 3, {"9": {"url": "https://example.itch.io/known", "note": "free"}})
            txt = p.read_text(encoding="utf-8")
            top = txt.split("## Details")[0]
            self.assertIn("a" * 32 + "  Some Hack.z64", top)            # name + hash list comes first
            self.assertIn("Base ROMs the official patches need", top)
            self.assertIn("Super Mario 64 (USA).z64", top)
            self.assertIn("retroachievements.org/game/42", txt)
            self.assertIn("https://itch.io/search?q=Tiny%20Game", txt)   # homebrew without known source
            self.assertIn("pokemon-mini.net", txt)
            self.assertIn("github.com/search?q=Dinosaur+arduboy", m.find_links({"id": 1, "title": "Dinosaur", "key": "arduboy"}, {})[0])
            self.assertIn("https://example.itch.io/known", txt)          # known author page wins
            self.assertNotIn("itch.io/search?q=Known", txt)

    def test_shipped_sources_file_is_valid(self):
        import json
        data = json.loads((Path(__file__).resolve().parent.parent / "sources.json").read_text(encoding="utf-8"))
        ids = [k for k in data if k.isdigit()]
        self.assertGreaterEqual(len(ids), 5)
        for k in ids:
            self.assertTrue(data[k]["url"].startswith("https://"))

    def test_patch_category(self):
        self.assertEqual(m.patch_category("https://github.com/RetroAchievements/RAPatches/raw/main/GBA/Fix/668-x.zip"), "Fix")
        self.assertEqual(m.patch_category("https://github.com/RetroAchievements/RAPatches/raw/refs/heads/main/NES/Improvement/2021-x.zip"), "Improvement")
        self.assertEqual(m.patch_category(None), "")



class N64Bases(unittest.TestCase):
    def test_patch_applies_to_byteswapped_base(self):
        import zlib
        z64 = b"\x80\x37\x12\x40" + os.urandom(4092)
        v64 = bytearray(z64); v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
        target = z64[:100] + b"HACKED" + z64[106:]
        body = b"BPS1" + _vi(len(z64)) + _vi(len(target)) + _vi(0)
        body += _vi(((100 - 1) << 2) | 0) + _vi(((6 - 1) << 2) | 1) + b"HACKED" + _vi(((len(target) - 106 - 1) << 2) | 0)
        body += zlib.crc32(z64).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
        patch = body + zlib.crc32(body).to_bytes(4, "little")
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d, "cache"); cache.mkdir()
            with zipfile.ZipFile(cache / "1-hack.zip", "w") as z:
                z.writestr("hack.bps", patch)
            lib = Path(d, "Super Mario 64 (USA).n64"); lib.write_bytes(bytes(v64))   # byte-swapped dump
            ents = [{"PatchUrl": m.PATCH_HOST + "N64/Hacks/Super%20Mario%2064/1-hack.zip", "MD5": "x"}]
            want = {hashlib.md5(target).hexdigest()}
            data, ext, base, why, needs = m.build_patched(ents, want, "n64", [str(lib)], 10**7, cache)
            self.assertEqual(data, target)
            self.assertEqual(ext, ".z64")
            self.assertEqual(why, "")

    def test_second_patch_version_is_tried(self):
        """Regression: an older patch version that applies but does not match must not use up the budget."""
        import zlib
        base = os.urandom(4096)
        def bps(word):
            target = base[:100] + word + base[100 + len(word):]
            body = b"BPS1" + _vi(len(base)) + _vi(len(target)) + _vi(0)
            body += _vi(((100 - 1) << 2) | 0) + _vi(((len(word) - 1) << 2) | 1) + word
            body += _vi(((len(target) - 100 - len(word) - 1) << 2) | 0)
            body += zlib.crc32(base).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
            return body + zlib.crc32(body).to_bytes(4, "little"), target
        old, _ = bps(b"OLDVER")
        new, want = bps(b"NEWVER")
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d, "cache"); cache.mkdir()
            with zipfile.ZipFile(cache / "3-multi.zip", "w") as z:
                z.writestr("a v1.0.1.bps", old)
                z.writestr("b v1.0.2.bps", new)
            libs = []
            for i in range(80):                     # many decoys, more than max_tries
                p = Path(d, f"decoy{i}.gb"); p.write_bytes(os.urandom(4096)); libs.append(str(p))
            real = Path(d, "base.gb"); real.write_bytes(base); libs.append(str(real))
            ents = [{"PatchUrl": m.PATCH_HOST + "GB/Hacks/3-multi.zip", "MD5": "x"}]
            data, _, _, why, _ = m.build_patched(ents, {hashlib.md5(want).hexdigest()}, "plain", libs, 10**7, cache)
            self.assertEqual(data, want, why)

    def test_reason_when_base_missing(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d, "cache"); cache.mkdir()
            with zipfile.ZipFile(cache / "2-x.zip", "w") as z:
                z.writestr("x.ips", b"PATCHEOF")
                z.writestr("readme.txt", "Use with: Some Game (USA).sfc")
            ents = [{"PatchUrl": m.PATCH_HOST + "SNES/Hacks/2-x.zip", "MD5": "x"}]
            data, _, _, why, needs = m.build_patched(ents, {"0" * 32}, "snes", [], 10**7, cache)
            self.assertIsNone(data)
            self.assertIn("base ROM missing", why)
            self.assertIn("Some Game (USA).sfc", why)
            self.assertEqual(len(needs), 1)
            self.assertIn("Some Game (USA).sfc", needs[0])


@unittest.skipUnless(m.archive_tool() or __import__("importlib").util.find_spec("py7zr"), "no 7z extractor installed")
class SevenZip(unittest.TestCase):
    def make_7z(self, d, members):
        import subprocess, shutil
        src = Path(d, "src"); src.mkdir()
        for n, b in members.items():
            (src / n).write_bytes(b)
        out = Path(d, "a.7z")
        exe = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
        if exe is None:
            self.skipTest("no 7z binary to create test archive")
        subprocess.run([exe, "a", "-bso0", "-bsp0", str(out)] + [str(src / n) for n in members], check=True)
        return out

    def test_rom_inside_7z_is_hashed(self):
        with tempfile.TemporaryDirectory() as d:
            rom = os.urandom(5000)
            p = self.make_7z(d, {"game.gb": rom})
            self.assertIn(hashlib.md5(rom).hexdigest(), m.file_hashes(str(p), "plain", 10**7))

    def test_patch_inside_7z(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make_7z(d, {"x.ips": b"PATCHEOF", "readme.txt": b"hi"})
            found = m.patches_in_zip(p.read_bytes())
            self.assertEqual([n for n, _ in found], ["x.ips"])


class Catalog(unittest.TestCase):
    PREFIX = "https://raw.githubusercontent.com/eried/ArduboyCollection/master/"

    def item(self, lic="MIT", url=None):
        return {"title": "Ardu-EZ Button", "author": "someone", "license": lic,
                "url": "https://community.arduboy.com/t/x/1", "sourceUrl": "https://github.com/someone/x",
                "binaries": [{"filename": url or self.PREFIX + "App/x.hex", "device": "Arduboy"}]}

    def test_permissive_licenses(self):
        for ok in ("MIT", "BSD-3-Clause", "BSD 3-Clause License", "GPL-3.0", "GPLv3", "Apache-2.0", "CC0-1.0", "Unlicense", "CC-BY-4.0"):
            self.assertTrue(m.is_permissive(ok), ok)
        for bad in ("Proprietary", "Unspecified", "", None, "CC-BY-NC-SA-4.0", "All rights reserved"):
            self.assertFalse(m.is_permissive(bad), bad)

    def test_lookup_by_title_and_by_ra_rom_name(self):
        items = [self.item()]
        self.assertIs(m.catalog_lookup(items, "~Homebrew~ Ardu-EZ Button", []), items[0])
        self.assertIs(m.catalog_lookup(items, "Something else", ["Ardu-EZ Button (World) (Program) (Erwin's Collection).hex"]), items[0])
        self.assertIsNone(m.catalog_lookup(items, "Unrelated", []))

    def test_imported_collection_is_matched_by_hash(self):
        """Clone-style layout: <library>/arduboy/<Collection>/<Category>/<Game>/game.hex plus extra files."""
        hexdata = b":100000000C945D00\r\n" * 40                    # CRLF checkout, as on Windows
        with tempfile.TemporaryDirectory() as d:
            game = Path(d, "Arduboy", "ArduboyCollection", "Action", "Some Game")
            game.mkdir(parents=True)
            (game / "game.hex").write_bytes(hexdata)
            (game / "game.ini").write_text("[game]\nlicense=MIT\n")
            (game / "banner.png").write_bytes(b"x" * 2000)
            db = m.open_db(Path(d)) if False else __import__("sqlite3").connect(":memory:")
            db.execute("create table files(path text primary key, size int, mtime real, hashes text)")
            spec = m.build_console_table(m.FALLBACK_CONSOLES)["arduboy"]
            idx = m.index_console(db, Path(d), spec, 10**7)
            want = hashlib.md5(hexdata.replace(b"\r\n", b"\n")).hexdigest()   # RA: Unix line endings
            self.assertEqual([os.path.basename(p) for p in idx[want]], ["game.hex"])
            self.assertEqual(sum(len(v) for v in idx.values()) > 0, True)
            self.assertFalse(any(p.endswith((".ini", ".png")) for v in idx.values() for p in v))

    def test_report_links_author_page_and_license(self):
        miss = {"id": 5, "title": "~Homebrew~ Ardu-EZ Button", "key": "arduboy",
                "catalog": self.item("Proprietary")}
        links = " ".join(m.find_links(miss, {}))
        self.assertIn("https://github.com/someone/x", links)
        self.assertIn("by someone", links)
        self.assertIn("not an open license", links)
        self.assertNotIn("arduboy.ried.cl", links)       # author page replaces the generic hub


class Portability(unittest.TestCase):
    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = m.FileLock(Path(d, "lock")), m.FileLock(Path(d, "lock"))
            try:
                self.assertTrue(a.acquire())
                self.assertFalse(b.acquire())
                a.release()
                self.assertTrue(b.acquire())   # free again after release
            finally:
                a.release()
                b.release()

    def test_default_dirs_are_absolute_and_per_user(self):
        for p in (m.CONFIG_DIR, m.STATE_DIR):
            self.assertTrue(p.is_absolute())
            self.assertEqual(p.name == "ra-wtp-sync" or p.parent.name == "ra-wtp-sync", True)
        self.assertTrue(os.path.isabs(m.DEFAULTS["state_dir"]))

    def test_log_timestamp_format_is_portable(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.log("hello \u00e9")
        self.assertRegex(buf.getvalue(), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d hello")

    def test_nfd_and_nfc_names_match_for_saves(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "Pok\u00e9mon - Crystal")
        nfc = unicodedata.normalize("NFC", "Pok\u00e9mon - Crystal")
        self.assertEqual(m.stem(nfd + ".srm"), m.stem(nfc + ".gbc"))

    def test_utf8_roundtrip_of_state_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "missing.txt")
            m.write_report(p, "u", [{"id": 1, "title": "Pok\u00e9mon \u30c6\u30b9\u30c8", "console": "Game Boy", "key": "gb", "reason": "x", "roms": [], "needs": []}], 0)
            self.assertIn("\u30c6\u30b9\u30c8", p.read_text(encoding="utf-8"))


class MergeMode(unittest.TestCase):
    """dest_mode = "merge": add into an existing ROM folder without touching anything else."""
    ROM = b"merge-test-rom" * 100

    def run_sync(self, root, listing, dest_mode="merge"):
        md5 = hashlib.md5(self.ROM).hexdigest()

        def fake_api(endpoint, key, **kw):
            if endpoint == "API_GetUserWantToPlayList.php":
                return {"Results": listing, "Total": len(listing)}
            if endpoint == "API_GetGameHashes.php":
                return {"Results": [{"MD5": md5, "Name": "Game.gb"}]}
            raise RuntimeError(endpoint)

        cfg = dict(m.DEFAULTS, username="u", src=str(root / "lib"), dest=str(root / "roms"),
                   state_dir=str(root / "state"), consoles=["gb"], dest_mode=dest_mode, build_patches=False)
        old = m.api, os.environ.get("RA_API_KEY")
        m.api, os.environ["RA_API_KEY"] = fake_api, "x"
        try:
            return m.sync(cfg, False)
        finally:
            m.api = old[0]
            os.environ.pop("RA_API_KEY") if old[1] is None else os.environ.update(RA_API_KEY=old[1])

    def test_reuses_existing_folder_and_leaves_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "lib" / "gb").mkdir(parents=True)
            (root / "lib" / "gb" / "Game.gb").write_bytes(self.ROM)
            mine = root / "roms" / "Game Boy" / "my own.gb"
            mine.parent.mkdir(parents=True)
            mine.write_text("keep me")
            listing = [{"ID": 1, "Title": "Game", "ConsoleID": 4}]
            self.run_sync(root, listing)
            added = root / "roms" / "Game Boy" / "Game.gb"
            self.assertTrue(added.exists(), "copied into the existing, differently spelled folder")
            self.assertFalse((root / "roms" / "gb").exists())
            # game leaves the list: only our copy goes, the folder and the user's file stay
            self.run_sync(root, [{"ID": 2, "Title": "Other", "ConsoleID": 99}])
            self.assertFalse(added.exists())
            self.assertTrue(mine.exists())

    def test_never_overwrites_or_deletes_foreign_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            (root / "lib" / "gb").mkdir(parents=True)
            (root / "lib" / "gb" / "Game.gb").write_bytes(self.ROM)
            theirs = root / "roms" / "gb" / "Game.gb"
            theirs.parent.mkdir(parents=True)
            theirs.write_text("different content")
            self.run_sync(root, [{"ID": 1, "Title": "Game", "ConsoleID": 4}])
            self.assertEqual(theirs.read_text(), "different content")
            self.run_sync(root, [{"ID": 2, "Title": "Other", "ConsoleID": 99}])
            self.assertTrue(theirs.exists(), "an untracked file is never deleted")

    def test_new_mode_still_uses_plain_key_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(m.dest_folder(Path(d), "gb", ["gb", "gameboy"], False), "gb")
            (Path(d) / "Game Boy").mkdir()
            self.assertEqual(m.dest_folder(Path(d), "gb", ["gb", "gameboy"], False), "gb")
            self.assertEqual(m.dest_folder(Path(d), "gb", ["gb", "gameboy"], True), "Game Boy")

    def test_merge_may_contain_library_but_not_equal_it(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = dict(m.DEFAULTS, username="u", src=d, dest=d, dest_mode="merge", state_dir=os.path.join(d, "s"))
            os.environ["RA_API_KEY"] = "x"
            with self.assertRaises(SystemExit):
                m.sync(cfg, True)


class Login(unittest.TestCase):
    def test_key_shape(self):
        self.assertTrue(m.looks_like_key("Ab3dE5fGh7Ij9kLm1nOp3qRs5tUv7wXy"))
        self.assertFalse(m.looks_like_key("short"))
        self.assertFalse(m.looks_like_key("Ab3dE5fGh7Ij9kLm1nOp3qRs5tUv7wX!"))


class Schedule(unittest.TestCase):
    def test_platform_order_is_linux_win_mac(self):
        import ra_wtp_schedule as sc
        self.assertEqual(sc.PLATFORMS, ("linux", "win", "mac"))
        self.assertEqual([sc.LABELS[k] for k in sc.PLATFORMS], ["Linux", "Windows", "macOS"])

    def test_manual_instructions_per_platform(self):
        import ra_wtp_schedule as sc
        self.assertIn("crontab", sc.manual("linux"))
        self.assertIn("schtasks", sc.manual("win", 15))
        self.assertIn("/MO 15", sc.manual("win", 15))
        self.assertIn("StartInterval", sc.manual("mac", 10))
        self.assertIn("<integer>600</integer>", sc.manual("mac", 10))

    def test_cron_interval(self):
        import ra_wtp_schedule as sc
        self.assertTrue(sc.cron_line(30).startswith("7,37 "))
        self.assertTrue(sc.cron_line(15).startswith("*/15 "))


if __name__ == "__main__":
    unittest.main()
