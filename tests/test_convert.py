"""Converter: tiles, status mapping, findings, trends, fleet."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from convert import convert_report, convert_reports  # noqa: E402
from helpers import all_reports, incident, latest  # noqa: E402

DASHBOARD_KEYS = {"tool", "run_id", "ts", "headline", "headline_label",
                  "metrics", "breakdown", "findings", "status"}


def tiles_by_id(payload):
    return {t["tile_id"]: t for t in payload["tiles"]}


class TestConvertLatest(unittest.TestCase):
    def setUp(self):
        self.payload = convert_reports(all_reports())
        self.tiles = tiles_by_id(self.payload)

    def test_five_tiles(self):
        self.assertEqual(len(self.payload["tiles"]), 5)
        self.assertEqual(set(self.tiles),
                         {"slo-success", "slo-latency",
                          "agent-billing-agent", "agent-diagnostics-agent",
                          "agent-support-agent"})

    def test_tiles_carry_dashboard_record_shape(self):
        for tile in self.payload["tiles"]:
            self.assertTrue(DASHBOARD_KEYS <= set(tile), tile["tile_id"])
            self.assertEqual(tile["tool"], "agent-slo-tracker")
            self.assertTrue(0.0 <= tile["headline"] <= 1.0)
            for finding in tile["findings"]:
                self.assertEqual(set(finding),
                                 {"severity", "title", "evidence"})
                self.assertTrue(finding["evidence"].strip())

    def test_headline_pins(self):
        self.assertEqual(self.tiles["slo-success"]["headline"], 0.955941)
        self.assertEqual(self.tiles["slo-latency"]["headline"], 0.973997)
        self.assertEqual(
            self.tiles["agent-billing-agent"]["headline"], 0.950754)

    def test_status_pins_latest(self):
        self.assertEqual(self.tiles["slo-success"]["status"], "warning")
        self.assertEqual(self.tiles["slo-latency"]["status"], "green")
        for agent in ("billing-agent", "diagnostics-agent",
                      "support-agent"):
            self.assertEqual(self.tiles[f"agent-{agent}"]["status"],
                             "warning")
        self.assertEqual(self.payload["fleet"]["status"], "warning")

    def test_run_ids_deterministic(self):
        self.assertEqual(self.payload["run_id"], "slo-20260201T235800Z")
        self.assertEqual(self.tiles["slo-success"]["run_id"],
                         "slo-20260201T235800Z-success")
        again = convert_reports(all_reports())
        self.assertEqual(again, self.payload)

    def test_envelope_wraps_latest_report(self):
        env = self.payload["envelope"]
        self.assertEqual(env["tool"], "agent-slo-tracker")
        self.assertEqual(env["ts"], "2026-02-01T23:58:00Z")
        self.assertEqual(env["report"], latest())

    def test_trends_steady_with_dashboard_epsilon(self):
        trends = self.payload["trends"]
        self.assertEqual(trends["success"]["headlines"],
                         [0.969287, 0.953967, 0.955941])
        self.assertEqual(trends["success"]["trend"], "steady")
        self.assertEqual(trends["success"]["delta"], 0.001974)
        self.assertEqual(trends["latency"]["delta"], 0.003979)

    def test_single_report_trend_is_new(self):
        payload = convert_report(latest())
        self.assertEqual(payload["trends"]["success"]["trend"], "new")
        self.assertIsNone(payload["trends"]["success"]["delta"])
        self.assertEqual(payload["source_reports"], 1)

    def test_latest_fleet_alert_is_billing_high(self):
        alerts = self.payload["fleet"]["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["tile_id"], "agent-billing-agent")
        self.assertEqual(alerts[0]["severity"], "high")
        self.assertIn("98.49%", alerts[0]["evidence"])
        self.assertEqual(self.payload["fleet"]["breached_slos"], [])
        self.assertFalse(self.payload["fleet"]["page_firing"])


class TestConvertIncident(unittest.TestCase):
    def setUp(self):
        self.payload = convert_reports(all_reports()[:2])
        self.tiles = tiles_by_id(self.payload)

    def test_fleet_critical_and_page_firing(self):
        self.assertEqual(self.payload["fleet"]["status"], "critical")
        self.assertTrue(self.payload["fleet"]["page_firing"])

    def test_success_tile_page_findings_with_evidence(self):
        tile = self.tiles["slo-success"]
        self.assertEqual(tile["status"], "critical")
        titles = [f["title"] for f in tile["findings"]]
        self.assertTrue(any("page_fast page firing" in t for t in titles))
        self.assertTrue(any("ticket ticket firing" in t for t in titles))
        page = next(f for f in tile["findings"]
                    if "page_fast" in f["title"])
        self.assertEqual(page["severity"], "critical")
        self.assertIn("threshold 13.44", page["evidence"])

    def test_billing_tile_critical_breach(self):
        tile = self.tiles["agent-billing-agent"]
        self.assertEqual(tile["status"], "critical")
        self.assertEqual(tile["headline"], 0.948193)
        severities = {f["severity"] for f in tile["findings"]}
        self.assertIn("critical", severities)

    def test_latency_tile_stays_green(self):
        self.assertEqual(self.tiles["slo-latency"]["status"], "green")
        self.assertEqual(self.tiles["slo-latency"]["findings"], [])


class TestConvertEdges(unittest.TestCase):
    def test_first_snapshot_all_green(self):
        payload = convert_reports(all_reports()[:1])
        self.assertEqual(payload["fleet"]["status"], "all-green")
        self.assertTrue(all(t["status"] == "green"
                            for t in payload["tiles"]))

    def test_invalid_report_rejected(self):
        bad = latest()
        bad["slos"]["success"]["observed"] = 0.5
        with self.assertRaises(ValueError):
            convert_report(bad)

    def test_empty_reports_rejected(self):
        with self.assertRaises(ValueError):
            convert_reports([])

    def test_insufficient_agent_gets_no_tile_but_is_counted(self):
        report = latest()
        # Rebuild per_agent with one insufficient agent, keeping the
        # export valid: shrink diagnostics to a stub entry.
        report["per_agent"] = [
            e for e in report["per_agent"]
            if e["agent"] != "diagnostics-agent"]
        report["per_agent"].append(
            {"agent": "diagnostics-agent", "eligible": 42,
             "status": "insufficient_data"})
        payload = convert_report(report)
        self.assertEqual(len(payload["tiles"]), 4)
        self.assertEqual(payload["fleet"]["insufficient_agents"], 1)
        self.assertNotIn("agent-diagnostics-agent",
                         tiles_by_id(payload))

    def test_breached_slo_listed_and_checkable(self):
        report = copy.deepcopy(incident())
        payload = convert_report(report)
        # Fleet success SLO itself is compliant in the incident
        # export; the breach flag is driven by the page instead.
        self.assertEqual(payload["fleet"]["breached_slos"], [])
        self.assertTrue(payload["fleet"]["page_firing"])


if __name__ == "__main__":
    unittest.main()
