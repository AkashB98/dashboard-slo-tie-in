"""Strict validator for agent-slo-tracker report exports.

PINNED CHOICE - unknown fields are REJECTED, not flagged-and-kept:
an export carrying any field outside the report shape fails
validation, at every level (top, SLO block, budget block, burn
table, alert, exhaustion, per-agent). A bridge that silently passes
through fields it does not understand will eventually render a
number nobody can explain; strictness here is the product.

SCHEMA VERSION: exports produced by this repo's generator carry a
top-level ``schema_version``. Native agent-slo-tracker exports
carry none - they are accepted as unversioned (reading them is the
bridge's whole job). When the field IS present it must be a string
equal to the supported version; any other version is rejected
loudly, by value, so a future shape change fails here instead of
converting silently into wrong tiles.

Beyond shape, the validator RECOMPUTES everything it can from the
raw counts, independently of how the export was built:

  * observed SLI  == round(good / eligible, 6)
  * budget allowed / consumed % / remaining, recomputed from the
    raw counts (good, bad, eligible) and target via (1 - target) -
    on mismatch the issue prints BOTH the exported value and the
    recomputed value, with the tolerance used (half a unit of the
    field's last rounded decimal, plus float slack)
  * compliant     == (observed >= target)
  * per-agent budget_consumed_pct == (1 - observed) / (1 - target)
  * alert thresholds == derived from their budget fractions
  * active_alerts == exactly the rules the success burn table fires
    under the two-window rule (both windows over threshold)
  * projected exhaustion days == hours / 24; an exhausted budget
    must project 0

Ranges: every ratio that is a fraction of runs (observed SLIs,
remaining_pct) must lie in 0..1; consumed percentages and burn
rates are >= 0 and may exceed 1 (that is what a breach looks like).
Bools are checked as real bools before ints - in Python ``True`` is
an ``int``, and a JSON ``true`` in a count field is a data bug, not
a 1. Reports issues, never raises on bad data, so the CLI can map
data problems to exit code 2 - distinct from a breach (exit 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from fixtures import (AGENTS, BURN_WINDOWS, RULES, SCHEMA_VERSION,
                      SLO_NAMES, TARGET)

TOP_FIELDS = {"schema_version", "now", "window_days", "total_runs",
              "eligible_runs", "target", "slos", "burn_rates",
              "alert_thresholds", "active_alerts",
              "projected_exhaustion", "per_agent"}
# The one top-level field a native tracker export legitimately
# lacks; every other top-level field is required.
OPTIONAL_TOP_FIELDS = {"schema_version"}
SUPPORTED_SCHEMA_VERSIONS = {SCHEMA_VERSION}
SLO_FIELDS = {"target", "eligible", "good", "bad", "observed",
              "compliant", "budget"}
BUDGET_FIELDS = {"allowed", "consumed", "consumed_pct", "remaining",
                 "remaining_pct"}
ALERT_FIELDS = {"rule", "severity", "slo", "threshold", "long_window",
                "long_burn", "short_window", "short_burn"}
EXHAUSTION_FIELDS = {"hours", "days"}
AGENT_OK_FIELDS = {"agent", "eligible", "status", "success", "latency"}
AGENT_SLO_FIELDS = {"observed", "compliant", "budget_consumed_pct"}
AGENT_INSUFFICIENT_FIELDS = {"agent", "eligible", "status"}
MIN_AGENT_RUNS = 500

# Comparison tolerances: half a unit of the last rounded decimal the
# source format carries, plus float slack.
TOL_4DP = 6e-5
TOL_6DP = 2e-6
TOL_AGENT_PCT = 3e-5  # agent pct is recomputed from a 6dp observed


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and x == x and x not in (float("inf"), float("-inf")))


def _fields(obj, expected, where, issues):
    if not isinstance(obj, dict):
        issues.append(f"{where}: must be an object")
        return False
    keys = set(obj)
    for field in sorted(expected - keys):
        issues.append(f"{where}: missing field {field!r}")
    for field in sorted(keys - expected):
        issues.append(f"{where}: unknown field {field!r} (rejected)")
    return keys == expected


def _parse_ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def _derived_thresholds() -> Dict[str, float]:
    # Independent re-derivation: fraction / (window / 28 days).
    out = {}
    for name, _sev, _lw, _sw, fraction, hours in RULES:
        out[name] = round(fraction / (hours / (28 * 24.0)), 6)
    return out


def _check_slo_block(name, block, report_target, report_eligible, issues):
    where = f"slos.{name}"
    if not _fields(block, SLO_FIELDS, where, issues):
        return
    target = block["target"]
    if not _is_num(target) or not 0.0 < target < 1.0:
        issues.append(f"{where}.target: must be a number in (0, 1)")
        return
    if abs(target - report_target) > TOL_6DP:
        issues.append(f"{where}.target: {target} != report target "
                      f"{report_target}")
    eligible, good, bad = block["eligible"], block["good"], block["bad"]
    for field, value in (("eligible", eligible), ("good", good),
                         ("bad", bad)):
        if not _is_int(value) or value < 0:
            issues.append(f"{where}.{field}: must be a non-negative int")
    if not all(_is_int(v) for v in (eligible, good, bad)):
        return
    if good + bad != eligible:
        issues.append(f"{where}: good + bad ({good}+{bad}) != eligible "
                      f"({eligible})")
    if eligible != report_eligible:
        issues.append(f"{where}.eligible: {eligible} != report "
                      f"eligible_runs ({report_eligible})")
    observed = block["observed"]
    if not _is_num(observed) or not 0.0 <= observed <= 1.0:
        issues.append(f"{where}.observed: must be a number in 0..1")
    else:
        expected = round(good / eligible, 6) if eligible else 0.0
        if abs(observed - expected) > TOL_6DP:
            issues.append(f"{where}.observed: {observed} != recomputed "
                          f"{expected} (good/eligible)")
    compliant = block["compliant"]
    if not isinstance(compliant, bool):
        issues.append(f"{where}.compliant: must be a bool")
    elif _is_num(observed) and compliant != (observed >= target):
        issues.append(f"{where}.compliant: {compliant} contradicts "
                      f"observed >= target")
    budget = block["budget"]
    if not _fields(budget, BUDGET_FIELDS, f"{where}.budget", issues):
        return
    allowed_raw = (1.0 - target) * eligible
    expected = {
        "allowed": round(allowed_raw, 4),
        "consumed_pct": round(bad / allowed_raw, 6) if allowed_raw else 0.0,
        "remaining": round(allowed_raw - bad, 4),
        "remaining_pct": (round((allowed_raw - bad) / allowed_raw, 6)
                          if allowed_raw else 0.0),
    }
    if budget["consumed"] != bad:
        issues.append(f"{where}.budget.consumed: {budget['consumed']} "
                      f"!= bad ({bad})")
    # Budget-remaining gets its own explicit recomputation from the
    # raw counts + target, failing loudly with BOTH values: it is
    # the number the check gate and the panel headline rest on.
    remaining_raw = (1.0 - target) * eligible - bad
    expected_remaining = round(remaining_raw, 4)
    value = budget["remaining"]
    if not _is_num(value):
        issues.append(f"{where}.budget.remaining: must be a number")
    elif abs(value - expected_remaining) > TOL_4DP:
        issues.append(
            f"{where}.budget.remaining: export says {value}, "
            f"recomputed {expected_remaining} from raw counts "
            f"(eligible={eligible}, bad={bad}, target={target}: "
            f"(1-target)*eligible - bad), tolerance {TOL_4DP}")
    for field, tol in (("allowed", TOL_4DP),
                       ("consumed_pct", TOL_6DP),
                       ("remaining_pct", TOL_6DP)):
        value = budget[field]
        if not _is_num(value):
            issues.append(f"{where}.budget.{field}: must be a number")
        elif abs(value - expected[field]) > tol:
            issues.append(f"{where}.budget.{field}: export says "
                          f"{value}, recomputed {expected[field]} "
                          f"(tolerance {tol})")
    if _is_num(budget.get("consumed_pct")) and budget["consumed_pct"] < 0:
        issues.append(f"{where}.budget.consumed_pct: must be >= 0")
    # remaining_pct is a ratio and sits in 0..1 while the budget is
    # unspent; on a breach it goes negative - either way its value is
    # pinned by the recomputation check above, so no range rule here.


def _check_burns(report, issues):
    burns = report["burn_rates"]
    if not isinstance(burns, dict):
        issues.append("burn_rates: must be an object")
        return
    if set(burns) != set(SLO_NAMES):
        issues.append(f"burn_rates: keys must be exactly {list(SLO_NAMES)}")
        return
    for slo in SLO_NAMES:
        table = burns[slo]
        if not isinstance(table, dict) or set(table) != set(BURN_WINDOWS):
            issues.append(f"burn_rates.{slo}: windows must be exactly "
                          f"{list(BURN_WINDOWS)}")
            continue
        for window, value in table.items():
            if not _is_num(value) or value < 0:
                issues.append(f"burn_rates.{slo}.{window}: must be a "
                              f"number >= 0")


def _check_alerts(report, issues):
    thresholds = report["alert_thresholds"]
    derived = _derived_thresholds()
    if not isinstance(thresholds, dict) or set(thresholds) != set(derived):
        issues.append("alert_thresholds: keys must be exactly "
                      f"{sorted(derived)}")
    else:
        for rule, expected in derived.items():
            value = thresholds[rule]
            if not _is_num(value) or abs(value - expected) > TOL_6DP:
                issues.append(f"alert_thresholds.{rule}: {value} != "
                              f"derived {expected}")
    alerts = report["active_alerts"]
    if not isinstance(alerts, list):
        issues.append("active_alerts: must be a list")
        return
    burns = report.get("burn_rates", {}).get("success", {})
    expected_rules = []
    if isinstance(burns, dict):
        for name, _sev, long_w, short_w, _frac, _hrs in RULES:
            thr = derived[name]
            if (burns.get(long_w, 0) > thr
                    and burns.get(short_w, 0) > thr):
                expected_rules.append(name)
    seen = []
    for i, alert in enumerate(alerts):
        where = f"active_alerts[{i}]"
        if not _fields(alert, ALERT_FIELDS, where, issues):
            continue
        seen.append(alert["rule"])
        rule_def = next((r for r in RULES if r[0] == alert["rule"]), None)
        if rule_def is None:
            issues.append(f"{where}.rule: unknown rule {alert['rule']!r}")
            continue
        _name, severity, long_w, short_w, _frac, _hrs = rule_def
        if alert["severity"] != severity:
            issues.append(f"{where}.severity: must be {severity!r} for "
                          f"rule {alert['rule']}")
        if alert["slo"] != "success":
            issues.append(f"{where}.slo: alerts are evaluated on the "
                          f"success SLO only")
        if (alert["long_window"], alert["short_window"]) != (long_w, short_w):
            issues.append(f"{where}: windows must be {long_w}/{short_w} "
                          f"for rule {alert['rule']}")
        thr = derived[alert["rule"]]
        if not _is_num(alert["threshold"]) \
                or abs(alert["threshold"] - thr) > TOL_6DP:
            issues.append(f"{where}.threshold: != derived {thr}")
        for field, window in (("long_burn", long_w),
                              ("short_burn", short_w)):
            value = alert[field]
            if not _is_num(value):
                issues.append(f"{where}.{field}: must be a number")
            elif isinstance(burns, dict) and window in burns \
                    and abs(value - burns[window]) > TOL_6DP:
                issues.append(f"{where}.{field}: {value} != burn_rates."
                              f"success.{window} ({burns[window]})")
            if _is_num(value) and value <= thr:
                issues.append(f"{where}.{field}: {value} does not exceed "
                              f"threshold {thr} - alert would not fire")
    if sorted(seen) != sorted(expected_rules):
        issues.append(f"active_alerts: rules {sorted(seen)} != rules the "
                      f"burn table fires {sorted(expected_rules)}")


def _check_exhaustion(report, issues):
    proj = report["projected_exhaustion"]
    if not isinstance(proj, dict) or set(proj) != set(SLO_NAMES):
        issues.append("projected_exhaustion: keys must be exactly "
                      f"{list(SLO_NAMES)}")
        return
    for slo in SLO_NAMES:
        value = proj[slo]
        where = f"projected_exhaustion.{slo}"
        if value is None:
            block = report.get("slos", {}).get(slo, {})
            remaining = block.get("budget", {}).get("remaining")
            if _is_num(remaining) and remaining <= 0:
                issues.append(f"{where}: exhausted budget must project "
                              f"0, not null")
            continue
        if not _fields(value, EXHAUSTION_FIELDS, where, issues):
            continue
        hours, days = value["hours"], value["days"]
        if not _is_num(hours) or not _is_num(days) or hours < 0 or days < 0:
            issues.append(f"{where}: hours/days must be numbers >= 0")
        elif abs(days - round(hours / 24.0, 4)) > TOL_4DP:
            issues.append(f"{where}.days: {days} != hours/24 "
                          f"({round(hours / 24.0, 4)})")
        block = report.get("slos", {}).get(slo, {})
        remaining = block.get("budget", {}).get("remaining")
        if _is_num(remaining) and remaining <= 0 and hours != 0.0:
            issues.append(f"{where}: exhausted budget must project 0 "
                          f"hours")


def _check_per_agent(report, issues):
    agents = report["per_agent"]
    if not isinstance(agents, list) or not agents:
        issues.append("per_agent: must be a non-empty list")
        return
    target = report.get("target", TARGET)
    if not _is_num(target) or not 0.0 < target < 1.0:
        # The invalid target is already reported at top level; fall
        # back so the recomputes below can never divide by zero -
        # this validator reports issues, it never raises on bad data.
        target = TARGET
    seen = set()
    for i, entry in enumerate(agents):
        where = f"per_agent[{i}]"
        if not isinstance(entry, dict):
            issues.append(f"{where}: must be an object")
            continue
        agent = entry.get("agent")
        if agent not in AGENTS:
            issues.append(f"{where}.agent: must be one of {AGENTS}")
        if agent in seen:
            issues.append(f"{where}.agent: duplicate agent {agent!r}")
        seen.add(agent)
        eligible = entry.get("eligible")
        if not _is_int(eligible) or eligible < 0:
            issues.append(f"{where}.eligible: must be a non-negative int")
        status = entry.get("status")
        if status == "insufficient_data":
            _fields(entry, AGENT_INSUFFICIENT_FIELDS, where, issues)
            if _is_int(eligible) and eligible >= MIN_AGENT_RUNS:
                issues.append(f"{where}: insufficient_data contradicts "
                              f"eligible >= {MIN_AGENT_RUNS}")
        elif status == "ok":
            if not _fields(entry, AGENT_OK_FIELDS, where, issues):
                continue
            if _is_int(eligible) and eligible < MIN_AGENT_RUNS:
                issues.append(f"{where}: status ok below the "
                              f"{MIN_AGENT_RUNS}-run volume guard")
            for slo in SLO_NAMES:
                sub = entry[slo]
                swhere = f"{where}.{slo}"
                if not _fields(sub, AGENT_SLO_FIELDS, swhere, issues):
                    continue
                observed = sub["observed"]
                if not _is_num(observed) or not 0.0 <= observed <= 1.0:
                    issues.append(f"{swhere}.observed: must be in 0..1")
                    continue
                if not isinstance(sub["compliant"], bool):
                    issues.append(f"{swhere}.compliant: must be a bool")
                elif sub["compliant"] != (observed >= target):
                    issues.append(f"{swhere}.compliant: contradicts "
                                  f"observed >= target")
                pct = sub["budget_consumed_pct"]
                expected = (1.0 - observed) / (1.0 - target)
                if not _is_num(pct) or pct < 0:
                    issues.append(f"{swhere}.budget_consumed_pct: must "
                                  f"be a number >= 0")
                elif abs(pct - expected) > TOL_AGENT_PCT:
                    issues.append(f"{swhere}.budget_consumed_pct: {pct} "
                                  f"!= recomputed {round(expected, 6)} "
                                  f"from observed")
        else:
            issues.append(f"{where}.status: must be 'ok' or "
                          f"'insufficient_data'")


def validate_report(report) -> List[str]:
    """All validation issues for one export ([] means valid)."""
    issues: List[str] = []
    if not isinstance(report, dict):
        return ["report is not an object"]
    # Top-level shape: every field except schema_version is required;
    # anything outside the allowed set is rejected (pinned choice).
    keys = set(report)
    for field in sorted((TOP_FIELDS - OPTIONAL_TOP_FIELDS) - keys):
        issues.append(f"report: missing field {field!r}")
    for field in sorted(keys - TOP_FIELDS):
        issues.append(f"report: unknown field {field!r} (rejected)")
    if "schema_version" in report:
        version = report["schema_version"]
        if not isinstance(version, str) \
                or version not in SUPPORTED_SCHEMA_VERSIONS:
            issues.append(
                f"schema_version: unsupported version {version!r} "
                f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}); "
                f"refusing to convert an export shape this validator "
                f"does not understand")
    now = report.get("now")
    if not isinstance(now, str):
        issues.append("now: must be an ISO Z timestamp string")
    else:
        try:
            _parse_ts(now)
        except (ValueError, TypeError):
            issues.append("now: must be ISO Z (YYYY-MM-DDTHH:MM:SSZ)")
    if not _is_int(report.get("window_days")) \
            or report.get("window_days", 0) <= 0:
        issues.append("window_days: must be a positive int")
    for field in ("total_runs", "eligible_runs"):
        if not _is_int(report.get(field)) or report.get(field, -1) < 0:
            issues.append(f"{field}: must be a non-negative int")
    if _is_int(report.get("total_runs")) \
            and _is_int(report.get("eligible_runs")) \
            and report["eligible_runs"] > report["total_runs"]:
        issues.append("eligible_runs must be <= total_runs")
    target = report.get("target")
    if not _is_num(target) or not 0.0 < target < 1.0:
        issues.append("target: must be a number in (0, 1)")
        target = TARGET
    slos = report.get("slos")
    if not isinstance(slos, dict) or set(slos) != set(SLO_NAMES):
        issues.append(f"slos: keys must be exactly {list(SLO_NAMES)}")
    else:
        for name in SLO_NAMES:
            _check_slo_block(name, slos[name], target,
                             report.get("eligible_runs"), issues)
    if isinstance(report.get("burn_rates"), dict):
        _check_burns(report, issues)
    else:
        issues.append("burn_rates: must be an object")
    if "alert_thresholds" in report and "active_alerts" in report:
        _check_alerts(report, issues)
    if isinstance(report.get("projected_exhaustion"), dict):
        _check_exhaustion(report, issues)
    else:
        issues.append("projected_exhaustion: must be an object")
    if "per_agent" in report:
        _check_per_agent(report, issues)
    return issues


def validate_reports(reports) -> List[str]:
    if not isinstance(reports, list) or not reports:
        return ["reports must be a non-empty list"]
    issues: List[str] = []
    for i, report in enumerate(reports):
        for issue in validate_report(report):
            issues.append(f"report[{i}]: {issue}")
    return issues
