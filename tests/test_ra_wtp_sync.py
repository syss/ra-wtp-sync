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


if __name__ == "__main__":
    unittest.main()
