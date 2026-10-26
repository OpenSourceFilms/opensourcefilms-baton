"""Synthetic SessionStart/pickup credential masking at the final context boundary.

Fixtures are invented, nonfunctional token-shaped strings. No local transcripts,
real BATON.md, cloud credentials, model calls or network access are used.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baton_pickup_test", ROOT / "baton.py")
BATON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATON)


class PickupRedactionContract(unittest.TestCase):
    @staticmethod
    def fake_fields():
        github = "github_" + "pat_" + "G" * 45
        hf = "hf_" + "H" * 36
        password_url = "https://tester:" + "secretExample9876" + "@example.invalid/private"
        return github, hf, password_url

    def assemble(self, tier0, tier1, meta, *, cap=None):
        with patch.object(BATON, "render_status", return_value=tier0), \
             patch.object(BATON, "_load_events_and_render_tier1", return_value=(tier1, "")), \
             patch.object(BATON, "load_traps", return_value=[{"class": "meta", "id": "SYNTHETIC"}]), \
             patch.object(BATON, "render_meta_traps", return_value=meta):
            if cap is None:
                return BATON.build_pickup("/synthetic/project", None)
            with patch.object(BATON, "PICKUP_HARD_MAX_CHARS", cap):
                return BATON.build_pickup("/synthetic/project", None)

    def test_all_assembled_sections_are_redacted(self):
        github, hf, url = self.fake_fields()
        content = self.assemble(
            "GPU healthy; endpoint " + url,
            "## NOW\nRead the source; note " + github + " as a dummy string.",
            "## TRAPS\nSynthetic " + hf + " not for re-injection.",
        )
        self.assertNotIn(github, content)
        self.assertNotIn(hf, content)
        self.assertNotIn(url, content)
        self.assertNotIn("secretExample9876", content)
        self.assertGreaterEqual(content.count("<redacted>"), 3)
        for safe in ("GPU healthy", "Read the source", "not for re-injection", "Precedence: Tier 0"):
            self.assertIn(safe, content)

    def test_plain_safe_pickup_context_remains_identical(self):
        expected = "\n\n".join([
            "Precedence: Tier 0 (observed reality) > BATON.md (intent) > compaction summary > everything else.",
            "## TIER 0 — REALITY (project)\ngit: main@12345678, clean",
            "## NOW\nInspect logs/test.log",
            "## TRAPS\nCheck results, not return codes",
        ])
        observed = self.assemble(
            "git: main@12345678, clean",
            "## NOW\nInspect logs/test.log",
            "## TRAPS\nCheck results, not return codes",
        )
        self.assertEqual(observed, expected)

    def test_redact_before_clipping_to_avoid_leaking_partial_tokens(self):
        partial_secret = "password=" + "Z" * 90
        tier0 = "observed: " + ("n" * 80) + " " + partial_secret + " VALID-SUFFIX"
        content = self.assemble(tier0, "", "", cap=230)
        self.assertNotIn("password=", content)
        self.assertNotIn("ZZZZZZ", content)
        self.assertIn("<redacted>", content)
        self.assertLessEqual(len(content), 230 + 100)  # clamp note is explicitly extra
        self.assertIn("observed:", content)

    def test_sessionstart_hook_output_masks_assembled_credentials(self):
        github, hf, url = self.fake_fields()
        payload = json.dumps({
            "session_id": "synthetic-test-session",
            "source": "resume", "cwd": "/synthetic/project",
            "transcript_path": "/synthetic/does-not-exist.jsonl",
        })
        fake_context = (
            "## NOW\nSynthetic secret " + github,
            "## TRAPS\nSynthetic token " + hf,
        )
        captured = io.StringIO()
        with patch.object(BATON, "find_project_root", return_value="/synthetic/project"), \
             patch.object(BATON, "render_status", return_value="probe " + url), \
             patch.object(BATON, "_load_events_and_render_tier1", return_value=(fake_context[0], "")), \
             patch.object(BATON, "load_traps", return_value=[]), \
             patch.object(BATON, "render_meta_traps", return_value=fake_context[1]), \
             patch.object(BATON, "audit_write"), \
             patch("sys.stdin", io.StringIO(payload)), \
             contextlib.redirect_stdout(captured):
            with self.assertRaises(SystemExit) as exc:
                BATON.cmd_hook([])
        self.assertEqual(exc.exception.code, 0)
        output = json.loads(captured.getvalue())
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        context = output["hookSpecificOutput"]["additionalContext"]
        for secret in (github, hf, url, "secretExample9876"):
            self.assertNotIn(secret, context)
        self.assertGreaterEqual(context.count("<redacted>"), 3)


if __name__ == "__main__":
    unittest.main()
