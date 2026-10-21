"""
agentctl ui — localhost dashboard for agentctl.

    agentctl ui [--port 8788] [--no-open]

A small app with a menu and one view per feature:
  Home      — at-a-glance: session bar + next reset per account, queue, daemon
  Usage     — full limit bars (session / weekly / weekly-scoped)
  Sessions  — live sessions, one-click renew
  Renew     — schedule auto-continues: datetime picker fed by detection,
              delay 0-30 in steps of 5, path pinning, optional extra query;
              queue with cancel
  Copy      — migrate sessions between accounts; origin + destination paths
  Accounts  — account list (every provider) + scaffolding
  Activity  — SQLite-backed audit log

Python stdlib only. Binds 127.0.0.1. All real work is delegated to `agentctl
<command>` so behavior lives in one place; actions are logged to
~/.agentctl/ui.db (table `events`).
"""
import argparse, json, os, sqlite3, subprocess, sys, urllib.parse, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agentctl

HOME = os.path.expanduser("~")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
AGENTCTL = os.path.join(REPO_ROOT, "bin", "agentctl")
DB = agentctl.UI_DB
RENEW_REGISTRY = agentctl.RENEW_REGISTRY
RENEW_PID = agentctl.RENEW_PIDFILE


# ------------------------------------------------------------------ storage ---
def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, kind TEXT NOT NULL, ok INTEGER NOT NULL,
        detail TEXT NOT NULL, output TEXT)""")
    return c


def log_event(kind, ok, detail, output=""):
    with db() as c:
        c.execute("INSERT INTO events (ts, kind, ok, detail, output) VALUES (?,?,?,?,?)",
                  (datetime.now().isoformat(timespec="seconds"), kind,
                   1 if ok else 0, detail, output[-4000:]))


def recent_events(n=60):
    with db() as c:
        rows = c.execute("SELECT ts, kind, ok, detail FROM events "
                         "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [{"ts": t, "kind": k, "ok": bool(o), "detail": d} for t, k, o, d in rows]


# ---------------------------------------------------------------- cli bridge ---
def run_cli(sub, *args, timeout=90):
    """`agentctl <sub> ...` — the CLI is the single source of behavior."""
    r = subprocess.run([sys.executable, AGENTCTL, sub, *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def cli_json(sub, *args, timeout=90):
    ok, out = run_cli(sub, *args, timeout=timeout)
    if not ok:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def overview(with_usage=False):
    accounts = cli_json("accounts", "list", "--json") or []
    # The usage endpoints rate-limit frequent checks (429) — they are only called
    # when the user explicitly asks (?usage=1), never on an automatic refresh.
    usage = (cli_json("status", "--json", timeout=120) or {}) \
        if with_usage else None
    sessions = {}
    for a in accounts:
        if a.get("logged_in"):
            sessions[a["alias"]] = (cli_json("copy", "--list", a["alias"],
                                             "--json") or [])
    try:
        renew = json.load(open(RENEW_REGISTRY))
    except Exception:
        renew = []
    daemon = False
    try:
        os.kill(int(open(RENEW_PID).read().strip()), 0)
        daemon = True
    except Exception:
        pass
    return {"accounts": accounts, "usage": usage, "sessions": sessions,
            "renew": renew, "renew_daemon": daemon, "events": recent_events(),
            "log": cli_json("log", "--json", "-n", "60") or [],
            "now": datetime.now().strftime("%a %d %b · %H:%M:%S")}


ACTIONS = {
    "renew_add": lambda p: run_cli(
        "renew", "add", p["account"], p["session"],
        *(["--path", p["path"]] if p.get("path") else []),
        *(["--reset", p["reset"]] if p.get("reset") else []),
        *(["--delay", str(p["delay"])] if p.get("delay") not in (None, "") else []),
        *(["--follow-up", p["follow_up"]] if p.get("follow_up") else []),
        *(["--query", p["query"]] if (p.get("query") or "").strip() else []),
        *(["--message", p["message"]] if (p.get("message") or "").strip() else [])),
    "renew_remove": lambda p: run_cli(
        "renew", "remove", p["session"],
        *(["--account", p["account"]] if p.get("account") else [])),
    "copy": lambda p: run_cli(
        "copy", p["src"], p["dst"], p["session"],
        *(["--path", p["src_path"]] if p.get("src_path") else []),
        *(["--to-path", p["dst_path"]] if p.get("dst_path") else []),
        *(["--dry-run"] if p.get("dry_run") else []), timeout=300),
    "account_add": lambda p: run_cli(
        "accounts", "add", p["alias"],
        *(["--provider", p["provider"]] if p.get("provider") else [])),
}


# --------------------------------------------------------------------- http ---
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif url.path == "/api/overview":
            q = urllib.parse.parse_qs(url.query)
            self._send(200, overview(q.get("usage", ["0"])[0] == "1"))
        elif url.path == "/api/reset":
            acct = urllib.parse.parse_qs(url.query).get("account", [""])[0]
            self._send(200, cli_json("renew", "detect", acct, "--json")
                       or {"reset_at": None, "info": "detect failed"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        name = self.path.rsplit("/", 1)[-1]
        fn = ACTIONS.get(name)
        if not fn:
            self._send(404, {"error": "unknown action"})
            return
        payload = {}
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            ok, out = fn(payload)
        except Exception as e:
            ok, out = False, str(e)
        log_event(name, ok, json.dumps(payload, ensure_ascii=False), out)
        self._send(200 if ok else 500, {"ok": ok, "output": out})


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>agentctl</title>
<style>
:root{--bg:#0a0a0a;--panel:#111;--line:#232323;--fg:#e8e8e8;--dim:#8a8a8a;
      --pink:#E5188E;--grn:#3ddc84;--yel:#ffc107;--red:#ff4d4d;--cyn:#4dd0e1}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);font:13px/1.55 "SF Mono",Menlo,monospace;
  display:flex;min-height:100vh}
a{color:inherit;text-decoration:none}
nav{width:190px;flex:none;border-right:1px solid var(--line);padding:22px 0}
nav .logo{padding:0 20px 18px;font-weight:700;letter-spacing:.1em}
nav .logo b{color:var(--pink)}
nav a{display:block;padding:9px 20px;color:var(--dim);letter-spacing:.08em;
  font-size:11px;text-transform:uppercase;border-left:2px solid transparent}
nav a.on{color:var(--fg);border-left-color:var(--pink);background:#141414}
nav a:hover{color:var(--fg)}
main{flex:1;padding:26px 30px;max-width:1100px;min-width:0}
h1{font-size:14px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:4px}
.sub{color:var(--dim);font-size:11px;margin-bottom:22px}
.card{background:var(--panel);border:1px solid var(--line);padding:16px 18px;margin-bottom:14px}
.card h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
  border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.aname{color:var(--pink);font-weight:700}
.prov{color:var(--dim);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  border:1px solid var(--line);padding:1px 6px;margin-left:6px;border-radius:2px}
.muted{color:var(--dim)}.small{font-size:11px}
.lim{display:flex;align-items:center;gap:8px;margin:3px 0}
.lim .lab{width:100px;color:var(--dim);font-size:11px;flex:none}
.bar{flex:1;height:8px;background:#1a1a1a;border:1px solid var(--line);min-width:60px}
.bar i{display:block;height:100%}
.lim .pct{width:40px;text-align:right;flex:none}
.lim .rst{width:150px;color:var(--dim);font-size:11px;text-align:right;
  white-space:nowrap;flex:none}
button{background:transparent;border:1px solid var(--pink);color:var(--pink);
  font:inherit;font-size:11px;padding:5px 14px;cursor:pointer;letter-spacing:.05em}
button:hover{background:var(--pink);color:#000}
button.dim{border-color:var(--line);color:var(--dim)}
button.dim:hover{background:#1c1c1c;color:var(--fg)}
select,input,textarea{background:#0e0e0e;border:1px solid var(--line);color:var(--fg);
  font:inherit;padding:6px 8px;width:100%;color-scheme:dark}
textarea{resize:vertical;min-height:52px}
label{color:var(--dim);font-size:11px;display:block;margin:10px 0 3px}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>div{flex:1;min-width:150px}
.list-item{display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:7px 0;border-bottom:1px dashed #1c1c1c}
.list-item:last-child{border-bottom:none}
.tt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.live-dot{color:var(--grn)}
.ev{padding:4px 0;border-bottom:1px dashed #1c1c1c;font-size:11px}
.ev .ok{color:var(--grn)}.ev .fail{color:var(--red)}.ev .ts{color:var(--dim)}
.stat{display:inline-block;margin-right:22px}
.stat b{font-size:19px}.stat span{color:var(--dim);font-size:11px;display:block}
#toast{position:fixed;bottom:18px;right:18px;max-width:460px;max-height:50vh;
  overflow:auto;background:#161616;border:1px solid var(--pink);
  padding:12px 15px;white-space:pre-wrap;display:none;font-size:11px;z-index:9}
.view{display:none}.view.on{display:block}
table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:6px 10px 6px 0;border-bottom:1px dashed #1c1c1c;font-weight:400}
th{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.1em}
@media(max-width:820px){
  body{flex-direction:column}
  nav{width:100%;display:flex;overflow-x:auto;padding:10px 6px;border-right:none;
    border-bottom:1px solid var(--line);align-items:center}
  nav .logo{padding:0 14px;white-space:nowrap}
  nav a{border-left:none;border-bottom:2px solid transparent;white-space:nowrap;padding:8px 12px}
  nav a.on{border-bottom-color:var(--pink);background:none}
  main{padding:18px 14px}
  .lim .rst{display:none}
}
</style></head><body>
<nav><div class="logo">agent<b>ctl</b></div>
  <a href="#home" data-v="home">Home</a>
  <a href="#usage" data-v="usage">Usage</a>
  <a href="#sessions" data-v="sessions">Sessions</a>
  <a href="#renew" data-v="renew">Renew</a>
  <a href="#copy" data-v="copy">Copy</a>
  <a href="#accounts" data-v="accounts">Accounts</a>
  <a href="#activity" data-v="activity">Activity</a>
  <div style="padding:16px 20px"><button class="dim refresh-btn"
    onclick="refresh(true)">↻ refresh usage</button></div>
</nav>
<main>
<div class="view" id="v-home">
  <h1>Home</h1><div class="sub" id="now"></div>
  <div class="card"><h2>Right now</h2><div id="h-stats"></div></div>
  <div class="cards" id="h-accounts"></div>
  <div class="card"><h2>Renew queue <span id="h-daemon"></span></h2><div id="h-queue"></div></div>
</div>

<div class="view" id="v-usage">
  <h1>Usage</h1><div class="sub">session · weekly · weekly-scoped — read-only.
  Fetched only when you ask: the endpoints rate-limit frequent checks.
  <button class="dim refresh-btn" onclick="refresh(true)">↻ refresh usage</button></div>
  <div id="u-accounts"></div>
</div>

<div class="view" id="v-sessions">
  <h1>Live sessions</h1><div class="sub">open terminals per account — renew schedules the default auto-continue for that session and path</div>
  <div class="card" id="s-list"></div>
</div>

<div class="view" id="v-renew">
  <h1>Renew</h1><div class="sub">continue an interrupted session the moment its limit resets</div>
  <div class="card"><h2>Schedule</h2>
    <div class="row">
      <div><label>account</label><select id="r_acct"></select></div>
      <div><label>session (live)</label><select id="r_sess"></select></div>
    </div>
    <label>path — where the session lives (auto-filled, editable)</label>
    <input id="r_path">
    <div class="row">
      <div><label>reset time</label><input type="datetime-local" id="r_reset">
        <button class="dim" style="margin-top:6px" onclick="detectReset()">↺ detect from usage API</button>
        <div class="small muted" id="r_detect_info"></div></div>
      <div><label>delay after reset</label><select id="r_delay">
        <option value="0">immediately</option><option value="5" selected>+5 min</option>
        <option value="10">+10 min</option><option value="15">+15 min</option>
        <option value="20">+20 min</option><option value="25">+25 min</option>
        <option value="30">+30 min</option></select>
      <label>follow-up anchor</label><select id="r_fu">
        <option value="+5h5m" selected>5 h 05 m after continue</option>
        <option value="off">off</option><option value="custom">at a time…</option></select>
      <input type="time" id="r_fu_time" style="display:none;margin-top:6px"></div>
    </div>
    <label>extra query — appended to the continue message (optional)</label>
    <textarea id="r_query" placeholder="e.g. start with the failing test in src/api/"></textarea>
    <button style="margin-top:14px" onclick="renewAdd()">schedule renew</button>
  </div>
  <div class="card"><h2>Queue <span id="r-daemon"></span></h2><div id="r-queue"></div></div>
</div>

<div class="view" id="v-copy">
  <h1>Copy session</h1><div class="sub">fork a session to another account — same or a different path. Exit the source session after copying. Claude accounts only.</div>
  <div class="card">
    <div class="row">
      <div><label>from account</label><select id="c_src"></select></div>
      <div><label>to account</label><select id="c_dst"></select></div>
    </div>
    <label>session</label><select id="c_sess"></select>
    <label>origin path (from the session)</label><input id="c_src_path">
    <label>destination path (edit to re-home the copy)</label><input id="c_dst_path">
    <div class="row" style="margin-top:14px">
      <div><button class="dim" style="width:100%" onclick="copySess(true)">dry-run</button></div>
      <div><button style="width:100%" onclick="copySess(false)">copy</button></div>
    </div>
  </div>
</div>

<div class="view" id="v-accounts">
  <h1>Accounts</h1><div class="sub">every provider's accounts on this Mac — new ones auto-join every command after first login</div>
  <div class="card"><table id="a-table"></table></div>
  <div class="card"><h2>New account</h2>
    <div class="row">
      <div><label>alias (a-z 0-9 -)</label><input id="a_name" placeholder="e.g. work"></div>
      <div><label>provider</label><select id="a_prov">
        <option value="claude">claude</option><option value="codex">codex</option>
      </select></div>
    </div>
    <button style="margin-top:12px" onclick="acctAdd()">scaffold account</button>
    <p class="muted small" style="margin-top:10px">Creates ~/.&lt;provider&gt;-&lt;alias&gt; +
    the shell alias. Then run it in a terminal and log in.</p></div>
</div>

<div class="view" id="v-activity">
  <h1>Activity</h1><div class="sub">scheduler fires, morning anchor pings, and UI actions</div>
  <div class="card"><h2>Scheduler &amp; pings <span class="muted">· agentctl log</span></h2>
    <div id="l-list"></div></div>
  <div class="card"><h2>UI actions <span class="muted">· sqlite</span></h2>
    <div id="e-list"></div></div>
</div>
</main>
<div id="toast" onclick="this.style.display='none'"></div>
<script>
let O=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tilde=p=>String(p||"").replace(/^\/Users\/[^/]+/,"~");
function toast(t,ms=8000){const e=$("toast");e.textContent=t;e.style.display="block";
  clearTimeout(e._t);e._t=setTimeout(()=>e.style.display="none",ms)}
function nav(){const v=(location.hash||"#home").slice(1);
  document.querySelectorAll("nav a").forEach(a=>a.classList.toggle("on",a.dataset.v===v));
  document.querySelectorAll(".view").forEach(d=>d.classList.toggle("on",d.id==="v-"+v))}
window.addEventListener("hashchange",nav);
function barColor(p,k){if(p>=85)return"var(--red)";if(p>=60)return"var(--yel)";
  return k==="session"?"var(--cyn)":"var(--grn)"}
function fmtReset(iso){if(!iso)return"";const d=new Date(iso),n=new Date();
  const t=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});
  return"resets "+(d.toDateString()===n.toDateString()?"":d.toLocaleDateString([],{weekday:"short"})+" ")+t}
function limLabel(l){if(l.kind==="session")return"session (5h)";
  if(l.kind==="weekly_all")return"weekly";
  return"weekly "+(((l.scope||{}).model||{}).display_name||(l.kind==="weekly_scoped"?"scoped":l.kind))}
function limRow(l){return`<div class="lim"><span class="lab">${limLabel(l)}</span>
  <span class="bar"><i style="width:${Math.min(100,l.percent||0)}%;
  background:${barColor(l.percent||0,l.kind)}"></i></span>
  <span class="pct">${l.percent??"?"}%</span>
  <span class="rst">${l.resets_at?fmtReset(l.resets_at):esc(l.reset_text||"")}</span></div>`}
function sessOf(l){return(l||[]).find(x=>x.kind==="session")||{}}
function provTag(a){return a.provider?`<span class="prov">${esc(a.provider)}</span>`:""}
function planLine(u){return u.plan?`<div class="muted small">plan: ${esc(u.plan)}</div>`:""}

let USAGE={},USAGE_AT=null;
async function refresh(withUsage=false){
  const btns=document.querySelectorAll(".refresh-btn");
  btns.forEach(b=>b.textContent="↻ …");
  try{O=await(await fetch("/api/overview?usage="+(withUsage?1:0))).json()}
  catch(e){toast("refresh failed: "+e);btns.forEach(b=>b.textContent="↻ refresh usage");return}
  if(O.usage){USAGE=O.usage;USAGE_AT=O.now}
  O.usage=USAGE;
  btns.forEach(b=>b.textContent="↻ refresh usage");
  $("now").textContent=O.now+(USAGE_AT?`  ·  usage as of ${USAGE_AT}`:"  ·  usage not fetched yet — hit refresh");
  const pend=O.renew.reduce((n,e)=>n+e.actions.filter(a=>!a.done).length,0);
  const liveN=Object.values(O.sessions).reduce((n,l)=>n+l.filter(s=>s.live).length,0);
  $("h-stats").innerHTML=`
    <span class="stat"><b>${O.accounts.length}</b><span>accounts</span></span>
    <span class="stat"><b>${liveN}</b><span>live sessions</span></span>
    <span class="stat"><b>${pend}</b><span>pending renews</span></span>
    <span class="stat"><b style="color:${O.renew_daemon?"var(--grn)":"var(--dim)"}">
      ${O.renew_daemon?"on":"off"}</b><span>renew daemon</span></span>`;
  $("h-accounts").innerHTML=O.accounts.map(a=>{
    const u=O.usage[a.alias]||{},s=sessOf(u.limits);
    return`<div class="card"><span class="aname">${esc(a.alias)}</span>${provTag(a)}
      <span class="muted small"> ${a.logged_in?esc(a.email):"not logged in"}
      · ${a.live_sessions} live</span>
      ${u.limits?limRow(s):`<div class="muted small">${esc(u.error?"✗ "+u.error:"usage not fetched — ↻ refresh")}</div>`}
      <div class="muted small">${(u.limits||[]).filter(l=>l.kind!=="session")
        .map(l=>`${limLabel(l)} ${l.percent}%`).join(" · ")}</div></div>`}).join("");
  const queue=O.renew.map(e=>{
    const acts=e.actions.map(x=>`${x.kind} @ ${x.at.slice(5,16).replace("T"," ")}${x.done?" ✓":""}`).join(" · ");
    return`<div class="list-item"><span class="tt"><span style="color:var(--cyn)">
      ${esc(e.account)}/${esc(e.session)}</span>
      <span class="muted small"> ${tilde(e.path)} · ${acts}</span></span>
      <button class="dim" onclick="renewRemove('${esc(e.session)}','${esc(e.account)}')">cancel</button>
      </div>`}).join("")||'<span class="muted">queue empty — register only interrupted work; the morning anchor job anchors fresh days</span>';
  $("h-queue").innerHTML=queue;$("r-queue").innerHTML=queue;
  const dtxt=O.renew_daemon?"· daemon ✓":"· daemon off";
  $("h-daemon").textContent=dtxt;$("r-daemon").textContent=dtxt;
  $("u-accounts").innerHTML=O.accounts.map(a=>{
    const u=O.usage[a.alias]||{};
    return`<div class="card"><span class="aname">${esc(a.alias)}</span>${provTag(a)}
      <span class="muted small"> ${a.logged_in?esc(a.email):"not logged in"}</span>
      ${planLine(u)}
      ${(u.limits||[]).map(limRow).join("")||`<div class="muted small">${esc(u.error?"✗ "+u.error:"usage not fetched — ↻ refresh")}</div>`}
      </div>`}).join("");
  const provOf={};O.accounts.forEach(a=>provOf[a.alias]=a.provider);
  $("s-list").innerHTML=Object.entries(O.sessions).map(([acct,list])=>
    list.filter(s=>s.live).map(s=>`<div class="list-item">
      <span class="tt"><span class="live-dot">●</span> <b>${esc(s.title||s.name||"(untitled)")}</b>
      <span class="muted small"> ${esc(acct)} · ${esc(provOf[acct]||"")} · ${esc(tilde(s.cwd))}</span></span>
      <button class="dim" onclick="quickRenew('${esc(acct)}','${esc(s.title||s.name||"")}','${esc(s.cwd||"")}')">renew</button>
      </div>`).join("")).join("")||'<span class="muted">no live sessions</span>';
  const opts=O.accounts.filter(a=>a.logged_in).map(a=>`<option>${esc(a.alias)}</option>`).join("");
  const copyOpts=O.accounts.filter(a=>a.logged_in&&a.provider==="claude")
    .map(a=>`<option>${esc(a.alias)}</option>`).join("");
  for(const id of["r_acct"]){const el=$(id),v=el.value;el.innerHTML=opts;if(v)el.value=v}
  for(const id of["c_src","c_dst"]){const el=$(id),v=el.value;
    el.innerHTML=copyOpts;if(v)el.value=v}
  fillRenew();fillCopy();
  $("a-table").innerHTML=`<tr><th>account</th><th>provider</th><th>shell alias</th>
    <th>state</th><th>credential</th><th>live</th></tr>`+O.accounts.map(a=>`<tr>
    <td class="aname">${esc(a.alias)}</td><td class="muted">${esc(a.provider||"")}</td>
    <td>${esc(a.shell_alias||"—")}</td>
    <td>${a.logged_in?"✓ "+esc(a.email):'<span style="color:var(--red)">not logged in</span>'}</td>
    <td>${a.keychain?"✓":"✗"}</td><td>${a.live_sessions}</td></tr>`).join("");
  const KC={"continue":"var(--cyn)","follow-up":"var(--pink)","reset-ping":"var(--grn)",
    "retry":"var(--yel)","gave-up":"var(--red)","scheduled":"#7ec8ff","removed":"var(--yel)"};
  $("l-list").innerHTML=(O.log||[]).slice().reverse().map(e=>`<div class="ev">
    <span class="ts">${esc(e.ts.slice(5,16))}</span>
    <b style="color:${KC[e.kind]||"var(--dim)"}">${esc(e.kind)}</b>
    <span class="muted">${esc(e.detail.slice(0,130))}</span></div>`).join("")
    ||'<span class="muted">nothing yet</span>';
  $("e-list").innerHTML=O.events.map(e=>`<div class="ev">
    <span class="${e.ok?"ok":"fail"}">${e.ok?"✓":"✗"}</span>
    <span class="ts">${e.ts.slice(5,16).replace("T"," ")}</span>
    <b>${esc(e.kind)}</b> <span class="muted">${esc(e.detail.slice(0,110))}</span></div>`).join("")
    ||'<span class="muted">nothing yet</span>';
}
function fillRenew(){
  const rl=(O.sessions[$("r_acct").value]||[]).filter(s=>s.live),v=$("r_sess").value;
  $("r_sess").innerHTML=rl.map(s=>`<option value="${esc(s.title||s.name||"")}"
    data-cwd="${esc(s.cwd||"")}">${esc(s.title||s.name||"(untitled)")}</option>`).join("");
  if(v)$("r_sess").value=v;
  const o=$("r_sess").selectedOptions[0];
  $("r_path").value=o?o.dataset.cwd||"":"";
}
function fillCopy(){
  const cl=O.sessions[$("c_src").value]||[],v=$("c_sess").value;
  $("c_sess").innerHTML=cl.map(s=>`<option value="${esc(s.sid)}"
    data-cwd="${esc(s.cwd||"")}">${esc(s.title||s.sid)} ${s.live?"· LIVE":""}</option>`).join("");
  if(v)$("c_sess").value=v;
  const o=$("c_sess").selectedOptions[0],p=o?o.dataset.cwd||"":"";
  $("c_src_path").value=p;
  if(!$("c_dst_path").value||$("c_dst_path").dataset.auto!=="0")$("c_dst_path").value=p;
}
document.addEventListener("change",e=>{
  if(e.target.id==="r_acct")fillRenew();
  if(e.target.id==="r_sess")fillRenew();
  if(e.target.id==="c_src"||e.target.id==="c_sess"){$("c_dst_path").dataset.auto="1";fillCopy()}
  if(e.target.id==="c_dst_path")e.target.dataset.auto="0";
  if(e.target.id==="r_fu")$("r_fu_time").style.display=
    e.target.value==="custom"?"block":"none";
});
async function detectReset(){
  const acct=$("r_acct").value;
  $("r_detect_info").textContent="detecting…";
  const r=await(await fetch("/api/reset?account="+encodeURIComponent(acct))).json();
  if(r.reset_at){$("r_reset").value=r.reset_at;
    $("r_detect_info").textContent="detected: "+r.reset_at.replace("T"," ")+" ("+r.info+")"}
  else $("r_detect_info").textContent="✗ "+(r.info||"couldn't detect");
}
async function post(action,payload){
  toast("running "+action+"…",60000);
  const r=await(await fetch("/api/"+action,{method:"POST",
    body:JSON.stringify(payload)})).json();
  toast((r.ok?"✓ ":"✗ ")+action+"\n"+r.output.slice(-1500));refresh();return r}
function fuValue(){const v=$("r_fu").value;
  return v==="custom"?($("r_fu_time").value||"+5h5m"):v}
function renewAdd(){
  if(!$("r_reset").value){toast("✗ pick a reset time (or detect it)");return}
  post("renew_add",{account:$("r_acct").value,session:$("r_sess").value,
    path:$("r_path").value||null,reset:$("r_reset").value,
    delay:$("r_delay").value,follow_up:fuValue(),
    query:$("r_query").value||null})}
function quickRenew(acct,sess,cwd){post("renew_add",{account:acct,session:sess,path:cwd||null})}
function renewRemove(s,a){post("renew_remove",{session:s,account:a})}
function copySess(dry){post("copy",{src:$("c_src").value,dst:$("c_dst").value,
  session:$("c_sess").value,src_path:$("c_src_path").value||null,
  dst_path:$("c_dst_path").value||null,dry_run:dry})}
function acctAdd(){const n=$("a_name").value.trim();
  if(n)post("account_add",{alias:n,provider:$("a_prov").value})}
// Manual refresh only — no polling. Opening the page loads the cheap local
// state (accounts, sessions, queue, events); usage is fetched on demand.
nav();refresh(false);
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentctl ui",
                                 description="Localhost dashboard for agentctl.")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}"
    print(f"agentctl ui on {url}  (Ctrl-C to stop; events -> {DB})")
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
