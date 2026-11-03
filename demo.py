"""End-to-end demo: generate -> validate -> convert -> render ->
check -> serve.

SYNTHETIC DATA ONLY (fictional Helios Home agents). Runs fully
offline; the serve round-trip is loopback-only on an ephemeral port.
Every stage asserts, so the demo doubles as a smoke test.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK  # noqa: E402
from cli import main as cli_main, make_server, reset_state  # noqa: E402
from convert import convert_reports  # noqa: E402
from fixtures import generate_reports  # noqa: E402
from render import payload_to_json, render_html  # noqa: E402
from validation import validate_reports  # noqa: E402


def _request(port: int, path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> None:
    print("== 1. generate seeded Helios Home SLO exports (seed=42) ==")
    reports = generate_reports()
    again = generate_reports()
    assert reports == again, "generator must be deterministic"
    assert len(reports) == 3, reports
    print(f"   {len(reports)} snapshots, deterministic: "
          f"{[r['now'] for r in reports]}")

    print("\n== 2. strict validation (budget math recomputed) ==")
    issues = validate_reports(reports)
    assert issues == [], issues[:3]
    print("   all 3 exports valid, 0 issues")

    print("\n== 3. convert to a dashboard payload ==")
    payload = convert_reports(reports)
    fleet = payload["fleet"]
    for tile in payload["tiles"]:
        print(f"   [{tile['status']:8s}] {tile['tile_id']:28s} "
              f"{tile['headline_label']}={tile['headline']:.4f} "
              f"findings={len(tile['findings'])}")
    print(f"   fleet: {fleet['status']} "
          f"(alerts={len(fleet['alerts'])}, "
          f"page_firing={fleet['page_firing']})")
    assert payload["tiles"][0]["headline"] == 0.955941  # success, latest
    assert fleet["status"] == "warning", fleet

    print("\n== 4. render: canonical JSON + self-contained HTML ==")
    text = payload_to_json(payload)
    assert payload_to_json(convert_reports(reports)) == text
    page = render_html(payload)
    assert "SIMULATED DATA" in page and "fleet: warning" in page
    assert "<script" not in page and "http://" not in page \
        and "https://" not in page
    print(f"   JSON payload {len(text)} bytes, HTML panel "
          f"{len(page)} bytes, no scripts, no external assets")

    print("\n== 5. the incident snapshot reads critical ==")
    incident = convert_reports(reports[:2])
    assert incident["fleet"]["status"] == "critical", incident["fleet"]
    assert incident["fleet"]["page_firing"] is True
    print("   fast-burn snapshot: page_fast + page_slow + ticket "
          "firing, billing-agent budget breached")

    print("\n== 6. CLI check gate exit codes ==")
    code_now = cli_main(["check"])
    code_err = cli_main(["check", "--input", "/tmp/no-such-tie-in.json"])
    assert code_now == EXIT_OK and code_err == EXIT_ERROR, \
        (code_now, code_err)
    print(f"   latest={code_now} missing-file={code_err} "
          f"(breach case pinned in evals)")

    print("\n== 7. live localhost serve round-trip ==")
    reset_state(reports)
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, health = _request(port, "/health")
        assert status == 200 and health == {"status": "ok"}
        status, served = _request(port, "/convert", payload={},
                                  method="POST")
        assert status == 200 and served == payload, "empty -> seeded"
        status, served2 = _request(port, "/convert",
                                   payload={"report": reports[-1]},
                                   method="POST")
        assert status == 200 \
            and served2 == convert_reports([reports[-1]])
        status, bad = _request(port, "/convert",
                               payload={"report": {"bogus": 1}},
                               method="POST")
        assert status == 400 and "error" in bad, (status, bad)
        print("   POST /convert (seeded + explicit + invalid->400) "
              "verified against local conversion")
    finally:
        server.shutdown()
        server.server_close()
        reset_state()

    print("\nDemo OK: generate, validate, convert, render, check and "
          "serve verified.")


if __name__ == "__main__":
    main()
