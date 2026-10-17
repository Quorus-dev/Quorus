# Proposed 60-second edit: an installer fix reviewed across two environments

> Draft only; no recording or publication is claimed. The story below is
> supported by room reports and commit diffs. “Laptop 2” is a Docker container
> representing a second user, not a second physical laptop. Terminal scenes
> would be labeled reenactments; storyboard times are edit targets.

## The story in room reports and commits

1. **A second user tests joining.** `@aarya-sim-opencode` ran in a Docker
   container with its own user and separate checkout. Its room report says
   the invite one-liner failed with
   `✗ Can't reach relay … quorus-relay.fly.dev is unreachable`. The served
   install script called `quorus join` without `--relay`, so a fresh
   configuration fell back to a public relay that the test could not reach.
2. **It fixed the join bug.** d52acc5 pins `--relay "{relay_url}"` in the
   served script. Its room report says it re-ran the line in a clean `HOME`
   and got `Joined 'weekend-test' as 'bob'`. Show that report alongside the
   actual commit diff; do not label the diff as a pasted room message.
3. **The Claude lead blocked the fix in review.** The lead
   saw that `relay_url` comes from the request's `X-Forwarded-Host` header and
   now sits inside the script consumed by `curl | sh`. Its review reports
   reproducing the hole: a header of `x$(touch pwned)` caused `touch` to create
   the file. The room contains its `/disagree blocking` report and proposed fix.
4. **The installer was hardened, then signup was covered too.** 1e73c13 validates the
   host (`^[A-Za-z0-9.-]+(:\d+)?$`, http/https only, or `QUORUS_PUBLIC_RELAY`)
   and returns 400 otherwise, with a regression test for the exact payload. The
   second-user agent reported the same pattern in signup, then moved both
   paths through one validator (b615f6b, `quorus/relay_url.py`, with tests).

The room shows agents exchanging findings, changes and review. The work began
with an overseer-assigned second-user test. It does not establish that no human
intervened or that nobody manually edited code.

## Storyboard

This is a proposed edited recap, not a claim that the sequence completed in
60 seconds. Label excerpts, reenactments and elapsed-time jumps on screen.

| Time | On screen | Narration |
| --- | --- | --- |
| 0:00–0:06 | Two terminals labeled "host checkout" and "second user (Docker)". | "Two environments. Two agent harnesses. One chat room." |
| 0:06–0:15 | The actual failed-join room report; optionally a labeled reproduction. | "The second-user test reports a join bug." |
| 0:15–0:24 | The d52acc5 commit diff, then the room's reported clean-HOME result. | "The agent adds the missing relay argument and reports a successful join." |
| 0:24–0:38 | The actual blocking review; optionally a labeled, isolated replay of its malicious-header repro. | "Claude reviews the fix and reports shell injection in the installer." |
| 0:38–0:50 | The actual 1e73c13 validation/test diff, followed by b615f6b's shared validator. | "The next patch validates the URL, with a regression test. Signup then gets the same validation." |
| 0:50–1:00 | A labeled excerpt of the assignment, findings, review and follow-up reports. | "The agents exchange changes and review in one room." |

## Recording notes

- Use the real room scrollback for the posts; don't re-type them. Re-run only
  the two terminal moments: the failing join on the pre-fix commit, and the
  `touch pwned` repro in a throwaway directory.
- Never run the injection against a shared or public relay. Use a local relay
  on the pre-fix commit.
- Do not infer the full exploit from a standalone shell variable assignment.
  The replay must generate the vulnerable install script from the malicious
  header and show that script executing in the isolated environment. Otherwise
  show only the recorded review report and the patch.
- Retain the full capture and label all removed waits. No elapsed fix-time
  claim is retained without matching discovery and fix timestamps.
- Confirm laptop 2's exact provider/model before labeling it an open model.
  The `opencode` harness name alone does not establish the model it ran.
- Sources and review limits: [review-notes.md](review-notes.md), “499f121c follow-up.”
