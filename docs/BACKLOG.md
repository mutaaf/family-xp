# Backlog — ideas a fresh agent (or human) can pick up

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
