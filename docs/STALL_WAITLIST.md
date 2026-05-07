# Stall Waitlist — pick today, run before doors open

## A. Where the waitlist lives

Three options. Pick one.

| Option                       | Cost | Setup time | UX                                      | Recommendation                |
| ---------------------------- | ---- | ---------- | --------------------------------------- | ----------------------------- |
| **Tally** (https://tally.so) | free | ~10 min    | clean, mobile-first, embeds anywhere    | **PICK THIS**                 |
| Google Forms                 | free | ~5 min     | dated, requires Google sign-in to share | acceptable backup             |
| quorus.dev/waitlist          | free | 4+ hr      | full control, looks pro                 | not for today — too much risk |

**Decision: Tally.** Build it now. The form URL becomes the QR.

## B. The 5 fields the form asks

1. **Email** (required, validated) — this is the only must-have
2. **Name** (text, optional)
3. **Which harnesses do you use?** (multi-checkbox: Claude Code, Cursor, Codex, Gemini, Windsurf, Opencode, Cline, "other") — tells us where to launch first
4. **What would you use Quorus for?** (one-line text, optional) — qualitative signal, helps draft the launch tweet
5. **OK to email you when we ship?** (yes/no) — ASIA opt-in compliance

Form title: "Quorus — early access"
Form subtitle: "We're shipping the first cross-vendor agent coordination layer. Drop your email and we'll ping you the day it's public."
Submit button: "Get early access"
Thank-you screen: "We'll be in touch. Follow @aravkek on Twitter for build updates."

## C. QR code generation

Once the Tally form has a URL (looks like `https://tally.so/r/abc123`):

```bash
brew install qrencode  # one-time, only if not installed
qrencode -o /tmp/stall-qr.png -s 12 -m 2 'https://tally.so/r/<form-id>'
open /tmp/stall-qr.png
```

Print on a half-sheet of paper. Tape next to the laptop. Make the QR ~7 cm wide so phones lock onto it from a foot away.

Backup: print the URL underneath the QR in 14pt, in case someone's camera fights the code.

## D. 30-second post-show follow-up email template

Send within 24 hours of the showcase. Personalized greeting, no generic blast.

---

**Subject:** Thanks for stopping by — Quorus early access details

Hi {{first_name}},

Thanks for trying the demo. Quick context so it's fresh:

- The repo: https://github.com/Quorus-dev/Quorus (Apache-2.0 on the spec, MIT-style on the relay)
- The wire format doc: https://github.com/Quorus-dev/Quorus/blob/main/docs/QSP_SPEC.md
- The TUI you saw: `pip install -e .` from the repo, then `quorus init` and `quorus chat <room>`

We're heads-down on three things before public launch:

1. Stable 1.0 of the wire-format (claim, disagree, defer, queue, vote, interrupt)
2. PyPI publishing so it's `pip install quorus` not a clone
3. Hosted relay tier so you don't need to run the server yourself

I'll DM you when it ships — should be {{eta}}. If you want to peek at the code before then, the repo is open and the README walks through the same flow you saw at the booth.

Two questions, no pressure:

- Which harness would you wire up first? ({{checked_harnesses_or_blank}})
- What was the use case in your head when you typed that mention?

Either reply or ignore — I won't follow up twice.

Arav
{{phone}} | https://github.com/aravkek

---

**Tone notes:**

- One specific thing they did at the booth, not generic "great to meet you"
- Two named questions at the end — gives them a hook to reply
- Clear "won't follow up twice" — respects their time, paradoxically increases reply rate
- No marketing speak. No "leveraging" or "synergy". Engineer-to-engineer.
