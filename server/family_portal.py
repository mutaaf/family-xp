#!/usr/bin/env python3
"""Family XP portal on port 80 — <yourname>family.local.

Serves the XP-desktop site from ~/projects/family-portal and powers it:

  Static     /                       XP desktop (login UI included)
             /kids/<kid>/<proj>/     kids' published pages   (signed-in only)
  Auth       POST /api/login         passcode -> signed cookie (per-user, 30d)
             POST /api/logout, GET /api/me
  Apps       GET  /api/projects      links + managed apps + kids' projects
             POST /api/app/<id>/start|stop   launch/stop a dev server (parent)
             GET  /api/app/<id>/log          last lines of the app's log
             GET  /app/<id>          302 to the app's port on this host
             /compass, /marketdigest 302 to :8550 (any signed-in user)
  Kids       POST /api/kids/new      {kid, name} -> scaffold a starter page
             GET  /api/kids/file?kid=&proj=   read a project's index.html
             POST /api/kids/file     {kid, proj, content} -> save it

Users (parent/kid roles), passcodes and the signing secret live in
~/.config/azizfamily-portal.json; the launchable-app registry lives in
~/.config/azizfamily-projects.json. Both are outside the web root.

Kids can create/edit only inside their own space; parents everywhere.
macOS allows non-root binding to port 80 since Mojave.
"""

import hashlib
import hmac
import base64
import io
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

PORTAL_DIR = Path.home() / "projects" / "family-portal"
KIDS_DIR = PORTAL_DIR / "kids"
CONFIG_PATH = Path.home() / ".config" / "azizfamily-portal.json"
REGISTRY_PATH = Path.home() / ".config" / "azizfamily-projects.json"
LOG_DIR = Path.home() / "Library" / "Logs" / "azizfamily"
APP_PORT = 8550
COOKIE = "azizportal"
SESSION_SECONDS = 30 * 86400
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

_failed = {}  # ip -> [timestamps of failed logins]

DEFAULT_USERS = [
    {"id": "family", "name": "Our Family", "passcode": "bliss",
     "role": "parent", "avatar": "avatar", "theme": "day", "onboarded": True},
    {"id": "astro", "name": "Astro", "passcode": "",
     "role": "kid", "avatar": "astro", "theme": "sunset", "onboarded": False},
    {"id": "robo", "name": "Robo", "passcode": "",
     "role": "kid", "avatar": "robo", "theme": "spring", "onboarded": False},
]

AVATAR_CHOICES = {"astro", "robo", "cat", "dino"}
THEME_CHOICES = {"day", "sunset", "spring", "night"}


def load_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            cfg = {}
    changed = False
    if not cfg.get("secret"):
        cfg["secret"] = secrets.token_hex(32)
        changed = True
    if not cfg.get("family_name"):
        cfg["family_name"] = "Our"
        changed = True
    if not cfg.get("hostname"):
        cfg["hostname"] = "family.local"
        changed = True
    if not cfg.get("users"):
        # migrate the old single-passcode config
        cfg["users"] = [dict(u) for u in DEFAULT_USERS]
        if cfg.get("passcode"):
            cfg["users"][0]["passcode"] = cfg["passcode"]
        changed = True
    for u in cfg["users"]:
        if "onboarded" not in u:
            # kids run the setup wizard on first sign-in; parents don't
            u["onboarded"] = u.get("role") == "parent"
            changed = True
    if changed:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


CFG = load_config()


def save_users():
    """Persist CFG to disk after in-place user edits."""
    CONFIG_PATH.write_text(json.dumps(CFG, indent=2) + "\n")


def users():
    return CFG.get("users", [])


def user_by_id(uid):
    return next((u for u in users() if u["id"] == uid), None)


_registry_cache = {"mtime": 0, "data": {"links": [], "apps": []}}


def registry():
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
        if mtime != _registry_cache["mtime"]:
            _registry_cache["data"] = json.loads(REGISTRY_PATH.read_text())
            _registry_cache["mtime"] = mtime
    except (OSError, json.JSONDecodeError):
        pass
    return _registry_cache["data"]


def app_entry(app_id):
    return next((a for a in registry().get("apps", []) if a["id"] == app_id), None)


# ---- signed cookie: "<expiry>|<userid>" -----------------------------------
def sign(value: str) -> str:
    mac = hmac.new(CFG["secret"].encode(), value.encode(), hashlib.sha256)
    return f"{value}.{mac.hexdigest()}"


