# Backlog — ideas a fresh agent (or human) can pick up

## North star (read this first)

Family XP exists to turn a household's spare computer into the most engaging
teacher and family-glue in the house. Every feature must serve at least one of:
**kids grow** (literacy first — listening-first always), **family bonds**
(shared rituals beat solo screens), **kids create** (real artifacts: pages,
art, code), or **delight** (nostalgia + confetti). Hard constraints: local-only,
no external network deps, parents approve all spends, children are the users.

## Vision features (highest engagement-per-effort, in rough order)

- [ ] **XP Savings Bank** — kids deposit XP for weekly compound interest
      (rates set by parents). Teaches saving vs. spending viscerally; leaderboard
      shows net worth. The single best fit for the existing economy.
- [ ] **Family Quests (co-op)** — one shared weekly goal ("family earns 500 XP
      → pizza night") with a joint progress bar on the TV leaderboard. Turns
      sibling rivalry into cooperation.
- [ ] **Sibling economy** — gift XP; post bounties ("30 XP if you help me clean
      my room", parent-approved). Negotiation + generosity, real stakes.
- [ ] **Story Nights** — the soft voice reads a bedtime story chapter nightly;
      kid taps along, earns XP for words they can read back. Progressive reader
      levels; parents can add their own stories as text files.
- [ ] **Podcast Studio** — MediaRecorder-based; kids record shows/interviews,
      episodes publish to their pages with retro cassette UI; grandparents
      reply in the guestbook.
- [ ] **Game Maker** — kid-facing editor (pick sprite, rules, win condition)
      that publishes tiny playable games to their pages. The Paint→page magic,
      but for games.
- [ ] **Pen-pal federation** — two Family XP houses exchange postcards and
      guestbook messages via exported bundles (parent-mediated, no cloud).
      Cousins network with zero servers.
- [ ] **Ask-me-anything tutor** — optional parent-supplied AI key wires Klippy
      into a real homework helper with TTS both ways and parent-visible
      transcripts. (Must degrade gracefully offline; off by default.)
- [ ] **Skill tree map** — visual constellation of missions/games lighting up
      as kids progress; print-a-certificate on milestones.
- [ ] **Seasonal world events** — snow drifts on Bliss in winter, lanterns for
      Ramadan, a birthday desktop takeover with the kid's name in lights.


Read `CLAUDE.md` first. Rules for contributing: tests green before restart and
push, iOS/TTS-first for anything kid-facing, no external dependencies, XP flows
through the server-side caps, and parents approve anything that spends or
awards. When you finish something, update `CLAUDE.md`'s feature inventory and
this file.

## Literacy (highest priority)

- [ ] Phonics mode in Reading Arcade: word families (-at/-an/-ig), segmented
      audio ("c … a … t → cat"), blend-tapping. Dolch lists are sight words;
      decoding practice is the missing half.
- [ ] Sentence builder: TTS reads a sentence, kid taps word cards into order.
- [ ] "Read to me" on kid pages: speaker button in guestbook.js that reads the
      kid's own page aloud.
- [ ] Parent literacy report in Control Panel: words practiced/day, accuracy
      per Dolch level, streaks (data already in `.progress.json` reading key).

## Engagement

- [ ] Weekly Family Quest: shared goal (e.g. family earns 500 XP together →
      pizza night), progress bar on the leaderboard.
- [ ] Badges shelf on desktop (earned badges as desktop trophies + TTS praise).
- [ ] Seasonal themes (winter Bliss, ramadan lanterns) via THEMES entry.
- [ ] More screensavers: 3D-pipes-style maze, photo slideshow from kids' art.

## Platform

- [ ] Server-sent events for the leaderboard (sub-second confetti) — keep the
      8s poll as fallback.
- [ ] Nightly `launchd` backup: `/api/admin/export?kid=all` ZIP to a dated
      folder (+ prune old ones).
- [ ] Health page `/status` (parent-only): agents, circuit-breaker states,
      last deploy, disk space.
- [ ] PWA offline shell so iPad home-screen app opens instantly.
- [ ] Per-kid mission track editor UI (today: JSON in Control Panel).

## Known papercuts

- Old kid pages may still contain pre-onboarding codenames — intentionally
  left as the "Time Machine" mission (e12/b16); don't bulk-rewrite kid content.
- `og-source.html` regeneration for brand assets is manual (headless Chrome,
  see README-CLAUDE.md).
- Guestbook has no profanity/length moderation beyond 300 chars — family LAN
  trust model; revisit if guests join.
