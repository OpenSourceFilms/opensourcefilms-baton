"""Synthetic BATON trap-file corruption containment tests.

Reads only disposable JSONL fixtures, exercises the real PreToolUse hook with
fake stdin and redirected audit/seen state. No installed BATON, services,
real transcripts, credentials, or production trap files touched.
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


class MalformedTrapIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="baton_malformed_traps_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.traps_file = self.root / "traps.jsonl"
        self.seen_file = self.root / "seen.json"
        self.audits = []

    def valid(self, trap_id="GOOD", key="paths", pattern="*.py"):
        return {
            "id": trap_id, "project": "*", "class": "path",
            "what": "synthetic valid warning", "why": "review before editing",
            "rule": "inspect", "triggers": {key: [pattern]},
        }

    def write(self, entries):
        self.traps_file.write_text(
            "\n".join(x if isinstance(x, str) else json.dumps(x)
                      for x in entries) + "\n"
        )

    def invoke(self, tool_input=None):
        tool_input = {"file_path": "example.py"} if tool_input is None else tool_input
        payload = {"session_id": "synthetic", "cwd": str(self.root),
                   "tool_name": "Edit", "tool_input": tool_input}
        output = io.StringIO()
        file_reader = baton.load_traps
        with (mock.patch.object(baton.sys, "stdin", io.StringIO(json.dumps(payload))),
              mock.patch.object(baton, "find_project_root", return_value=str(self.root)),
              mock.patch.object(baton, "load_traps",
                                side_effect=lambda: file_reader(self.traps_file)),
              mock.patch.object(baton, "_seen_traps_file", return_value=str(self.seen_file)),
              mock.patch.object(baton, "audit_write",
                                side_effect=lambda value: self.audits.append(value)),
              contextlib.redirect_stdout(output)):
            with self.assertRaises(SystemExit) as exited:
                baton.cmd_pretool([])
        self.assertEqual(exited.exception.code, 0)
        response = json.loads(output.getvalue())["hookSpecificOutput"]
        self.assertEqual(response["permissionDecision"], "allow")
        self.assertEqual(response["hookEventName"], "PreToolUse")
        return response.get("additionalContext", "")

    def test_scalar_and_unparseable_jsonl_entries_cannot_suppress_valid_trap(self):
        self.write([None, 42, ["not", "a", "trap"], "not JSON", self.valid()])
        entries = baton.load_traps(self.traps_file)
        self.assertEqual(len(entries), 1)
        result = self.invoke()
        self.assertIn("[TRAP GOOD]", result)
        self.assertEqual(json.loads(self.seen_file.read_text()), ["GOOD"])
        self.assertEqual(self.audits[0]["traps"], ["GOOD"])

    def test_malformed_trigger_forms_are_skipped_without_suppressing_later_trap(self):
        invalid = [
            {**self.valid("WRONG-TYPE"), "triggers": ["not a mapping"]},
            {**self.valid("BAD-STATUS"), "status": ["active"]},
            {**self.valid("BAD-ID"), "id": ["unhashable"]},
            {**self.valid("BAD-PROJECT"), "project": ["not a project"]},
            {**self.valid("BAD-LIST"), "triggers": {"paths": {"*.py": True}}},
            {**self.valid("BAD-PATTERN"), "triggers": {"paths": [123, None]}},
        ]
        self.write(invalid + [self.valid("GOOD")])
        result = self.invoke()
        self.assertIn("[TRAP GOOD]", result)
        self.assertEqual(result.count("[TRAP "), 1)
        self.assertEqual(json.loads(self.seen_file.read_text()), ["GOOD"])

    def test_invalid_pattern_does_not_prevent_valid_pattern_on_same_record(self):
        good = self.valid()
        good["triggers"]["paths"] = [None, 42, "*.py"]
        self.write([good])
        self.assertIn("[TRAP GOOD]", self.invoke())

    def test_nonstring_tool_path_can_still_match_valid_command(self):
        self.write([self.valid("CMD", key="commands", pattern="git status*")])
        result = self.invoke({"file_path": {"wrong": "shape"},
                              "command": "git status --short"})
        self.assertIn("[TRAP CMD]", result)

    def test_nonmapping_hook_tool_input_is_safe_and_does_not_mark_seen(self):
        self.write([self.valid()])
        self.assertEqual(self.invoke(["not", "mapping"]), "")
        self.assertFalse(self.seen_file.exists())
        self.assertEqual(self.invoke().count("[TRAP "), 1)

    def test_invalid_meta_trap_does_not_suppress_valid_meta_warning(self):
        bad = {**self.valid("BAD"), "class": "meta", "status": ["active"]}
        good = {**self.valid("META"), "class": "meta"}
        text = baton.render_meta_traps([None, bad, good], "some-project")
        self.assertIn("[META]", text)
        self.assertNotIn("[BAD]", text)


if __name__ == "__main__":
    unittest.main()
