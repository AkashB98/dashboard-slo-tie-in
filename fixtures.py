"""Seeded fixture generator: SLO status exports in the exact report
shape of ``agent-slo-tracker``, for the fictional Helios Home agents.

SYNTHETIC DATA ONLY. No real company, customer, or agent data is ever
used. The generator emits three snapshot exports (a few windows onto
the same rolling 28-day SLO story):

  snap 0  2026-01-12  healthy: budgets ~61% / ~44% spent, no alerts
  snap 1  2026-01-29  fast burn: both page rules + the ticket rule
                      fire on the success SLO, billing-agent breaches
                      its own success budget, fleet reads critical
  snap 2  2026-02-01  quiet again ("now"): no alerts firing, success
                      budget 88.1% spent - compliant but tight

The export shape is NOT invented here: it is the exact output
shape of agent-slo-tracker's ``report.build_report`` (verified
against that repo's real output by ``tests/test_contract.py``),
plus ONE deliberate addition - a top-level ``schema_version``
field, so future shape changes are versioned and the validator can
reject versions it does not understand. Native tracker exports
carry no ``schema_version``; the validator accepts them as
unversioned and rejects any version it does not know.

Every derived number (observed SLI, error budget, projected
exhaustion, per-agent blocks, active alerts) is computed here from
raw good/bad counts and the burn table, using the same formulas as
agent-slo-tracker - and ``validation.py`` recomputes all of it
independently, so a fixture that drifts from the math fails loudly.

Determinism: the scenario tables are the seed-42 fixture set. A
different seed deterministically perturbs the per-agent bad counts
by a few runs (totals, budgets and alerts are then recomputed from
those counts), so any seed still yields a valid, self-consistent
export - only seed 42 is pinned by the evals.
"""

from __future__ import annotations

import random
from typing import Dict, List

DEFAULT_SEED = 42
SCHEMA_VERSION = "1.0"
TARGET = 0.95
WINDOW_DAYS = 28
AGENTS = ["support-agent", "billing-agent", "diagnostics-agent"]
SLO_NAMES = ("success", "latency")
BURN_WINDOWS = ("5m", "30m", "1h", "6h", "3d", "28d")

# Alert rules, as in agent-slo-tracker: a rule fires only when BOTH
# its windows exceed the same threshold, and the threshold is derived
# from a budget fraction - never hard-coded in the exports.
# (rule, severity, long window, short window, budget fraction,
#  long-window hours)
RULES = [
    ("page_fast", "page", "1h", "5m", 0.02, 1.0),
    ("page_slow", "page", "6h", "30m", 0.05, 6.0),
    ("ticket", "ticket", "3d", "6h", 0.10, 72.0),
]


def alert_threshold(budget_fraction: float, window_hours: float,
                    window_days: int = WINDOW_DAYS) -> float:
    """Threshold derivation, mirroring agent-slo-tracker: to consume
    ``budget_fraction`` of the whole budget inside one window, the bad
    rate there must run at fraction / (window / total window) times
    the budgeted rate."""
    return budget_fraction / (window_hours / (window_days * 24.0))


def rule_thresholds() -> Dict[str, float]:
    return {name: round(alert_threshold(frac, hours), 6)
            for name, _sev, _lw, _sw, frac, hours in RULES}


def _slo_block(eligible: int, good: int, target: float = TARGET) -> Dict:
    """Budget math identical to agent-slo-tracker's slo_block."""
    bad = eligible - good
    allowed = (1.0 - target) * eligible
    observed = (good / eligible) if eligible else 0.0
    consumed_pct = (bad / allowed) if allowed else 0.0
    remaining = allowed - bad
    return {
        "target": target,
        "eligible": eligible,
        "good": good,
        "bad": bad,
        "observed": round(observed, 6),
        "compliant": observed >= target,
        "budget": {
            "allowed": round(allowed, 4),
            "consumed": bad,
            "consumed_pct": round(consumed_pct, 6),
            "remaining": round(remaining, 4),
            "remaining_pct": round(remaining / allowed, 6) if allowed else 0.0,
        },
    }


def _exhaustion(remaining: float, bad_1h: int):
    """Projected exhaustion, agent-slo-tracker semantics: an already
    exhausted budget reports 0; zero bad runs in the trailing hour
    means no projection is honest (None)."""
    if remaining <= 0:
        return {"hours": 0.0, "days": 0.0}
    if bad_1h == 0:
        return None
    hours = remaining / bad_1h
    return {"hours": round(hours, 4), "days": round(hours / 24.0, 4)}


def _derive_alerts(burns_success: Dict[str, float]) -> List[Dict]:
    thresholds = rule_thresholds()
    active = []
    for name, severity, long_w, short_w, _frac, _hrs in RULES:
        thr = thresholds[name]
        if burns_success[long_w] > thr and burns_success[short_w] > thr:
            active.append({"rule": name, "severity": severity,
                           "slo": "success", "threshold": thr,
                           "long_window": long_w,
                           "long_burn": burns_success[long_w],
                           "short_window": short_w,
                           "short_burn": burns_success[short_w]})
    return active


