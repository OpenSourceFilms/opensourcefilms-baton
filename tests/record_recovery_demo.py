#!/usr/bin/env python3
"""Run the real BATON CLI through a synthetic interrupted-session recovery.

Only standard-library modules and the checked-in fabricated transcript are used.
This is a real subprocess integration, NOT a captured live Claude Code crash.
Runs in temporary project/transcript directories and does not alter user data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
BATON = REPO / "baton.py"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_dead_session.jsonl"
REQUIRED = (
    "## NOW",
    "[STATE: INTERRUPTED EXECUTION]",
    "Died during:",
    "Verify current state before resuming",
)


def run_baton(cwd: Path, *args: str, payload: dict | None = None, env: dict | None = None) -> str:
    result = subprocess.run(
        [sys.executable, "-B", str(BATON), *args],
        input=None if payload is None else json.dumps(payload),
        cwd=cwd, text=True, capture_output=True, timeout=40, env=env,
    )
    if result.returncode:
        raise RuntimeError(
            f"baton {args}: exit {result.returncode}: {result.stderr[:400]}"
        )
    return result.stdout


def verify_context(context: str) -> None:
    for expected in REQUIRED:
        if expected not in context:
            raise AssertionError(f"Missing recovery indicator: {expected}")
    if "sk-fakeSECRETtoken1234567" in context:
        raise AssertionError("Fabricated credential from transcript leaked")
    if "data = open(path).read()" in context:
        raise AssertionError("Raw old transcript content was injected")
    if len(context) > 14_500:
        raise AssertionError("Recovery context exceeded the published cap")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="baton_demo_") as temp:
        root = Path(temp)
        project = root / "synthetic-project"
        transcript_dir = root / "transcripts"
        project.mkdir()
        transcript_dir.mkdir()
        (project / "BATON.md").write_text(
            "## MISSION\nRefactor a synthetic ingest script safely.\n\n"
            "## NOW\nVerify the interrupted test call; inspect its actual effects.\n\n"
            "## BLOCKED\nDo not presume an issued Bash tool call completed.\n",
            encoding="utf-8",
        )
        prev = transcript_dir / "previous-session.jsonl"
        shutil.copyfile(FIXTURE, prev)
        current = transcript_dir / "fresh-session.jsonl"
        current.write_text(
            json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "Continue carefully"},
                "timestamp": "2026-07-10T10:01:00.000Z",
            }) + "\n",
            encoding="utf-8",
        )
        env = {**os.environ, "HOME": str(root / "home"), "CUDA_VISIBLE_DEVICES": ""}
        (root / "home").mkdir()
        # First executable process: the previous transcript still has an orphan
        # Bash tool_use as its very last event, with no subsequent tool_result.
        manual = run_baton(project, "pickup", str(prev), env=env)
        verify_context(manual)
        # New executable process: a fresh/near-empty current transcript causes
        # the real SessionStart hook to choose the previous sibling transcript.
        payload = {
            "session_id": "synthetic-new-session",
            "source": "startup",
            "cwd": str(project),
            "transcript_path": str(current),
        }
        raw = run_baton(project, "hook", payload=payload, env=env)
        parsed = json.loads(raw)
        hook = parsed["hookSpecificOutput"]
        assert hook["hookEventName"] == "SessionStart", hook
        context = hook["additionalContext"]
        verify_context(context)
        assert "[STATE: INTERRUPTED EXECUTION]" in manual
        assert context.count("[STATE: INTERRUPTED EXECUTION]") == 1
        print("$ python baton.py pickup <previous synthetic transcript>")
        print("observed: interruption context rendered from prior transcript")
        print("$ python baton.py hook <new SessionStart synthetic payload>")
        print("hookEventName: " + hook["hookEventName"])
        # Print only exact lines emitted by the real BATON implementation.
        # Stable generated status rows and temp paths are intentionally omitted.
        for line in context.splitlines():
            if (line.startswith("Precedence:") or line.startswith("## NOW")
                    or line.startswith("[STATE:") or
                    line.lstrip().startswith(("Active file:", "Last action:",
                                               "Died during:", "Note:"))):
                print(line)
        print("PASS: new-process recovery, bounded context, redaction and no raw tail")


if __name__ == "__main__":
    main()
