"""Converter: agent-slo-tracker report exports -> the normalized
records agent-eval-dashboard's health model and renderer consume.

The dashboard (agent-eval-dashboard) ingests artifacts as envelopes
``{"tool", "run_id", "ts", "report"}`` and its adapters normalize
each into a run summary::

    {"tool", "run_id", "ts", "headline" (0..1), "headline_label",
     "metrics", "breakdown" [{label, value}],
     "findings" [{"severity", "title", "evidence"}], "status"}

This module is the missing adapter for agent-slo-tracker, kept as a
standalone bridge: it validates an SLO export strictly (see
validation.py), then emits a dashboard payload containing

  * ``envelope`` - the ingest envelope for the latest export,
  * ``tiles``    - one normalized record per SLO plus one per agent
                   with enough volume (the dashboard calls these run
                   summaries; on a panel they are the tiles),
  * ``trends``   - per-SLO headline trend across the snapshots,
                   using the dashboard's own rule (delta rounded to
                   9 decimals, epsilon 0.02),
  * ``fleet``    - one roll-up verdict + the critical/high findings
                   flattened as fleet alerts, dashboard-style.

Status mapping (documented here and in the README, pinned by tests):
  critical - SLO/agent not compliant, budget exhausted
             (consumed >= 100%), or a page alert firing for it
  warning  - budget >= 75% consumed, a ticket alert firing, or
             projected exhaustion under 2 days away
  green    - otherwise
Agents reported ``insufficient_data`` by the tracker get no tile -
a headline over a handful of runs is noise; they are counted in
``fleet.insufficient_agents`` instead.
"""

from __future__ import annotations

from typing import Dict, List

from fixtures import SLO_NAMES
from validation import validate_reports

TOOL = "agent-slo-tracker"
TREND_EPS = 0.02
WARN_BUDGET_PCT = 0.75
HIGH_BUDGET_PCT = 0.90
WARN_EXHAUSTION_DAYS = 2.0

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3,
                  "critical": 4}
_STATUS_RANK = {"green": 0, "warning": 1, "critical": 2}


def _worst(a: str, b: str) -> str:
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


def _run_id(now: str) -> str:
    return "slo-" + now.replace("-", "").replace(":", "")


def _sort_findings(findings: List[Dict]) -> List[Dict]:
    return sorted(findings,
                  key=lambda f: (-_SEVERITY_RANK[f["severity"]],
                                 f["title"]))


def _alert_findings(report: Dict, slo: str) -> List[Dict]:
    out = []
    for alert in report["active_alerts"]:
        if alert["slo"] != slo:
            continue
        severity = "critical" if alert["severity"] == "page" else "high"
        out.append({
            "severity": severity,
            "title": f"{alert['rule']} {alert['severity']} firing "
                     f"for {slo} SLO",
            "evidence": (f"{alert['long_window']} burn "
                         f"{alert['long_burn']} and "
                         f"{alert['short_window']} burn "
                         f"{alert['short_burn']} both exceed "
                         f"threshold {alert['threshold']}"),
        })
    return out


def _budget_findings(subject: str, compliant: bool, consumed_pct: float,
                     observed: float, target: float, detail: str) -> List[Dict]:
    out = []
    if not compliant:
        out.append({"severity": "critical",
                    "title": f"{subject} below target",
                    "evidence": f"observed {observed} below target "
                                f"{target} ({detail})"})
    if consumed_pct >= 1.0:
        out.append({"severity": "critical",
                    "title": f"{subject} error budget exhausted",
                    "evidence": f"budget consumed "
                                f"{round(consumed_pct * 100, 2)}% "
                                f"({detail})"})
    elif consumed_pct >= HIGH_BUDGET_PCT:
        out.append({"severity": "high",
                    "title": f"{subject} error budget nearly exhausted",
                    "evidence": f"budget consumed "
                                f"{round(consumed_pct * 100, 2)}% "
                                f"({detail})"})
    elif consumed_pct >= WARN_BUDGET_PCT:
        out.append({"severity": "medium",
                    "title": f"{subject} error budget mostly spent",
                    "evidence": f"budget consumed "
                                f"{round(consumed_pct * 100, 2)}% "
                                f"({detail})"})
    return out


