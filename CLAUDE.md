# CLAUDE.md — agent guide for Family XP

If a user just asked you to INSTALL/set this up: follow `AGENTS.md` instead.
This file is the contract for changing code.

Read this fully before changing anything. `README.md` = human overview,
`docs/BACKLOG.md` = what to build next, `tests/test_portal.py` = executable
spec, `README-CLAUDE.md` = historical build notes.

## What this is

A Windows-XP-styled family portal on the home LAN at `http://<name>family.local/`
(this Mac, port 80). Parents manage dev apps + a chore/XP economy; kids
(account ids never change once created) build webpages, do
quest missions, play learning games, and spend XP at a Prize Counter. A TV
dashboard lives at `/leaderboard`. **Real children use this daily on iPads and
iPhones — treat it as production.** Some kids may not read yet: every kid-facing feature must work listening-first (TTS via
`speak()`), and reading practice is a core product goal.

## Feature inventory (all live)

- **Desktop OS**: boot → per-user login tiles → OOBE setup wizard (first
  sign-in: name/avatar/theme/passcode) → themed Bliss desktop, taskbar, Start
  menu, draggable windows, balloons, Klippy assistant, idle screensaver
  (starfield + bouncing logo; corner hit = confetti).
- **Kids**: page projects (`/kids/<id>/<proj>/`) + in-browser Code Editor w/
  snippet palette; Quest Log (CSTA missions, auto-checked vs their real HTML);
  Robot Playground (8 levels, sequences→loops→typed code); Reading Arcade
  (listen-first Dolch sight words); Math Blaster (spoken questions,
  easy/hard); Spelling Bee (hear-then-build); Memory Match; Circuit Lab (3 rotating modes: switch puzzles, conductor tests, wire-hunt
  troubleshooting, with animated electron flow); Physics Lab (Matter.js slingshot: towers, momentum, Earth/Moon/Jupiter gravity); Court Vision (basketball-IQ 5v5 reads + court-zone/play vocabulary); Gridiron IQ (football downs/clock/terms). All arcade games have
  ENDLESS procedural levels (1..2000) persisted via POST /api/arcade
  {game,xp,level} (never regresses; resume via GET /api/arcade). TTS uses a
  friendly female voice (pickVoice()). Paint ("Send to my page" → PNG onto their site); Chore Board;
  Prize Counter; guestbooks + hit counters on pages; login streaks; Today's
  Adventure (daily auto-window: spoken word-of-day + rotating activities).
- **Parents**: Quest management in each kid's Quest Log (toggle completions,
  inline edit/delete/add missions via /api/admin/quest + /api/admin/missions);
  My Projects (start/stop/log real dev servers; `/app/<id>`
  redirects use LAN IP because Vite/Next/CRA block unknown hostnames); Chore
  Board HQ (approve claims/prizes, edit chores+prizes, XP grant/deduct w/
  note); Control Panel (add up to 10 kids, remove→archives to
  `kids/.archive/`, per-kid + all ZIP export, rename/avatar/theme/passcode,
  redo-setup, JSON editors for missions/registry/chores); Demo Day slideshow.
- **TV**: `/leaderboard` — dark glass dashboard, 8s poll, confetti on XP
  gains, leader-change splash, tap-to-zoom, 4K-scaled units.

## XP accounting (the contract)

```
level      = quest_xp // 100 + 1            (never decreases)
balance    = quest_xp + chore_xp + xp_adjust + reading.xp
             + streak_xp + Σ arcade[game].xp
             − Σ redemptions (pending + granted)
daily caps: reading 100/day · each arcade game 60/day · streak +5/day (+25 each 7th)
```
Kids can never self-award: chores need parent approval; redemptions reserve XP
until a parent grants/denies; quest checks verify real artifacts server-side.

## Map

| Path | What |
|---|---|
| `index.html` | ENTIRE desktop UI (inline CSS+JS, no build, no CDNs) |
| `server/family_portal.py` | zero-dep server; MUST run on `/usr/bin/python3` (3.9) |
| `leaderboard.html` | TV dashboard |
| `guestbook.js` | injected into kid pages |
| `kids/` | FAMILY DATA — gitignored; never delete/commit; export via Control Panel |
| `tests/test_portal.py` | e2e suite (isolated $HOME); run before every restart/push |
| `.github/workflows/ci.yml` | CI: py3.9+3.12 tests, JS syntax, assets |
| `bin/deploy.sh` / `bin/watchdog.sh` | CD pull-loop / self-heal w/ circuit breaker |

Config (all outside web root; editable in Control Panel → Advanced):
`~/.config/azizfamily-portal.json` (users+secret), `azizfamily-projects.json`
(dev apps), `azizfamily-missions.json` (quest tracks; **defaults in server only
seed a MISSING file** — to change live missions, edit the config too),
`azizfamily-chores.json` (chores+prizes).

launchd: `com.azizfamily.portal|mdns|deploy|watchdog`.
Logs in `~/projects/market-digest/logs/` (portal, mdns, deploy, watchdog).

## Invariants — do not break

1. Python 3.9, stdlib only. No walrus in hot paths untested, no `match`, no pip.
2. No external NETWORK deps in any page (works with internet down). Libraries
   may be VENDORED into `vendor/` (self-hosted, e.g. matter.min.js for the
   physics engine) — never loaded from a CDN.
3. Kid ids permanent; renames touch `name` only.
4. Dotfiles + `/server` `/tests` `/bin` never served.
5. Roles enforced server-side FIRST in every route.
6. iOS first: inputs ≥16px on touch, big targets, TTS-first for kid features,
   `pointer: coarse` single-tap, windows maximize on phones.
7. XP contract above; parents approve everything.
8. Tests green before kickstart AND before push. CI must stay green.
9. DATA INTEGRITY (v1 contract): all JSON writes are atomic (tmp + os.replace)
   with a .bak of the previous version; corrupt files are quarantined
   (`*.corrupt-<ts>`) and reads fall back to .bak; every mutation is serialized
   under _DATA_LOCK; every XP movement lands in the per-kid append-only
   `ledger`; nightly ZIP backups (com.azizfamily.backup → ~/Backups/azizfamily,
   30 kept). Parent tools: /api/admin/integrity, /api/admin/ledger,
   /api/admin/reset {scope: progress|missions|chores}. Reset keeps kid pages,
   so auto-verified page missions re-award by design.

## Workflow (dev happens ON the prod machine)

```bash
python3 tests/test_portal.py                             # green?
launchctl kickstart -k gui/$UID/com.azizfamily.portal    # apply local edits
git commit && git push                                   # CI; CD is for pulls only
```
Never force-push main. Never edit `index.html`/server with sed-like blind
replaces without running the JS check:
`node --check <(python3 -c "import re;print('\n;\n'.join(re.findall(r'<script>(.*?)</script>', open('index.html').read(), re.S)))")`

## Extension patterns

- Window: `makeWindow(title, icon, html, width)`; delegate events on returned el.
- Desktop icon / Start menu: `desktopItems()` + `smAdd()` (parent/kid branches).
- Pixel icon: 16×16 grid in `ICON_ART`; palette `PAL`.
- API route: role-check → `self._json`; add a test.
- Mission check types: claim, contains{pattern,n}, not_contains (ANY page
  lacks), none_contain (NO page has), projects{n}, visits{n}, gb_given{n},
  robot{level}, reading{xp}.
- Sounds `blip/ding/tada/chime`, `speak(text)` TTS, `confetti()`.
- Kid XP from a new game: POST `/api/arcade {game, xp}` — whitelist the game
  name server-side, respect the 60/day cap pattern.
