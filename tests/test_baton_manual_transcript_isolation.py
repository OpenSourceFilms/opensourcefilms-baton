"""Offline project-isolation checks for manual BATON transcript inference.

All Claude JSONL records, paths and tool calls here are synthetic.
No real session archives, installed BATON, RunPod or workboard are accessed.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baton_manual_under_test", ROOT / "baton.py")
baton = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baton)


class ManualTranscriptIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="baton_project_transcripts_")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.a = self.base / "alpha"
        self.b = self.base / "alpha-extra"
        self.a.mkdir()
        self.b.mkdir()
        self.claude = self.base / ".claude" / "projects"
        self.claude.mkdir(parents=True)
        (self.a / "BATON.md").write_text(
            "## MISSION\nAlpha only\n## NOW\nRun `pytest tests/test_alpha.py`\n"
        )
        self.glob = mock.patch.object(
            baton, "CLAUDE_TRANSCRIPTS_GLOB",
            str(self.claude / "*" / "*.jsonl"),
        )
        self.glob.start()
        self.addCleanup(self.glob.stop)

    def transcript(self, directory, filename, cwd=None, command=None, extra=None):
        d = self.claude / directory
        d.mkdir(exist_ok=True)
        p = d / filename
        records = []
        if cwd is not None:
            records.append({
                "type": "user",
                "cwd": str(cwd),
                "message": {"role": "user", "content": "Synthetic task"},
            })
        if command:
            records.extend([
                {
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use", "name": "Bash", "id": "toolu_synthetic",
                        "input": {"command": command},
                    }]},
                    "timestamp": "2026-10-26T01:00:00Z",
                },
                {
                    "type": "user", "message": {"content": [{
                        "type": "tool_result", "tool_use_id": "toolu_synthetic",
                        "content": "Synthetic successful command",
                    }]},
                },
            ])
        records.extend(extra or [])
        p.write_text("".join(json.dumps(record) + "\n" for record in records))
        return p

    def invoke_now(self, args=()):
        out = io.StringIO()
        with mock.patch.object(baton, "find_project_root", return_value=str(self.a)):
            with contextlib.redirect_stdout(out):
                baton.cmd_now(list(args))
        return out.getvalue()

    def test_newer_foreign_session_cannot_falsely_mark_now_as_executed(self):
        own = self.transcript("alpha", "own.jsonl", self.a,
                              "echo no command from NOW")
        foreign = self.transcript("other", "other.jsonl", self.b,
                                  "pytest tests/test_alpha.py")
        os.utime(own, (100, 100))
        os.utime(foreign, (200, 200))
        self.assertEqual(baton.latest_project_transcript(str(self.a)), str(own))
        now = self.invoke_now()
        self.assertIn("pytest tests/test_alpha.py", now)
        self.assertNotIn("SUSPECT", now)
        self.assertNotIn("not checked", now)

    def test_genuine_current_project_execution_marks_now_suspect(self):
        self.transcript("own", "a.jsonl", self.a, "pytest tests/test_alpha.py")
        out = self.invoke_now()
        self.assertIn("SUSPECT", out)
        self.assertIn("pytest tests/test_alpha.py", out)

    def test_nested_cwd_matches_own_project_but_prefix_sibling_does_not(self):
        nested = self.a / "src" / "lib"
        nested.mkdir(parents=True)
        good = self.transcript("nested", "own.jsonl", nested)
        bad = self.transcript("sibling", "foreign.jsonl", self.b)
        self.assertTrue(baton._transcript_belongs_to_project(str(good), str(self.a)))
        self.assertFalse(baton._transcript_belongs_to_project(str(bad), str(self.a)))

    def test_missing_cwd_evidence_fails_closed(self):
        orphan = self.transcript("other", "orphan.jsonl", None,
                                 "pytest tests/test_alpha.py")
        self.assertFalse(baton._transcript_belongs_to_project(str(orphan), str(self.a)))
        self.assertIsNone(baton.latest_project_transcript(str(self.a)))
        now = self.invoke_now()
        self.assertNotIn("SUSPECT", now)
        self.assertIn("NOW execution not checked", now)

    def test_conflicting_cwd_markers_reject_entire_session(self):
        mixed = self.transcript("mixed", "mixed.jsonl", self.a, extra=[{
            "type": "user", "cwd": str(self.b), "message": {"content": "other"},
        }])
        self.assertFalse(baton._transcript_belongs_to_project(str(mixed), str(self.a)))

    def test_oversized_early_record_is_not_trusted(self):
        d = self.claude / "oversized"
        d.mkdir()
        p = d / "large.jsonl"
        p.write_bytes(b"x" * (baton.MANUAL_TRANSCRIPT_MAX_LINE_BYTES + 100) + b"\n"
                      + json.dumps({"cwd": str(self.a)}).encode() + b"\n")
        self.assertFalse(baton._transcript_belongs_to_project(str(p), str(self.a)))

    def test_manual_pickup_selects_own_transcript_not_global_newest(self):
        own = self.transcript("own", "own.jsonl", self.a)
        foreign = self.transcript("other", "foreign.jsonl", self.b)
        os.utime(own, (100, 100))
        os.utime(foreign, (200, 200))
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(baton, "find_project_root", return_value=str(self.a)):
            with mock.patch.object(baton, "build_pickup", return_value="synthetic pickup") as build:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    baton.cmd_pickup([])
                build.assert_called_once_with(str(self.a), str(own))
        self.assertEqual(out.getvalue().strip(), "synthetic pickup")
        self.assertEqual(err.getvalue(), "")

    def test_explicit_transcript_override_is_preserved(self):
        foreign = self.transcript("foreign", "foreign.jsonl", self.b,
                                  "pytest tests/test_alpha.py")
        self.assertIn("SUSPECT", self.invoke_now([str(foreign)]))
        with mock.patch.object(baton, "find_project_root", return_value=str(self.a)):
            with mock.patch.object(baton, "build_pickup", return_value="mock") as build:
                with contextlib.redirect_stdout(io.StringIO()):
                    baton.cmd_pickup([str(foreign)])
                build.assert_called_once_with(str(self.a), str(foreign))

    def test_pickup_with_unverified_transcript_omits_interrupted_state(self):
        self.transcript("unknown", "unknown.jsonl", None)
        with mock.patch.object(baton, "find_project_root", return_value=str(self.a)):
            with mock.patch.object(baton, "build_pickup", return_value="local only") as build:
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    baton.cmd_pickup([])
                build.assert_called_once_with(str(self.a), None)
                self.assertIn("no verified project transcript", stderr.getvalue())
                self.assertIn("local only", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
