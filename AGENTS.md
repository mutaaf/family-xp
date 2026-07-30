# AGENTS.md — runbook for AI assistants

A parent pasted this repo and said "get me started." Follow this exactly.
Everything is local to their Mac; never send their data anywhere.

## Prerequisites (check first)

- macOS (Darwin). Linux/Windows are not supported yet — say so and stop.
- `/usr/bin/python3` exists (if not: have them run `xcode-select --install`).
- Port 80 free: `lsof -ti :80` should print nothing. If busy, identify the
  process and ask the user before touching it.

## Install

```bash
git clone https://github.com/mutaaf/family-xp ~/family-xp
cd ~/family-xp
FAMILY_NAME="<ask the parent their family name>" ./install.sh
```

The installer is idempotent (an existing config is kept), needs no sudo, and
ends with a machine-readable line:
`SETUP_COMPLETE url=http://<name>family.local/ passcode=<parent passcode>`

## Verify (all three, in order)

```bash
curl -sf http://localhost/api/me            # {"ok": false} = server up
curl -sf http://localhost/api/branding      # shows their family name
dscacheutil -q host -a name <name>family.local   # resolves to LAN IP
```

## Tell the parent (verbatim checklist)

1. Their URL and parent passcode (from SETUP_COMPLETE — also stored in
   `~/.config/azizfamily-portal.json`).
2. Open the URL on any device on home wifi; each kid taps their card and runs
   the setup wizard (name, avatar, passcode) themselves.
3. Parent signs in → Chore Board to post chores → Prize Counter prices are
   editable → put `/leaderboard` on the TV.
4. iPads: Share → Add to Home Screen; download a "Premium" voice in Settings →
   Accessibility → Spoken Content for the best read-aloud voice.

## Troubleshooting

| Symptom | Fix |
|---|---|
| port 80 busy | another web server is running; ask the user |
| hostname won't resolve | `launchctl kickstart -k gui/$UID/com.familyxp.mdns`; use `http://<Mac's IP>/` meanwhile (`ipconfig getifaddr en0`) |
| server down | logs at `~/Library/Logs/azizfamily/portal.log`; `launchctl kickstart -k gui/$UID/com.familyxp.portal` |
| tests (for changes) | `python3 tests/test_portal.py` must stay green |

## Boundaries

- Do NOT expose the portal to the internet, change passcodes without the
  parent, or commit/publish anything under `kids/` (family data).
- For development work on the code itself, read `CLAUDE.md` — it is the
  engineering contract.