def _slo_status(report: Dict, slo: str) -> str:
    block = report["slos"][slo]
    budget = block["budget"]
    status = "green"
    if not block["compliant"] or budget["consumed_pct"] >= 1.0:
        status = "critical"
    if any(a["slo"] == slo and a["severity"] == "page"
           for a in report["active_alerts"]):
        status = "critical"
    if status != "critical":
        if budget["consumed_pct"] >= WARN_BUDGET_PCT:
            status = "warning"
        if any(a["slo"] == slo and a["severity"] == "ticket"
               for a in report["active_alerts"]):
            status = "warning"
        proj = report["projected_exhaustion"][slo]
        if proj is not None and proj["days"] < WARN_EXHAUSTION_DAYS:
            status = "warning"
    return status


def slo_tile(report: Dict, slo: str, base_run_id: str) -> Dict:
    block = report["slos"][slo]
    budget = block["budget"]
    burns = report["burn_rates"][slo]
    detail = (f"{block['good']} good of {block['eligible']} eligible "
              f"runs")
    findings = _alert_findings(report, slo)
    findings += _budget_findings(f"{slo} SLO", block["compliant"],
                                 budget["consumed_pct"],
                                 block["observed"], block["target"],
                                 detail)
    proj = report["projected_exhaustion"][slo]
    if proj is not None and proj["days"] < WARN_EXHAUSTION_DAYS \
            and budget["remaining"] > 0:
        findings.append({
            "severity": "high",
            "title": f"{slo} SLO budget projected to exhaust soon",
            "evidence": f"at the trailing 1h burn, the remaining "
                        f"{budget['remaining']} bad runs last "
                        f"{proj['hours']} hours ({proj['days']} days)",
        })
    breakdown = [{"label": f"burn {w}", "value": burns[w]}
                 for w in ("5m", "30m", "1h", "6h", "3d", "28d")]
    breakdown.append({"label": "budget remaining (bad runs)",
                      "value": budget["remaining"]})
    return {
        "tool": TOOL,
        "run_id": f"{base_run_id}-{slo}",
        "ts": report["now"],
        "headline": block["observed"],
        "headline_label": f"{slo} SLO observed",
        "metrics": {
            "target": block["target"],
            "eligible": block["eligible"],
            "good": block["good"],
            "bad": block["bad"],
            "budget_consumed_pct": budget["consumed_pct"],
            "budget_remaining": budget["remaining"],
            "burn_28d": burns["28d"],
            "burn_1h": burns["1h"],
        },
        "breakdown": breakdown,
        "findings": _sort_findings(findings),
        "status": _slo_status(report, slo),
        "tile_id": f"slo-{slo}",
        "kind": "slo",
    }


def agent_tile(report: Dict, entry: Dict, base_run_id: str) -> Dict:
    agent = entry["agent"]
    success = entry["success"]
    latency = entry["latency"]
    detail = f"{entry['eligible']} eligible runs for {agent}"
    findings = _budget_findings(
        f"{agent} success", success["compliant"],
        success["budget_consumed_pct"], success["observed"],
        report["target"], detail)
    if latency["budget_consumed_pct"] >= HIGH_BUDGET_PCT:
        findings.append({
            "severity": "medium",
            "title": f"{agent} latency budget nearly exhausted",
            "evidence": f"latency budget consumed "
                        f"{round(latency['budget_consumed_pct'] * 100, 2)}"
                        f"% ({detail})",
        })
    status = "green"
    if not success["compliant"] \
            or success["budget_consumed_pct"] >= 1.0:
        status = "critical"
    elif success["budget_consumed_pct"] >= WARN_BUDGET_PCT \
            or latency["budget_consumed_pct"] >= HIGH_BUDGET_PCT:
        status = "warning"
    return {
        "tool": TOOL,
        "run_id": f"{base_run_id}-{agent}",
        "ts": report["now"],
        "headline": success["observed"],
        "headline_label": "success SLO observed",
        "metrics": {
            "eligible": entry["eligible"],
            "success_observed": success["observed"],
            "latency_observed": latency["observed"],
            "success_budget_consumed_pct":
                success["budget_consumed_pct"],
            "latency_budget_consumed_pct":
                latency["budget_consumed_pct"],
        },
        "breakdown": [
            {"label": "success observed", "value": success["observed"]},
            {"label": "latency observed", "value": latency["observed"]},
            {"label": "success budget consumed_pct",
             "value": success["budget_consumed_pct"]},
            {"label": "latency budget consumed_pct",
             "value": latency["budget_consumed_pct"]},
        ],
        "findings": _sort_findings(findings),
        "status": status,
        "tile_id": f"agent-{agent}",
        "kind": "agent",
    }


