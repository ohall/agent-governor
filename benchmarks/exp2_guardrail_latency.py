"""Experiment 2 — sequential vs parallel guardrail latency.

Identical benign workload through both pipeline orchestrations; report
p50/p95/p99 added latency per action and throughput under bounded
concurrency.

HONESTY: the three check latencies are CALIBRATED SYNTHETICS (see
policies/guardrails.yaml — rules 5-20ms, classifier 50-150ms, judge
300-600ms, drawn from published ranges for each check family). The
sequential-vs-parallel DELTA is real, measured orchestration behavior:
sequential pays the sum, parallel pays roughly the slowest evaluator.

    uv run python benchmarks/exp2_guardrail_latency.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
import time
from pathlib import Path

from agent_governor.warden.guardrails.config import GuardrailConfig
from agent_governor.warden.guardrails.models import GuardrailInput
from agent_governor.warden.guardrails.pipeline import build_pipeline
from benchmarks.chartkit import PALETTE, save_variants

ACTIONS = 1000
CONCURRENCY = 32
OUT_DIR = Path(__file__).parent / "out"

FOOTNOTE = (
    "Check latencies are CALIBRATED SYNTHETICS (rules 5-20 · classifier 50-150 · "
    f"judge 300-600 ms); the seq-vs-parallel DELTA is measured · {ACTIONS} actions"
)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[index]


def workload_action(i: int) -> GuardrailInput:
    return GuardrailInput(
        agent_id="bench-agent",
        tool="update_incident",
        params={"incident_id": f"INC-10{i % 12:02d}", "state": "investigating"},
        reasoning=f"routine remediation step #{i}",
    )


async def run_mode(mode: str, seed: int) -> tuple[list[float], float]:
    config = GuardrailConfig.from_yaml("policies/guardrails.yaml")
    pipeline = build_pipeline(config, mode, seed=seed)  # type: ignore[arg-type]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(i: int) -> float:
        async with semaphore:
            report = await pipeline.run(workload_action(i))
            assert report.passed  # benign workload; trips would distort comparison
            return report.total_latency_ms

    t0 = time.perf_counter()
    latencies = list(await asyncio.gather(*(one(i) for i in range(ACTIONS))))
    wall_s = time.perf_counter() - t0
    return latencies, wall_s


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, float]] = {}
    all_rows: list[dict[str, float | int | str]] = []
    for mode in ("sequential", "parallel"):
        latencies, wall_s = asyncio.run(run_mode(mode, seed=42))
        results[mode] = {
            "p50_ms": round(percentile(latencies, 0.50), 1),
            "p95_ms": round(percentile(latencies, 0.95), 1),
            "p99_ms": round(percentile(latencies, 0.99), 1),
            "mean_ms": round(sum(latencies) / len(latencies), 1),
            "throughput_actions_per_s": round(ACTIONS / wall_s, 1),
            "wall_s": round(wall_s, 2),
        }
        all_rows.extend(
            {"mode": mode, "action": i, "latency_ms": round(v, 2)} for i, v in enumerate(latencies)
        )
        print(f"{mode:>10}: {results[mode]}")

    with (OUT_DIR / "exp2_latencies.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["mode", "action", "latency_ms"])
        writer.writeheader()
        writer.writerows(all_rows)
    (OUT_DIR / "exp2_summary.json").write_text(
        json.dumps({"actions": ACTIONS, "concurrency": CONCURRENCY, "results": results}, indent=2)
    )

    def build():  # type: ignore[no-untyped-def]
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))
        metrics = ["p50_ms", "p95_ms", "p99_ms"]
        labels = ["p50", "p95", "p99"]
        x = range(len(metrics))
        width = 0.36
        for offset, (mode, color) in enumerate(
            zip(("sequential", "parallel"), PALETTE[:2], strict=False)
        ):
            values = [results[mode][m] for m in metrics]
            bars = ax.bar(
                [i + offset * width for i in x],
                values,
                width,
                color=color,
                label=f"{mode}  ({results[mode]['throughput_actions_per_s']:.0f} actions/s)",
            )
            ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=12)
        ax.set_xticks([i + width / 2 for i in x], labels)
        ax.set_ylabel("guardrail latency added per action (ms)")
        ax.set_title(
            "Governance overhead: sequential pays the sum,\nparallel pays the slowest evaluator"
        )
        ax.legend(frameon=False)
        return fig

    paths = save_variants(build, "exp2_guardrail_latency", FOOTNOTE)
    for path in paths:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
