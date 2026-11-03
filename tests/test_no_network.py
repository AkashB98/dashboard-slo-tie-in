"""No-network enforcement: with sockets blocked, generating,
validating, converting and rendering must all still work. (``serve``
is the only intentional exception - it binds loopback on demand,
and is not used here.)"""

import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Blocker:
    def __init__(self, *a, **k):
        raise RuntimeError("network access blocked in hermetic test")


class TestNoNetwork(unittest.TestCase):
    def setUp(self):
        self._real_socket = socket.socket
        socket.socket = _Blocker
        import urllib.request as urlreq
        self._real_urlopen = urlreq.urlopen
        urlreq.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("network access blocked in hermetic test"))

    def tearDown(self):
        socket.socket = self._real_socket
        import urllib.request as urlreq
        urlreq.urlopen = self._real_urlopen

    def test_full_pipeline_offline(self):
        from convert import convert_reports
        from fixtures import generate_reports
        from render import payload_to_json, render_html
        from validation import validate_reports
        reports = generate_reports()
        self.assertEqual(validate_reports(reports), [])
        payload = convert_reports(reports)
        self.assertEqual(payload["fleet"]["status"], "warning")
        self.assertIn("fleet: warning", render_html(payload))
        self.assertIn('"run_id": "slo-20260201T235800Z"',
                      payload_to_json(payload))

    def test_no_module_opens_sockets_at_import(self):
        for mod in ("fixtures", "validation", "convert", "render",
                    "cli", "demo"):
            sys.modules.pop(mod, None)
        import cli, convert, demo, fixtures, render, validation  # noqa: F401
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