def _trend(headlines: List[float]) -> Dict:
    if len(headlines) < 2:
        return {"trend": "new", "delta": None, "headlines": headlines}
    # Rounded to 9 decimals before comparing, exactly as the
    # dashboard's health model does: 0.92 - 0.90 is
    # 0.020000000000000004 in float and must not read as improving.
    delta = round(headlines[-1] - headlines[-2], 9)
    trend = ("improving" if delta > TREND_EPS
             else "declining" if delta < -TREND_EPS else "steady")
    return {"trend": trend, "delta": delta, "headlines": headlines}


def convert_reports(reports: List[Dict]) -> Dict:
    """Convert one or more SLO exports (chronological) into a single
    dashboard payload. Tiles and fleet verdict come from the latest
    export; trends span all of them. Raises ValueError on invalid
    input - conversion never proceeds on unvalidated data."""
    issues = validate_reports(reports)
    if issues:
        raise ValueError(f"invalid SLO export: {issues[0]}")
    latest = reports[-1]
    base_run_id = _run_id(latest["now"])
    tiles = [slo_tile(latest, slo, base_run_id) for slo in SLO_NAMES]
    insufficient = 0
    for entry in latest["per_agent"]:
        if entry["status"] == "insufficient_data":
            insufficient += 1
            continue
        tiles.append(agent_tile(latest, entry, base_run_id))

    fleet_status = "all-green"
    for tile in tiles:
        if tile["status"] == "critical":
            fleet_status = "critical"
        elif tile["status"] == "warning" and fleet_status == "all-green":
            fleet_status = "warning"
    alerts = []
    for tile in tiles:
        for finding in tile["findings"]:
            if finding["severity"] in ("critical", "high"):
                alerts.append({"tool": TOOL,
                               "run_id": tile["run_id"],
                               "tile_id": tile["tile_id"],
                               "severity": finding["severity"],
                               "title": finding["title"],
                               "evidence": finding["evidence"]})
    alerts.sort(key=lambda a: (a["tile_id"], a["severity"], a["title"]))

    breached = [slo for slo in SLO_NAMES
                if not latest["slos"][slo]["compliant"]
                or latest["slos"][slo]["budget"]["remaining"] < 0]
    page_firing = any(a["severity"] == "page"
                      for a in latest["active_alerts"])
    return {
        "tool": TOOL,
        "run_id": base_run_id,
        "ts": latest["now"],
        "schema_version": latest.get("schema_version", "unversioned"),
        "source_reports": len(reports),
        "window_days": latest["window_days"],
        "envelope": {"tool": TOOL, "run_id": base_run_id,
                     "ts": latest["now"], "report": latest},
        "tiles": tiles,
        "trends": {slo: _trend([r["slos"][slo]["observed"]
                                 for r in reports])
                   for slo in SLO_NAMES},
        "fleet": {
            "status": fleet_status,
            "n_tiles": len(tiles),
            "alerts": alerts,
            "breached_slos": breached,
            "page_firing": page_firing,
            "insufficient_agents": insufficient,
        },
    }


def convert_report(report: Dict) -> Dict:
    return convert_reports([report])
