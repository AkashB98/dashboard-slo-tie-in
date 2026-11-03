"""Renderer: a dashboard payload -> (a) its canonical JSON form and
(b) a single self-contained HTML panel.

The HTML panel is deliberately boring technology: inline CSS in one
``<style>`` block, no JavaScript, no external assets, no web fonts -
it renders identically offline, in an email client preview, or
saved to disk months later. Every dynamic string is HTML-escaped,
so a hostile agent name or evidence string in an export becomes
inert text, never markup.
"""

from __future__ import annotations

import html
import json
from typing import Dict

_STATUS_COLOR = {"green": "#1a7f37", "warning": "#9a6700",
                 "critical": "#cf222e"}
_FLEET_COLOR = {"all-green": "#1a7f37", "warning": "#9a6700",
                "critical": "#cf222e"}
_SEVERITY_COLOR = {"critical": "#cf222e", "high": "#cf222e",
                   "medium": "#9a6700", "low": "#57606a",
                   "info": "#57606a"}
_TREND_ARROW = {"improving": "&#8599;", "steady": "&#8594;",
                "declining": "&#8600;", "new": "&#9679;"}


def payload_to_json(payload: Dict) -> str:
    """Canonical JSON: sorted keys, one trailing newline, so the
    same payload is byte-identical everywhere it is produced."""
    return json.dumps(payload, sort_keys=True) + "\n"


def _esc(x) -> str:
    return html.escape(str(x), quote=True)


def _finding_html(f: Dict) -> str:
    color = _SEVERITY_COLOR.get(f["severity"], "#57606a")
    return (
        f'<div class="finding" style="border-left:4px solid {color}">'
        f'<b>[{_esc(f["severity"])}]</b> {_esc(f["title"])}'
        f'<div class="evidence">&ldquo;{_esc(f["evidence"])}&rdquo;'
        f"</div></div>"
    )


def _tile_html(tile: Dict, trend: Dict = None) -> str:
    color = _STATUS_COLOR[tile["status"]]
    metrics = "".join(
        f"<tr><td>{_esc(k)}</td><td><b>{_esc(v)}</b></td></tr>"
        for k, v in tile["metrics"].items())
    breakdown = "".join(
        f"<tr><td>{_esc(row['label'])}</td><td>{_esc(row['value'])}"
        f"</td></tr>"
        for row in tile["breakdown"])
    findings = "".join(_finding_html(f) for f in tile["findings"])
    trend_html = ""
    if trend is not None:
        arrow = _TREND_ARROW[trend["trend"]]
        trend_html = (f'<div class="trend">{arrow} '
                      f'{_esc(trend["trend"])}'
                      f'<div class="label">SLO trend across '
                      f'{len(trend["headlines"])} exports</div></div>')
    return (
        f'<section class="card" id="tile-{_esc(tile["tile_id"])}">'
        f'<h2><span class="dot" style="background:{color}"></span>'
        f'{_esc(tile["tile_id"])} '
        f'<small class="muted">{_esc(tile["kind"])} tile</small></h2>'
        f'<div class="headrow"><div class="headline">'
        f'{_esc(round(tile["headline"], 4))}'
        f'<div class="label">{_esc(tile["headline_label"])}</div></div>'
        f"{trend_html}</div>"
        f'<table class="metrics">{metrics}</table>'
        f'<h3>Breakdown</h3><table class="metrics">{breakdown}</table>'
        + (f"<h3>Findings ({len(tile['findings'])})</h3>" + findings
           if findings
           else "<p class='muted'>No findings on this tile.</p>")
        + "</section>"
    )


def render_html(payload: Dict) -> str:
    """One self-contained HTML panel for a dashboard payload.
    Deterministic: same payload -> byte-identical page."""
    fleet = payload["fleet"]
    color = _FLEET_COLOR[fleet["status"]]
    trends = payload.get("trends", {})
    cards = "\n".join(
        _tile_html(tile,
                   trends.get(tile["tile_id"][4:])
                   if tile["kind"] == "slo" else None)
        for tile in payload["tiles"])
    alerts = "".join(
        f'<div class="finding" style="border-left:4px solid '
        f'{"#cf222e" if a["severity"] == "critical" else "#9a6700"}">'
        f'<b>[{_esc(a["severity"])}]</b> {_esc(a["tile_id"])} &mdash; '
        f'{_esc(a["title"])}'
        f'<div class="evidence">&ldquo;{_esc(a["evidence"])}&rdquo;'
        f"</div></div>"
        for a in fleet["alerts"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dashboard-slo-tie-in &mdash; SLO panel</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:1080px;margin:0 auto;padding:24px;color:#1f2328;background:#f6f8fa}}
h1{{font-size:1.6em}} h2{{font-size:1.2em}} h3{{font-size:1em;margin:14px 0 6px}}
.muted{{color:#57606a}} .label{{font-size:.75em;color:#57606a}}
.card{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:16px 20px;margin:16px 0}}
.dot{{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px}}
.headrow{{display:flex;gap:32px;align-items:center;margin:8px 0}}
.headline{{font-size:2em;font-weight:700}} .trend{{font-size:1.2em}}
.metrics{{border-collapse:collapse;margin:8px 0}} .metrics td{{border:1px solid #d0d7de;padding:4px 10px;font-size:.9em}}
.finding{{background:#fff;border:1px solid #d0d7de;border-radius:6px;padding:8px 12px;margin:8px 0;font-size:.9em}}
.evidence{{color:#57606a;font-style:italic;margin-top:4px;word-break:break-word}}
.sim{{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:10px 14px;font-weight:600}}
.statusline{{font-size:1.1em;padding:10px 14px;border-radius:6px;color:#fff;background:{color};display:inline-block}}
</style>
</head>
<body>
<div class="sim">SIMULATED DATA &mdash; this panel is built from fictional
Helios Home sample exports, never personal data.</div>
<h1>SLO panel &mdash; agent-slo-tracker via dashboard-slo-tie-in</h1>
<p class="muted">Error budgets, burn rates and alerts from the SLO tracker,
in the fleet view of the eval dashboard: one page, every verdict carrying
the evidence that produced it.</p>
<p><span class="statusline">fleet: {_esc(fleet["status"])} &middot;
{_esc(fleet["n_tiles"])} tiles &middot; {_esc(payload["source_reports"])}
export(s) &middot; as of {_esc(payload["ts"])}</span></p>
<h2>Fleet alerts ({len(fleet["alerts"])})</h2>
{alerts or "<p class='muted'>No critical/high alerts. Fleet is quiet.</p>"}
{cards}
<hr><p class="muted">dashboard-slo-tie-in &mdash; zero-dependency bridge
from SLO exports to dashboard tiles.</p>
</body>
</html>
"""
