"""Fixture generator: determinism, internal consistency, story arc."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixtures import AGENTS, SLO_NAMES, generate_report, generate_reports  # noqa: E402
from validation import validate_reports  # noqa: E402


class TestFixtures(unittest.TestCase):
    def setUp(self):
        self.reports = generate_reports()

    def test_three_snapshots_chronological(self):
        self.assertEqual(len(self.reports), 3)
        nows = [r["now"] for r in self.reports]
        self.assertEqual(nows, sorted(nows))
        self.assertEqual(nows[-1], "2026-02-01T23:58:00Z")

    def test_deterministic(self):
        self.assertEqual(generate_reports(), self.reports)
        self.assertEqual(generate_report(), self.reports[-1])

    def test_all_snapshots_validate_clean(self):
        self.assertEqual(validate_reports(self.reports), [])

    def test_agents_are_the_three_helios_agents(self):
        for report in self.reports:
            self.assertEqual(
                sorted(a["agent"] for a in report["per_agent"]),
                sorted(AGENTS))

    def test_per_agent_eligible_sums_to_report_eligible(self):
        for report in self.reports:
            self.assertEqual(
                sum(a["eligible"] for a in report["per_agent"]),
                report["eligible_runs"])

    def test_slo_counts_internally_consistent(self):
        for report in self.reports:
            for slo in SLO_NAMES:
                block = report["slos"][slo]
                self.assertEqual(block["good"] + block["bad"],
                                 block["eligible"])
                self.assertEqual(block["eligible"],
                                 report["eligible_runs"])
                self.assertEqual(block["budget"]["consumed"],
                                 block["bad"])

    def test_latest_headline_numbers(self):
        latest = self.reports[-1]
        self.assertEqual(latest["eligible_runs"], 12960)
        self.assertEqual(latest["slos"]["success"]["observed"], 0.955941)
        self.assertEqual(latest["slos"]["latency"]["observed"], 0.973997)
        self.assertEqual(
            latest["slos"]["success"]["budget"]["consumed_pct"], 0.881173)
        self.assertEqual(
            latest["slos"]["latency"]["budget"]["consumed_pct"], 0.520062)

    def test_incident_snapshot_alerts(self):
        incident = self.reports[1]
        self.assertEqual([a["rule"] for a in incident["active_alerts"]],
                         ["page_fast", "page_slow", "ticket"])
        self.assertEqual(self.reports[0]["active_alerts"], [])
        self.assertEqual(self.reports[-1]["active_alerts"], [])

    def test_incident_billing_agent_breached(self):
        billing = next(a for a in self.reports[1]["per_agent"]
                       if a["agent"] == "billing-agent")
        self.assertFalse(billing["success"]["compliant"])
        self.assertGreater(billing["success"]["budget_consumed_pct"], 1.0)

    def test_latest_exhaustion_shape(self):
        latest = self.reports[-1]
        self.assertIsNone(latest["projected_exhaustion"]["success"])
        self.assertEqual(latest["projected_exhaustion"]["latency"],
                         {"hours": 155.5, "days": 6.4792})

    def test_thresholds_are_derived_values(self):
        for report in self.reports:
            self.assertEqual(report["alert_thresholds"],
                             {"page_fast": 13.44, "page_slow": 5.6,
                              "ticket": 0.933333})

    def test_other_seed_still_valid_and_differs(self):
        other = generate_reports(seed=7)
        self.assertEqual(validate_reports(other), [])
        self.assertNotEqual(other, self.reports)


if __name__ == "__main__":
    unittest.main()
