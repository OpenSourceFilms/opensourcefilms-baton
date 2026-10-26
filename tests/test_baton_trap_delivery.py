"""Offline BATON PreToolUse trap-delivery regressions.

Actual hook function, synthetic trap entries and private disposable seen state.
Does not touch installed BATON, real transcripts or live trap/audit files.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baton_under_test", ROOT / "baton.py")
baton = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baton)


class PreToolTrapDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="baton_pretool_delivered_")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.seen_file = self.work / "seen.json"
        self.audits = []
        self.traps = [
            {
                "id": f"T-TEST-{n:02d}",
                "project": "*",
                "class": "path",
                "what": f"synthetic problem {n}",
                "why": "do not repeat",
                "rule": "use known alternative",
                "triggers": {"paths": ["*.py"]},
            }
            for n in range(1, 13)
        ]

    def invoke(self, *, session="synthetic-session", file_path="demo.py", traps=None):
        data = {
            "session_id": session,
            "cwd": str(self.work),
            "tool_name": "Edit",
            "tool_input": {"file_path": file_path},
        }
        out = io.StringIO()
        with (mock.patch.object(baton.sys, "stdin", io.StringIO(json.dumps(data))),
              mock.patch.object(baton, "find_project_root", return_value=str(self.work)),
              mock.patch.object(baton, "load_traps", return_value=self.traps if traps is None else traps),
              mock.patch.object(baton, "_seen_traps_file", return_value=str(self.seen_file)),
              mock.patch.object(baton, "audit_write", side_effect=lambda rec: self.audits.append(rec)),
              contextlib.redirect_stdout(out)):
            with self.assertRaises(SystemExit) as exited:
                baton.cmd_pretool([])
        self.assertEqual(exited.exception.code, 0)
        payload = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(payload["permissionDecision"], "allow")
        self.assertEqual(payload["hookEventName"], "PreToolUse")
        return payload.get("additionalContext", "")

    def test_more_than_five_matches_delivered_over_successive_calls(self):
        first = self.invoke()
        self.assertEqual(first.count("[TRAP "), 5)
        self.assertIn("[TRAP T-TEST-01]", first)
        self.assertIn("[TRAP T-TEST-05]", first)
        self.assertNotIn("T-TEST-06", first)
        self.assertEqual(json.loads(self.seen_file.read_text()),
                         [f"T-TEST-{n:02d}" for n in range(1, 6)])
        self.assertEqual(self.audits[0]["traps"],
                         [f"T-TEST-{n:02d}" for n in range(1, 6)])

        second = self.invoke()
        self.assertEqual(second.count("[TRAP "), 5)
        self.assertIn("[TRAP T-TEST-06]", second)
        self.assertIn("[TRAP T-TEST-10]", second)
        self.assertNotIn("T-TEST-11", second)
        self.assertEqual(self.audits[1]["traps"],
                         [f"T-TEST-{n:02d}" for n in range(6, 11)])

        third = self.invoke()
        self.assertEqual(third.count("[TRAP "), 2)
        self.assertIn("[TRAP T-TEST-11]", third)
        self.assertIn("[TRAP T-TEST-12]", third)
        self.assertEqual(self.audits[2]["traps"], ["T-TEST-11", "T-TEST-12"])

        fourth = self.invoke()
        self.assertEqual(fourth, "")
        self.assertEqual(len(self.audits), 3)
        self.assertEqual(json.loads(self.seen_file.read_text()),
                         [f"T-TEST-{n:02d}" for n in range(1, 13)])

    def test_exactly_five_traps_are_delivered_once(self):
        five = self.traps[:5]
        self.assertEqual(self.invoke(traps=five).count("[TRAP "), 5)
        self.assertEqual(self.invoke(traps=five), "")
        self.assertEqual(len(self.audits), 1)

    def test_nonmatching_tool_does_not_consume_future_traps(self):
        self.assertEqual(self.invoke(file_path="no_match.txt"), "")
        self.assertFalse(self.seen_file.exists())
        self.assertEqual(self.invoke().count("[TRAP "), 5)

    def test_nonobject_jsonl_rows_cannot_suppress_valid_hook_warning(self):
        source = self.work / "synthetic-traps.jsonl"
        source.write_text("\\n".join(json.dumps(t) for t in (
            None, [], 17, "unexpected JSON string", self.traps[0],
        )) + "\\n")
        loaded = baton.load_traps(source)
        self.assertEqual(loaded, [self.traps[0]])
        context = self.invoke(traps=loaded)
        self.assertIn("[TRAP T-TEST-01]", context)
        self.assertEqual(self.audits[0]["traps"], ["T-TEST-01"])

    def test_malformed_trigger_types_cannot_hide_subsequent_valid_warnings(self):
        def trap(tid, trigger):
            return {
                "id": tid, "project": "*", "class": "path",
                "what": "synthetic", "why": "test",
                "rule": "inspect valid warnings", "triggers": trigger,
            }

        input_traps = [
            None,
            ["unexpected list"],
            trap("BAD-OBJECT", ["*.py"]),
            trap("BAD-PATTERNS", {"paths": [None, 123]}),
            trap("BAD-STRING-LIST", {"paths": "*.py"}),
            trap("GOOD-MIXED", {"paths": [None, "*.py"]}),
            self.traps[0],
        ]
        first = self.invoke(traps=input_traps)
        self.assertEqual(first.count("[TRAP "), 2)
        self.assertIn("[TRAP GOOD-MIXED]", first)
        self.assertIn("[TRAP T-TEST-01]", first)
        self.assertEqual(self.audits[0]["traps"], ["GOOD-MIXED", "T-TEST-01"])
        self.assertEqual(self.invoke(traps=input_traps), "")

    def test_nonobject_meta_trap_does_not_hide_valid_meta_warning(self):
        meta = {"class": "meta", "id": "META-TEST", "project": "*",
                "what": "check synthetic state", "rule": "verify before rerun"}
        result = baton.render_meta_traps([None, [], 9, meta], "demo")
        self.assertIn("[META-TEST]", result)
        self.assertIn("verify before rerun", result)

    def test_pretool_redacts_secret_shaped_text_before_delivery(self):
        fake_token = "hf_" + "A" * 30
        traps = [{
            "id": "T-REDACT-01",
            "project": "*",
            "class": "path",
            "what": "synthetic " + fake_token,
            "why": "test",
            "rule": "do not print token",
            "triggers": {"paths": ["*.py"]},
        }]
        result = self.invoke(traps=traps)
        self.assertNotIn(fake_token, result)
        self.assertIn("<redacted>", result)
        self.assertEqual(self.audits[0]["traps"], ["T-REDACT-01"])


if __name__ == "__main__":
    unittest.main()