def token_user(token):
    """Return the user dict for a valid token, else None."""
    if not token or "." not in token:
        return None
    value, mac = token.rsplit(".", 1)
    good = hmac.new(CFG["secret"].encode(), value.encode(),
                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        exp, uid = value.split("|", 1)
        if float(exp) < time.time():
            return None
    except ValueError:
        return None
    return user_by_id(uid)


# ---- process manager -------------------------------------------------------
SPAWN_ENV = dict(os.environ,
                 PATH="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""),
                 HOME=str(Path.home()))


_lan_ip = {"ip": "", "ts": 0}


def lan_ip():
    """Mac's LAN IP, cached 60s. Dev servers (Vite etc.) host-block unknown
    hostnames like <name>family.local but always allow raw IPs, so /app
    redirects use this."""
    if time.time() - _lan_ip["ts"] > 60:
        for iface in ("en0", "en1"):
            try:
                out = subprocess.run(["ipconfig", "getifaddr", iface],
                                     capture_output=True, text=True, timeout=3)
                if out.stdout.strip():
                    _lan_ip.update(ip=out.stdout.strip(), ts=time.time())
                    break
            except Exception:
                continue
        else:
            _lan_ip.update(ts=time.time())
    return _lan_ip["ip"]


def port_alive(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def pidfile(app_id):
    return LOG_DIR / f"{app_id}.pid"


def app_pid(app_id):
    try:
        pid = int(pidfile(app_id).read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def start_app(entry):
    if port_alive(entry["port"]):
        return {"ok": True, "note": "already running"}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"{entry['id']}.log", "ab")
    log.write(f"\n===== portal start {time.strftime('%F %T')} =====\n".encode())
    proc = subprocess.Popen(
        entry["cmd"], shell=True, cwd=entry["dir"],
        stdout=log, stderr=log, env=SPAWN_ENV,
        start_new_session=True)   # own process group -> clean stop
    pidfile(entry["id"]).write_text(str(proc.pid))
    return {"ok": True, "pid": proc.pid}


def stop_app(entry):
    pid = app_pid(entry["id"])
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.15)
                if app_pid(entry["id"]) is None:
                    break
            else:
                os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            pidfile(entry["id"]).unlink()
        except OSError:
            pass
        return {"ok": True}
    if port_alive(entry["port"]):
        return {"error": "Running, but it wasn't started from the portal — "
                         "stop it from the terminal."}
    return {"ok": True, "note": "not running"}


# ---- kids' spaces ----------------------------------------------------------
KID_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — an original by {owner}</title>
<meta property="og:site_name" content="Family XP">
<meta property="og:title" content="{title} — hot off the family web press! 👨‍🍳">
<meta property="og:description" content="Handcrafted HTML by {owner}, baked fresh on the family server. Quality-checked by absolutely nobody.">
<meta property="og:image" content="/og.png">
<link rel="icon" type="image/png" href="/favicon-32.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
  /* CSS makes things PRETTY. Try changing these colors! */
  body {{ font-family: "Comic Sans MS", "Chalkboard SE", sans-serif;
         background: linear-gradient(#7ec8ff, #eaf7ff); min-height: 100vh;
         display: flex; flex-direction: column; align-items: center;
         justify-content: center; gap: 20px; margin: 0; text-align: center; }}
  h1 {{ font-size: 3em; color: #1446a0; margin: 0; }}
  button {{ font-size: 1.5em; padding: 12px 24px; border-radius: 14px;
           border: 3px solid #1446a0; background: #ffd23f; cursor: pointer; }}
  button:active {{ transform: scale(.94); }}
</style>
</head>
<body>
  <!-- HTML is the STUFF on the page. Change these words! -->
  <h1>{title}</h1>
  <p>Made by {owner} on the family server 🖥️</p>
  <button onclick="party()">Press me!</button>

<script>
// JavaScript makes the page DO things. This one makes emoji rain!
function party() {{
  for (let i = 0; i < 40; i++) {{
    const e = document.createElement("div");
    e.textContent = ["🎉","⭐","🚀","🤖","🌈"][i % 5];
    e.style.cssText = "position:fixed;top:-40px;font-size:28px;left:" +
      Math.random() * 100 + "vw;transition:top 2.5s linear " +
      Math.random() + "s";
    document.body.appendChild(e);
    setTimeout(() => e.style.top = "110vh", 20);
    setTimeout(() => e.remove(), 4000);
  }}
}}
</script>
<script src="/guestbook.js" defer></script>
</body>
</html>
"""

# ---- curriculum: missions, progress, guestbook, visits ---------------------
MISSIONS_PATH = Path.home() / ".config" / "azizfamily-missions.json"

DEFAULT_MISSIONS = {
    "_help": ("Quest Log missions. Tracks are assigned per kid in 'assign'. "
              "check types: claim (honor button), contains {pattern,n?}, "
              "not_contains {pattern}, projects {n}, visits {n}, "
              "gb_given {n}, robot {level}. Patterns are regexes matched "
              "against the kid's pages. Edit freely; restart the portal."),
    "assign": {"astro": "builder", "robo": "explorer"},
    "tracks": {
        "explorer": [
            {"id": "e1", "title": "Secret Agent Sign-In", "xp": 20,
             "story": "Sign in all by yourself. Your passcode is a SECRET — that's rule one of computers.",
             "std": "Digital citizenship: passwords",
             "check": {"type": "claim"}},
            {"id": "e2", "title": "Word Wizard", "xp": 25,
             "story": "Open the Code Editor and change what your button says. Words on pages are just code you can edit!",
             "std": "HTML: text", "check": {"type": "not_contains", "pattern": "Press me!"}},
            {"id": "e3", "title": "Color Changer", "xp": 25,
             "story": "Find the sky color #7ec8ff in your code and change it to a new color. Try 'hotpink'!",
             "std": "CSS: properties", "check": {"type": "not_contains", "pattern": "#7ec8ff"}},
            {"id": "e4", "title": "Robot Steps", "xp": 30,
             "story": "Open the Robot Playground and beat level 2. Robots only do EXACTLY what you tell them.",
             "std": "Sequences (CSTA 1A-AP-10)", "check": {"type": "robot", "level": 2}},
            {"id": "e5", "title": "Loop-de-Loop", "xp": 40, "badge": "refresh",
             "story": "Beat the Robot Playground level that needs the repeat block. Loops save so much tapping!",
             "std": "Loops (CSTA 1A-AP-10)", "check": {"type": "robot", "level": 5}},
            {"id": "e6", "title": "Master Builder", "xp": 30,
             "story": "Use 'Build Something New!' to make a second project about anything you love.",
             "std": "Creating digital artifacts", "check": {"type": "projects", "n": 2}},
            {"id": "e7", "title": "Famous!", "xp": 25,
             "story": "Get 5 visits on your pages. Every visit is a computer asking our server for your page!",
             "std": "How the web works", "check": {"type": "visits", "n": 5}},
            {"id": "e8", "title": "Kind Visitor", "xp": 25, "badge": "heart",
             "story": "Sign someone else's guestbook with something NICE. The internet remembers what we write.",
             "std": "Digital citizenship: kindness", "check": {"type": "gb_given", "n": 1}},
            {"id": "e9", "title": "Wifi Detective", "xp": 20,
             "story": "Find 5 things in our house that use wifi and tell a parent. (Hint: some are hiding!)",
             "std": "Networks (CSTA 1A-NI-04)", "check": {"type": "claim"}},
            {"id": "e11", "title": "Word Catcher", "xp": 30,
             "story": "Play the Reading Arcade and earn 30 reading XP. The iPad SAYS the word — you find it!",
             "std": "Literacy: sight words", "check": {"type": "reading", "xp": 30}},
            {"id": "e12", "title": "Time Machine", "xp": 30,
             "story": "One of your pages still says your OLD codename. Open the Code Editor, find it, and change it to your real name!",
             "std": "Editing + identity",
             "check": {"type": "none_contain", "pattern": "Robo"}},
            {"id": "e10", "title": "Demo Star", "xp": 50, "badge": "star",
             "story": "Show your page to everyone at Family Demo Day and tell us how you built it!",
             "std": "Communicating about computing", "check": {"type": "claim"}},
        ],
        "builder": [
            {"id": "b1", "title": "Make It Yours", "xp": 20,
             "story": "Open the Code Editor and change what your button says.",
             "std": "HTML: text", "check": {"type": "not_contains", "pattern": "Press me!"}},
            {"id": "b2", "title": "Style Master", "xp": 25,
             "story": "Your heading color is #1446a0. Find it in the CSS and pick your own color.",
             "std": "CSS (CSTA 1B-AP-12: modify programs)",
             "check": {"type": "not_contains", "pattern": "color:\\s*#1446a0"}},
            {"id": "b3", "title": "Headline Act", "xp": 25,
             "story": "Add a brand-new <h2> headline anywhere on a page.",
             "std": "HTML structure", "check": {"type": "contains", "pattern": "<h2"}},
            {"id": "b4", "title": "Picture Perfect", "xp": 30,
             "story": "Add a picture with an <img> tag. Ask a parent to help find an image address.",
             "std": "HTML media", "check": {"type": "contains", "pattern": "<img"}},
            {"id": "b5", "title": "Button Boss", "xp": 35,
             "story": "Add a SECOND button that does something different when clicked.",
             "std": "Events (CSTA 1B-AP-15)",
             "check": {"type": "contains", "pattern": "onclick", "n": 2}},
            {"id": "b6", "title": "List Legend", "xp": 25,
             "story": "Add a list (<ul> and <li>) of your top 3 anythings.",
             "std": "HTML structure", "check": {"type": "contains", "pattern": "<[uo]l"}},
            {"id": "b7", "title": "Serial Builder", "xp": 25,
             "story": "Ship a second project with 'Build Something New!'",
             "std": "Creating digital artifacts", "check": {"type": "projects", "n": 2}},
            {"id": "b8", "title": "Linked Up", "xp": 30,
             "story": "Make a link (<a href=\"...\">) from one of your pages to any other kid page.",
             "std": "Hyperlinks + networks",
             "check": {"type": "contains", "pattern": "href=[\"']/kids/"}},
            {"id": "b9", "title": "Robot Navigator", "xp": 35,
             "story": "Beat Robot Playground level 5 — you'll need the repeat block.",
             "std": "Loops (CSTA 1B-AP-10)", "check": {"type": "robot", "level": 5}},
            {"id": "b10", "title": "Robot Coder", "xp": 45, "badge": "robo",
             "story": "Beat a code-mode level by TYPING the commands. That's real programming syntax.",
             "std": "Programming (CSTA 1B-AP-11)", "check": {"type": "robot", "level": 8}},
            {"id": "b11", "title": "Crowd Pleaser", "xp": 30,
             "story": "Get 10 visits across your pages. Tell the family at dinner!",
             "std": "How the web works", "check": {"type": "visits", "n": 10}},
            {"id": "b12", "title": "Good Neighbor", "xp": 25, "badge": "heart",
             "story": "Sign 2 guestbooks with kind, helpful comments.",
             "std": "Digital citizenship", "check": {"type": "gb_given", "n": 2}},
            {"id": "b13", "title": "The Teacher", "xp": 40,
             "story": "Help your sibling finish one of THEIR missions. Teaching is the final boss of learning.",
             "std": "Collaboration (CSTA 1B-IC-22)", "check": {"type": "claim"}},
            {"id": "b15", "title": "Word Champion", "xp": 40,
             "story": "Earn 60 reading XP in the Reading Arcade. Words are cheat codes for everything else.",
             "std": "Literacy: sight words", "check": {"type": "reading", "xp": 60}},
            {"id": "b16", "title": "Time Machine", "xp": 30,
             "story": "Your oldest page still says your OLD codename. Find every one in the Code Editor and make it yours.",
             "std": "Editing + debugging",
             "check": {"type": "none_contain", "pattern": "Astro"}},
            {"id": "b14", "title": "Demo Star", "xp": 50, "badge": "star",
             "story": "Present a project at Family Demo Day: what it does, and one thing that was hard.",
             "std": "Communicating about computing", "check": {"type": "claim"}},
        ],
    },
}


def missions_cfg():
    if not MISSIONS_PATH.exists():
        MISSIONS_PATH.write_text(json.dumps(DEFAULT_MISSIONS, indent=2) + "\n")
    try:
        return json.loads(MISSIONS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_MISSIONS


_DATA_LOCK = threading.RLock()


def _read_json(path, default):
    """Read main file; on corruption quarantine it and fall back to .bak."""
    with _DATA_LOCK:
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError):
            try:  # quarantine, never overwrite evidence
                path.rename(Path(str(path) + ".corrupt-%d" % int(time.time())))
            except OSError:
                pass
        try:
            return json.loads(Path(str(path) + ".bak").read_text())
        except (OSError, json.JSONDecodeError):
            return default


def _write_json(path, data):
    """Atomic write: serialize+verify, keep previous version as .bak, os.replace."""
    with _DATA_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(data, indent=2) + "\n"
        json.loads(blob)   # never persist something we can't read back
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(blob)
        if path.exists():
            try:
                os.replace(str(path), str(path) + ".bak")
            except OSError:
                pass
        os.replace(str(tmp), str(path))


def ledger(prog, source, delta, note=""):
    """Append-only audit trail for every XP movement."""
    prog.setdefault("ledger", []).append(
        {"ts": time.time(), "source": source, "delta": int(delta),
         **({"note": note[:80]} if note else {})})
    prog["ledger"] = prog["ledger"][-1000:]


def progress_path(kid):
    return KIDS_DIR / kid / ".progress.json"


def stats_path(kid):
    return KIDS_DIR / kid / ".stats.json"


def guestbook_path(kid):
    return KIDS_DIR / kid / ".guestbook.json"


def kid_pages_text(kid):
    out = []
    for p in kid_projects(kid):
        f = kid_file(kid, p["name"])
        if f and f.exists():
            out.append(f.read_text(errors="replace"))
    return out


def run_check(kid, check):
    ctype = check.get("type")
    if ctype == "claim":
        return False   # only completes via explicit claim
    if ctype == "contains":
        n = int(check.get("n", 1))
        pat = re.compile(check.get("pattern", ""), re.I)
        return any(len(pat.findall(text)) >= n for text in kid_pages_text(kid))
    if ctype == "not_contains":
        pat = re.compile(check.get("pattern", ""), re.I)
        pages = kid_pages_text(kid)
        return bool(pages) and any(not pat.search(t) for t in pages)
    if ctype == "none_contain":
        pat = re.compile(check.get("pattern", ""), re.I)
        pages = kid_pages_text(kid)
        return bool(pages) and all(not pat.search(t) for t in pages)
    if ctype == "projects":
        return len(kid_projects(kid)) >= int(check.get("n", 2))
    if ctype == "visits":
        stats = _read_json(stats_path(kid), {})
        return sum(stats.values()) >= int(check.get("n", 5))
    if ctype == "gb_given":
        count = 0
        for other in kid_ids():
            if other == kid:
                continue
            for entry in _read_json(guestbook_path(other), []):
                if entry.get("from_id") == kid:
                    count += 1
        return count >= int(check.get("n", 1))
    if ctype == "reading":
        prog = _read_json(progress_path(kid), {})
        return int(prog.get("reading", {}).get("xp", 0)) >= int(check.get("xp", 30))
    if ctype == "robot":
        prog = _read_json(progress_path(kid), {})
        return int(prog.get("robot", 0)) >= int(check.get("level", 1))
    return False


def kid_track(kid):
    cfg = missions_cfg()
    track = cfg.get("assign", {}).get(kid, "explorer")
    return track, cfg.get("tracks", {}).get(track, [])


def quest_state(kid, claim_id=None):
    """Evaluate all missions; auto-award newly passed ones. Returns state."""
    with _DATA_LOCK:
        return _quest_state_locked(kid, claim_id)


def _quest_state_locked(kid, claim_id):
    _, missions = kid_track(kid)
    prog = _read_json(progress_path(kid), {})
    done = prog.setdefault("done", {})
    changed = False
    revoked = set(prog.get("revoked", []))
    for m in missions:
        if m["id"] in done or m["id"] in revoked:
            continue
        passed = run_check(kid, m.get("check", {}))
        if not passed and claim_id == m["id"] and \
                m.get("check", {}).get("type") == "claim":
            passed = True
        if passed:
            done[m["id"]] = time.time()
            ledger(prog, "quest:" + m["id"], m["xp"], m["title"])
            changed = True
    prog["xp"] = sum(m["xp"] for m in missions if m["id"] in done)
    if changed or not progress_path(kid).exists():
        _write_json(progress_path(kid), prog)
    return {
        "track": kid_track(kid)[0],
        "balance": balance(kid),
        "xp": prog["xp"],
        "level": prog["xp"] // 100 + 1,
        "robot": int(prog.get("robot", 0)),
        "missions": [{**m, "done": m["id"] in done,
                      "revoked": m["id"] in revoked} for m in missions],
        "badges": [m["badge"] for m in missions
                   if m["id"] in done and m.get("badge")],
    }


def kid_ids():
    return [u["id"] for u in users() if u.get("role") == "kid"]


def kid_projects(kid):
    base = KIDS_DIR / kid
    out = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "index.html").exists():
                out.append({"name": d.name,
                            "url": f"/kids/{kid}/{d.name}/",
                            "mtime": (d / "index.html").stat().st_mtime})
    return out


def kid_file(kid, proj):
    """Validated path to a kid project's index.html, or None."""
    if kid not in kid_ids() or not NAME_RE.match(proj or ""):
        return None
    return KIDS_DIR / kid / proj / "index.html"



# ---- chores + prize counter ------------------------------------------------
CHORES_PATH = Path.home() / ".config" / "azizfamily-chores.json"

DEFAULT_CHORES = {
    "_help": ("Chore Board + Prize Counter config. cadence: once|daily|weekly. "
              "kids: \"all\" or a list of kid ids. Prices are in XP. "
              "Editable here or from the parent Chore Board / Control Panel."),
    "chores": [
        {"id": "bed", "title": "Make your bed", "xp": 10, "cadence": "daily", "kids": "all"},
        {"id": "reading", "title": "Read for 20 minutes", "xp": 20, "cadence": "daily", "kids": "all"},
        {"id": "dishes", "title": "Help clear + load the dishes", "xp": 15, "cadence": "daily", "kids": "all"},
        {"id": "kind", "title": "Do something kind for your sibling", "xp": 20, "cadence": "daily", "kids": "all"},
        {"id": "room", "title": "Clean your whole room", "xp": 30, "cadence": "weekly", "kids": "all"},
        {"id": "trash", "title": "Take out the trash + recycling", "xp": 15, "cadence": "weekly", "kids": "all"},
    ],
    "rewards": [
        {"id": "screen", "emoji": "🎮", "title": "30 minutes extra screen time", "price": 150},
        {"id": "dinner", "emoji": "🍕", "title": "You pick the next family dinner", "price": 250},
        {"id": "cash10", "emoji": "💵", "title": "$10 deposited to your savings account", "price": 400},
        {"id": "outing", "emoji": "🎡", "title": "You pick the next weekend outing", "price": 500},
        {"id": "yes", "emoji": "🙏", "title": "One YES to any reasonable ask", "price": 750},
        {"id": "vacation", "emoji": "🌴", "title": "You pick the next family vacation", "price": 2500},
    ],
}

CADENCE_SECONDS = {"daily": 20 * 3600, "weekly": 6 * 86400, "once": None}


def chores_cfg():
    if not CHORES_PATH.exists():
        CHORES_PATH.write_text(json.dumps(DEFAULT_CHORES, indent=2) + "\n")
    try:
        return json.loads(CHORES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CHORES


def kid_chores(kid):
    return [c for c in chores_cfg().get("chores", [])
            if c.get("kids", "all") == "all" or kid in c.get("kids", [])]


def balance(kid):
    prog = _read_json(progress_path(kid), {})
    earned = int(prog.get("xp", 0)) + int(prog.get("chore_xp", 0)) \
        + int(prog.get("xp_adjust", 0)) \
        + int(prog.get("reading", {}).get("xp", 0)) \
        + int(prog.get("streak_xp", 0)) \
        + sum(int(g.get("xp", 0)) for g in prog.get("arcade", {}).values())
    reserved = sum(int(r.get("price", 0))
                   for r in prog.get("redemptions", [])
                   if r.get("status") in ("pending", "granted"))
    return earned - reserved


def chore_state(kid, chore):
    """available | pending | cooldown for this kid right now."""
    prog = _read_json(progress_path(kid), {})
    if any(p.get("chore") == chore["id"] for p in prog.get("chore_pending", [])):
        return "pending"
    window = CADENCE_SECONDS.get(chore.get("cadence", "daily"), 20 * 3600)
    last = max((e.get("ts", 0) for e in prog.get("chore_log", [])
                if e.get("chore") == chore["id"]), default=0)
    if last and (window is None or time.time() - last < window):
        return "cooldown"
    return "available"



MAX_KIDS = 10


def new_kid_id(name):
    base = re.sub(r"[^a-z0-9]", "", (name or "").lower())[:12] or "kid"
    kid, n = base, 1
    while user_by_id(kid):
        n += 1
        kid = f"{base}{n}"
    return kid


def export_zip(kid_list):
    """ZIP of the given kids' folders + a manifest (parent backup)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {"exported_at": time.strftime("%F %T"),
                    "users": [u for u in users() if u["id"] in kid_list]}
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for kid in kid_list:
            base = KIDS_DIR / kid
            if not base.is_dir():
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    zf.writestr(f"{kid}/{f.relative_to(base)}", f.read_bytes())
    return buf.getvalue()


# ---- HTTP handler ----------------------------------------------------------
class PortalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PORTAL_DIR), **kwargs)

    def end_headers(self):
        # iPads cache index.html/js hard; force revalidation so shipped UI
        # changes actually reach devices (304s keep it cheap)
        path = self.path.split("?")[0]
        if path.endswith((".html", ".js")) or path.endswith("/") or path == "":
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _cookie(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(COOKIE + "="):
                return unquote(part[len(COOKIE) + 1:])
        return None

    def _user(self):
        return token_user(self._cookie() or "")

    def _json(self, obj, status=200, set_cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _go(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- GET ----
    def do_GET(self):
        host = (self.headers.get("Host") or "localhost").split(":")[0]
        parsed = urlparse(self.path)
        path = parsed.path
        user = self._user()

        if path == "/api/branding":   # public: login screen needs it pre-auth
            return self._json({"family": CFG.get("family_name", "Our"),
                               "hostname": CFG.get("hostname", "")})

        if path == "/api/me":
            if not user:
                return self._json({"ok": False})
            return self._json({"ok": True, "user": {
                "id": user["id"], "name": user["name"], "role": user["role"],
                "avatar": user.get("avatar", "avatar"),
                "theme": user.get("theme", "day")}})

        if path == "/api/users":   # login tiles (no passcodes!)
            return self._json({"users": [
                {"id": u["id"], "name": u["name"], "role": u["role"],
                 "avatar": u.get("avatar", "avatar"),
                 "onboarded": bool(u.get("onboarded", True))} for u in users()]})

        if path == "/api/admin/export":
            if not user or user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [""])[0]
            kid_list = kid_ids() if kid == "all" else [kid]
            if kid != "all" and kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            blob = export_zip(kid_list)
            fname = f"familyxp-{kid}-{time.strftime('%Y%m%d')}.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.end_headers()
            return self.wfile.write(blob)

        if path == "/api/admin/integrity":
            if not user or user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            issues, report = [], {}
            for kid in kid_ids():
                prog = _read_json(progress_path(kid), None)
                if prog is None:
                    report[kid] = {"balance": 0, "note": "no progress yet"}
                    continue
                bal = balance(kid)
                led = sum(e.get("delta", 0) for e in prog.get("ledger", []))
                granted = sum(int(r.get("price", 0))
                              for r in prog.get("redemptions", [])
                              if r.get("status") == "granted")
                if bal < 0:
                    issues.append("%s: negative balance %d" % (kid, bal))
                for f in (stats_path(kid), guestbook_path(kid)):
                    if f.exists():
                        try:
                            json.loads(f.read_text())
                        except (OSError, json.JSONDecodeError):
                            issues.append("%s: unreadable %s" % (kid, f.name))
                corrupt = list(progress_path(kid).parent.glob("*.corrupt-*"))
                if corrupt:
                    issues.append("%s: %d quarantined corrupt file(s)"
                                  % (kid, len(corrupt)))
                report[kid] = {"balance": bal, "ledger_sum": led,
                               "granted_spend": granted,
                               "ledger_entries": len(prog.get("ledger", []))}
            return self._json({"ok": not issues, "issues": issues,
                               "kids": report})

        if path == "/api/admin/ledger":
            if not user or user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [""])[0]
            if kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            prog = _read_json(progress_path(kid), {})
            return self._json({"ledger": prog.get("ledger", [])[-100:],
                               "balance": balance(kid)})

        if path == "/api/admin/config":
            if not user or user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            return self._json({
                "users": [{k: u.get(k) for k in
                           ("id", "name", "role", "avatar", "theme",
                            "passcode", "onboarded")} for u in users()],
                "missions_raw": MISSIONS_PATH.read_text()
                    if MISSIONS_PATH.exists() else "{}",
                "registry_raw": REGISTRY_PATH.read_text()
                    if REGISTRY_PATH.exists() else "{}",
                "chores_raw": CHORES_PATH.read_text()
                    if CHORES_PATH.exists() else "{}",
                "avatars": sorted(AVATAR_CHOICES | {"avatar"}),
                "themes": sorted(THEME_CHOICES)})

        if path.startswith("/compass") or path.rstrip("/") == "/marketdigest":
            if not user:
                return self._go("/")
            if path.startswith("/compass"):
                return self._go(f"http://{host}:{APP_PORT}{self.path}")
            return self._go(f"http://{host}:{APP_PORT}/")

        if path == "/api/projects":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            reg = registry()
            apps = []
            for a in reg.get("apps", []):
                apps.append({"id": a["id"], "label": a["label"],
                             "icon": a.get("icon", "briefcase"),
                             "desc": a.get("desc", ""), "port": a["port"],
                             "live": port_alive(a["port"]),
                             "startedByPortal": app_pid(a["id"]) is not None})
            links = []
            for link in reg.get("links", []):
                links.append({**link, "live": port_alive(link.get("port", 0))})
            kids = {k: kid_projects(k) for k in kid_ids()}
            return self._json({"links": links, "apps": apps, "kids": kids,
                               "role": user["role"]})

        if path.startswith("/api/app/") and path.endswith("/log"):
            if not user:
                return self._json({"error": "sign in first"}, 401)
            app_id = path[len("/api/app/"):-len("/log")]
            entry = app_entry(app_id)
            if not entry:
                return self._json({"error": "unknown app"}, 404)
            try:
                lines = (LOG_DIR / f"{app_id}.log").read_text(
                    errors="replace").splitlines()[-80:]
            except OSError:
                lines = ["(no log yet)"]
            return self._json({"log": "\n".join(lines)})

        if path.startswith("/app/"):
            if not user:
                return self._go("/")
            entry = app_entry(path[len("/app/"):].strip("/"))
            if not entry:
                return self._json({"error": "unknown app"}, 404)
            # raw IP: hostname-allowlists in Vite/Next/CRA never block IPs
            target = host if host in ("localhost", "127.0.0.1") \
                else (lan_ip() or host)
            return self._go(f"http://{target}:{entry['port']}/")

        if path == "/api/kids/file":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [""])[0]
            proj = (q.get("proj") or [""])[0]
            f = kid_file(kid, proj)
            if not f or not f.exists():
                return self._json({"error": "not found"}, 404)
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "that's not your project"}, 403)
            return self._json({"content": f.read_text(errors="replace")})

        if path == "/api/chores":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            cfg = chores_cfg()
            if user["role"] == "kid":
                kid = user["id"]
                prog = _read_json(progress_path(kid), {})
                return self._json({
                    "balance": balance(kid),
                    "chores": [{**c, "state": chore_state(kid, c)}
                               for c in kid_chores(kid)],
                    "rewards": cfg.get("rewards", []),
                    "redemptions": prog.get("redemptions", [])[-12:]})
            out = {}
            for kid in kid_ids():
                prog = _read_json(progress_path(kid), {})
                out[kid] = {
                    "name": (user_by_id(kid) or {}).get("name", kid),
                    "balance": balance(kid),
                    "pending": prog.get("chore_pending", []),
                    "redemptions": [r for r in prog.get("redemptions", [])
                                    if r.get("status") == "pending"]}
            return self._json({"kids": out, "chores": cfg.get("chores", []),
                               "rewards": cfg.get("rewards", [])})

        if path.rstrip("/") == "/leaderboard":
            if not user:
                return self._go("/")
            self.path = "/leaderboard.html"
            return super().do_GET()

        if path == "/api/leaderboard":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            kids, events = [], []
            for kid in kid_ids():
                u = user_by_id(kid) or {}
                prog = _read_json(progress_path(kid), {})
                st = quest_state(kid)
                stats = _read_json(stats_path(kid), {})
                name = u.get("name", kid)
                kids.append({
                    "id": kid, "name": name, "avatar": u.get("avatar", "robo"),
                    "level": st["level"], "questXp": st["xp"],
                    "balance": balance(kid),
                    "missionsDone": sum(1 for m in st["missions"] if m["done"]),
                    "missionsTotal": len(st["missions"]),
                    "robot": st["robot"],
                    "words": int(prog.get("reading", {}).get("words", 0)),
                    "pages": len(kid_projects(kid)),
                    "visits": sum(stats.values()),
                    "streak": int(prog.get("streak", {}).get("count", 0)),
                    "today": sum(e.get("delta", 0)
                                 for e in prog.get("ledger", [])
                                 if e.get("delta", 0) > 0 and
                                 time.strftime("%F", time.localtime(e.get("ts", 0)))
                                 == time.strftime("%F")),
                    "recent": prog.get("ledger", [])[-3:],
                    "pending": len(prog.get("chore_pending", [])) +
                        sum(1 for r in prog.get("redemptions", [])
                            if r.get("status") == "pending")})
                for e in prog.get("chore_log", [])[-10:]:
                    events.append({"ts": e["ts"], "who": name,
                                   "what": f"earned +{e['xp']} XP", "emoji": "🧹"})
                for r in prog.get("redemptions", []):
                    if r.get("status") == "granted":
                        events.append({"ts": r["ts"], "who": name, "emoji": r.get("emoji", "🎁"),
                                       "what": f"redeemed: {r['title']}"})
                for e in prog.get("xp_log", [])[-5:]:
                    events.append({"ts": e["ts"], "who": name, "emoji": "✨",
                                   "what": f"{'bonus' if e['delta'] > 0 else 'penalty'} "
                                           f"{e['delta']:+d} XP — {e.get('note') or 'from Mom/Dad'}"})
            events.sort(key=lambda e: -e["ts"])
            return self._json({"kids": kids, "events": events[:10],
                               "family": CFG.get("family_name", "Our")})

        if path == "/api/arcade":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [user["id"]])[0]
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "not yours"}, 403)
            prog = _read_json(progress_path(kid), {})
            return self._json({"games": prog.get("arcade", {}),
                               "reading": prog.get("reading", {})})

        if path == "/api/quests":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [user["id"]])[0]
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "not your quest log"}, 403)
            if kid not in kid_ids():
                return self._json({"error": "not a kid account"}, 404)
            return self._json(quest_state(kid))

        if path == "/api/guestbook":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            q = parse_qs(parsed.query)
            kid = (q.get("kid") or [""])[0]
            proj = (q.get("proj") or [""])[0]
            if not kid_file(kid, proj):
                return self._json({"error": "not found"}, 404)
            entries = [e for e in _read_json(guestbook_path(kid), [])
                       if e.get("proj") == proj]
            visits = _read_json(stats_path(kid), {}).get(proj, 0)
            return self._json({"entries": entries[-30:], "visits": visits,
                               "me": user["name"],
                               "owner": (user_by_id(kid) or {}).get("name", kid),
                               "mine": user["id"] == kid})

        if path == "/api/demo":
            if not user:
                return self._json({"error": "sign in first"}, 401)
            pages = []
            for kid in kid_ids():
                owner = user_by_id(kid)
                for p in kid_projects(kid):
                    pages.append({"kid": kid,
                                  "owner": (owner or {}).get("name", kid),
                                  "name": p["name"], "url": p["url"]})
            return self._json({"pages": pages})

        # never serve the server/test sources over HTTP
        if path.startswith(("/server", "/tests", "/bin")):
            return self._json({"error": "not found"}, 404)
        # kids' pages require sign-in; everything else static is the shell
        if path.startswith("/kids/"):
            if not user:
                return self._go("/")
            m = re.match(r"^/kids/([^/]+)/([^/]+)/(index\.html)?$", path)
            if m and kid_file(m.group(1), m.group(2)):
                stats = _read_json(stats_path(m.group(1)), {})
                stats[m.group(2)] = stats.get(m.group(2), 0) + 1
                _write_json(stats_path(m.group(1)), stats)
        if any(seg.startswith(".") for seg in path.split("/") if seg):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    # ---- POST ----
    def do_POST(self):
        with _DATA_LOCK:          # every mutation is serialized: no lost updates
            return self._post_locked()

    def _post_locked(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(min(length, 500_000)) or b"{}")
        except json.JSONDecodeError:
            data = {}
        user = self._user()

        if path == "/api/login":
            ip = self.client_address[0]
            now = time.time()
            tries = [t for t in _failed.get(ip, []) if now - t < 60]
            if len(tries) >= 8:
                _failed[ip] = tries
                return self._json({"error": "Too many tries. Wait a minute."}, 429)
            code = str(data.get("passcode", ""))
            uid = str(data.get("user", ""))
            u = user_by_id(uid)
            fresh_kid = u and u.get("role") == "kid" and not u.get("onboarded")
            if u and (fresh_kid or (code and code == str(u.get("passcode")))):
                _failed.pop(ip, None)
                value = f"{now + SESSION_SECONDS}|{u['id']}"
                cookie = (f"{COOKIE}={quote(sign(value))}; "
                          f"Max-Age={SESSION_SECONDS}; Path=/; HttpOnly; "
                          f"SameSite=Lax")
                return self._json({"ok": True}, set_cookie=cookie)
            tries.append(now)
            _failed[ip] = tries
            return self._json({"error": "Incorrect passcode."}, 403)

        if path == "/api/logout":
            return self._json({"ok": True},
                              set_cookie=f"{COOKIE}=; Max-Age=0; Path=/")

        if not user:
            return self._json({"error": "sign in first"}, 401)

        if path.startswith("/api/app/") and path.endswith("/start"):
            if user["role"] != "parent":
                return self._json({"error": "ask a parent to start apps"}, 403)
            entry = app_entry(path[len("/api/app/"):-len("/start")])
            if not entry:
                return self._json({"error": "unknown app"}, 404)
            return self._json(start_app(entry))

        if path.startswith("/api/app/") and path.endswith("/stop"):
            if user["role"] != "parent":
                return self._json({"error": "ask a parent to stop apps"}, 403)
            entry = app_entry(path[len("/api/app/"):-len("/stop")])
            if not entry:
                return self._json({"error": "unknown app"}, 404)
            return self._json(stop_app(entry))

        if path == "/api/kids/new":
            kid = str(data.get("kid", ""))
            raw = str(data.get("name", "")).strip().lower()
            proj = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:40]
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "you can only build in your own space"}, 403)
            f = kid_file(kid, proj)
            if not f:
                return self._json({"error": "pick a name with letters or numbers"}, 400)
            if f.exists():
                return self._json({"error": "you already have a project with that name"}, 400)
            owner = user_by_id(kid)
            f.parent.mkdir(parents=True, exist_ok=True)
            title = str(data.get("name", proj)).strip()[:60] or proj
            f.write_text(KID_TEMPLATE.format(
                title=title, owner=(owner or {}).get("name", "me")))
            return self._json({"ok": True, "name": proj,
                               "url": f"/kids/{kid}/{proj}/"})

        if path == "/api/onboard":
            if user["role"] != "kid":
                return self._json({"error": "only kids run the setup wizard"}, 403)
            name = str(data.get("name", "")).strip()[:24]
            avatar = str(data.get("avatar", ""))
            theme = str(data.get("theme", ""))
            passcode = str(data.get("passcode", "")).strip()
            if len(name) < 1:
                return self._json({"error": "tell us your name!"}, 400)
            if avatar not in AVATAR_CHOICES or theme not in THEME_CHOICES:
                return self._json({"error": "pick a look and a world"}, 400)
            if not (3 <= len(passcode) <= 30):
                return self._json({"error": "passcode needs at least 3 letters"}, 400)
            u = user_by_id(user["id"])
            u.update(name=name, avatar=avatar, theme=theme,
                     passcode=passcode, onboarded=True)
            save_users()
            return self._json({"ok": True})

        if path == "/api/admin/quest":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("kid", ""))
            if kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            mid = str(data.get("mission", ""))
            _, missions = kid_track(kid)
            if not any(m["id"] == mid for m in missions):
                return self._json({"error": "unknown mission"}, 404)
            prog = _read_json(progress_path(kid), {})
            done = prog.setdefault("done", {})
            revoked = set(prog.get("revoked", []))
            m = next(x for x in missions if x["id"] == mid)
            if data.get("done"):
                if mid not in done:
                    done[mid] = time.time()
                    ledger(prog, "quest_parent:" + mid, m["xp"], "parent marked done")
                revoked.discard(mid)
            else:
                if mid in done:
                    done.pop(mid)
                    ledger(prog, "quest_undo:" + mid, -m["xp"], "parent undo")
                revoked.add(mid)   # sticky: auto-checks can't instantly re-award
            prog["revoked"] = sorted(revoked)
            _write_json(progress_path(kid), prog)
            return self._json(quest_state(kid))

        if path == "/api/admin/missions":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("kid", ""))
            if kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            new = data.get("missions")
            if not isinstance(new, list) or not new:
                return self._json({"error": "missions list required"}, 400)
            ok_types = {"claim", "contains", "not_contains", "none_contain",
                        "projects", "visits", "gb_given", "robot", "reading"}
            clean, seen = [], set()
            for m in new:
                mid = str(m.get("id", "")).strip()
                title = str(m.get("title", "")).strip()[:60]
                if not mid or mid in seen or not title:
                    return self._json({"error": f"bad mission: {mid or title}"}, 400)
                check = m.get("check") or {"type": "claim"}
                if check.get("type") not in ok_types:
                    return self._json({"error": f"bad check on {mid}"}, 400)
                seen.add(mid)
                clean.append({"id": mid, "title": title,
                              "story": str(m.get("story", ""))[:300],
                              "std": str(m.get("std", ""))[:80],
                              "xp": max(0, min(500, int(m.get("xp", 10) or 10))),
                              **({"badge": m["badge"]} if m.get("badge") else {}),
                              "check": check})
            cfg = missions_cfg()
            track = cfg.get("assign", {}).get(kid, "explorer")
            cfg.setdefault("tracks", {})[track] = clean
            MISSIONS_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
            return self._json(quest_state(kid))

        if path == "/api/admin/xp":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("kid", ""))
            if kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            delta = max(-10000, min(10000, int(data.get("delta", 0) or 0)))
            if not delta:
                return self._json({"error": "delta required"}, 400)
            prog = _read_json(progress_path(kid), {})
            prog["xp_adjust"] = int(prog.get("xp_adjust", 0)) + delta
            ledger(prog, "parent_adjust", delta, str(data.get("note", "")))
            prog.setdefault("xp_log", []).append(
                {"delta": delta, "note": str(data.get("note", ""))[:80],
                 "ts": time.time()})
            prog["xp_log"] = prog["xp_log"][-100:]
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "balance": balance(kid)})

        if path == "/api/admin/adduser":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            if len(kid_ids()) >= MAX_KIDS:
                return self._json(
                    {"error": f"that's the limit — {MAX_KIDS} kids max!"}, 400)
            name = str(data.get("name", "")).strip()[:24] or "New Kid"
            kid = new_kid_id(name)
            avatars = sorted(AVATAR_CHOICES)
            themes = sorted(THEME_CHOICES)
            n = len(kid_ids())
            CFG["users"].append({
                "id": kid, "name": name, "passcode": "", "role": "kid",
                "avatar": avatars[n % len(avatars)],
                "theme": themes[n % len(themes)], "onboarded": False})
            save_users()
            (KIDS_DIR / kid).mkdir(parents=True, exist_ok=True)
            return self._json({"ok": True, "id": kid})

        if path == "/api/admin/removeuser":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("id", ""))
            target = user_by_id(kid)
            if not target or target.get("role") != "kid":
                return self._json({"error": "can only remove kid accounts"}, 404)
            CFG["users"] = [u for u in users() if u["id"] != kid]
            save_users()
            base = KIDS_DIR / kid
            if base.is_dir():   # archive, never delete — dot-dir is unservable
                arc = KIDS_DIR / ".archive"
                arc.mkdir(parents=True, exist_ok=True)
                base.rename(arc / f"{kid}-{int(time.time())}")
            return self._json({"ok": True, "archived": base is not None})

        if path == "/api/admin/user":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            u = user_by_id(str(data.get("id", "")))
            if not u:
                return self._json({"error": "no such user"}, 404)
            if data.get("reset"):
                u.update(passcode="", onboarded=False)
            else:
                name = str(data.get("name", "")).strip()[:24]
                if name:
                    u["name"] = name
                if data.get("avatar") in AVATAR_CHOICES | {"avatar"}:
                    u["avatar"] = data["avatar"]
                if data.get("theme") in THEME_CHOICES:
                    u["theme"] = data["theme"]
                pc = str(data.get("passcode", "")).strip()
                if pc:
                    u["passcode"] = pc
                    u["onboarded"] = True
            save_users()
            return self._json({"ok": True})

        if path == "/api/admin/save":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            which = str(data.get("which", ""))
            content = str(data.get("content", ""))
            target = {"missions": MISSIONS_PATH,
                      "registry": REGISTRY_PATH,
                      "chores": CHORES_PATH}.get(which)
            if not target:
                return self._json({"error": "unknown config"}, 404)
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                return self._json({"error": f"Not valid JSON: {e}"}, 400)
            target.write_text(content if content.endswith("\n") else content + "\n")
            return self._json({"ok": True})

        if path == "/api/chores/claim":
            if user["role"] != "kid":
                return self._json({"error": "chores are for kids — nice try"}, 403)
            kid = user["id"]
            chore = next((c for c in kid_chores(kid)
                          if c["id"] == str(data.get("chore"))), None)
            if not chore:
                return self._json({"error": "unknown chore"}, 404)
            if chore_state(kid, chore) != "available":
                return self._json({"error": "that one's already claimed or done"}, 400)
            prog = _read_json(progress_path(kid), {})
            prog.setdefault("chore_pending", []).append(
                {"chore": chore["id"], "title": chore["title"],
                 "xp": chore["xp"], "ts": time.time()})
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True})

        if path == "/api/chores/approve":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("kid", ""))
            if kid not in kid_ids():
                return self._json({"error": "unknown kid"}, 404)
            prog = _read_json(progress_path(kid), {})
            ts = float(data.get("ts", 0))
            pend = prog.get("chore_pending", [])
            claim = next((p for p in pend if abs(p["ts"] - ts) < 0.001), None)
            if not claim:
                return self._json({"error": "claim not found"}, 404)
            pend.remove(claim)
            if data.get("ok"):
                prog["chore_xp"] = int(prog.get("chore_xp", 0)) + int(claim["xp"])
                ledger(prog, "chore:" + claim["chore"], claim["xp"], claim["title"])
                prog.setdefault("chore_log", []).append(
                    {"chore": claim["chore"], "xp": claim["xp"], "ts": time.time()})
                prog["chore_log"] = prog["chore_log"][-300:]
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "balance": balance(kid)})

        if path == "/api/rewards/redeem":
            if user["role"] != "kid":
                return self._json({"error": "the prize counter is for kids"}, 403)
            kid = user["id"]
            reward = next((r for r in chores_cfg().get("rewards", [])
                           if r["id"] == str(data.get("reward"))), None)
            if not reward:
                return self._json({"error": "unknown prize"}, 404)
            if balance(kid) < int(reward["price"]):
                return self._json({"error": "not enough XP yet — keep going!"}, 400)
            prog = _read_json(progress_path(kid), {})
            if any(r.get("status") == "pending"
                   for r in prog.get("redemptions", [])):
                return self._json(
                    {"error": "one prize at a time — a parent has to OK it first"}, 400)
            ledger(prog, "redeem:" + reward["id"], -int(reward["price"]), reward["title"])
            prog.setdefault("redemptions", []).append(
                {"reward": reward["id"], "title": reward["title"],
                 "emoji": reward.get("emoji", "🎁"), "price": reward["price"],
                 "ts": time.time(), "status": "pending"})
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "balance": balance(kid)})

        if path == "/api/rewards/resolve":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            kid = str(data.get("kid", ""))
            prog = _read_json(progress_path(kid), {})
            ts = float(data.get("ts", 0))
            r = next((x for x in prog.get("redemptions", [])
                      if abs(x["ts"] - ts) < 0.001 and x["status"] == "pending"), None)
            if not r:
                return self._json({"error": "redemption not found"}, 404)
            r["status"] = "granted" if data.get("ok") else "denied"
            if r["status"] == "denied":
                ledger(prog, "refund:" + r.get("reward", "?"), int(r.get("price", 0)))
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "balance": balance(kid)})

        if path == "/api/chores/config":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            cfg = chores_cfg()
            for key in ("chores", "rewards"):
                if isinstance(data.get(key), list):
                    cfg[key] = data[key]
            CHORES_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
            return self._json({"ok": True})

        if path == "/api/admin/reset":
            if user["role"] != "parent":
                return self._json({"error": "parents only"}, 403)
            scope = str(data.get("scope", ""))
            if scope == "progress":
                kid = str(data.get("kid", ""))
                if kid not in kid_ids():
                    return self._json({"error": "unknown kid"}, 404)
                pf = progress_path(kid)
                arc = KIDS_DIR / ".archive"
                arc.mkdir(parents=True, exist_ok=True)
                if pf.exists():
                    pf.rename(arc / ("progress-%s-%d.json" % (kid, int(time.time()))))
                for suffix in (".bak", ".tmp"):
                    try:
                        Path(str(pf) + suffix).unlink()
                    except OSError:
                        pass
                return self._json({"ok": True, "note": "progress archived + reset"})
            if scope == "all":
                arc = KIDS_DIR / ".archive"
                arc.mkdir(parents=True, exist_ok=True)
                now = int(time.time())
                for kid in kid_ids():
                    pf = progress_path(kid)
                    if pf.exists():
                        pf.rename(arc / ("progress-%s-%d.json" % (kid, now)))
                    for suffix in (".bak", ".tmp"):
                        try:
                            Path(str(pf) + suffix).unlink()
                        except OSError:
                            pass
                    _, missions = kid_track(kid)
                    fresh = {"revoked": sorted(
                        m["id"] for m in missions
                        if m.get("check", {}).get("type") != "claim"
                        and run_check(kid, m.get("check", {})))}
                    _write_json(pf, fresh)
                return self._json({"ok": True,
                                   "note": "all kids archived + zeroed; "
                                           "already-earned page missions are "
                                           "switched off until re-allowed"})
            if scope in ("missions", "chores"):
                target = {"missions": MISSIONS_PATH, "chores": CHORES_PATH}[scope]
                arc = Path(str(target) + ".pre-reset-%d" % int(time.time()))
                if target.exists():
                    target.rename(arc)
                return self._json({"ok": True,
                                   "note": "restored defaults; old file kept at "
                                           + arc.name})
            return self._json({"error": "scope: progress|missions|chores"}, 400)

        if path == "/api/quests/check":
            kid = user["id"] if user["role"] != "parent" else \
                str(data.get("kid", ""))
            if kid not in kid_ids():
                return self._json({"error": "not a kid account"}, 404)
            before = {m["id"] for m in quest_state(kid)["missions"] if m["done"]}
            state = quest_state(kid, claim_id=str(data.get("mission", "")))
            after = {m["id"] for m in state["missions"] if m["done"]}
            return self._json({**state, "new": sorted(after - before)})

        if path == "/api/streak":
            if user["role"] != "kid":
                return self._json({"ok": True, "count": 0, "awarded": 0})
            kid = user["id"]
            prog = _read_json(progress_path(kid), {})
            st = prog.setdefault("streak", {"last": "", "count": 0})
            today = time.strftime("%F")
            yesterday = time.strftime("%F", time.localtime(time.time() - 86400))
            awarded = 0
            if st["last"] != today:
                st["count"] = st["count"] + 1 if st["last"] == yesterday else 1
                st["last"] = today
                awarded = 5 + (25 if st["count"] % 7 == 0 else 0)
                prog["streak_xp"] = int(prog.get("streak_xp", 0)) + awarded
                ledger(prog, "streak", awarded, "day %d" % st["count"])
                _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "count": st["count"],
                               "awarded": awarded, "balance": balance(kid)})

        if path == "/api/arcade":
            game = str(data.get("game", ""))
            if game not in ("math", "word", "memory", "circuit", "physics", "court", "gridiron"):
                return self._json({"error": "unknown game"}, 400)
            kid = user["id"]   # parents get levels (demo mode) but never XP
            prog = _read_json(progress_path(kid), {})
            g = prog.setdefault("arcade", {}).setdefault(
                game, {"xp": 0, "today": "", "today_xp": 0})
            today = time.strftime("%F")
            if g.get("today") != today:
                g["today"] = today
                g["today_xp"] = 0
            earn = max(0, min(30, int(data.get("xp", 0) or 0)))
            if user["role"] != "kid":
                earn = 0
            grant = min(earn, max(0, 120 - g["today_xp"]))  # 120 XP/day/game
            g["xp"] += grant
            g["today_xp"] += grant
            if grant:
                ledger(prog, "game:" + game, grant)
            lvl = max(0, min(2000, int(data.get("level", 0) or 0)))
            if lvl > int(g.get("level", 1)):
                g["level"] = lvl
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "granted": grant,
                               "level": g.get("level", 1),
                               "balance": balance(kid)})

        if path == "/api/reading":
            if user["role"] != "kid":
                return self._json({"ok": True, "note": "parents read fine"})
            kid = user["id"]
            prog = _read_json(progress_path(kid), {})
            r = prog.setdefault("reading", {
                "xp": 0, "words": 0, "streak": 0, "level": 0,
                "today": "", "today_xp": 0})
            today = time.strftime("%F")
            if r.get("today") != today:
                r["today"] = today
                r["today_xp"] = 0
            earn = max(0, min(40, int(data.get("xp", 0) or 0)))
            grant = min(earn, max(0, 150 - r["today_xp"]))  # 150 XP/day cap
            r["xp"] += grant
            r["today_xp"] += grant
            if grant:
                ledger(prog, "reading", grant)
            r["words"] += max(0, min(30, int(data.get("words", 0) or 0)))
            r["streak"] = max(r["streak"], min(50, int(data.get("streak", 0) or 0)))
            r["level"] = max(r["level"], min(2000, int(data.get("level", 0) or 0)))
            _write_json(progress_path(kid), prog)
            return self._json({"ok": True, "granted": grant, "reading": r,
                               "balance": balance(kid)})

        if path == "/api/robot":
            if user["role"] != "kid":
                return self._json({"ok": True, "note": "parents play for fun"})
            level = max(0, min(20, int(data.get("level", 0) or 0)))
            prog = _read_json(progress_path(user["id"]), {})
            if level > int(prog.get("robot", 0)):
                prog["robot"] = level
                _write_json(progress_path(user["id"]), prog)
            return self._json({"ok": True, "robot": prog.get("robot", 0)})

        if path == "/api/guestbook":
            kid = str(data.get("kid", ""))
            proj = str(data.get("proj", ""))
            msg = str(data.get("msg", "")).strip()[:300]
            if not kid_file(kid, proj):
                return self._json({"error": "not found"}, 404)
            if not msg:
                return self._json({"error": "write something first!"}, 400)
            entries = _read_json(guestbook_path(kid), [])
            entries.append({"proj": proj, "from_id": user["id"],
                            "from": user["name"], "msg": msg,
                            "ts": time.time()})
            _write_json(guestbook_path(kid), entries[-200:])
            return self._json({"ok": True})

        if path == "/api/kids/art":
            kid = str(data.get("kid", ""))
            proj = str(data.get("proj", ""))
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "that's not your page"}, 403)
            f = kid_file(kid, proj)
            if not f or not f.exists():
                return self._json({"error": "make a project first!"}, 404)
            png = str(data.get("png", ""))
            prefix = "data:image/png;base64,"
            if not png.startswith(prefix) or len(png) > 2_000_000:
                return self._json({"error": "bad image"}, 400)
            try:
                blob = base64.b64decode(png[len(prefix):])
            except Exception:
                return self._json({"error": "bad image"}, 400)
            name = f"art-{int(time.time())}.png"
            (f.parent / name).write_bytes(blob)
            html = f.read_text(errors="replace")
            tag = (f'\n<img src="{name}" alt="my drawing" '
                   f'style="max-width:90%;border-radius:12px">\n')
            html = html.replace("</body>", tag + "</body>") \
                if "</body>" in html else html + tag
            f.write_text(html)
            return self._json({"ok": True, "url": f"/kids/{kid}/{proj}/{name}"})

        if path == "/api/kids/file":
            kid = str(data.get("kid", ""))
            proj = str(data.get("proj", ""))
            content = str(data.get("content", ""))
            if user["role"] != "parent" and user["id"] != kid:
                return self._json({"error": "that's not your project"}, 403)
            f = kid_file(kid, proj)
            if not f or not f.exists():
                return self._json({"error": "not found"}, 404)
            if len(content) > 400_000:
                return self._json({"error": "too big"}, 400)
            f.write_text(content)
            return self._json({"ok": True})

        return self._json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    PORTAL_DIR.mkdir(parents=True, exist_ok=True)
    KIDS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORTAL_PORT", 80))), PortalHandler).serve_forever()
