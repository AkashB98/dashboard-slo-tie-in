"""Strict validator: shape, recomputed math, alert re-derivation.

PINNED CHOICE under test: unknown fields are REJECTED at every
level, and every derived number is recomputed from the raw counts -
an export that merely *claims* a budget figure fails.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import incident, latest  # noqa: E402
from validation import validate_report  # noqa: E402


def issues_for(mutate):
    report = latest()
    mutate(report)
    return validate_report(report)


class TestValidBaseline(unittest.TestCase):
    def test_seeded_latest_is_valid(self):
        self.assertEqual(validate_report(latest()), [])

    def test_seeded_incident_is_valid(self):
        self.assertEqual(validate_report(incident()), [])

    def test_non_object_report(self):
        self.assertEqual(validate_report([1, 2]),
                         ["report is not an object"])


class TestStrictShape(unittest.TestCase):
    def test_unknown_top_level_field_rejected(self):
        issues = issues_for(lambda r: r.update({"surprise": 1}))
        self.assertTrue(any("unknown field 'surprise'" in i
                            for i in issues), issues)

    def test_missing_top_level_field(self):
        def drop(r):
            del r["burn_rates"]
        issues = issues_for(drop)
        self.assertTrue(any("missing field 'burn_rates'" in i
                            for i in issues), issues)

    def test_unknown_slo_block_field_rejected(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"owner": "team-x"}))
        self.assertTrue(any("unknown field 'owner'" in i for i in issues))

    def test_unknown_budget_field_rejected(self):
        issues = issues_for(
            lambda r: r["slos"]["success"]["budget"].update({"note": "x"}))
        self.assertTrue(any("unknown field 'note'" in i for i in issues))

    def test_unknown_burn_window_rejected(self):
        issues = issues_for(
            lambda r: r["burn_rates"]["success"].update({"2h": 0.5}))
        self.assertTrue(any("burn_rates.success" in i for i in issues))

    def test_unknown_slo_name_rejected(self):
        def add(r):
            r["slos"]["uptime"] = r["slos"]["success"]
        issues = issues_for(add)
        self.assertTrue(any("slos: keys" in i for i in issues))

    def test_bad_timestamp(self):
        issues = issues_for(lambda r: r.update({"now": "Feb 1 2026"}))
        self.assertTrue(any("now:" in i for i in issues))

    def test_bool_trap_on_counts(self):
        issues = issues_for(lambda r: r.update({"total_runs": True}))
        self.assertTrue(any("total_runs" in i for i in issues))

    def test_bool_trap_on_target(self):
        issues = issues_for(lambda r: r.update({"target": True}))
        self.assertTrue(any("target" in i for i in issues))

    def test_target_out_of_range(self):
        for bad in (0.0, 1.0, 1.5, -0.1):
            issues = issues_for(lambda r, b=bad: r.update({"target": b}))
            self.assertTrue(any("target" in i for i in issues), bad)

    def test_eligible_exceeds_total(self):
        issues = issues_for(lambda r: r.update({"total_runs": 10}))
        self.assertTrue(any("eligible_runs must be <=" in i
                            for i in issues))


class TestRecomputedMath(unittest.TestCase):
    def test_observed_out_of_range(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"observed": 1.2}))
        self.assertTrue(any("observed: must be a number in 0..1" in i
                            for i in issues))

    def test_observed_mismatch_recomputed(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"observed": 0.99}))
        self.assertTrue(any("recomputed" in i and "observed" in i
                            for i in issues), issues)

    def test_good_plus_bad_must_equal_eligible(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"good": 1}))
        self.assertTrue(any("good + bad" in i for i in issues))

    def test_slo_eligible_must_match_report(self):
        # eligible 12961 with good+bad kept consistent still fails the
        # cross-check against report.eligible_runs.
        def mutate(r):
            block = r["slos"]["success"]
            block["eligible"] = 12961
            block["good"] += 1
        issues = issues_for(mutate)
        self.assertTrue(any("!= report eligible_runs" in i
                            for i in issues), issues)

    def test_budget_allowed_recomputed(self):
        issues = issues_for(
            lambda r: r["slos"]["success"]["budget"].update(
                {"allowed": 999.0}))
        self.assertTrue(any("budget.allowed" in i for i in issues))

    def test_budget_consumed_pct_recomputed(self):
        issues = issues_for(
            lambda r: r["slos"]["success"]["budget"].update(
                {"consumed_pct": 0.5}))
        self.assertTrue(any("budget.consumed_pct" in i for i in issues))

    def test_budget_consumed_must_equal_bad(self):
        issues = issues_for(
            lambda r: r["slos"]["success"]["budget"].update(
                {"consumed": 0}))
        self.assertTrue(any("budget.consumed:" in i for i in issues))

    def test_compliant_contradiction(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"compliant": False}))
        self.assertTrue(any("compliant" in i for i in issues))

    def test_compliant_must_be_bool(self):
        issues = issues_for(
            lambda r: r["slos"]["success"].update({"compliant": "yes"}))
        self.assertTrue(any("compliant: must be a bool" in i
                            for i in issues))

    def test_negative_burn_rejected(self):
        issues = issues_for(
            lambda r: r["burn_rates"]["latency"].update({"1h": -0.5}))
        self.assertTrue(any("burn_rates.latency.1h" in i for i in issues))

    def test_agent_budget_pct_recomputed_from_observed(self):
        def mutate(r):
            entry = r["per_agent"][0]
            entry["success"]["budget_consumed_pct"] = 0.1
        issues = issues_for(mutate)
        self.assertTrue(any("budget_consumed_pct" in i and "recomputed" in i
                            for i in issues), issues)


class TestAlertsRederived(unittest.TestCase):
    def test_threshold_value_must_match_derivation(self):
        issues = issues_for(
            lambda r: r["alert_thresholds"].update({"ticket": 1.0}))
        self.assertTrue(any("alert_thresholds.ticket" in i
                            for i in issues))

    def test_phantom_alert_rejected(self):
        # Latest export has no firing rules; adding one must fail both
        # the burn cross-check and the rule-set comparison.
        def mutate(r):
            r["active_alerts"] = [{
                "rule": "ticket", "severity": "ticket", "slo": "success",
                "threshold": 0.933333, "long_window": "3d",
                "long_burn": 0.383, "short_window": "6h",
                "short_burn": 0.183}]
        issues = issues_for(mutate)
        self.assertTrue(any("would not fire" in i or "does not exceed" in i
                            or "!= rules the burn table fires" in i
                            for i in issues), issues)

    def test_dropped_alert_rejected(self):
        report = incident()
        report["active_alerts"] = report["active_alerts"][:1]
        issues = validate_report(report)
        self.assertTrue(any("!= rules the burn table fires" in i
                            for i in issues), issues)

    def test_alert_on_latency_slo_rejected(self):
        report = incident()
        report["active_alerts"][0]["slo"] = "latency"
        issues = validate_report(report)
        self.assertTrue(any("success SLO only" in i for i in issues))

    def test_alert_burn_must_match_burn_table(self):
        report = incident()
        report["active_alerts"][0]["long_burn"] = 99.0
        issues = validate_report(report)
        self.assertTrue(any("long_burn" in i for i in issues))

    def test_alert_unknown_field_rejected(self):
        report = incident()
        report["active_alerts"][0]["channel"] = "pager"
        issues = validate_report(report)
        self.assertTrue(any("unknown field 'channel'" in i
                            for i in issues))


class TestExhaustionAndAgents(unittest.TestCase):
    def test_exhaustion_days_must_be_hours_over_24(self):
        def mutate(r):
            r["projected_exhaustion"]["latency"] = {"hours": 48.0,
                                                    "days": 9.0}
        issues = issues_for(mutate)
        self.assertTrue(any("days" in i for i in issues))

    def test_exhausted_budget_cannot_claim_null_projection(self):
        def mutate(r):
            block = r["slos"]["success"]
            # Force a breach the validator can recompute consistently:
            block["good"] = 0
            block["bad"] = block["eligible"]
            block["observed"] = 0.0
            block["compliant"] = False
            budget = block["budget"]
            budget["consumed"] = block["bad"]
            budget["consumed_pct"] = 20.0
            budget["remaining"] = round(648.0 - 12960, 4)
            budget["remaining_pct"] = round(
                (648.0 - 12960) / 648.0, 6)
            r["projected_exhaustion"]["success"] = None
        issues = issues_for(mutate)
        self.assertTrue(any("must project 0" in i for i in issues),
                        issues)

    def test_duplicate_agent_rejected(self):
        def mutate(r):
            r["per_agent"].append(dict(r["per_agent"][0]))
        issues = issues_for(mutate)
        self.assertTrue(any("duplicate agent" in i for i in issues))

    def test_unknown_agent_rejected(self):
        def mutate(r):
            r["per_agent"][0]["agent"] = "rogue-agent"
        issues = issues_for(mutate)
        self.assertTrue(any("must be one of" in i for i in issues))

    def test_ok_agent_below_volume_guard_rejected(self):
        def mutate(r):
            r["per_agent"][0]["eligible"] = 120
        issues = issues_for(mutate)
        self.assertTrue(any("volume guard" in i for i in issues))

    def test_insufficient_agent_with_slo_blocks_rejected(self):
        def mutate(r):
            r["per_agent"][0]["status"] = "insufficient_data"
        issues = issues_for(mutate)
        self.assertTrue(any("unknown field" in i for i in issues),
                        issues)


class TestSchemaVersion(unittest.TestCase):
    def test_seeded_exports_carry_supported_version(self):
        report = latest()
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(validate_report(report), [])

    def test_unknown_version_rejected_loudly(self):
        issues = issues_for(
            lambda r: r.update({"schema_version": "2.0"}))
        self.assertTrue(any("unsupported version" in i and "'2.0'" in i
                            for i in issues), issues)

    def test_non_string_version_rejected(self):
        issues = issues_for(lambda r: r.update({"schema_version": 1}))
        self.assertTrue(any("unsupported version" in i for i in issues))

    def test_missing_version_accepted_as_native_tracker_export(self):
        # Native agent-slo-tracker exports are unversioned; reading
        # them is the bridge's whole job, so absence is not an error
        # (an unknown PRESENT version is - see above).
        def drop(r):
            del r["schema_version"]
        self.assertEqual(issues_for(drop), [])


class TestBudgetRemainingLoud(unittest.TestCase):
    def test_remaining_mismatch_prints_both_values_and_tolerance(self):
        issues = issues_for(
            lambda r: r["slos"]["success"]["budget"].update(
                {"remaining": 999.0}))
        remaining = [i for i in issues if "budget.remaining" in i]
        self.assertEqual(len(remaining), 1, issues)
        self.assertIn("export says 999.0", remaining[0])
        self.assertIn("recomputed 77.0", remaining[0])
        self.assertIn("tolerance", remaining[0])
        self.assertIn("target=0.95", remaining[0])


if __name__ == "__main__":
    unittest.main()
