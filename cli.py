"""CLI: generate | convert | validate | render | report | check |
demo | serve.

SYNTHETIC DATA ONLY - Helios Home is a fictional company and every
export handled here is fabricated sample data.

Exit codes:
  check   : 0 = no SLO breached and no page firing in the latest
            export, 1 = an SLO breached or a page firing,
            2 = usage / IO / data error
  validate: 0 = export(s) valid, 2 = invalid / IO error
  others  : 0 = success, 2 = usage / IO / data error

Commands without --input run on the seeded fixture exports
(seed 42, three snapshots; the latest is the fleet's "now").
--input accepts one export (a JSON object in agent-slo-tracker
report shape) or several (a JSON array of them, chronological).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert import convert_reports  # noqa: E402
from fixtures import DEFAULT_SEED, generate_reports  # noqa: E402
from render import payload_to_json, render_html  # noqa: E402
from validation import validate_reports  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _reports_or_error(args):
    """(reports, exit_code): --input file, or the seeded exports."""
    if not getattr(args, "input", None):
        return generate_reports(), EXIT_OK
    try:
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"error: file not found: {args.input}", file=sys.stderr)
        return None, EXIT_ERROR
    except (ValueError, OSError) as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return None, EXIT_ERROR
    reports = data if isinstance(data, list) else [data]
    issues = validate_reports(reports)
    if issues:
        for issue in issues[:20]:
            print(f"data-quality: {issue}", file=sys.stderr)
        return None, EXIT_ERROR
    return reports, EXIT_OK


def _payload_or_error(args):
    reports, code = _reports_or_error(args)
    if reports is None:
        return None, code
    try:
        return convert_reports(reports), EXIT_OK
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, EXIT_ERROR


def _write_out(args, text: str, summary: str) -> int:
    if getattr(args, "out", None):
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(summary)
    else:
        print(text, end="")
    return EXIT_OK


def cmd_generate(args) -> int:
    reports = generate_reports(seed=args.seed)
    return _write_out(args, canonical(reports) + "\n",
                      f"generated {len(reports)} seeded SLO exports "
                      f"(seed={args.seed}) -> {args.out}")


def cmd_convert(args) -> int:
    payload, code = _payload_or_error(args)
    if payload is None:
        return code
    return _write_out(args, payload_to_json(payload),
                      f"dashboard payload -> {args.out}")


def cmd_validate(args) -> int:
    if getattr(args, "input", None):
        try:
            with open(args.input, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            print(f"error: file not found: {args.input}",
                  file=sys.stderr)
            return EXIT_ERROR
        except (ValueError, OSError) as exc:
            print(f"error: cannot read {args.input}: {exc}",
                  file=sys.stderr)
            return EXIT_ERROR
        reports = data if isinstance(data, list) else [data]
    else:
        reports = generate_reports()
    issues = validate_reports(reports)
    if issues:
        for issue in issues[:20]:
            print(f"invalid: {issue}")
        return EXIT_ERROR
    print(f"valid: {len(reports)} SLO export(s), 0 issues")
    return EXIT_OK


def cmd_render(args) -> int:
    payload, code = _payload_or_error(args)
    if payload is None:
        return code
    if args.format == "json":
        return _write_out(args, payload_to_json(payload),
                          f"dashboard payload -> {args.out}")
    return _write_out(args, render_html(payload),
                      f"HTML panel -> {args.out}")


def cmd_report(args) -> int:
    payload, code = _payload_or_error(args)
    if payload is None:
        return code
    fleet = payload["fleet"]
    lines = [f"fleet: {fleet['status']}  ({fleet['n_tiles']} tiles, "
             f"{payload['source_reports']} export(s), "
             f"as of {payload['ts']})"]
    for tile in payload["tiles"]:
        lines.append(
            f"  [{tile['status']:8s}] {tile['tile_id']:28s} "
            f"{tile['headline_label']}={tile['headline']:.4f} "
            f"findings={len(tile['findings'])}")
    for slo, trend in payload["trends"].items():
        delta = "" if trend["delta"] is None \
            else f" delta={trend['delta']:+.4f}"
        lines.append(f"  trend {slo}: {trend['trend']}{delta}")
    if fleet["alerts"]:
        lines.append(f"alerts ({len(fleet['alerts'])}):")
        for a in fleet["alerts"]:
            lines.append(f"  [{a['severity']}] {a['tile_id']}: "
                         f"{a['title']}")
    text = "\n".join(lines) + "\n"
    return _write_out(args, text, f"report -> {args.out}")


def cmd_check(args) -> int:
    payload, code = _payload_or_error(args)
    if payload is None:
        return code
    fleet = payload["fleet"]
    latest_tiles = [t for t in payload["tiles"] if t["kind"] == "slo"]
    for tile in latest_tiles:
        m = tile["metrics"]
        print(f"{tile['tile_id']}: headline={tile['headline']:.4f} "
              f"budget_consumed_pct={m['budget_consumed_pct']:.4f} "
              f"status={tile['status']}")
    print(f"fleet={fleet['status']} breached={fleet['breached_slos']} "
          f"page_firing={fleet['page_firing']}")
    if fleet["breached_slos"]:
        print(f"check FAIL: SLO breached: {fleet['breached_slos']}")
        return EXIT_FAIL
    if fleet["page_firing"]:
        print("check FAIL: a page alert is firing in the latest export")
        return EXIT_FAIL
    print("check PASS")
    return EXIT_OK


def cmd_demo(args) -> int:
    import demo as demo_mod
    try:
        demo_mod.main()
    except AssertionError as exc:
        print(f"demo FAILED: {exc}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------- serve

class _State:
    def __init__(self):
        self.reports: List[Dict] = generate_reports()


_STATE = _State()


def get_state() -> _State:
    return _STATE


def reset_state(reports: Optional[List[Dict]] = None) -> None:
    _STATE.reports = list(reports) if reports is not None \
        else generate_reports()


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: Any) -> None:
        body = (canonical(payload) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        return self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self):  # noqa: N802
        if self.path != "/convert":
            return self._send_json(404, {"error": "unknown endpoint"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw.strip() else {}
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            if "reports" in body:
                reports = body["reports"]
            elif "report" in body:
                reports = [body["report"]]
            elif body:
                reports = [body]  # the body IS one export
            else:
                reports = _STATE.reports  # empty body -> seeded
            return self._send_json(200, convert_reports(reports))
        except (ValueError, TypeError, KeyError) as exc:
            return self._send_json(400, {"error": f"bad request: {exc}"})

    def log_message(self, *a: Any) -> None:
        pass


def make_server(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), _Handler)


def cmd_serve(args) -> int:
    if args.input:
        reports, code = _reports_or_error(args)
        if reports is None:
            return code
        reset_state(reports)
    server = make_server(args.port)
    print(f"serving on http://127.0.0.1:{args.port}  "
          f"(POST /convert, GET /health)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dashboard-slo-tie-in",
        description="Bridge agent-slo-tracker SLO exports into "
                    "agent-eval-dashboard tiles (Helios Home sample "
                    "data).")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate",
                       help="generate the seeded SLO exports (JSON)")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_generate)

    for name, fn, helptext in (
            ("convert", cmd_convert, "dashboard payload (JSON)"),
            ("validate", cmd_validate, "strict validation of exports"),
            ("report", cmd_report, "human-readable fleet summary"),
            ("check", cmd_check,
             "CI gate: exit 0 pass / 1 breach or page / 2 error")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--input", default=None,
                       help="SLO export file (JSON object or array); "
                            "default: seeded exports")
        s.add_argument("--out", default=None)
        s.set_defaults(func=fn)

    r = sub.add_parser("render", help="HTML panel (or JSON payload)")
    r.add_argument("--input", default=None)
    r.add_argument("--out", default=None)
    r.add_argument("--format", choices=("html", "json"), default="html")
    r.set_defaults(func=cmd_render)

    sub.add_parser("demo", help="run the end-to-end demo").set_defaults(
        func=cmd_demo)

    s = sub.add_parser("serve", help="localhost JSON API")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--input", default=None)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
