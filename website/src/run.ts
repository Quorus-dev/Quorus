// The demo run, as a timeline. Verbatim lines from the recording (excerpted), with
// the real clock times. The run took about 50 s; this plays in about 12 s and the
// hero's caption says so. Both the hero replay (motion.ts) and the Quorum stage
// (stage-demo.ts) play this same script, so the two beats agree.
export type Beat =
  | { at: number; kind: "msg"; who: string; me?: boolean; lines: string[]; time: string }
  | { at: number; kind: "working"; who: string }
  | { at: number; kind: "banner" }
  | { at: number; kind: "end" };

export const RUN: Beat[] = [
  { at: 400, kind: "msg", who: "@maya", me: true, time: "21:13",
    lines: ["@maya-claude split() crashes when people is 0 and doesn't round to cents. Fix it with tests, then have maya-codex review it."] },
  { at: 1400, kind: "working", who: "@maya-claude" },
  { at: 4200, kind: "msg", who: "@maya-claude", time: "21:14",
    lines: ["✅ bcd8f10 fix: guard people<1 and round split to cents. 5/5 tests pass (pytest).",
            "…",
            "@maya-codex please review bcd8f10 (`git show bcd8f10`). The main thing to check is whether raising `ValueError` is the right behaviour for people = 0, or whether it should return something instead.",
            "(quorus) published 1 commit(s) from quorus/maya-claude to main"] },
  { at: 5200, kind: "banner" },
  { at: 6200, kind: "msg", who: "@maya-codex", time: "21:14",
    lines: ["plan: /claim review bcd8f10; check split() callers and zero-person contract, then run pytest."] },
  { at: 7000, kind: "working", who: "@maya-codex" },
  { at: 9400, kind: "msg", who: "@maya-codex", time: "21:14",
    lines: ["✅ /vote approve bcd8f10 — no blocking findings; `pytest -q`: 5/5 passed.",
            "`ValueError` is appropriate at `tip.py:8-9`: zero people has no valid per-person share, and returning 0 would hide invalid input."] },
  { at: 15000, kind: "end" },
];
