# Governor

**Autonomy is earned, measured, and revocable — or it isn't governance.**

Governor is a reference implementation of the *Autonomy Budget*: error budgets applied to AI-agent
agency. Every action an agent takes flows through a governance gateway that knows who the agent is
(short-lived identity), what it may do at its **current** autonomy level (L1–L5, tiered
authorization with hard human gates), whether it is behaving (guardrail pipelines), and how much
trust it has left (six SLOs over a rolling window). Breach the budget and the agent is
**automatically demoted** one level; keep a clean record and it earns its way back up; trip a
catastrophic signal and a kill-switch freezes it entirely. Every decision is reconstructable after
the fact: `warden audit <action_id>` answers "under what governance regime was this action
authorized?" from the trace store alone.

> Status: **early development** — building milestone M1 of 6. APIs will change.
> Built for [LeadDev LDX3 New York 2026](https://leaddev.com/ldx3-new-york): *"The Accountability
> Gap: Engineering Governance for Autonomous AI."*

## What's here so far

| Component | Status | Purpose |
|---|---|---|
| `mockops` | ✅ M1 | Deterministic mock ITSM estate + controllable fault injection |
| `agent` | ✅ M2 | LangGraph ops-remediation agent, sim mode with dial-a-mistake-rate `p` (live mode later) |
| `warden` | 🚧 M2–M5 | The governance gateway — pass-through chokepoint live (manifest scope + action log); identity, authorization, guardrails, budget loop landing next |
| `benchmarks` | ⏳ M6 | Reproducible experiments: p^n decay, guardrail latency, demotion timeline |

Try the sim agent (in-process, no server needed):

```bash
uv run python examples/m2_sim_run.py
```

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). No API keys, no Docker, no external services.

```bash
uv sync
make test          # full test suite
make run-mockops   # mock ops estate on http://127.0.0.1:8010 (docs at /docs)
```

Try the fault-injection layer:

```bash
curl -s localhost:8010/incidents/INC-1001 | head -c 200          # clean
curl -si -H 'X-Inject-Fault: rate_limit' localhost:8010/incidents/INC-1001 | head -1   # 429
curl -s -H 'X-Inject-Fault: wrong_data' localhost:8010/incidents/INC-1001 | head -c 200 # one field silently wrong
```

Injected responses carry `X-Fault-Injected: <kind>` so harnesses have ground truth for
recovery-rate measurement. Probabilistic injection is seeded — same seed, same fault sequence.

## Design principles

- **Keyless by default.** Everything except live-mode LLM reasoning runs with zero API keys.
- **Deterministic by default.** Seeded fixtures, seeded faults, seeded sim agent — benchmarks are
  reproducible or they are marketing.
- **Honest numbers.** Measured results are labeled measured; calibrated simulations are labeled
  calibrated, on the chart itself.

## License

MIT © 2026 Sandeep Bharadwaj Mannapur
