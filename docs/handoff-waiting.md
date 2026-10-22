# Waiting for a busy handoff receiver

For a room with handoff enabled, a sender that hits a vendor limit can wait for
an eligible teammate to finish its current turn. It posts one status notice
immediately, keeps its claim and work unpublished, and waits in the background.
Other sender wakes can run; they cannot write or sync over the waiting worktree.

The default cap is 15 minutes. The owner can set a cap from 1 to 60 minutes:

```sh
quorus room bind ROOM /path/to/repo --handoff-wait-minutes 30
```

This setting does not enable handoff. The room still needs its owner-selected
check and the separate, terminal-confirmed `--handoff on` switch.

Polling starts after 30 seconds and backs off to 60 seconds. Each poll rechecks
the room switch, claim, membership, presence and the sender's frozen head/tree.
Offline, degraded and quota-paused receivers are excluded. Sending still requires
an active receiver, an owned claim and the normal receiver proof gate.

At expiry, loss of authority or a changed head, the sender alerts its owner and
does not post a chat invitation for another agent to take over. A restart fails
the old wait closed as interrupted instead of automatically resending it. The
read-only handoff report shows waiting and interrupted attempts separately from
successful proof outcomes. Work remains on its unpublished branch for the owner
to inspect and resolve.
