import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "nightly_failures", Path(__file__).resolve().parents[1] / "scripts/nightly_failures.py"
)
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)


class NightlyFailureTests(unittest.TestCase):
    def test_junit_identity_excludes_failure_output_and_parameters(self):
        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "run.xml"
            evidence.write_text(
                '<testsuite><testcase classname="api" name="test_call[private]">'
                '<failure message="private output"/></testcase>'
                '<testcase classname="api" name="test_ok"/></testsuite>'
            )
            cases = [nightly.failure_record(case) for case in nightly.failures(evidence)]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["label"], "api::test_call")
        self.assertNotIn("private", repr(cases))

    def test_report_links_closed_issue_and_deduplicates_open_issue(self):
        closed = nightly.failure_record("closed case")
        active = nightly.failure_record("active case")
        issues = [
            {"number": 8, "state": "closed", "body": nightly.marker(closed)},
            {"number": 9, "state": "open", "body": nightly.marker(active)},
        ]
        with (
            patch.dict(os.environ, GITHUB_REPOSITORY="example/tests", GITHUB_RUN_ID="42"),
            patch.object(nightly, "list_issues", return_value=issues),
            patch.object(nightly, "api") as api,
        ):
            nightly.report([closed, active, closed])
        api.assert_called_once()
        _, payload = api.call_args.args
        self.assertIn("closed issue #8", payload["body"])
        self.assertIn("/actions/runs/42", payload["body"])


if __name__ == "__main__":
    unittest.main()
