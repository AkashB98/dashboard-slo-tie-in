"""Renderer: canonical JSON + self-contained, escaped HTML panel."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from convert import convert_reports  # noqa: E402
from helpers import all_reports  # noqa: E402
from render import payload_to_json, render_html  # noqa: E402


class TestRender(unittest.TestCase):
    def setUp(self):
        self.payload = convert_reports(all_reports())
        self.page = render_html(self.payload)

    def test_json_is_canonical_and_deterministic(self):
        text = payload_to_json(self.payload)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(payload_to_json(convert_reports(all_reports())),
                         text)
        self.assertIn('"status": "warning"', text)

    def test_html_deterministic(self):
        self.assertEqual(render_html(self.payload), self.page)

    def test_html_shows_fleet_and_tiles(self):
        self.assertIn("fleet: warning", self.page)
        self.assertIn("SIMULATED DATA", self.page)
        for tile_id in ("slo-success", "slo-latency",
                        "agent-billing-agent"):
            self.assertIn(tile_id, self.page)

    def test_html_has_no_javascript_or_external_assets(self):
        self.assertNotIn("<script", self.page)
        self.assertNotIn("http://", self.page)
        self.assertNotIn("https://", self.page)
        self.assertNotIn("<link", self.page)
        self.assertNotIn("src=", self.page)

    def test_html_css_is_inline(self):
        self.assertIn("<style>", self.page)
        self.assertIn(".card", self.page)

    def test_dynamic_strings_are_escaped(self):
        payload = copy.deepcopy(self.payload)
        payload["tiles"][0]["headline_label"] = \
            '<script>alert("x")</script>'
        payload["tiles"][0]["findings"] = [{
            "severity": "high", "title": "<b>bold</b>",
            "evidence": 'a "quoted" <tag> & more'}]
        page = render_html(payload)
        self.assertNotIn("<script", page)
        self.assertNotIn("<b>bold</b>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;tag&gt;", page)
        self.assertIn("&quot;quoted&quot;", page)

    def test_alerts_section_lists_fleet_alerts(self):
        self.assertIn("Fleet alerts (1)", self.page)
        self.assertIn("billing-agent success error budget nearly "
                      "exhausted", self.page)


if __name__ == "__main__":
    unittest.main()
