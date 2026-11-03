"""CLI: commands, exit codes (check 0/1/2), serve round-trip."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK  # noqa: E402
from cli import main as cli_main, make_server, reset_state  # noqa: E402
from convert import convert_reports  # noqa: E402
from fixtures import generate_reports  # noqa: E402
from helpers import incident  # noqa: E402


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCliCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tie-in-cli-")

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_generate_outputs_three_valid_exports(self):
        code, out, _ = run_cli(["generate"])
        self.assertEqual(code, EXIT_OK)
        reports = json.loads(out)
        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[-1]["now"], "2026-02-01T23:58:00Z")

    def test_convert_seeded_payload(self):
        code, out, _ = run_cli(["convert"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["fleet"]["status"], "warning")
        self.assertEqual(len(payload["tiles"]), 5)

    def test_convert_single_export_file(self):
        path = self._write("one.json", incident())
        code, out, _ = run_cli(["convert", "--input", path])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out)["source_reports"], 1)

    def test_convert_invalid_file_exit_2(self):
        path = self._write("bad.json", {"bogus": 1})
        code, _, err = run_cli(["convert", "--input", path])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("data-quality", err)

    def test_validate_seeded_and_invalid(self):
        code, out, _ = run_cli(["validate"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("valid: 3", out)
        path = self._write("bad.json", {"bogus": 1})
        code, out, _ = run_cli(["validate", "--input", path])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("invalid:", out)

    def test_render_html_and_json(self):
        code, out, _ = run_cli(["render"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("fleet: warning", out)
        code, out, _ = run_cli(["render", "--format", "json"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out)["tool"], "agent-slo-tracker")

    def test_report_summary_lines(self):
        code, out, _ = run_cli(["report"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("fleet: warning", out)
        self.assertIn("slo-success", out)
        self.assertIn("trend success: steady", out)

    def test_out_file_written(self):
        path = os.path.join(self.tmp, "payload.json")
        code, out, _ = run_cli(["convert", "--out", path])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("dashboard payload ->", out)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["run_id"],
                             "slo-20260201T235800Z")


class TestCheckGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tie-in-check-")

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_check_seeded_latest_passes(self):
        code, out, _ = run_cli(["check"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("check PASS", out)

    def test_check_page_firing_fails_with_1(self):
        path = self._write("incident.json", incident())
        code, out, _ = run_cli(["check", "--input", path])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("check FAIL", out)

    def test_check_missing_file_is_2(self):
        code, _, _ = run_cli(
            ["check", "--input", "/tmp/no-such-tie-in-xyz.json"])
        self.assertEqual(code, EXIT_ERROR)

    def test_check_breached_slo_fails_with_1(self):
        # A validator-clean breached export: every success run bad,
        # budget math and exhaustion projection recomputed to match.
        from helpers import latest
        report = latest()
        block = report["slos"]["success"]
        allowed = (1.0 - 0.95) * block["eligible"]
        block["good"] = 0
        block["bad"] = block["eligible"]
        block["observed"] = 0.0
        block["compliant"] = False
        block["budget"].update({
            "consumed": block["bad"],
            "consumed_pct": round(block["bad"] / allowed, 6),
            "remaining": round(allowed - block["bad"], 4),
            "remaining_pct": round(
                (allowed - block["bad"]) / allowed, 6),
        })
        report["projected_exhaustion"]["success"] = {"hours": 0.0,
                                                     "days": 0.0}
        code, out, _ = run_cli(["check", "--input",
                                self._write("breached.json", report)])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("SLO breached", out)


class TestServe(unittest.TestCase):
    def setUp(self):
        reset_state(generate_reports())
        self.server = make_server(0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        reset_state()

    def _post(self, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/convert",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())

    def test_health(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/health",
                timeout=10) as resp:
            self.assertEqual(json.loads(resp.read()), {"status": "ok"})

    def test_convert_empty_body_is_seeded(self):
        status, payload = self._post({})
        self.assertEqual(status, 200)
        self.assertEqual(payload, convert_reports(generate_reports()))

    def test_convert_explicit_report(self):
        status, payload = self._post({"report": incident()})
        self.assertEqual(status, 200)
        self.assertEqual(payload["fleet"]["status"], "critical")

    def test_convert_bare_export_body(self):
        status, payload = self._post(incident())
        self.assertEqual(status, 200)
        self.assertTrue(payload["fleet"]["page_firing"])

    def test_convert_invalid_is_400(self):
        import urllib.error
        try:
            self._post({"report": {"bogus": 1}})
            self.fail("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
