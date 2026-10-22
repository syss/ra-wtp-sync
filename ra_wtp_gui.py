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
"""Small settings GUI for ra-wtp-sync: a local web page (no extra packages needed).

    ./ra_wtp_gui.py            # opens http://127.0.0.1:<port>/ in your browser

Pick your consoles, folders and preferences, save config.toml, and start a dry run
or a real run. The server only listens on 127.0.0.1 and requires a per-start token.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import threading
import tomllib
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ra_wtp_schedule as sched
import ra_wtp_sync as core

HERE = Path(__file__).resolve().parent
try:
    DIAGRAM = (HERE / "docs" / "how-it-works.svg").read_text(encoding="utf-8").replace("<svg ", '<svg style="max-width:100%;height:auto" ', 1)
except OSError:
    DIAGRAM = ""
LANGS = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "ja": "Japanese"}
REGIONS = {"europe": "Europe", "usa": "USA", "japan": "Japan", "world": "World"}
SIMPLE_KEYS = ("username", "api_key_file", "src", "dest", "dest_mode", "state_dir", "saves", "consoles", "exclude_consoles",
               "include_started", "build_patches", "max_rom_mb", "hash_cache_days", "min_list_fraction",
               "patched_dir", "report_file", "build_variants", "sources_file")


# ----------------------------------------------------------------------------- TOML writing
def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(type(v))


def dump_toml(data: dict, prefix: str = "") -> str:
    lines, tables = [], []
    for k, v in data.items():
        (tables if isinstance(v, dict) else lines).append((k, v))
    out = "".join(f"{k} = {toml_value(v)}\n" for k, v in lines)
    for k, v in tables:
        name = f"{prefix}.{k}" if prefix else k
        out += f"\n[{name}]\n" + dump_toml(v, name)
    return out


class State:
    def __init__(self, config: Path):
        self.config = config
        self.token = secrets.token_urlsafe(24)
        self.log: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def load(self) -> dict:
        cfg = json.loads(json.dumps(core.DEFAULTS))
        if self.config.exists():
            cfg = core.deep_merge(cfg, tomllib.loads(self.config.read_text(encoding="utf-8")))
        return cfg

    def key_file(self, cfg: dict) -> Path:
        kf = Path(cfg["api_key_file"]).expanduser()
        return kf if kf.is_absolute() else HERE / kf

    def api_key(self, cfg: dict) -> str | None:
        kf = self.key_file(cfg)
        return kf.read_text(encoding="utf-8").strip() if kf.exists() else None

    def payload(self) -> dict:
        cfg = self.load()
        state = Path(cfg["state_dir"]).expanduser()
        state.mkdir(parents=True, exist_ok=True)
        table = core.build_console_table(core.console_names(self.api_key(cfg), state))
        return {
            "config": cfg,
            "has_key": self.key_file(cfg).exists(),
            "consoles": [{"key": k, "id": v["id"], "label": v["label"], "title_match": v["hash"] == "name"}
                         for k, v in table.items()],
            "default_consoles": core.DEFAULT_CONSOLES,
            "languages": LANGS, "regions": REGIONS,
            "config_path": str(self.config),
            "schedule": {"current": sched.current(), "installed": sched.installed(),
                         "platforms": [{"key": k, "label": sched.LABELS[k], "manual": sched.manual(k)}
                                       for k in sched.PLATFORMS]},
        }

    def save(self, form: dict) -> None:
        cfg = self.load()
        new = form["config"]
        for k in SIMPLE_KEYS:
            if k in new:
                cfg[k] = new[k]
        cfg["preferences"] = core.deep_merge(cfg["preferences"], new.get("preferences", {}))
        key = (form.get("api_key") or "").strip()
        if key:
            kf = self.key_file(cfg)
            kf.parent.mkdir(parents=True, exist_ok=True)
            kf.touch(mode=0o600)
            kf.chmod(0o600)
            kf.write_text(key, encoding="utf-8")
        # only write what differs from the built-in defaults
        out = {k: v for k, v in cfg.items() if k == "preferences" or v != core.DEFAULTS.get(k) or k in ("username", "src", "dest")}
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.touch(mode=0o600)
        self.config.chmod(0o600)
        self.config.write_text("# Written by ra_wtp_gui.py - edit freely\n" + dump_toml(out), encoding="utf-8")

    def run(self, dry: bool) -> bool:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return False
            self.log = []
            cmd = [sys.executable, str(HERE / "ra_wtp_sync.py"), "-c", str(self.config)] + (["-n"] if dry else [])
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=HERE)
            threading.Thread(target=self._pump, daemon=True).start()
            return True

    def _pump(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.log.append(line.rstrip())
        self.proc.wait()
        self.log.append(f"-- finished (exit code {self.proc.returncode}) --")

    def browse(self, path: str) -> dict:
        """List sub-folders so the page can offer a folder picker (this machine only)."""
        if not path:
            if sys.platform == "win32":
                import string
                drives = [f"{c}:\\" for c in string.ascii_uppercase if Path(f"{c}:\\").exists()]
                return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives], "home": str(Path.home())}
            path = str(Path.home())
        p = Path(path).expanduser()
        while not p.is_dir() and p != p.parent:
            p = p.parent
        try:
            dirs = [{"name": d.name, "path": str(d)} for d in sorted(p.iterdir(), key=lambda x: x.name.lower())
                    if d.is_dir() and not d.name.startswith(".")]
        except OSError:
            dirs = []
        parent = str(p.parent) if p.parent != p else ("" if sys.platform == "win32" else None)
        return {"path": str(p), "parent": parent, "dirs": dirs, "home": str(Path.home())}

    def test_login(self, body: dict) -> dict:
        cfg = self.load()
        user = (body.get("username") or cfg["username"] or "").strip()
        key = (body.get("api_key") or "").strip() or self.api_key(cfg) or ""
        if not user or not key:
            return {"ok": False, "message": "Enter your username and API key first."}
        ok, msg = core.check_login(key, user)
        if ok and not core.looks_like_key(key):
            msg += " (Note: the key looks unusual - they normally have 32 characters.)"
        return {"ok": ok, "message": msg}

    def report(self, which: str = "missing") -> str:
        cfg = self.load()
        p = Path(cfg["report_file"] or Path(cfg["state_dir"]).expanduser() / "missing-roms.txt").expanduser()
        if which == "installed":
            p = p.with_name("installed-roms.txt")
        return p.read_text(encoding="utf-8") if p.exists() else "(nothing yet - run a preview first)"


def make_handler(st: State, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self) -> bool:
            host_ok = self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")
            if not host_ok:
                self.send_error(403)
                return False
            return True

        def _auth(self) -> bool:
            if self.headers.get("X-Token") != st.token:
                self.send_error(403)
                return False
            return True

        def _send(self, body: bytes, ctype="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._ok():
                return
            path = self.path.split("?")[0]
            if path == "/":
                self._send(PAGE.replace("__TOKEN__", st.token).replace("__DIAGRAM__", DIAGRAM).encode(), "text/html")
            elif path == "/api/state" and self._auth():
                self._send(json.dumps(st.payload()).encode())
            elif path == "/api/log" and self._auth():
                since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
                running = bool(st.proc and st.proc.poll() is None)
                self._send(json.dumps({"lines": st.log[since:], "next": len(st.log), "running": running}).encode())
            elif path == "/api/browse" and self._auth():
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(self.path).query)
                self._send(json.dumps(st.browse(q.get("path", [""])[0])).encode())
            elif path == "/api/report" and self._auth():
                self._send(st.report("installed" if "which=installed" in self.path else "missing").encode(), "text/plain")
            else:
                self.send_error(404)

        def do_POST(self):
            if not (self._ok() and self._auth()):
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/save":
                st.save(body)
                self._send(b'{"ok":true}')
            elif self.path == "/api/run":
                st.save(body)
                self._send(json.dumps({"ok": st.run(bool(body.get("dry")))}).encode())
            elif self.path == "/api/test":
                self._send(json.dumps(st.test_login(body)).encode())
            elif self.path == "/api/schedule":
                minutes = max(5, min(int(body.get("minutes", 10)), 1440))
                ok, msg = sched.remove() if body.get("remove") else sched.install(minutes)
                self._send(json.dumps({"ok": ok, "message": msg, "installed": sched.installed()}).encode())
            else:
                self.send_error(404)

    return H


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-c", "--config", help="config file (default: config.toml next to the script)")
    ap.add_argument("--port", type=int, default=0, help="port (default: random free port)")
    ap.add_argument("--no-browser", action="store_true", help="only print the URL")
    a = ap.parse_args()
    st = State(Path(a.config).expanduser() if a.config else HERE / "config.toml")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), lambda *args: None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(st, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"ra-wtp-sync settings: {url}   (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ra-wtp-sync</title>
<style>
:root{--bg:#fff;--fg:#1c1e21;--mut:#6b7280;--card:#f5f6f8;--line:#d9dce1;--acc:#2f6fed;--acc2:#e8f0ff}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e8eb;--mut:#9aa1ab;--card:#1e2126;--line:#33383f;--acc:#6b9bff;--acc2:#1d2a45}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:980px;margin:0 auto;padding:20px 16px 80px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px}p.s{color:var(--mut);margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
label.f{display:block;margin:10px 0 4px;font-weight:600}input[type=text],input[type=password],textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
.row{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}@media(max-width:700px){.row{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:2px 12px}
.grid label{display:flex;gap:8px;align-items:center;padding:3px 4px;border-radius:5px;cursor:pointer}.grid label:hover{background:var(--acc2)}
.tag{font-size:11px;color:var(--mut)}button{font:inherit;padding:7px 14px;border-radius:7px;border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer}
button.p{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.5;cursor:default}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.hint{color:var(--mut);font-size:13px}
pre{background:#0d1117;color:#d1d5db;padding:12px;border-radius:8px;max-height:340px;overflow:auto;white-space:pre-wrap;font-size:12.5px}
.sticky{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:10px 0;margin-top:20px;display:flex;gap:8px;flex-wrap:wrap}
.ok{color:#16a34a}.bad{color:#dc2626}
.opt{display:flex;gap:8px;align-items:flex-start;margin:6px 0;cursor:pointer}.opt input{margin-top:4px}
.pick{display:flex;gap:8px}.pick input{flex:1}
dialog{border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg);width:min(560px,92vw)}
.dl{max-height:320px;overflow:auto;margin:8px 0}.dl div{padding:5px 8px;cursor:pointer;border-radius:5px}.dl div:hover{background:var(--acc2)}
details summary{cursor:pointer;margin:8px 0}
.tabs{display:flex;gap:4px;margin-bottom:8px}.tabs button.on{background:var(--acc);border-color:var(--acc);color:#fff}
details.card summary{cursor:pointer}pre#schedcmd{max-height:220px}
</style></head><body><main>
<h1>ra-wtp-sync</h1>
<p class="s">Mirror your RetroAchievements <em>Want to Play</em> list into a folder. Settings file: <code id="cfgpath"></code></p>

<details class="card"><summary><b>How does this work? (picture)</b></summary>__DIAGRAM__</details>
<div class="card" id="welcome"><b>New here? Four steps and you are done:</b>
<ol><li>Enter your RetroAchievements <b>username</b> and <b>API key</b> (help below).</li>
<li>Pick the folder with <b>your ROMs</b> and an <b>output folder</b> for the games you want to play.</li>
<li>Tick the <b>systems</b> you care about (the default is fine).</li>
<li>Press <b>Preview</b> to see what would happen, then <b>Run now</b>. Nothing in your ROM folder is ever changed.</li></ol></div>

<h2>1. Your RetroAchievements account</h2><div class="card"><div class="row">
<div><label class="f">Username</label><input type="text" id="username" placeholder="e.g. Scott"></div>
<div><label class="f">Web API key <span class="hint" id="keystate"></span></label><input type="password" id="apikey" autocomplete="off" placeholder="e.g. Ab3dE5fGh7Ij9kLm1nOp3qRs5tUv7wXy"></div>
</div>
<div class="bar"><button onclick="testLogin()">Test login</button><span id="loginmsg" class="hint"></span></div>
<details><summary><b>Where do I find my API key?</b></summary>
<ol><li>Log in at <a href="https://retroachievements.org" target="_blank" rel="noopener">retroachievements.org</a> (free account).</li>
<li>Open <a href="https://retroachievements.org/settings" target="_blank" rel="noopener">Settings</a> and scroll to the <b>Keys</b> section.</li>
<li>Copy the <b>Web API Key</b> (not the Connect key). It is 32 letters and digits, like <code>Ab3dE5fGh7Ij9kLm1nOp3qRs5tUv7wXy</code> (an example, it will not work).</li>
<li>Paste it above and press <b>Test login</b>.</li></ol>
<p class="hint">The key is only used to read your Want to Play list. It is stored in a file readable by your user only and never shown again. If you ever leak it, press <i>Reset</i> next to it on the settings page.</p></details></div>

<h2>2. Folders</h2><div class="card">
<label class="f">Your ROM library <span class="hint">(read only, never modified)</span></label><div class="pick"><input type="text" id="src" placeholder="/path/to/roms"><button onclick="browse('src')">Browse…</button></div>
<label class="f">Where should the games from your list go?</label>
<label class="opt"><input type="radio" name="mode" value="new" checked> <span><b>A new, separate folder</b> (recommended) <span class="hint">- the tool owns it: one sub-folder per system, and it may delete its own copies when a game leaves your list</span></span></label>
<label class="opt"><input type="radio" name="mode" value="merge"> <span><b>Straight into my existing ROM folder</b> <span class="hint">- e.g. your frontend's or handheld's ROM folder. Existing system folders (even spelled differently, like <i>Game Boy</i>) are reused; files you already have are never overwritten, and only files this tool added are ever removed</span></span></label>
<div class="pick"><input type="text" id="dest" placeholder="/path/to/roms-wtp"><button onclick="browse('dest')">Browse…</button></div>
</div>

<h2>3. Systems</h2><div class="card">
<div class="bar"><button onclick="sel('defaults')">Recommended</button><button onclick="sel('all')">Select all</button><button onclick="sel('none')">Select none</button><span class="hint" id="count"></span></div>
<b>Matched by hash</b> <span class="hint">– cartridge systems; only ROMs that earn achievements are copied</span>
<div class="grid" id="hashgrid"></div>
<p></p><b>Matched by title</b> <span class="hint">– discs and other formats; files can be large (use CHD/ISO/RVZ/zip, not cue/bin)</span>
<div class="grid" id="namegrid"></div></div>

<h2>4. Preview and run</h2><div class="card">
<div class="bar"><button onclick="run(true)">Save &amp; preview (changes nothing)</button><button class="p" onclick="run(false)">Save &amp; run now</button><button onclick="report()">Show missing-ROMs list</button><button onclick="report('installed')">Show chosen versions</button></div>
<pre id="log">Nothing started yet.</pre></div>

<h2>Advanced <span class="hint">(optional - the defaults are fine)</span></h2>
<details class="card"><summary><b>Choosing between several matching ROMs, saves, patches</b></summary>
<div class="row">
<div><label class="f">Preferred languages <span class="hint">(first = most wanted)</span></label><div class="grid" id="langs"></div></div>
<div><label class="f">Preferred regions</label><div class="grid" id="regions"></div></div></div>
<p class="hint">Order of rules: existing save file → quality-of-life version → language → region → highest revision.</p>
<label class="f">Save-file folders <span class="hint">(one per line; a ROM that already has a save is preferred)</span></label><textarea id="saves" rows="2"></textarea>
<p><label><input type="checkbox" id="build"> Build missing hacks from RetroAchievements' official patches (needs the base ROM in your library)</label><br>
<label><input type="checkbox" id="variants"> Also build RetroAchievements' official <b>Fix / Improvement</b> versions of games I own (e.g. Pokémon Emerald RNG fix)</label><br>
<label><input type="checkbox" id="started"> Also keep games I started but have not beaten yet</label></p></details>

<h2>Schedule</h2><div class="card">
<div class="tabs" id="schedtabs"></div>
<pre id="schedcmd"></pre>
<div class="bar"><button class="p" id="schedinst" onclick="schedule(false)">Run automatically every</button>
<input type="number" id="schedmin" value="10" min="5" max="1440" style="width:70px"> <span class="hint">minutes</span>
<button onclick="schedule(true)">Remove schedule</button><span class="hint" id="schedmsg"></span></div>
<p class="hint">Every run first asks RetroAchievements for your list (one quick call) and stops if nothing changed, so a short interval costs next to nothing; the full pass over your library runs when the list changes, and at least once a day. Installing works for the system you are on; the other tabs show how to set it up by hand there.</p></div>

<h2>Add-ons <span class="hint">(optional)</span></h2>
<details class="card"><summary><b>Share the output folder with Syncthing</b></summary>
<p>This tool only fills the output folder; any sync tool can carry it to your other devices. For <a href="https://syncthing.net" target="_blank" rel="noopener">Syncthing</a>:</p>
<ul><li>Share the output folder as a normal send &amp; receive folder (or send-only from the machine running this).
When a game leaves your list, the file is deleted there and Syncthing propagates it.</li>
<li>Mind the folder's file versioning if you want deletions to be final.</li>
<li>Copies are written atomically via a <code>.syncthing.*.tmp</code> file that Syncthing ignores, so peers never see half-written files.</li></ul></details>

<div class="sticky"><button class="p" onclick="save()">Save settings</button><span id="msg" class="hint"></span></div>
<dialog id="dlg"><b id="dlgpath"></b><div id="dlglist" class="dl"></div>
<div class="bar"><button onclick="dlg.close()">Cancel</button><button class="p" onclick="pickHere()">Use this folder</button></div></dialog>
</main>
<script>
const T="__TOKEN__";let S,sel_=new Set(),langs=[],regions=[];
const H={"X-Token":T,"Content-Type":"application/json"};
const $=id=>document.getElementById(id);
async function api(p,o){const r=await fetch(p,Object.assign({headers:H},o));if(!r.ok)throw new Error(r.status);return r}
function chk(grid,items,set,onchange){grid.innerHTML="";for(const [k,l,tag] of items){const e=document.createElement("label");e.innerHTML=`<input type=checkbox ${set.has(k)?"checked":""}> <span>${l}</span> <span class=tag>${tag||""}</span>`;e.firstChild.onchange=ev=>{ev.target.checked?set.add(k):set.delete(k);onchange&&onchange()};grid.appendChild(e)}}
function draw(){const c=S.consoles;
 chk($("hashgrid"),c.filter(x=>!x.title_match).map(x=>[x.key,x.label,x.key]),sel_,cnt);
 chk($("namegrid"),c.filter(x=>x.title_match).map(x=>[x.key,x.label,x.key]),sel_,cnt);cnt()}
function cnt(){$("count").textContent=sel_.size+" selected"}
function sel(m){const c=S.consoles.map(x=>x.key);sel_=new Set(m=="all"?c:m=="none"?[]:S.default_consoles.filter(k=>c.includes(k)));draw()}
function ordered(set,pref){return [...pref.filter(x=>set.has(x)),...[...set].filter(x=>!pref.includes(x))]}
function form(){const L=[...$("langs").querySelectorAll("input")].map((e,i)=>e.checked&&Object.keys(S.languages)[i]).filter(Boolean);
 const R=[...$("regions").querySelectorAll("input")].map((e,i)=>e.checked&&Object.keys(S.regions)[i]).filter(Boolean);
 const all=S.consoles.map(x=>x.key);const allSel=all.every(k=>sel_.has(k));
 return {api_key:$("apikey").value,config:{username:$("username").value.trim(),src:$("src").value.trim(),dest:$("dest").value.trim(),dest_mode:document.querySelector('input[name=mode]:checked').value,
  saves:$("saves").value.split("\n").map(s=>s.trim()).filter(Boolean),consoles:allSel?["all"]:all.filter(k=>sel_.has(k)),exclude_consoles:[],
  build_patches:$("build").checked,build_variants:$("variants").checked?["Fix","Improvement"]:[],include_started:$("started").checked,preferences:{languages:L,regions:R}}}}
async function save(quiet){const f=form();if(!f.config.username||!f.config.src||!f.config.dest){$("msg").textContent="username, library and output folder are required";throw 0}
 await api("/api/save",{method:"POST",body:JSON.stringify(f)});$("apikey").value="";$("msg").textContent=quiet?"":"Saved ✓";if(!quiet)load(false)}
let since=0,timer;
async function run(dry){try{const f=form();if(!f.config.username||!f.config.src||!f.config.dest){$("msg").textContent="username, library and output folder are required";return}
 $("log").textContent="";since=0;const r=await (await api("/api/run",{method:"POST",body:JSON.stringify(Object.assign(f,{dry}))})).json();
 if(!r.ok){$("log").textContent="A run is already in progress.";}$("apikey").value="";clearInterval(timer);timer=setInterval(poll,800)}catch(e){}}
async function poll(){const r=await (await api("/api/log?since="+since)).json();since=r.next;const l=$("log");if(r.lines.length){l.textContent+=r.lines.join("\n")+"\n";l.scrollTop=l.scrollHeight}if(!r.running&&!r.lines.length)clearInterval(timer)}
let browseTarget,browsePath="";
async function browse(id,path){browseTarget=id;const r=await (await api("/api/browse?path="+encodeURIComponent(path===undefined?$(id).value:path))).json();browsePath=r.path;
 $("dlgpath").textContent=r.path||"Drives";const l=$("dlglist");l.innerHTML="";
 const add=(label,p)=>{const d=document.createElement("div");d.textContent=label;d.onclick=()=>browse(id,p);l.appendChild(d)};
 if(r.parent!==null)add("⬆ ..",r.parent);for(const d of r.dirs)add("📁 "+d.name,d.path);
 if(!$("dlg").open)$("dlg").showModal()}
function pickHere(){if(browsePath)$(browseTarget).value=browsePath;$("dlg").close()}
async function testLogin(){const m=$("loginmsg");m.className="hint";m.textContent="Checking…";
 const r=await (await api("/api/test",{method:"POST",body:JSON.stringify({username:$("username").value.trim(),api_key:$("apikey").value})})).json();
 m.className=r.ok?"ok":"bad";m.textContent=r.message}
async function report(w){$("log").textContent=await (await api("/api/report?which="+(w||"missing"))).text()}
async function load(first){S=await (await api("/api/state")).json();const c=S.config;$("cfgpath").textContent=S.config_path;
 $("username").value=c.username;$("src").value=c.src;$("dest").value=c.dest;document.querySelector(`input[name=mode][value=${c.dest_mode==="merge"?"merge":"new"}]`).checked=true;$("saves").value=c.saves.join("\n");
 $("build").checked=c.build_patches;$("variants").checked=(c.build_variants||[]).length>0;$("started").checked=c.include_started;$("keystate").innerHTML=S.has_key?'<span class=ok>– saved ✓</span>':'<span>– not set</span>';
 const keys=S.consoles.map(x=>x.key);const want=c.consoles.map(String);
 sel_=new Set(want.includes("all")?keys:keys.filter(k=>want.includes(k)||want.includes(String(S.consoles.find(x=>x.key==k).id))));
 for(const x of c.exclude_consoles||[])sel_.delete(String(x));
 const lp=c.preferences.languages,rp=c.preferences.regions;
 chk($("langs"),Object.entries(S.languages).map(([k,v])=>[k,v]),new Set(lp));chk($("regions"),Object.entries(S.regions).map(([k,v])=>[k,v]),new Set(rp));draw()}
let schedTab;
function drawSched(){const sc=S.schedule;schedTab=schedTab||sc.current;const t=$("schedtabs");t.innerHTML="";
 for(const p of sc.platforms){const b=document.createElement("button");b.textContent=p.label+(p.key==sc.current?" (this system)":"");b.className=p.key==schedTab?"on":"";b.onclick=()=>{schedTab=p.key;drawSched()};t.appendChild(b)}
 $("schedcmd").textContent=sc.platforms.find(p=>p.key==schedTab).manual;
 $("schedinst").disabled=schedTab!=sc.current;$("schedmsg").textContent=sc.installed?"Schedule is installed on this system ✓":""}
async function schedule(rm){const r=await (await api("/api/schedule",{method:"POST",body:JSON.stringify({remove:rm,minutes:+$("schedmin").value||10})})).json();
 S.schedule.installed=r.installed;drawSched();$("schedmsg").textContent=r.message}
load(true).then(drawSched);
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
