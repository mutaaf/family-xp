# Family XP 🏠

> **Home is where the host is.** A Windows-XP-style family OS you run on a Mac
> in your living room — chores become XP, XP becomes real-world prizes, and the
> built-in arcade teaches reading, spelling, math, memory, circuits, physics
> and sports IQ. No cloud, no accounts, no ads. Works with the internet down.

![Family XP](og.png)

## Quick start (macOS)

```bash
git clone https://github.com/mutaaf/family-xp && cd family-xp
./install.sh
```

Answer one question (your family name) and open `http://<name>family.local/`
from any device in the house. Kids tap their sign-in card and run an XP-style
setup wizard: real name, avatar, wallpaper world, secret passcode.

## What's inside

| Who | Gets |
|---|---|
| **Kids** | Their own desktop + webpage projects with an in-browser code editor, Quest Log (CSTA-mapped missions auto-verified against their real code), Robot Playground, Reading Arcade & Spelling Bee (listen-first, TTS), Math Blaster, Memory Match, Circuit Lab, a Matter.js Physics Lab, Court Vision & Gridiron IQ, Paint (drawings publish to their page), Chore Board and a Prize Counter |
| **Parents** | Chore/prize management with approvals, quest editing, XP overrides with an audit ledger, Control Panel (up to 10 kids, exports, resets, integrity checks), a living-room TV leaderboard at `/leaderboard`, nightly backups |

Everything is bottomless: games scale procedurally past level 1000, with
per-kid levels persisted server-side.

## Design commitments

- **Local-only**: one zero-dependency Python file on the stock macOS python3;
  all data stays in your house. `kids/` holds family data and is gitignored.
- **Listening-first**: every kid-facing feature speaks (Web Speech API), so
  pre-readers can use all of it.
- **Bulletproof XP**: atomic writes with automatic backups, corruption
  quarantine + recovery, serialized mutations, an append-only XP ledger, and a
  one-tap integrity checker. Parents approve every spend.
- **Family-trust security**: passcodes gate the UI; this is not hardened
  against a hostile actor already on your LAN.

## Develop

`python3 tests/test_portal.py` runs the end-to-end suite (36 tests). CI runs
it on Python 3.9 + 3.12 plus JS/asset checks. See `CLAUDE.md` for the working
agreement (works great with AI coding agents) and `docs/BACKLOG.md` for ideas.

MIT licensed. Built with ❤️ (and a lot of pixel art) for the home LAN.
