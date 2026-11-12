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
            TEST_PAGE_COUNT="0",
            TEST_REJECT_THROUGH="0",
            TEST_LIST_EXIT="0",
            TEST_LIST_DIAGNOSTIC="",
        )

    def run_items(self, *arguments, answer="", terminal=False, clock=None, **env):
        self.log.unlink(missing_ok=True)
        command = [sys.executable]
        if clock is not None:
            command.extend([
                "-c",
                "from datetime import datetime; import json, runpy, sys; "
                "times = iter(map(datetime.fromisoformat, json.loads(sys.argv.pop(1)))); "
                "main = runpy.run_path(sys.argv.pop(1))['main']; "
                "sys.exit(main(now=lambda: next(times)))",
                json.dumps(clock),
            ])
        elif terminal:
            command.extend([
                "-c",
                "import runpy, sys; sys.stdout.isatty = lambda: True; "
                "runpy.run_path(sys.argv.pop(1), run_name='__main__')",
            ])
        result = subprocess.run(
            [*command, str(SCRIPT), *arguments],
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
                self.assertRegex(
                    result.stdout,
                    r"^\[\d{4}-\d{2}-\d{2}\]\n\[\d{2}:\d{2}:\d{2}\] 7 matching items\.",
                )
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
        result, edits = self.run_items(
            "search", "--title", "^No such item$",
            clock=["2026-11-12T09:05:04", "2026-11-12T09:05:05"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(
            result.stdout,
            r"^\[\d{4}-\d{2}-\d{2}\]\n\[\d{2}:\d{2}:\d{2}\] No matching items\.\n$",
        )
        self.assertEqual(edits, [])

    def test_workday_rename_continues_after_rejected_edit(self):
        result, edits = self.run_items(
            "change", "--has-tag", "Workday", "--replace", "Myworkdayjobs - ",
            "Workday | ", "--prefix", *LOGIN_FILTERS, answer="all\ny\n", terminal=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ssoLogin", result.stdout)
        self.assertEqual([edit[0] for edit in edits], ["work1", "work2", "work3"])
        self.assertEqual([edit[-2:] for edit in edits], [
            ["--title", "Workday | Acme"],
            ["--title", "Workday | Reject"],
            ["--title", "Workday | Zenith"],
        ])
        self.assertRegex(
            result.stdout, r"\n\[\d{2}:\d{2}:\d{2}\] Applied 2 changes\. Skipped due to errors: 1\."
        )
        self.assertIn("\n2. Skipped 'Myworkdayjobs - Reject' [Personal]", result.stdout)
        self.assertNotIn("Updated '", result.stdout)
        progress = [line for line in result.stdout.splitlines() if "Applying changes" in line]
        self.assertEqual(
            [line.removeprefix("Choice: ") for line in progress],
            [f"Applying changes… {processed}/3 items processed." for processed in range(4)],
        )

    def test_google_tag_preserves_existing_tags(self):
        result, edits = self.run_items(
            "tag", "Google Accounts", "--title", "google|gmail", *LOGIN_FILTERS,
            answer="1\ny\n", TEST_REFRESH_TAG="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("'Gmail'", result.stdout)
        self.assertRegex(result.stdout, r"\n\n\[\d{2}:\d{2}:\d{2}\] You previewed 1 of 1 proposed changes\.\n\n")
        self.assertEqual(result.stdout.count("Applying changes…"), 1)
        self.assertIn("Applying changes… 0/1 items processed.", result.stdout)
        self.assertEqual(edits, [[
            "google", "--vault", "vault-id", "--tags",
            "Personal,Added after preview,Google Accounts",
        ]])

    def test_tag_cancellation_and_invalid_selection_send_no_edits(self):
        for answer in ["q\n", "2\nq\n", "1\nn\nq\n", "1\n\nq\n"]:
            with self.subTest(answer=answer):
                result, edits = self.run_items(
                    "tag", "Google Accounts", "--title", "google|gmail", *LOGIN_FILTERS,
                    answer=answer,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Canceled.", result.stdout)
                self.assertNotIn("Applying changes", result.stdout)
                self.assertEqual(edits, [])

    def test_change_sectioned_field(self):
        result, edits = self.run_items(
            "change", "--title", "^Google$", "--field", "Contact", "--section", "Details",
            "--set", "new@example.com", *LOGIN_FILTERS, answer="all\ny\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(edits, [
            ["google", "--vault", "vault-id", "sect123.abc123=new@example.com"]
        ])
        self.assertIn("'old@example.com' → 'new@example.com'", result.stdout)

    def test_change_rejects_password_field(self):
        result, edits = self.run_items(
            "change", "--title", "^Google$", "--field", "password", "--set", "sample",
            *LOGIN_FILTERS, answer="all\ny\n",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("supported plain-text field", result.stdout)
        self.assertEqual(edits, [])

    def test_search_pages_at_ten_items(self):
        for count, answer, expected in [
            (10, "y\n", 10),
            (11, "y\n\n", 10),
            (11, "y\ny\n", 11),
            (23, "y\ny\ny\n", 23),
            (23, "\n", 0),
        ]:
            with self.subTest(count=count, answer=answer):
                result, edits = self.run_items("search", answer=answer, TEST_PAGE_COUNT=str(count))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, rf"\n\[\d{{2}}:\d{{2}}:\d{{2}}\] {count} matching items\. ")
                self.assertEqual(result.stdout.count("'Sample "), expected)
                self.assertEqual(edits, [])
                if count == 23 and expected == 23:
                    self.assertIn("Show the first 10?", result.stdout)
                    self.assertIn("Show the next 10 of 13 remaining results?", result.stdout)
                    self.assertIn("Show the next 3 results?", result.stdout)

    def test_shown_applies_only_to_previewed_items(self):
        for command in [("change", "--set", "Renamed"), ("tag", "Sample Tag")]:
            with self.subTest(command=command[0]):
                result, edits = self.run_items(
                    *command, "--title", "^Sample", answer="y\nn\nshown\ny\n",
                    TEST_PAGE_COUNT="23",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("23 proposed changes.\n\n1. 'Sample 01'", result.stdout)
                self.assertRegex(
                    result.stdout,
                    r"\n\n\[\d{2}:\d{2}:\d{2}\] Show the next 10 of 13 remaining proposed changes\?\n\n",
                )
                self.assertIn("11. 'Sample 11'", result.stdout)
                self.assertNotIn("21. 'Sample 21'", result.stdout)
                self.assertEqual([edit[0] for edit in edits], [
                    f"page{number:02}" for number in range(1, 21)
                ])
                self.assertNotIn("Updated 'Sample ", result.stdout)
                self.assertRegex(
                    result.stdout,
                    r"Applied 20 changes\. Skipped due to errors: 0\.\n(?:\[\d{4}-\d{2}-\d{2}\]\n)?$",
                )

    def test_mixed_selection_previews_only_unseen_selected_rows(self):
        result, edits = self.run_items(
            "tag", "Sample Tag", "--title", "^Sample",
            answer="y\nn\n1,4,7-11, 20-22, 8\np\ny\n", TEST_PAGE_COUNT="23",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Apply changes to 10 items? 2 items haven't been previewed.", result.stdout)
        self.assertIn("21. 'Sample 21'", result.stdout)
        self.assertIn("22. 'Sample 22'", result.stdout)
        self.assertNotIn("23. 'Sample 23'", result.stdout)
        self.assertIn(
            "Apply changes to 10 items?\n\n  y    Apply changes\n  n    Go back to item selection\n  q    Quit without applying changes\n\nChoice:",
            result.stdout,
        )
        self.assertEqual([edit[0] for edit in edits], [
            f"page{number:02}" for number in [1, 4, 7, 8, 9, 10, 11, 20, 21, 22]
        ])

    def test_all_can_apply_without_previewing_every_item(self):
        result, edits = self.run_items(
            "tag", "Sample Tag", "--title", "^Sample",
            answer="n\nall\ny\nn\n", TEST_PAGE_COUNT="11",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Apply changes to 11 items? 1 item hasn't been previewed.", result.stdout)
        self.assertNotIn("11. 'Sample 11'", result.stdout)
        self.assertEqual([edit[0] for edit in edits], [
            f"page{number:02}" for number in range(1, 12)
        ])

    def test_shown_remembers_nonconsecutive_rows_after_going_back(self):
        result, edits = self.run_items(
            "tag", "Sample Tag", "--title", "^Sample",
            answer="n\n22\np\nn\nshown\ny\nn\n", TEST_PAGE_COUNT="23",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("22. 'Sample 22'", result.stdout)
        self.assertIn("You previewed 11 of 23 proposed changes.", result.stdout)
        self.assertIn(
            "Apply changes to 11 items?\n\n  y    Apply changes\n  n    Go back to item selection\n  q    Quit without applying changes\n\nChoice:",
            result.stdout,
        )
        self.assertEqual([edit[0] for edit in edits], [
            f"page{number:02}" for number in [*range(1, 11), 22]
        ])

    def test_partial_unseen_preview_can_resume_without_editing(self):
        result, edits = self.run_items(
            "tag", "Sample Tag", "--title", "^Sample",
            answer="n\nall\np\nn\np\nn\nq\n", TEST_PAGE_COUNT="23",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("13 items haven't been previewed.", result.stdout)
        self.assertIn("  n    Go back to item selection\n  p    Preview the 13 unseen items", result.stdout)
        self.assertIn("  n    Return to confirmation", result.stdout)
        self.assertIn("3 items haven't been previewed.", result.stdout)
        self.assertIn("23. 'Sample 23'", result.stdout)
        self.assertIn(
            "Apply changes to 23 items?\n\n  y    Apply changes\n  n    Go back to item selection\n  q    Quit without applying changes\n\nChoice:",
            result.stdout,
        )
        self.assertEqual(result.stdout.count("11. 'Sample 11'"), 1)
        self.assertEqual(edits, [])

    def test_invalid_selections_and_confirmation_retry(self):
        result, edits = self.run_items(
            "tag", "Sample Tag", "--title", "^Sample",
            answer="n\n1,,2\n3-2\n22-24\n0\n3 - 4, 4\nmaybe\ny\n", TEST_PAGE_COUNT="23",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Enter all, shown, item numbers or ranges, or q.", result.stdout)
        self.assertEqual(result.stdout.count("Use item numbers from 1 to 23"), 3)
        self.assertIn("Enter y, n, or q.", result.stdout)
        self.assertEqual([edit[0] for edit in edits], ["page03", "page04"])

    def test_preparation_failures_are_paged(self):
        result, edits = self.run_items(
            "change", "--field", "Missing", "--set", "Sample", "--title", "^Sample",
            answer="q\n", TEST_PAGE_COUNT="11",
        )
        self.assertEqual(result.returncode, 1)
        self.assertRegex(
            result.stdout, r"No changes to apply\.\n\[\d{2}:\d{2}:\d{2}\] Skipped due to errors: 11\."
        )
        self.assertEqual(result.stdout.count("Skipped 'Sample "), 10)
        self.assertIn("Show the next error?", result.stdout)
        self.assertEqual(edits, [])

    def test_blank_invalid_and_quit_at_every_dialog(self):
        tag = ("tag", "Sample Tag", "--title", "^Sample")
        cases = [
            ("search first", ("search",), "", 23, "Show the first 10?", 0),
            ("search more", ("search",), "y\n", 23, "Finish viewing results", 0),
            ("edit preview", tag, "", 23, "Continue to item selection", 0),
            ("item selection", tag, "", 1, "Select the only item", 0),
            ("confirm unseen", tag, "n\nall\n", 23, "Preview the 13 unseen items", 0),
            ("page unseen", tag, "n\nall\np\n", 23, "Return to confirmation", 0),
            ("confirm seen", tag, "1\n", 1, "Go back to item selection", 0),
            ("errors", tag, "n\nall\ny\n", 12, "Finish viewing errors", 12),
        ]
        for name, command, prefix, count, label, applied in cases:
            with self.subTest(dialog=name):
                result, edits = self.run_items(
                    *command, answer=prefix + "\nj\n7\nq\ny\n", TEST_PAGE_COUNT=str(count),
                    TEST_REJECT_THROUGH="11" if name == "errors" else "0",
                )
                self.assertEqual(result.returncode, int(name == "errors"), result.stderr)
                self.assertIn(label, result.stdout)
                if name == "item selection":
                    self.assertRegex(
                        result.stdout,
                        r"Selection: \nSelection: \n\[\d{2}:\d{2}:\d{2}\] Enter all, shown, item numbers or ranges, or q\.",
                    )
                    self.assertIn("Use item numbers from 1 to 1", result.stdout)
                else:
                    keys = "y, n, p, or q" if name == "confirm unseen" else "y, n, or q"
                    hint = f"Enter {keys}."
                    self.assertRegex(
                        result.stdout, rf"Choice: \nChoice: \n\[\d{{2}}:\d{{2}}:\d{{2}}\] {hint}"
                    )
                    self.assertEqual(result.stdout.count(hint), 2)
                self.assertEqual(len(edits), applied)
                if applied:
                    self.assertIn("Applied 1 change. Skipped due to errors: 11.", result.stdout)
                    self.assertIn("Show the next error?", result.stdout)
                    self.assertEqual(result.stdout.count("Skipped 'Sample "), 10)
                    self.assertIn("10. Skipped 'Sample 10'", result.stdout)
                    self.assertNotIn("11. Skipped 'Sample 11'", result.stdout)
                    self.assertNotIn("Updated '", result.stdout)
                else:
                    self.assertNotIn("Applied ", result.stdout)

    def test_primary_messages_have_local_timestamps_and_rows_do_not(self):
        result, edits = self.run_items(
            "search", answer="j\ny\n", clock=[
                "2026-11-12T09:05:04", "2026-11-12T09:05:06", "2026-11-12T09:05:10",
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(
            "[2026-11-12]\n[09:05:04] 7 matching items. Show all 7 matching items?\n\n"
        ))
        self.assertIn("\n  y    Show results\n", result.stdout)
        self.assertIn("\nChoice: \n[09:05:06] Enter y, n, or q.\nChoice: \n", result.stdout)
        self.assertIn("\n'Google' [Personal] (LOGIN)\n", result.stdout)
        self.assertEqual(result.stdout.count("[2026-11-12]"), 1)
        self.assertEqual(edits, [])

    def test_date_footer_on_quitting_after_midnight(self):
        result, edits = self.run_items(
            "search", answer="q\n", clock=["2026-11-12T23:59:58", "2026-11-13T00:00:01"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("[2026-11-12]\n[23:59:58]"))
        self.assertTrue(result.stdout.endswith("Choice: \n[2026-11-13]\n"))
        self.assertEqual(edits, [])

    def test_failed_cli_diagnostic_follows_timestamped_error(self):
        result, edits = self.run_items(
            "search", clock=["2026-11-12T23:59:58", "2026-11-13T00:00:01"],
            TEST_LIST_EXIT="8", TEST_LIST_DIAGNOSTIC="diagnostic line\ncontinued",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr,
                         "[2026-11-12]\n[23:59:58] 1Password CLI failed (exit 8).\n"
                         "diagnostic line\ncontinued\n[2026-11-13]\n")
        self.assertEqual(edits, [])

    def test_help_and_argument_errors_have_no_timestamps(self):
        for arguments, status in [(("--help",), 0), (("tag",), 2)]:
            with self.subTest(arguments=arguments):
                result, edits = self.run_items(*arguments, clock=[])
                self.assertEqual(result.returncode, status)
                self.assertIn("usage:", result.stdout + result.stderr)
                self.assertNotRegex(result.stdout + result.stderr, r"\[(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})\]")
                self.assertEqual(edits, [])
