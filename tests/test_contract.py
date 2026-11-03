"""Contract tests: the fixture exports follow the REAL
agent-slo-tracker report shape.

The expected shape below was taken from that repo's actual output -
``report.build_report(generate_runs())``, plus an alert object from
``alerts.evaluate_alerts`` at the fast-burn boundary - not invented
for this repo. The ONLY deliberate deviation is the top-level
``schema_version`` field this repo's exports add (versioned so the
validator can reject shapes it does not understand; native tracker
exports are unversioned and still validate).

When the sibling repo is present on disk next to this one, the
final test re-derives the shape from it live (in a subprocess, so
the sibling's same-named modules cannot collide with ours) and
compares recursively - keys and scalar types.
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixtures import SCHEMA_VERSION, generate_reports  # noqa: E402
from validation import validate_report  # noqa: E402

# --- the real agent-slo-tracker report shape (from report.py) ------
REAL_TOP_KEYS = {"now", "window_days", "total_runs", "eligible_runs",
                 "target", "slos", "burn_rates", "alert_thresholds",
                 "active_alerts", "projected_exhaustion", "per_agent"}
REAL_SLO_KEYS = {"target", "eligible", "good", "bad", "observed",
                 "compliant", "budget"}
REAL_BUDGET_KEYS = {"allowed", "consumed", "consumed_pct", "remaining",
                    "remaining_pct"}
REAL_BURN_WINDOWS = {"5m", "30m", "1h", "6h", "3d", "28d"}
REAL_THRESHOLD_KEYS = {"page_fast", "page_slow", "ticket"}
REAL_AGENT_KEYS = {"agent", "eligible", "status", "success", "latency"}
REAL_AGENT_SLO_KEYS = {"observed", "compliant", "budget_consumed_pct"}
REAL_ALERT_KEYS = {"rule", "severity", "slo", "threshold",
                   "long_window", "long_burn", "short_window",
                   "short_burn"}
REAL_SLO_TYPES = {"target": float, "eligible": int, "good": int,
                  "bad": int, "observed": float, "compliant": bool}


class TestContractPinned(unittest.TestCase):
    def setUp(self):
        self.reports = generate_reports()

    def test_top_level_keys_match_real_shape_plus_version(self):
        for report in self.reports:
            self.assertEqual(set(report) - {"schema_version"},
                             REAL_TOP_KEYS)
            self.assertEqual(report["schema_version"], SCHEMA_VERSION)

    def test_slo_and_budget_keys_match_real_shape(self):
        for report in self.reports:
            for slo in ("success", "latency"):
                block = report["slos"][slo]
                self.assertEqual(set(block), REAL_SLO_KEYS)
                self.assertEqual(set(block["budget"]), REAL_BUDGET_KEYS)
                for field, typ in REAL_SLO_TYPES.items():
                    self.assertIs(type(block[field]), typ,
                                  f"{slo}.{field}")

    def test_burn_and_threshold_keys_match_real_shape(self):
        for report in self.reports:
            self.assertEqual(set(report["burn_rates"]),
                             {"success", "latency"})
            for table in report["burn_rates"].values():
                self.assertEqual(set(table), REAL_BURN_WINDOWS)
                self.assertTrue(all(type(v) is float
                                    for v in table.values()))
            self.assertEqual(set(report["alert_thresholds"]),
                             REAL_THRESHOLD_KEYS)

    def test_per_agent_keys_match_real_shape(self):
        for report in self.reports:
            for entry in report["per_agent"]:
                self.assertEqual(set(entry), REAL_AGENT_KEYS)
                for slo in ("success", "latency"):
                    self.assertEqual(set(entry[slo]),
                                     REAL_AGENT_SLO_KEYS)

    def test_alert_keys_match_real_shape(self):
        incident = self.reports[1]
        self.assertTrue(incident["active_alerts"])
        for alert in incident["active_alerts"]:
            self.assertEqual(set(alert), REAL_ALERT_KEYS)
            self.assertIs(type(alert["threshold"]), float)
            self.assertIs(type(alert["long_burn"]), float)

    def test_exhaustion_values_are_none_or_real_shape(self):
        for report in self.reports:
            self.assertEqual(set(report["projected_exhaustion"]),
                             {"success", "latency"})
            for value in report["projected_exhaustion"].values():
                self.assertTrue(
                    value is None
                    or (set(value) == {"hours", "days"}
                        and type(value["hours"]) is float
                        and type(value["days"]) is float))


_SIBLING = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "..", "agent-slo-tracker")

_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from generator import generate_runs, parse_ts
from report import build_report
from slo import WindowIndex
from alerts import evaluate_alerts
runs = generate_runs()
rep = build_report(runs)
idx = WindowIndex(runs)
alerts = evaluate_alerts(idx, parse_ts("2026-01-29T12:00:00Z"))
print(json.dumps({"report": rep, "alert": alerts[0] if alerts else None}))
"""


def _shapes_match(mine, real, path=""):
    """Recursive key/type comparison; None on either side is a
    wildcard (projected_exhaustion is legitimately None or an
    object in both codebases), empty lists skip element checks."""
    if mine is None or real is None:
        return ""
    if isinstance(mine, dict) and isinstance(real, dict):
        if set(mine) != set(real):
            return f"{path}: keys {sorted(mine)} != {sorted(real)}"
        for key in mine:
            diff = _shapes_match(mine[key], real[key], f"{path}.{key}")
            if diff:
                return diff
        return ""
    if isinstance(mine, list) and isinstance(real, list):
        if mine and real:
            return _shapes_match(mine[0], real[0], f"{path}[0]")
        return ""
    if type(mine) is not type(real):
        return f"{path}: type {type(mine).__name__} != " \
               f"{type(real).__name__}"
    return ""


@unittest.skipUnless(os.path.isdir(_SIBLING),
                     "sibling agent-slo-tracker repo not on disk")
class TestContractLiveSibling(unittest.TestCase):
    def test_fixture_shape_matches_live_sibling_output(self):
        out = subprocess.run(
            [sys.executable, "-c", _PROBE, os.path.abspath(_SIBLING)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        probe = json.loads(out.stdout)
        real = probe["report"]
        latest = generate_reports()[-1]
        mine = {k: v for k, v in latest.items()
                if k != "schema_version"}
        self.assertEqual(_shapes_match(mine, real), "")
        incident_alert = generate_reports()[1]["active_alerts"][0]
        self.assertEqual(
            _shapes_match(incident_alert, probe["alert"]), "")

    def test_real_sibling_export_validates_unversioned(self):
        # The bridge's whole job, proven end to end: a genuine
        # agent-slo-tracker export - its own seeded numbers, no
        # schema_version - passes this repo's strict validator with
        # zero issues (shape, recomputed budgets, re-derived alerts
        # and all).
        out = subprocess.run(
            [sys.executable, "-c", _PROBE, os.path.abspath(_SIBLING)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        real = json.loads(out.stdout)["report"]
        self.assertNotIn("schema_version", real)
        self.assertEqual(validate_report(real), [])


if __name__ == "__main__":
    unittest.main()
