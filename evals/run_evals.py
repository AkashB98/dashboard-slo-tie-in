"""Golden evals for dashboard-slo-tie-in.

SYNTHETIC DATA ONLY (fictional Helios Home agents). Every eval is
deterministic: run_evals.py writes eval_report.json and the file
must be byte-identical across runs. No timestamps, ports, or temp
paths enter the report - only names, booleans, pinned counts and
hashes. ("now" values below are the seeded exports' own timestamps,
never the system clock.)

E1-E4 pin the seeded exports and the validator's clean pass. E5-E7
pin the strictness story (recomputed math, rejected unknown fields,
derived thresholds). E8-E10 pin conversion: tiles, statuses, fleet,
trends. E11-E12 pin rendering determinism. E13-E15 pin the check
gate, the serve round-trip, and the volume-guard edge. E16 pins
the cross-check contract: the export shape is the real
agent-slo-tracker report shape plus schema_version, and unknown
schema versions are rejected.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK  # noqa: E402
from cli import main as cli_main, make_server, reset_state  # noqa: E402
from convert import convert_report, convert_reports  # noqa: E402
from fixtures import generate_reports  # noqa: E402
from render import payload_to_json, render_html  # noqa: E402
from validation import validate_report, validate_reports  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append({"name": name, "passed": bool(passed),
                    "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}"
          + (f" - {detail}" if detail else ""))
    return passed


def _run_cli(argv):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cli_main(argv)


def main():
    print("== golden evals: dashboard-slo-tie-in ==")
    reports = generate_reports()
    latest = reports[-1]
    payload = convert_reports(reports)

    # E1: generator determinism (canonical bytes pinned)
    text = json.dumps(generate_reports(), sort_keys=True)
    md5 = hashlib.md5(text.encode()).hexdigest()
    check("E1 generator determinism (seed=42, exports md5 pinned)",
          text == json.dumps(reports, sort_keys=True)
          and md5 == "2e06d5ffd48a90104845a269a5bd068b",
          f"exports_md5={md5}")

    # E2: all three seeded exports validate clean
    check("E2 seeded exports validate clean (3 snapshots, 0 issues)",
          validate_reports(reports) == []
          and len(reports) == 3)

    # E3: latest SLO blocks pinned
    s = latest["slos"]["success"]
    lat = latest["slos"]["latency"]
    check("E3 latest SLOs pinned (success 0.955941/88.12%, "
          "latency 0.973997/52.01%)",
          (s["good"], s["bad"], s["observed"]) == (12389, 571, 0.955941)
          and s["budget"]["consumed_pct"] == 0.881173
          and s["budget"]["remaining"] == 77.0
          and (lat["good"], lat["observed"]) == (12623, 0.973997)
          and lat["budget"]["consumed_pct"] == 0.520062)

    # E4: incident snapshot alerts pinned
    incident = reports[1]
    check("E4 incident alerts pinned (page_fast, page_slow, ticket)",
          [a["rule"] for a in incident["active_alerts"]]
          == ["page_fast", "page_slow", "ticket"]
          and reports[0]["active_alerts"] == []
          and latest["active_alerts"] == [])

    # E5: validator recomputes budget math (a claimed figure fails)
    tampered = json.loads(json.dumps(latest))
    tampered["slos"]["success"]["budget"]["consumed_pct"] = 0.5
    issues = validate_report(tampered)
    check("E5 validator recompute catches tampered budget",
          any("budget.consumed_pct" in i and "recomputed" in i
              for i in issues), f"issues={len(issues)}")

    # E6: unknown fields rejected (pinned strictness choice)
    tampered2 = json.loads(json.dumps(latest))
    tampered2["owner"] = "team-x"
    tampered2["slos"]["success"]["budget"]["note"] = "hi"
    issues2 = validate_report(tampered2)
    check("E6 unknown fields rejected at top and budget level",
          sum("unknown field" in i for i in issues2) == 2)

    # E7: thresholds derived, not hard-coded (13.44 / 5.6 / 0.933333)
    check("E7 alert thresholds pinned to derived values",
          latest["alert_thresholds"] == {"page_fast": 13.44,
                                          "page_slow": 5.6,
                                          "ticket": 0.933333})

    # E8: conversion of the latest export pinned
    tiles = {t["tile_id"]: t for t in payload["tiles"]}
    check("E8 latest tiles pinned (5 tiles, success warning, "
          "latency green)",
          len(tiles) == 5
          and tiles["slo-success"]["headline"] == 0.955941
          and tiles["slo-success"]["status"] == "warning"
          and tiles["slo-latency"]["status"] == "green"
          and tiles["agent-billing-agent"]["headline"] == 0.950754
          and payload["fleet"]["status"] == "warning"
          and payload["run_id"] == "slo-20260201T235800Z",
          f"fleet={payload['fleet']['status']}")

    # E9: incident conversion pinned (critical fleet, page evidence)
    incident_payload = convert_reports(reports[:2])
    success_tile = next(t for t in incident_payload["tiles"]
                        if t["tile_id"] == "slo-success")
    page = next(f for f in success_tile["findings"]
                if "page_fast" in f["title"])
    check("E9 incident payload critical, page finding carries evidence",
          incident_payload["fleet"]["status"] == "critical"
          and incident_payload["fleet"]["page_firing"] is True
          and page["severity"] == "critical"
          and "threshold 13.44" in page["evidence"]
          and len(incident_payload["fleet"]["alerts"]) == 7,
          f"alerts={len(incident_payload['fleet']['alerts'])}")

    # E10: trends pinned (dashboard epsilon rule, steady/steady)
    trends = payload["trends"]
    check("E10 trends pinned (success +0.001974 steady, "
          "latency +0.003979 steady)",
          trends["success"] == {"trend": "steady", "delta": 0.001974,
                                "headlines": [0.969287, 0.953967,
                                              0.955941]}
          and trends["latency"]["delta"] == 0.003979
          and trends["latency"]["trend"] == "steady")

    # E11: payload JSON deterministic, md5 pinned
    payload_text = payload_to_json(payload)
    payload_md5 = hashlib.md5(payload_text.encode()).hexdigest()
    check("E11 payload JSON deterministic (md5 pinned)",
          payload_to_json(convert_reports(reports)) == payload_text
          and payload_md5 == "90efad7276808b59c5539ca26da404e9",
          f"payload_md5={payload_md5}")

    # E12: HTML panel deterministic, md5 pinned, no scripts/external
    page = render_html(payload)
    html_md5 = hashlib.md5(page.encode()).hexdigest()
    check("E12 HTML panel deterministic (md5 pinned, self-contained)",
          render_html(payload) == page
          and html_md5 == "68f365412e26e9966fdfbaf50d5bf39b"
          and "<script" not in page and "https://" not in page,
          f"html_md5={html_md5}")

    # E13: CLI check exit codes 0 / 1 / 2
    tmp = tempfile.mkdtemp(prefix="tie-in-evals-")
    incident_path = os.path.join(tmp, "incident.json")
    with open(incident_path, "w", encoding="utf-8") as fh:
        json.dump(reports[1], fh)
    codes = (_run_cli(["check"]),
             _run_cli(["check", "--input", incident_path]),
             _run_cli(["check", "--input", "/tmp/no-such-tie-in.json"]))
    check("E13 CLI check exit codes 0/1/2",
          codes == (EXIT_OK, EXIT_FAIL, EXIT_ERROR), f"codes={codes}")

    # E14: serve round-trip matches local conversion (seeded + explicit)
    reset_state(reports)
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def _post(body):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/convert",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        serve_ok = _post({}) == payload \
            and _post({"report": latest}) == convert_report(latest)
    finally:
        server.shutdown()
        server.server_close()
        reset_state()
    check("E14 serve round-trip matches local conversion", serve_ok)

    # E15: story arc + volume guard (green -> critical -> warning;
    # insufficient agent counted, not tiled)
    arc = [convert_reports(reports[:i])["fleet"]["status"]
           for i in (1, 2, 3)]
    stub = json.loads(json.dumps(latest))
    stub["per_agent"] = [e for e in stub["per_agent"]
                         if e["agent"] != "diagnostics-agent"]
    stub["per_agent"].append({"agent": "diagnostics-agent",
                              "eligible": 42,
                              "status": "insufficient_data"})
    stub_payload = convert_report(stub)
    check("E15 fleet arc all-green/critical/warning + volume guard",
          arc == ["all-green", "critical", "warning"]
          and stub_payload["fleet"]["insufficient_agents"] == 1
          and len(stub_payload["tiles"]) == 4,
          f"arc={arc}")

    # E16: contract - exports are the real agent-slo-tracker report
    # shape (keys taken from that repo's report.py output) plus
    # schema_version; unknown schema versions are rejected, and a
    # versionless native export still validates.
    real_top_keys = {"now", "window_days", "total_runs",
                     "eligible_runs", "target", "slos", "burn_rates",
                     "alert_thresholds", "active_alerts",
                     "projected_exhaustion", "per_agent"}
    future = json.loads(json.dumps(latest))
    future["schema_version"] = "9.9"
    native = json.loads(json.dumps(latest))
    del native["schema_version"]
    check("E16 contract + schema_version (real shape, unknown "
          "version rejected, native accepted)",
          all(set(r) - {"schema_version"} == real_top_keys
              and r["schema_version"] == "1.0" for r in reports)
          and any("unsupported version" in i
                  for i in validate_report(future))
          and validate_report(native) == []
          and payload["schema_version"] == "1.0")

    passed = sum(1 for r in RESULTS if r["passed"])
    total_n = len(RESULTS)
    summary_doc = {"passed": passed, "total": total_n,
                   "all_passed": passed == total_n, "evals": RESULTS}
    with open(os.path.join(HERE, "eval_report.json"), "w",
              encoding="utf-8") as fh:
        fh.write(json.dumps(summary_doc, sort_keys=True) + "\n")
    print(f"\n{passed}/{total_n} evals passed -> evals/eval_report.json")
    return 0 if passed == total_n else 1


if __name__ == "__main__":
    sys.exit(main())
