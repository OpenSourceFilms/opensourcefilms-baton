# BATON interruption and recovery: reproducible terminal example

This example runs **the published BATON executable** twice in separate Python
processes against a fabricated, checked-in Claude-style transcript. A
`SessionStart` hook receives a newly started near-empty transcript and recovers
the earlier, interrupted transcript from its sibling directory.

It demonstrates BATON's **implemented local continuity mechanism**. It is
**not** footage or a terminal capture of a real Claude Code process crashing;
no Claude service, live user session, model inference, network connection,
provider credentials, or production project is used.

## Run

From the repository root on Python 3.11+:

```bash
python -B tests/record_recovery_demo.py
```

The script uses only Python's standard library, temporary directories and
[the synthetic interrupted-session fixture](../tests/fixtures/synthetic_dead_session.jsonl).
It prints lines read from the **actual** `baton pickup` and `baton hook`
responses, then checks and reports a PASS or fails with a nonzero exit.
GitHub Actions runs it after the existing [BATON self-check](../tests/selfcheck.sh).

## What is exercised

1. Create an isolated temporary Git-independent project, with a minimal
   `BATON.md` specifying one next action and a constraint against assuming a
   pending tool call completed.
2. Use the existing fabricated transcript. Its final event issues a Bash tool
   call, but there is no matching result or later message. The fixture includes
   a fabricated credential-shaped string solely to exercise redaction.
3. Invoke `python baton.py pickup <previous transcript>` as a real subprocess.
   Assert BATON identifies the interrupted execution state and emits bounded,
   structured context instead of the original raw transcript.
4. Start a **fresh process** invoking `python baton.py hook` with a
   synthetic `SessionStart` event referencing a near-empty current transcript
   in the same temporary transcript directory.
5. Verify that BATON selects the previous sibling transcript, identifies the
   interruption, retains the task constraints and omits the fabricated
   credential and raw transcript body.

The mechanism works even when the new current transcript contains almost no
content. The last unreturned tool call is **suspected interrupted**, not
automatically considered successful or safe to repeat. The output directs the
resuming assistant to verify the external effect first.

## Boundaries

This demonstration does **not** establish hook registration against a
particular current Claude Code version, real crash recovery across a process
death, GPU/RunPod persistence, a production session's correctness, or the
accuracy of every ephemeral Tier-0 probe. Those require separate integration
evidence. It also does not test the deferred `Stop` hook, which is not shipped.

The actual CI run and its terminal output are available from the repository's
[GitHub Actions self-check workflow](../.github/workflows/selfcheck.yml). The
synthetic fixture and executable assertions make the same example reproducible
on any compatible Linux machine with Git and Python.
