# Evidence review of c9494dc

Reviewed 2026-10-17 against this checkout and the supplied `weekend-quorus`
transcript. Drafts remain unpublished. No fresh install, live vendor-limit
experiment or new demo recording was performed for this review.

| Claim | Review result / change | Evidence |
| --- | --- | --- |
| Version 0.5.0 / MIT | Keep as source metadata, not proof of a registry release. | `pyproject.toml` `[project]`; `LICENSE`. |
| Agents returned work, commits were integrated, another model reviewed them | Keep as the observed weekend workflow; remove guarantees that every task requests or passes review. | Room publish/approval messages for `33ad026`, `a4e110c`, `8e41e88` and `a484f40`. |
| Four harnesses participated | Keep the named weekend participants; narrow “any model” to the actual DeepSeek/opencode example. | [Weekend team](../BRIEF.md); [shared context](../../internal/CONTEXT.md), “Proactive + notified for every agent” and “Any-model agents.” |
| A second laptop participated | Say a second user in a Docker container. | [Weekend brief](../BRIEF.md), team definition and supplied second-user reports. |
| The open model independently found the installer injection | Do not attribute it that way. The second-user agent found the missing relay argument; Claude's review identified command substitution in the resulting installer line. The second-user agent then fixed URL validation. | Supplied room reports: `d52acc5`, Claude's blocking review, `1e73c13` and `b615f6b`. This can support a joint bug-fix/review story, not a solo discovery claim. |
| `@open` gives work to the first free agent | Replace with auction-based assignment. | `quorus/routes/triage.py`, `claim_winner`: bid score and fairness credit determine the winner. |
| Busy agents never look dead | Cut the guarantee; retain the working indicator. | `scripts/reflexd.py`, `_wake_and_reply`; `quorus_tui/working.py`; second-machine mid-task-kill investigation is still pending. |
| Relay does not see code / everything stays on one machine | Remove; describe configurable relay hosting and provider configuration. | `quorus/services/room_msg_svc.py` stores message content; harness wake prompts carry repo/context; second-user flow uses a shared relay. |
| One-minute unedited build, review and publish | Cut; make 60 seconds an edit target, require labeled time jumps. | No recording proving that timing was supplied; old script contradicted itself about editing waits. |
| Any model / free or local model doing the same demo | Remove universal availability/pricing/performance claims. | Provider/model selection exists; [context](../../internal/CONTEXT.md) records Qwen making an edit but also context-size and speed limitations. |
| A clean install leads to an answer in one try | Not established; remove from this demo's setup sequence. | [Quick-start findings](../readme-quickstart-run.md): network install skipped and real `agent add` not executed. |
| Live failover demonstrated this weekend | Do not claim it; distinguish implementation/tests from a captured live incident. | `8e41e88`, `a484f40`, `tests/test_reflexd_failover.py`, `tests/test_claude_json_errors.py`; lead approvals record test results. |
| Competitors lack coordination; communities have particular habits; writers want video | Cut factual framing; mark outreach as hypotheses requiring research. | No supporting evidence in c9494dc. |

The accepted third-party teardown rewrites are on `weekend/laptop2`, according
to the lead's room reviews. This checkout's older research files were not used
as proof for competitor claims or current outreach availability.
