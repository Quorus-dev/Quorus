# Proposed 60-second demo edit (draft)

Recording plan only; no completed clip or one-minute completion time is claimed.
The weekend room contains task assignments, replies, published commit lines and
reviews by a second model. This storyboard proposes recording that workflow
again in a throwaway repo. See [review-notes.md](review-notes.md) for evidence.

## Prepare before recording

- Use an installed, configured Quorus relay and two installed, authenticated
  harnesses. Keep installation and first-run setup out of this clip; they need
  their own fresh-user validation.
- Create a throwaway repo with a small failing test and commit the baseline.
  Substitute its actual path for `/absolute/path/to/demo-repo` below.
- Add the two agents, then confirm the names printed by `agent add` or shown
  by `quorus agent list`. Use those names in the mentions.

```bash
quorus agent add claude --room general --repo /absolute/path/to/demo-repo --mode autonomous --lead
quorus agent add codex --room general --repo /absolute/path/to/demo-repo --mode autonomous
quorus agent list
quorus chat general
```

## Storyboard for the edited clip

Times are edit targets, not measured wake/build/review times. Record the full
run and label every elapsed-time jump. If the sequence needs longer, keep the
real duration or change the edit; do not manufacture a reply, review or commit.

| Edit time | Capture | Narration |
| --- | --- | --- |
| 0:00–0:08 | The configured room and both agent names. | “Two coding agents, one shared room.” |
| 0:08–0:17 | Mention the actual builder: `@<builder-name> fix the failing test in this repo; report the commit and ask @<reviewer-name> to review it`. | “I give one agent a small task and name the reviewer.” |
| 0:17–0:27 | Its actual working indicator. | “The room shows when the wake is working.” |
| 0:27–0:40 | Its real result and the daemon's real publish line. Label any time jump. | “Here is the result, and the branch integrated into the local repo.” |
| 0:40–0:54 | The second model's actual review. If no handoff arrived, explicitly mention the reviewer and show that request. | “A different model reviews the commit.” |
| 0:54–1:00 | The actual commit and test result. | “Instructions, results and the review are in the room.” |

“Publish” here means integrating into the configured local branch. A remote
push or a GitHub PR is not part of this sequence.

## Evidence to retain with the recording

- Full capture with timestamps and the exact source revision/configuration.
- The builder's commit hash, test output and reviewer message.
- Actual model/provider labels, including any retries or failures that affected
  the result. A working indicator is presence information, not proof that a
  process cannot have crashed.
- A note naming each wait removed from the public edit, with real elapsed time.

## Optional follow-up recordings

- Replace the second harness with the same opencode/provider/model combination
  used in the weekend room. Bind the repo explicitly and record the real run.
  Availability and pricing must be checked at recording time.
- Send `@open <task>` to show auction-based assignment; do not call this
  “the first free agent.”
- Failover is implemented and regression-tested (`8e41e88`, `a484f40`), but no
  live vendor-outage handoff recording was established in this review. Record
  and label a controlled failure before using it as a live demonstration.
