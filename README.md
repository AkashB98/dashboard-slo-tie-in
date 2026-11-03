# dashboard-slo-tie-in

**The bridge that plugs the SLO tracker into the fleet dashboard: it takes an SLO status export from `agent-slo-tracker`, validates it strictly enough to catch a lying number, and converts it into the tiles `agent-eval-dashboard` renders — so the operations capstone and the fleet view finally show the same truth on one page.**

Two sibling projects in this portfolio each tell half the story. `agent-slo-tracker` knows whether an AI agent fleet is keeping its reliability promise — an **SLO** — service level objective, the target you commit to (here: 95% of runs good over 28 days). `agent-eval-dashboard` is the mission-control page where a **forward-deployed engineer** — the engineer who ships AI systems into real customer environments and then has to operate them there — checks the whole stack each morning. But the dashboard has no way to read an SLO export, and the tracker's report speaks a different language. This repo is the missing **adapter** — a translator between two tools' data formats. It proves the skill that decides whether integrations survive contact with a customer environment: take another system's output, refuse to trust it, recompute its claims from the raw counts, and only then let it onto the page everyone watches.

> **Synthetic data only.** Every export is fabricated by a seeded generator for a fictional smart-home company (**Helios Home**) and its support / billing / diagnostics agents. No real company, customer, or agent data is used anywhere in this project.

Stdlib-only Python — deliberately, matching both siblings. A conversion layer is exactly where a silent dependency or a drifting float would corrupt a verdict downstream; anyone should be able to read every line, re-run it offline, and get byte-identical outputs. Zero network, zero API keys, zero external dependencies.

## Quickstart

```bash
# 1. Generate the seeded SLO exports (3 snapshots, seed 42)
python3 cli.py generate --out exports.json

# 2. Strictly validate exports (budget math recomputed, alerts
#    re-derived from the burn table, unknown fields rejected)
python3 cli.py validate            # seeded exports
python3 cli.py validate --input exports.json

# 3. Convert to a dashboard payload (JSON tiles + fleet roll-up)
python3 cli.py convert --out payload.json

# 4. Render the self-contained HTML panel (inline CSS, no JS)
python3 cli.py render --out panel.html

# 5. Human-readable fleet summary
python3 cli.py report

# 6. CI gate: exit 0 = no SLO breached and no page firing,
#    1 = SLO breached or a page firing, 2 = data/IO error
python3 cli.py check; echo $?      # 0 on the latest seeded export

# 7. One-command end-to-end demo
python3 demo.py

# 8. Tiny localhost API: POST /convert, GET /health on 127.0.0.1
python3 cli.py serve --port 8766
curl -s -X POST localhost:8766/convert -d '{}'   # seeded payload
```

## The seeded story at a glance (seed 42, three snapshots)

The generator emits three **snapshots** — exports of the same rolling 28-day window at three moments — because one export shows a state and three show a story:

| Snapshot | Fleet verdict | What happened |
|---|---|---|
| 2026-01-12 | **all-green** | success budget 61.43% spent, latency 43.61%, no alerts |
| 2026-01-29 | **critical** | fast burn: `page_fast` + `page_slow` + `ticket` firing on success; billing-agent breaches its own success budget (observed 0.948193, 103.61% consumed) |
| 2026-02-01 ("now") | **warning** | quiet again — no alerts — but the success budget is 88.12% spent, so the fleet stays visibly tight |

**The latest export, converted.** 12,960 eligible runs; tiles are the dashboard's normalized records (headline = observed **SLI** — service level indicator, the measured ratio the SLO judges):

| Tile | Headline | Budget consumed | Status |
|---|---|---|---|
| slo-success | 0.955941 (12,389 good) | **88.12%** (571 of 648.0 allowed bad; 77.0 left) | ⚠️ warning |
| slo-latency | 0.973997 (12,623 good) | 52.01% | ✅ green |
| agent-billing-agent | 0.950754 | 98.49% (success) | ⚠️ warning — the fleet's one high alert |
| agent-diagnostics-agent | 0.955830 | 88.34% (success) | ⚠️ warning |
| agent-support-agent | 0.960619 | 78.76% (success) | ⚠️ warning |

