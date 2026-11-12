import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "1password-items.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
LOGIN_FILTERS = ["--category", "Login", "--vault", "Personal"]


class ItemCommandTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.log = Path(scratch.name) / "op.log"
        self.env = dict(
            os.environ,
            PATH=os.pathsep.join([str(FIXTURES), str(Path(sys.executable).parent), os.defpath]),
            TEST_OP_LOG=str(self.log),
            TEST_REFRESH_TAG="",
        )

    def run_items(self, *arguments, answer="", **env):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            input=answer,
            capture_output=True,
            text=True,
            env=dict(self.env, **env),
        )
        edits = (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists() else []
        )
        return result, edits

    def test_fake_op_contract(self):
        result = subprocess.run(
            [str(FIXTURES / "op"), "item", "list", "--format", "json"],
            capture_output=True, text=True, check=True, env=self.env,
        )
        items = {item["id"]: item for item in json.loads(result.stdout)}
        self.assertEqual(items["google"]["tags"], ["Personal"])
        self.assertEqual(items["gmail"]["tags"], ["Google Accounts"])
        self.assertNotIn("fields", items["other"])
        details = subprocess.run(
            [str(FIXTURES / "op"), "item", "get", "other", "--vault", "vault-id",
             "--format", "json"],
            capture_output=True, text=True, check=True, env=self.env,
        )
        self.assertEqual(json.loads(details.stdout)["fields"][0]["value"], "example@gmail.com")
        rejected = subprocess.run(
            [str(FIXTURES / "op"), "item", "edit", "work2", "--vault", "vault-id",
             "--title", "Workday | Reject"],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(rejected.returncode, 8)
        self.assertIn("ssoLogin", rejected.stderr)

    def test_search_counts_before_showing_titles(self):
        for answer, show in [("y\n", True), ("yes\n", True), ("no\n", False),
                             ("\n", False), ("", False)]:
            with self.subTest(answer=answer):
                result, edits = self.run_items("search", answer=answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith(
                    "7 matching items.\nShow all matching items? [y/N] "
                ), result.stdout)
                self.assertEqual("'Google' [Personal]" in result.stdout, show)
                self.assertEqual(edits, [])

    def test_search_matches_titles_only(self):
        result, edits = self.run_items(
            "search", "--title", "google|gmail", *LOGIN_FILTERS, answer="yes\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 matching items.", result.stdout)
        self.assertIn("'Gmail'", result.stdout)
        self.assertIn("'Google'", result.stdout)
        self.assertNotIn("Third party", result.stdout)
        self.assertEqual(edits, [])

    def test_search_with_no_matches_does_not_prompt(self):
        result, edits = self.run_items("search", "--title", "^No such item$")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "No matching items.\n")
        self.assertEqual(edits, [])

    def test_workday_rename_continues_after_rejected_edit(self):
        result, edits = self.run_items(
            "change", "--has-tag", "Workday", "--replace", "Myworkdayjobs - ",
            "Workday | ", "--prefix", *LOGIN_FILTERS, answer="all\n",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ssoLogin", result.stderr)
        self.assertEqual([edit[0] for edit in edits], ["work1", "work2", "work3"])
        self.assertEqual([edit[-2:] for edit in edits], [
            ["--title", "Workday | Acme"],
            ["--title", "Workday | Reject"],
            ["--title", "Workday | Zenith"],
        ])
        self.assertIn("Applied 2 changes. Skipped due to errors: 1.", result.stdout)

    def test_google_tag_preserves_existing_tags(self):
        result, edits = self.run_items(
            "tag", "Google Accounts", "--title", "google|gmail", *LOGIN_FILTERS,
            answer="1\n", TEST_REFRESH_TAG="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("'Gmail'", result.stdout)
        self.assertEqual(edits, [[
            "google", "--vault", "vault-id", "--tags",
            "Personal,Added after preview,Google Accounts",
        ]])

    def test_tag_cancellation_and_invalid_selection_send_no_edits(self):
        for answer, code in [("\n", 0), ("2\n", 1)]:
            with self.subTest(answer=answer):
                result, edits = self.run_items(
                    "tag", "Google Accounts", "--title", "google|gmail", *LOGIN_FILTERS,
                    answer=answer,
                )
                self.assertEqual(result.returncode, code)
                self.assertEqual(edits, [])

    def test_change_sectioned_field(self):
        result, edits = self.run_items(
            "change", "--title", "^Google$", "--field", "Contact", "--section", "Details",
            "--set", "new@example.com", *LOGIN_FILTERS, answer="all\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(edits, [
            ["google", "--vault", "vault-id", "sect123.abc123=new@example.com"]
        ])
        self.assertIn("'old@example.com' → 'new@example.com'", result.stdout)

    def test_change_rejects_password_field(self):
        result, edits = self.run_items(
            "change", "--title", "^Google$", "--field", "password", "--set", "sample",
            *LOGIN_FILTERS, answer="all\n",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("supported plain-text field", result.stderr)
        self.assertEqual(edits, [])
