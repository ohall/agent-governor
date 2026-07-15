"""Experiment 1 — p^n reliability decay, MEASURED.

Sweep per-step success probability p x task length n; >=300 seeded sim runs
per cell; compare measured end-to-end task success to the theoretical p^n.
This is genuinely emergent data: the sim commits realistic per-step errors
and success is scored from ground truth, so measured points hugging the
curves is evidence, not decoration.

    uv run python benchmarks/exp1_pn_decay.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_governor.agent.models import SimConfig
from agent_governor.agent.runner import BENCHMARK_MANIFEST, AgentRunner
from agent_governor.mockops.app import create_app
from agent_governor.mockops.config import MockOpsSettings
from agent_governor.warden.gateway import Gateway
from benchmarks.chartkit import PALETTE, save_variants

P_VALUES = [0.99, 0.95, 0.90, 0.85]
N_VALUES = [3, 5, 10, 15]
RUNS_PER_CELL = 300
OUT_DIR = Path(__file__).parent / "out"

FOOTNOTE = (
    f"MEASURED: {RUNS_PER_CELL} seeded sim-mode runs per point "
    "(deterministic scripted policy, faults off)"
)


def run_cell(p: float, n: int) -> float:
    client = TestClient(create_app(MockOpsSettings(seed=42)))
    runner = AgentRunner(
        Gateway(client),
        SimConfig(p=p, seed=int(p * 100) * 100 + n, plan_length=n),
        manifest=BENCHMARK_MANIFEST,
    )
    outcomes = runner.run_batch(
        "INC-1001", RUNS_PER_CELL, reset_estate=lambda: client.post("/admin/reset")
    )
    return sum(o.success for o in outcomes) / len(outcomes)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    summary = OUT_DIR / "exp1_summary.json"
    if "--chart-only" in sys.argv and summary.exists():
        rows = json.loads(summary.read_text())["rows"]
        render(rows)
        return
    rows = []
    for p in P_VALUES:
        for n in N_VALUES:
            t0 = time.perf_counter()
            measured = run_cell(p, n)
            rows.append(
                {
                    "p": p,
                    "n": n,
                    "runs": RUNS_PER_CELL,
                    "measured_success": round(measured, 4),
                    "theoretical_pn": round(p**n, 4),
                }
            )
            print(
                f"p={p:.2f} n={n:>2}  measured={measured:.3f}  theory={p**n:.3f}"
                f"  ({time.perf_counter() - t0:.1f}s)"
            )

    csv_path = OUT_DIR / "exp1_pn_decay.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (OUT_DIR / "exp1_summary.json").write_text(
        json.dumps({"rows": rows, "runs_per_cell": RUNS_PER_CELL}, indent=2)
    )
    render(rows)
    print(f"total {time.perf_counter() - started:.0f}s")


def render(rows: list[dict[str, float | int]]) -> None:
    def build():  # type: ignore[no-untyped-def]
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))
        ns = list(range(1, 17))
        for color, p in zip(PALETTE, P_VALUES, strict=False):
            ax.plot(
                ns,
                [p**i for i in ns],
                "--",
                color=color,
                linewidth=1.6,
                alpha=0.8,
                label=f"theory p={p:.2f}",
            )
            measured = [r for r in rows if r["p"] == p]
            ax.plot(
                [r["n"] for r in measured],
                [r["measured_success"] for r in measured],
                "o",
                color=color,
                markersize=10,
                label=f"measured p={p:.2f}",
            )
        ax.annotate(
            "95% per step ≈ coin flip by 10 steps",
            xy=(10, 0.95**10),
            xytext=(10.4, 0.82),
            arrowprops={"arrowstyle": "->", "color": "#888888"},
            fontsize=12,
        )
        ax.set_xlabel("task length n (tool-call steps)")
        ax.set_ylabel("end-to-end task success rate")
        ax.set_title("Reliability decays as p^n — measured vs theory")
        ax.set_ylim(0, 1.02)
        ax.set_xlim(1, 16)
        ax.legend(ncols=2, frameon=False)
        return fig

    paths = save_variants(build, "exp1_pn_decay", FOOTNOTE)
    for path in paths:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
