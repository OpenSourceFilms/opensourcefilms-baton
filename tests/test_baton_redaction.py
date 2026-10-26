"""Synthetic-only tests for sensitive text redaction in BATON hook context.

Test strings are fabricated and cannot authenticate with any service.
No transcript archive, user environment or actual credentials are accessed.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baton_module", ROOT / "baton.py")
BATON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATON)


class RedactionContract(unittest.TestCase):
    @staticmethod
    def synthetic_tokens():
        return (
            "github_" + "pat_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890123456789",
            "ghp_" + "A" * 40,
            "gho_" + "B" * 40,
            "ghs_" + "C" * 40,
            "ghu_" + "D" * 40,
            "ghr_" + "E" * 40,
            "hf_" + "F" * 32,
        )

    def test_bare_access_tokens_are_removed(self):
        for secret in self.synthetic_tokens():
            with self.subTest(prefix=secret[:5]):
                result = BATON.redact("tool arguments: " + secret)
                self.assertNotIn(secret, result)
                self.assertIn("<redacted>", result)

    def test_authentication_urls_with_userinfo_are_removed(self):
        for url in (
            "https://demo:" + "sensitivepassword" + "@example.invalid/path",
            "http://name:" + "sensitivepassword" + "@127.0.0.1:8000/api",
            "ftp://guest:" + "sensitivepassword" + "@files.example.invalid/a",
        ):
            with self.subTest(scheme=url.split(":",1)[0]):
                result = BATON.redact("received " + url)
                self.assertNotIn("sensitivepassword", result)
                self.assertNotIn(url, result)
                self.assertIn("<redacted>", result)

    def test_safe_urls_and_normal_strings_remain_visible(self):
        samples = (
            "See https://example.org/docs/install?q=readme for details.",
            "Download path is /tmp/project/README.md",
            "The user asked about Python tokenisation in a tutorial.",
        )
        for sample in samples:
            self.assertEqual(BATON.redact(sample), sample)

    def test_prior_bearer_keyvalue_and_sk_redaction_preserved(self):
        fragments = [
            "Authorization: Bearer " + "Q" * 32,
            "password=" + "testpasswordABC123",
            "sk-" + "Z" * 35,
        ]
        for value in fragments:
            result = BATON.redact("tool output " + value)
            self.assertNotIn(value, result)
            self.assertIn("<redacted>", result)

    def test_redaction_preserves_empty_value_contract(self):
        self.assertEqual(BATON.redact(""), "")
        self.assertIsNone(BATON.redact(None))

    def test_mixed_token_and_safe_context(self):
        text = "GPU healthy. " + self.synthetic_tokens()[0] + " Source unchanged."
        result = BATON.redact(text)
        self.assertNotIn(self.synthetic_tokens()[0], result)
        self.assertIn("GPU healthy.", result)
        self.assertIn("Source unchanged.", result)


if __name__ == "__main__":
    unittest.main()
