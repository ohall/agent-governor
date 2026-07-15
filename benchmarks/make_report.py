"""Build a single self-contained HTML report from benchmark outputs.

Images are base64-embedded so reports/out/index.html opens anywhere with no
server, no docker, and no external assets — the deliberate alternative to a
Grafana stack (see docs/design-decisions.md).

    uv run python -m benchmarks.make_report
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

BENCH_DIR = Path(__file__).parent
OUT_DIR = BENCH_DIR / "out"
CHARTS_DIR = BENCH_DIR / "charts"
REPORT_DIR = Path(__file__).parent.parent / "reports" / "out"

_STYLE = """
body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
       max-width: 980px; margin: 2rem auto; padding: 0 1rem; color: #1a1a2e; }
h1 { font-size: 1.9rem; } h2 { margin-top: 2.5rem; }
img { max-width: 100%; border-radius: 8px; border: 1px solid #e2e2ea; }
table { border-collapse: collapse; margin: 1rem 0; }
td, th { border: 1px solid #d5d5df; padding: 6px 12px; text-align: right; }
th { background: #f3f3f8; }
.note { background: #fff7e0; border-left: 4px solid #e8a33d; padding: 10px 14px;
        border-radius: 4px; margin: 1rem 0; }
footer { margin-top: 3rem; color: #777; font-size: 0.85rem; }
"""


def img_tag(name: str) -> str:
    path = CHARTS_DIR / f"{name}_light.png"
    if not path.exists():
        return f"<p><em>chart missing: run the experiment to generate {path.name}</em></p>"
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f'<img alt="{name}" src="data:image/png;base64,{encoded}">'


def load(name: str) -> dict | None:
    path = OUT_DIR / name
    return json.loads(path.read_text()) if path.exists() else None


def exp1_section() -> str:
    data = load("exp1_summary.json")
    if data is None:
        return "<p><em>exp1 not yet run</em></p>"
    rows = "".join(
        f"<tr><td>{r['p']:.2f}</td><td>{r['n']}</td><td>{r['measured_success']:.3f}</td>"
        f"<td>{r['theoretical_pn']:.3f}</td>"
        f"<td>{abs(r['measured_success'] - r['theoretical_pn']):.3f}</td></tr>"
        for r in data["rows"]
    )
    return f"""
    <p><strong>Measured.</strong> {data["runs_per_cell"]} seeded sim-mode runs per cell;
    success scored against estate ground truth. The measured points track the
    theoretical p<sup>n</sup> curves because the math is real, not because the chart
    was drawn that way.</p>
    {img_tag("exp1_pn_decay")}
    <table><tr><th>p</th><th>n</th><th>measured</th><th>p^n</th><th>|Δ|</th></tr>{rows}</table>
    """


def exp2_section() -> str:
    data = load("exp2_summary.json")
    if data is None:
        return "<p><em>exp2 not yet run</em></p>"
    rows = "".join(
        f"<tr><td style='text-align:left'>{mode}</td><td>{r['p50_ms']}</td>"
        f"<td>{r['p95_ms']}</td><td>{r['p99_ms']}</td>"
        f"<td>{r['throughput_actions_per_s']}</td></tr>"
        for mode, r in data["results"].items()
    )
    return f"""
    <div class="note"><strong>Calibration note (read before quoting):</strong> the three
    check latencies are synthetic sleeps calibrated to published ranges per check family
    (rules 5-20ms, classifier 50-150ms, LLM-judge 300-600ms — see
    <code>policies/guardrails.yaml</code>). The sequential-vs-parallel <em>delta</em> is
    real measured orchestration behavior.</div>
    {img_tag("exp2_guardrail_latency")}
    <table><tr><th>mode</th><th>p50 (ms)</th><th>p95 (ms)</th><th>p99 (ms)</th>
    <th>actions/s</th></tr>{rows}</table>
    <p>{data["actions"]} identical benign actions per mode, concurrency
    {data["concurrency"]}.</p>
    """


def exp3_section() -> str:
    data = load("exp3_summary.json")
    if data is None:
        return "<p><em>exp3 not yet run</em></p>"
    changes = "".join(
        f"<li><code>{c['kind']}</code> — {json.dumps(c['payload'])}</li>" for c in data["changes"]
    )
    return f"""
    <div class="note"><strong>Illustrative run:</strong> a scripted fault schedule against
    the demo budget profile (compressed thresholds, banner-labeled). This chart shows the
    enforcement <em>mechanism</em>; it is not a field failure-rate measurement.</div>
    {img_tag("exp3_governance_timeline")}
    <p>{data["actions"]} actions; final state: level
    {"FROZEN" if data["frozen"] else "L" + str(data["final_level"])}.</p>
    <ul>{changes}</ul>
    """


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>agent-governor — benchmark report</title>
<style>{_STYLE}</style></head><body>
<h1>agent-governor — benchmark report</h1>
<p>Reference implementation of the <strong>autonomy budget</strong>: error budgets
applied to AI-agent agency. Every number below regenerates from seeded runs via
<code>make bench &amp;&amp; make report</code>.</p>

<h2>Experiment 1 — reliability decays as p<sup>n</sup> (measured)</h2>
{exp1_section()}

<h2>Experiment 2 — the latency cost of guardrails (calibrated + measured delta)</h2>
{exp2_section()}

<h2>Experiment 3 — the budget burning down (illustrative)</h2>
{exp3_section()}

<footer>agent-governor · MIT · built for LeadDev LDX3 New York 2026 ·
generated {generated}</footer>
</body></html>
"""
    out = REPORT_DIR / "index.html"
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