# Scenario tables: (now, total_runs, {agent: eligible},
# {agent: success bad}, {agent: latency bad},
# {slo: burn table}, {slo: bad runs in trailing 1h})
_SCENARIOS = [
    {
        "now": "2026-01-12T23:59:00Z",
        "total_runs": 6680,
        "eligible": {"support-agent": 2280, "billing-agent": 1960,
                     "diagnostics-agent": 2272},
        "success_bad": {"support-agent": 62, "billing-agent": 68,
                        "diagnostics-agent": 70},
        "latency_bad": {"support-agent": 48, "billing-agent": 44,
                        "diagnostics-agent": 50},
        "burns": {
            "success": {"5m": 0.0, "30m": 0.32, "1h": 0.48, "6h": 0.61,
                        "3d": 0.72, "28d": 0.614},
            "latency": {"5m": 0.0, "30m": 0.2, "1h": 0.3, "6h": 0.4,
                        "3d": 0.44, "28d": 0.436},
        },
        "bad_1h": {"success": 2, "latency": 2},
    },
    {
        "now": "2026-01-29T12:00:00Z",
        "total_runs": 11056,
        "eligible": {"support-agent": 3810, "billing-agent": 3320,
                     "diagnostics-agent": 3710},
        "success_bad": {"support-agent": 165, "billing-agent": 172,
                        "diagnostics-agent": 162},
        "latency_bad": {"support-agent": 108, "billing-agent": 102,
                        "diagnostics-agent": 115},
        "burns": {
            "success": {"5m": 18.4, "30m": 12.6, "1h": 14.8, "6h": 6.4,
                        "3d": 1.24, "28d": 0.921},
            "latency": {"5m": 2.0, "30m": 1.8, "1h": 1.6, "6h": 1.1,
                        "3d": 0.7, "28d": 0.6},
        },
        "bad_1h": {"success": 19, "latency": 4},
    },
    {
        "now": "2026-02-01T23:58:00Z",
        "total_runs": 13248,
        "eligible": {"support-agent": 4520, "billing-agent": 3980,
                     "diagnostics-agent": 4460},
        "success_bad": {"support-agent": 178, "billing-agent": 196,
                        "diagnostics-agent": 197},
        "latency_bad": {"support-agent": 110, "billing-agent": 105,
                        "diagnostics-agent": 122},
        "burns": {
            "success": {"5m": 0.0, "30m": 0.0, "1h": 0.0, "6h": 0.183,
                        "3d": 0.383, "28d": 0.881},
            "latency": {"5m": 0.0, "30m": 0.0, "1h": 0.05, "6h": 0.21,
                        "3d": 0.4, "28d": 0.52},
        },
        "bad_1h": {"success": 0, "latency": 2},
    },
]


def _build_report(spec: Dict, rng: random.Random, perturb: bool) -> Dict:
    eligible_by_agent = dict(spec["eligible"])
    bad = {}
    for slo, key in (("success", "success_bad"), ("latency", "latency_bad")):
        bad[slo] = {}
        for agent in AGENTS:
            n = spec[key][agent]
            if perturb:
                n = max(0, min(eligible_by_agent[agent],
                               n + rng.randint(-3, 3)))
            bad[slo][agent] = n

    total_eligible = sum(eligible_by_agent.values())
    slos = {}
    for slo in SLO_NAMES:
        total_bad = sum(bad[slo].values())
        slos[slo] = _slo_block(total_eligible, total_eligible - total_bad)

    per_agent = []
    for agent in sorted(AGENTS):
        eligible = eligible_by_agent[agent]
        entry = {"agent": agent, "eligible": eligible, "status": "ok"}
        for slo in SLO_NAMES:
            block = _slo_block(eligible, eligible - bad[slo][agent])
            entry[slo] = {"observed": block["observed"],
                          "compliant": block["compliant"],
                          "budget_consumed_pct":
                              block["budget"]["consumed_pct"]}
        per_agent.append(entry)

    burns = {slo: dict(spec["burns"][slo]) for slo in SLO_NAMES}
    return {
        "schema_version": SCHEMA_VERSION,
        "now": spec["now"],
        "window_days": WINDOW_DAYS,
        "total_runs": spec["total_runs"],
        "eligible_runs": total_eligible,
        "target": TARGET,
        "slos": slos,
        "burn_rates": burns,
        "alert_thresholds": rule_thresholds(),
        "active_alerts": _derive_alerts(burns["success"]),
        "projected_exhaustion": {
            slo: _exhaustion(slos[slo]["budget"]["remaining"],
                             spec["bad_1h"][slo])
            for slo in SLO_NAMES},
        "per_agent": per_agent,
    }


def generate_reports(seed: int = DEFAULT_SEED) -> List[Dict]:
    """The three seeded snapshot exports, chronological order."""
    rng = random.Random(seed)
    perturb = seed != DEFAULT_SEED
    return [_build_report(spec, rng, perturb) for spec in _SCENARIOS]


def generate_report(seed: int = DEFAULT_SEED) -> Dict:
    """The latest seeded snapshot (the fleet's current export)."""
    return generate_reports(seed)[-1]
