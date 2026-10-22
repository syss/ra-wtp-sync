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

import ra_wtp_sync as core

HERE = Path(__file__).resolve().parent
LANGS = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "ja": "Japanese"}
REGIONS = {"europe": "Europe", "usa": "USA", "japan": "Japan", "world": "World"}
SIMPLE_KEYS = ("username", "api_key_file", "src", "dest", "state_dir", "saves", "consoles", "exclude_consoles",
               "include_started", "build_patches", "max_rom_mb", "hash_cache_days", "min_list_fraction",
               "patched_dir", "report_file")


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

    def report(self) -> str:
        cfg = self.load()
        p = Path(cfg["report_file"] or Path(cfg["state_dir"]).expanduser() / "missing-roms.txt").expanduser()
        return p.read_text(encoding="utf-8") if p.exists() else "(no report yet - run a dry run first)"


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
                self._send(PAGE.replace("__TOKEN__", st.token).encode(), "text/html")
            elif path == "/api/state" and self._auth():
                self._send(json.dumps(st.payload()).encode())
            elif path == "/api/log" and self._auth():
                since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
                running = bool(st.proc and st.proc.poll() is None)
                self._send(json.dumps({"lines": st.log[since:], "next": len(st.log), "running": running}).encode())
            elif path == "/api/report" and self._auth():
                self._send(st.report().encode(), "text/plain")
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
    print(f"ra-wtp-sync settings: {url}   (Ctrl+C to stop)")
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
.ok{color:#16a34a}
</style></head><body><main>
<h1>ra-wtp-sync</h1>
<p class="s">Mirror your RetroAchievements <em>Want to Play</em> list into a folder. Config: <code id="cfgpath"></code></p>

<h2>Account</h2><div class="card"><div class="row">
<div><label class="f">RetroAchievements username</label><input type="text" id="username"></div>
<div><label class="f">Web API key <span class="hint" id="keystate"></span></label><input type="password" id="apikey" autocomplete="off" placeholder="paste here (leave empty to keep the saved key)"></div>
</div><p class="hint">Get the key at retroachievements.org → Settings → Keys. It is stored in a file with mode 600, never shown again.</p></div>

<h2>Folders</h2><div class="card">
<label class="f">ROM library to read from <span class="hint">(never modified)</span></label><input type="text" id="src" placeholder="/path/to/roms">
<label class="f">Output folder <span class="hint">(e.g. inside your Syncthing folder; owned by this tool)</span></label><input type="text" id="dest" placeholder="/path/to/Sync/roms-wtp">
<label class="f">Save-file folders <span class="hint">(one per line; a ROM that already has a save is preferred)</span></label><textarea id="saves" rows="2"></textarea>
</div>

<h2>Consoles</h2><div class="card">
<div class="bar"><button onclick="sel('defaults')">Small cartridge systems</button><button onclick="sel('all')">Select all</button><button onclick="sel('none')">Select none</button><span class="hint" id="count"></span></div>
<b>Matched by hash</b> <span class="hint">– cartridge systems; only ROMs that earn achievements are copied</span>
<div class="grid" id="hashgrid"></div>
<p></p><b>Matched by title</b> <span class="hint">– discs and other formats RA hashes in its own way; files can be large (use CHD/ISO/RVZ/zip, not cue/bin)</span>
<div class="grid" id="namegrid"></div></div>

<h2>Choosing between several matching ROMs</h2><div class="card"><div class="row">
<div><label class="f">Preferred languages <span class="hint">(first = most wanted)</span></label><div class="grid" id="langs"></div></div>
<div><label class="f">Preferred regions</label><div class="grid" id="regions"></div></div></div>
<p class="hint">Order of rules: existing save file → quality-of-life version → language → region → highest revision.</p></div>

<h2>Options</h2><div class="card">
<label><input type="checkbox" id="build"> Build missing hacks from RetroAchievements' official patches (needs the base ROM in your library)</label><br>
<label><input type="checkbox" id="started"> Also keep games I started but have not beaten yet</label></div>

<h2>Run</h2><div class="card">
<div class="bar"><button onclick="run(true)">Save &amp; dry run</button><button class="p" onclick="run(false)">Save &amp; run now</button><button onclick="report()">Show missing-ROMs list</button></div>
<pre id="log">Nothing started yet.</pre></div>

<div class="sticky"><button class="p" onclick="save()">Save settings</button><span id="msg" class="hint"></span></div>
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
 return {api_key:$("apikey").value,config:{username:$("username").value.trim(),src:$("src").value.trim(),dest:$("dest").value.trim(),
  saves:$("saves").value.split("\n").map(s=>s.trim()).filter(Boolean),consoles:allSel?["all"]:all.filter(k=>sel_.has(k)),exclude_consoles:[],
  build_patches:$("build").checked,include_started:$("started").checked,preferences:{languages:L,regions:R}}}}
async function save(quiet){const f=form();if(!f.config.username||!f.config.src||!f.config.dest){$("msg").textContent="username, library and output folder are required";throw 0}
 await api("/api/save",{method:"POST",body:JSON.stringify(f)});$("apikey").value="";$("msg").textContent=quiet?"":"Saved ✓";if(!quiet)load(false)}
let since=0,timer;
async function run(dry){try{const f=form();if(!f.config.username||!f.config.src||!f.config.dest){$("msg").textContent="username, library and output folder are required";return}
 $("log").textContent="";since=0;const r=await (await api("/api/run",{method:"POST",body:JSON.stringify(Object.assign(f,{dry}))})).json();
 if(!r.ok){$("log").textContent="A run is already in progress.";}$("apikey").value="";clearInterval(timer);timer=setInterval(poll,800)}catch(e){}}
async function poll(){const r=await (await api("/api/log?since="+since)).json();since=r.next;const l=$("log");if(r.lines.length){l.textContent+=r.lines.join("\n")+"\n";l.scrollTop=l.scrollHeight}if(!r.running&&!r.lines.length)clearInterval(timer)}
async function report(){$("log").textContent=await (await api("/api/report")).text()}
async function load(first){S=await (await api("/api/state")).json();const c=S.config;$("cfgpath").textContent=S.config_path;
 $("username").value=c.username;$("src").value=c.src;$("dest").value=c.dest;$("saves").value=c.saves.join("\n");
 $("build").checked=c.build_patches;$("started").checked=c.include_started;$("keystate").innerHTML=S.has_key?'<span class=ok>– saved ✓</span>':'<span>– not set</span>';
 const keys=S.consoles.map(x=>x.key);const want=c.consoles.map(String);
 sel_=new Set(want.includes("all")?keys:keys.filter(k=>want.includes(k)||want.includes(String(S.consoles.find(x=>x.key==k).id))));
 for(const x of c.exclude_consoles||[])sel_.delete(String(x));
 const lp=c.preferences.languages,rp=c.preferences.regions;
 chk($("langs"),Object.entries(S.languages).map(([k,v])=>[k,v]),new Set(lp));chk($("regions"),Object.entries(S.regions).map(([k,v])=>[k,v]),new Set(rp));draw()}
load(true);
</script></body></html>
"""

if __name__ == "__main__":
    sys.exit(main())