**Trends** — whether a score is improving, steady, or declining across exports — use the dashboard's own rule (delta rounded to 9 decimals, ε = 0.02): success steady (+0.001974 over headlines 0.969287 → 0.953967 → 0.955941), latency steady (+0.003979). The incident dent is visible in the headline history even though the latest delta reads steady — which is exactly why the panel shows the history, not just the arrow.

## How it works

**The input** is the `agent-slo-tracker` report shape — not an invented approximation of it: the key sets and types are contract-tested against that repo's real `report.build_report` output (and, when the sibling repo is on disk, a genuine tracker export is fed through this validator live in the test suite and passes with zero issues). Per-SLO blocks (target, eligible / good / bad counts, observed SLI, an **error budget** — the failure you are allowed: at a 95% target over 12,960 eligible runs, exactly 648.0 bad runs), **burn rates** — how fast the budget is being spent (observed bad rate ÷ budgeted bad rate) — for six windows (5m…28d), alert thresholds, active alerts, projected exhaustion, and a per-agent table behind a 500-run volume guard. This repo's exports add exactly one field to that shape: a top-level **`schema_version`** — a label naming which version of the export format a file uses (`"1.0"` here) — so future shape changes are versioned; the validator rejects any version it does not understand, loudly and by value, while native tracker exports (unversioned) are accepted as-is.

**The validator is strict, and the choice is pinned: unknown fields are REJECTED**, at every level of the export — not flagged and passed through. A bridge that silently carries fields it does not understand will eventually render a number nobody can explain. Beyond shape, the validator *recomputes* rather than trusts:

