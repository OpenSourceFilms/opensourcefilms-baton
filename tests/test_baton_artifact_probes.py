"""BATON job-artifact probe must keep valid warnings after malformed rows.

Synthetic local files and stubbed Tier 0 checks only. No real project,
GPU, native scheduler, audit ledger or installed BATON state is touched.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baton_artifact_under_test", ROOT / "baton.py")
baton = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baton)


class ArtifactProbeDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="baton_artifact_probe_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.good = self.root / "built.json"
        self.good.write_text('{"status":"ok"}')
        self.ref = self.root / "source.txt"
        self.ref.write_text("newer-source")

    def probe(self, jobs):
        with mock.patch.object(baton, "read_probes_config", return_value={"jobs": jobs}):
            return baton.probe_project_jobs(str(self.root))

    def test_non_mapping_entry_does_not_hide_valid_artifact(self):
        lines = self.probe([
            {"name": "first", "artifact": "missing.dat"},
            None,
            ["not a job"],
            {"name": "last", "artifact": "built.json"},
        ])
        self.assertEqual(len(lines), 4)
        self.assertIn("job first: MISSING (missing.dat)", lines)
        self.assertEqual(lines.count("job ?: malformed entry"), 2)
        self.assertTrue(lines[-1].startswith("job last: present,"), lines)

    def test_invalid_paths_are_reported_without_losing_other_warnings(self):
        lines = self.probe([
            {"name": "missing-artifact"},
            {"name": "null-artifact", "artifact": None},
            {"name": "numeric-artifact", "artifact": 123},
            {"name": "invalid-reference", "artifact": "built.json", "newer_than": []},
            {"name": "bad-name", "artifact": "built.json", "newer_than": {}},
            {"name": "okay", "artifact": "built.json"},
        ])
        self.assertEqual(len(lines), 6)
        self.assertEqual(sum("missing/invalid artifact path" in x for x in lines), 3)
        self.assertEqual(sum("invalid newer_than path" in x for x in lines), 2)
        self.assertTrue(lines[-1].startswith("job okay: present,"))

    def test_stale_warning_survives_malformed_entry(self):
        original = self.good.stat().st_mtime - 120
        os.utime(self.good, (original, original))
        lines = self.probe([
            99,
            {"name": "stale", "artifact": "built.json", "newer_than": "source.txt"},
            {"name": "absent", "artifact": "missing.json"},
        ])
        self.assertEqual(len(lines), 3)
        self.assertIn("job ?: malformed entry", lines)
        self.assertIn("STALE vs source.txt", lines[1])
        self.assertIn("job absent: MISSING", lines[2])

    def test_disappearing_file_reports_failure_without_raising(self):
        actual = baton.os.path.getsize

        def fail_one(path):
            if os.fspath(path) == str(self.good):
                raise PermissionError("synthetic private absolute path")
            return actual(path)

        with mock.patch.object(baton.os.path, "getsize", side_effect=fail_one):
            lines = self.probe([
                {"name": "vanished", "artifact": "built.json"},
                {"name": "missing", "artifact": "never-made"},
            ])
        self.assertEqual(lines[0], "job vanished: unavailable while probing")
        self.assertNotIn("synthetic private absolute path", "\n".join(lines))
        self.assertEqual(lines[1], "job missing: MISSING (never-made)")

    def test_render_status_retains_job_diagnostics_instead_of_swallowing(self):
        jobs = [
            {"name": "broken", "artifact": {"unexpected": "object"}},
            {"name": "real", "artifact": "missing-render-output.png"},
        ]
        with (
            mock.patch.object(baton, "read_probes_config", return_value={"jobs": jobs}),
            mock.patch.object(baton, "probe_git", return_value=[]),
            mock.patch.object(baton, "probe_venvs", return_value=[]),
            mock.patch.object(baton, "probe_gpu", return_value=None),
            mock.patch.object(baton, "probe_command_probes", return_value=[]),
            mock.patch.object(baton, "probe_paths", return_value=[]),
            mock.patch.object(baton, "probe_volatile", return_value=[]),
        ):
            status = baton.render_status(str(self.root))
        self.assertIn("job broken: missing/invalid artifact path", status)
        self.assertIn("job real: MISSING (missing-render-output.png)", status)

    def test_legacy_bare_list_and_bad_jobs_section(self):
        with mock.patch.object(
            baton, "read_probes_config",
            return_value={"jobs": [{"name": "legacy", "artifact": "missing"}]}
        ):
            self.assertIn("job legacy: MISSING", baton.probe_project_jobs(str(self.root))[0])
        with mock.patch.object(baton, "read_probes_config", return_value={"jobs": {}}):
            self.assertIn("not a list", baton.probe_project_jobs(str(self.root))[0])


if __name__ == "__main__":
    unittest.main()
