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

import hashlib, os, sys, tempfile, unittest, zipfile
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
    def test_lynx_and_7800_headers(self):
        body = b"z" * 2048
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"LYNX" + b"\0" * 60 + body, "lynx"))
        self.assertIn(hashlib.md5(body).hexdigest(), m.md5s(b"\1ATARI7800" + b"\0" * 118 + body, "a7800"))


class Report(unittest.TestCase):
    def test_report_lists_reason_and_link(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "r.txt")
            m.write_report(p, "me", [(42, "Some Hack", "Nintendo 64", "base ROM missing in the library")], 3)
            txt = p.read_text()
            self.assertIn("[Nintendo 64] Some Hack", txt)
            self.assertIn("base ROM missing", txt)
            self.assertIn("retroachievements.org/game/42", txt)


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
            m.write_report(p, "u", [(1, "Pok\u00e9mon \u30c6\u30b9\u30c8", "Game Boy", "x")], 0)
            self.assertIn("\u30c6\u30b9\u30c8", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