- observed SLI == `round(good / eligible, 6)`; budget allowed / consumed % / remaining recomputed from the raw counts and target via `(1 − target)` — budget-remaining gets its own explicit recomputation (`(1−target) × eligible − bad`), and on any mismatch the issue prints **both** values (export says X, recomputed Y) with the tolerance used (half a unit of the field's last rounded decimal, plus float slack); `compliant` == `observed >= target`
- per-agent budget % == `(1 − observed) / (1 − target)`
- alert thresholds == re-derived from their budget fractions (2% in 1h → 13.44, 5% in 6h → 5.6, 10% in 3d → 0.933333)
- `active_alerts` == *exactly* the rules the success burn table fires under the two-window rule (both windows over threshold) — a phantom alert and a dropped alert both fail
- projected-exhaustion days == hours / 24; an exhausted budget must project 0, never null
- ranges: observed SLIs must lie in 0..1; consumed % and burn rates are ≥ 0 and may exceed 1 — that is what a breach looks like. Bools are checked as real bools before ints (in Python `True` is an `int`; a JSON `true` in a count field is a data bug, not a 1)

**The converter** refuses invalid input outright, then maps the latest export into dashboard tiles: one per SLO, one per agent with enough volume (`insufficient_data` agents get no tile — a headline over a handful of runs is noise; they are counted in the fleet block instead). Each tile is a normalized dashboard record — headline, labeled metrics, a breakdown, and findings that always carry an **evidence quote** — the exact numbers that produced the finding, so a red tile is never a mystery. The payload also carries the **envelope** — the wrapper (`{tool, run_id, ts, report}`) the dashboard's store ingests — plus per-SLO trends across all snapshots and a fleet **roll-up** — one combined verdict from many checks: critical if any tile is critical, warning if any is warning, all-green otherwise.

**Status mapping** (pinned by tests): **critical** = not compliant, budget exhausted (≥ 100% consumed), or a page alert firing; **warning** = budget ≥ 75% consumed, a ticket alert firing, or projected exhaustion under 2 days away; **green** = otherwise. Page findings are severity critical, ticket findings high — so both surface as fleet alerts, while a merely *mostly-spent* budget (medium) stays on its tile.

**The renderer** produces the payload's canonical JSON (sorted keys — the same payload is byte-identical everywhere) and a single self-contained HTML panel: inline CSS in one `<style>` block, no JavaScript, no external assets, every dynamic string HTML-escaped. It renders identically offline or saved to disk months later.

**The `check` CI gate** — an automated check that blocks bad states from shipping — reads the *latest* export only: exit 0 healthy / 1 any SLO breached or page firing / 2 data error. That numbering is the **portfolio convention**, kept deliberately: it matches `agent-slo-tracker`'s gate and every sibling CLI in this portfolio (0 = pass, 1 = the thing you gate against fired, 2 = the data or IO failed), so a CI pipeline never has to learn a new dialect per repo. A warning fleet (budget tight, nothing breached, no page) passes the gate and still shows warning on the panel — the gate and the page answer different questions, deliberately.

## Architecture

| Module | Job |
|---|---|
| `fixtures.py` | **Seeded** — reproducible from a starting number — generator of SLO exports in the tracker's real report shape (contract-tested, see Evals) plus a `schema_version` field: three snapshots for the three Helios Home agents, with all derived numbers (budgets, exhaustion, alerts) computed from raw counts and the burn table; a non-42 seed deterministically perturbs the bad counts and still yields valid exports |
| `validation.py` | The strict gate: exact field sets (unknown rejected), `schema_version` checked against the supported versions (unknown rejected by value; absent = native unversioned tracker export, accepted), type/range checks incl. the bool trap, and independent recomputation of every derived number — budget-remaining explicitly, with both values printed on mismatch — the derived thresholds, and the firing alert set |
| `convert.py` | The adapter: SLO blocks + per-agent table → dashboard tiles/records, alert/budget findings with evidence, cross-snapshot trends (dashboard ε rule), fleet roll-up, breached/page flags for the gate |
| `render.py` | Canonical JSON + the self-contained HTML panel (inline CSS, no JS, escaped content) |
| `cli.py` | `generate` / `convert` / `validate` / `render` / `report` / `check` (CI exit 0/1/2) / `demo` / `serve` (stdlib `http.server` on 127.0.0.1: `POST /convert` with one export, `{"report": …}`, `{"reports": […]}`, or an empty body for the seeded exports; `GET /health`) |
| `demo.py` | End-to-end narrative with asserts: determinism, validation, conversion, the incident snapshot reading critical, check exit codes, live localhost serve round-trip incl. invalid input → 400 |

## Evals

`python3 evals/run_evals.py` → **16/16 golden evals**, report committed at `evals/eval_report.json` (md5 `b2224469756a1d0866af044d95f90f3e`, byte-identical across 3 verified runs):

- **E1** generator determinism — seed 42 exports pinned by md5
- **E2** all three seeded exports validate clean
- **E3** latest SLO blocks pinned (success 0.955941 / 88.12%, latency 0.973997 / 52.01%)
- **E4** incident alerts pinned (page_fast, page_slow, ticket; silence either side)
- **E5** validator recompute catches a tampered budget figure
- **E6** unknown fields rejected at top and budget level (the pinned strictness choice)
- **E7** thresholds are the derived values (13.44 / 5.6 / 0.933333)
- **E8** latest conversion pinned (5 tiles, statuses, run id)
- **E9** incident payload critical; the page finding's evidence quotes threshold 13.44; 7 fleet alerts
- **E10** trends pinned (both steady, deltas +0.001974 / +0.003979)
- **E11** payload JSON deterministic, md5 pinned (`90efad7276808b59c5539ca26da404e9`)
- **E12** HTML panel deterministic, md5 pinned (`68f365412e26e9966fdfbaf50d5bf39b`), self-contained
- **E13** CLI `check` exit codes 0 / 1 / 2
- **E14** serve round-trip matches local conversion (seeded and explicit)
- **E15** the fleet arc all-green → critical → warning, and the volume-guard edge
- **E16** contract + `schema_version`: exports match the real tracker shape (plus the version field), an unknown version (`"9.9"`) is rejected, and a versionless native export still validates

Unit tests: `python3 -m unittest discover -s tests` → **106 hermetic** — fully offline, no network — tests, including a no-network enforcement test (sockets stubbed), hand-mutated exports for every recomputation the validator performs, phantom/dropped alert cases, status-mapping pins, HTML escaping of hostile strings, CLI exit codes, serve round-trips, and **contract tests** pinning the fixture shape to the real `agent-slo-tracker` report shape — including, when the sibling repo is on disk, feeding a genuine live tracker export through this validator (it passes with zero issues).

## Development loop — what the checks actually caught

1. **The validator crashed on the invalid input it exists to reject.** With `target: 1.0` (or the JSON bool trap `target: true`, which Python reads as 1), the per-agent recompute divided by `(1 − target)` and raised `ZeroDivisionError` — a validator that throws on bad data turns a data bug into a stack trace and breaks the CLI's exit-code contract (data error must be exit 2, not a crash). Caught by the target range / bool-trap tests; verified by reverting the fix and watching both tests error again. Fix: an invalid target is reported once at top level, and the deeper recomputes fall back to the default target so they can never divide by zero.
2. **The healthy snapshot wasn't.** The first snapshot was specced with a trailing-hour bad count that put its projected exhaustion at 1.74 days — under the converter's 2-day warning line — so the "healthy" snapshot converted to a warning fleet and contradicted its own story. The build's smoke run surfaced it before the arc assertions were written; the scenario's trailing-hour count was corrected (projection now 2.62 days) and the arc — all-green → critical → warning — is pinned by tests and E15 so a future scenario edit can't quietly re-break it.
3. **Validated design decisions worth keeping.** The validator re-derives the firing alert set from the burn table instead of checking alerts one by one — that single decision makes *both* phantom alerts and silently dropped alerts fail, which a per-alert field check cannot do. The trend delta is rounded to 9 decimals before the ε comparison, copied from the dashboard's own health model (unrounded, `0.92 − 0.90` is `0.020000000000000004` in float and would misread as improving). And conversion validates first and raises on any issue: there is no code path that turns an unvalidated export into a tile.

## Scope and honest limits

- **Deliberately standalone.** This repo stays its own small repo rather than a module inside either sibling — it is deliberately the thin, well-tested seam between the two repos, versioned (`schema_version`) and contract-tested against the tracker's real output shape, so either side can change without silently breaking the join.
- **This repo is the adapter, kept standalone.** `agent-eval-dashboard`'s store ingests envelopes, but it has no registered adapter for `agent-slo-tracker` — registering one there is a small change in *that* repo. Until then, this bridge emits both halves of its contract: the ingest envelope, and the normalized tile records in exactly the shape its health model and renderer already consume.
- **Alerts are success-SLO only** — inherited from the source: the tracker evaluates its two-window rules on the success SLO, and the validator enforces that (an alert claiming the latency SLO is rejected). The latency SLO converts with the same status mapping and could carry its own rules if the tracker grows them.
- **Snapshots are scenario exports, not a stream.** Each is a self-consistent rolling-window export; the bridge converts what it is given and computes trends across whatever snapshots it receives. Nothing here polls, schedules, or persists — the store of record stays the tracker on one side and the dashboard on the other.
- **The API is a demo surface.** It binds 127.0.0.1 only, holds its seeded exports in memory, and resets on restart.

## Why this project

Integration work is where forward-deployed engineering is won or lost: two systems that are each correct can still disagree, and the disagreement lands on the page a customer trusts. This bridge takes the unglamorous position that the converter's job is not to move data but to *adjudicate* it — strict shape, recomputed math, re-derived alerts, evidence on every verdict — so that when the fleet page says warning, the number behind it has already survived an audit.

## License

MIT — see LICENSE.
