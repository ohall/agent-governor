# Governor

[![CI](https://github.com/sandeepmb/agent-governor/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeepmb/agent-governor/actions/workflows/ci.yml)

**Autonomy is earned, measured, and revocable — or it isn't governance.**

Everyone now agrees AI agents should "earn trust." Almost nobody has shipped the part that
matters: the mechanism that **takes autonomy away**. Governor is a working reference
implementation of the **autonomy budget** — error budgets applied to agency.

Every agent action flows through a governance gateway that knows **who** the agent is
(short-lived JWT identity bound to a manifest with a *named human owner*), **what** it may do —
a **three-tier trust model** classifies every action by **blast radius** (read-only /
reversible-write / always-approve, conditioned on the target), crossed with the agent's
*current* L1–L5 autonomy level into an automation-vs-human-approval decision matrix, with five
always-approve categories that hard-gate on a human — **whether** it is behaving (guardrail
pipelines: rules → classifier → judge, sequential or parallel), and **how much trust it has
left** — six SLOs computed over a rolling window: task completion, tool-call success, recovery
rate, p99 latency, guardrail trip rate, and a trace-grounded score.

Breach the budget and the agent is **automatically demoted one level** — the same call that ran
autonomously an hour ago now waits for a human. Sustain a clean record and it earns its way
back up, never above its registered maximum. Trip a catastrophic signal — a guardrail-trip
burst, or reaching for an always-approve action *while already demoted* — and a **circuit
breaker** freezes it entirely. And for any action ever taken, the trace store alone answers the auditor's
question: *under what governance regime was this action authorized?* — which agent, whose
owner, which level, which policy version, which path, approved by whom, with what stated
reasoning. If a record can't answer that, the logging is wrong.

> Built for **LeadDev LDX3 New York 2026** — *"The Accountability Gap: Engineering Governance
> for Autonomous AI."* MIT licensed. Runs on a laptop with **zero API keys, no Docker, no
> external services**.

## The 5-minute quickstart

Requires [uv](https://docs.astral.sh/uv/). Everything below is keyless and deterministic.

```bash
git clone https://github.com/sandeepmb/agent-governor && cd agent-governor
uv sync
make test     # 168 tests, ~10s
make demo     # the full story in ~2 minutes (interactive approval — see below)
```

`make demo` walks the whole arc: an agent registers at L4 with a named owner → remediates an
incident end-to-end with every action authorized and audited → hits the human gate on a PII
deletion (approve it live from a second terminal with `warden approvals approve <id> --by you`,
or pass `--auto`) → fault injection and an adversarial prompt burn the budget with **live
demotions printed as they happen** → the demoted agent reaches for a production deploy to
payments and trips the kill-switch → `warden audit` reconstructs the approved deletion from the trace store alone.

Per-milestone stories live in [`examples/`](examples/), and the CLI works cross-process:

```bash
uv run warden approvals list --db .governor/approvals.sqlite3
uv run warden audit ACT-xxxx --db .governor/audit.sqlite3
uv run warden budget ops-remediator-01 --db .governor/audit.sqlite3
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full diagram and request lifecycle.

```
agent (LangGraph, sim|live) ──tool call + reasoning──▶ warden gateway ──▶ mockops estate
                                │ identity → classify → authorize → guardrails │   + fault injection
                                ▼                                              
                    audit store (actions · spans · events) ⇄ budget engine (6 SLOs)
                                ▲                                   │ demote / promote / freeze
                        warden audit|budget CLI ◀── human owner ──▶ warden approvals
```

## Observability

Every stage of every action emits a span; span names follow OpenTelemetry GenAI semantic
conventions, persist to the SQLite trace store (the source of truth for `warden audit`), and —
with `WARDEN_OTEL_ENABLED=1` — export as real OTel span trees (console by default,
`WARDEN_OTEL_ENDPOINT=http://host:4318` for OTLP via the `otlp` extra). Root spans carry the
full authorization story as `governor.*` attributes, so the audit trail lands in whatever
tracing backend your legal and security teams already trust.

## Benchmarks — reproduce every number

```bash
make bench    # exp1 (~2 min) + exp2 (~35s) + exp3 (~5s); CSVs + light/dark PNGs
make report   # single self-contained HTML: reports/out/index.html
```

| # | Question | Epistemic standing |
|---|---|---|
| 1 | How fast does task reliability decay with length? | **Measured**: 300 seeded sim runs per (p, n) cell vs theoretical p^n |
| 2 | What do guardrails cost in latency? | **Calibrated synthetics** per check (labeled on the chart); the sequential-vs-parallel **delta** is measured orchestration behavior |
| 3 | What does a budget burning down look like? | **Illustrative** scripted run — shows the mechanism, not a field failure rate |

Honesty labels are baked into the chart images themselves; the calibration rationale lives in
[policies/guardrails.yaml](policies/guardrails.yaml) and
[docs/design-decisions.md](docs/design-decisions.md).

## Repo layout

```
src/agent_governor/
  mockops/     deterministic mock ITSM estate + seeded fault injection
  agent/       LangGraph remediation agent; sim mode with per-step success p
  warden/      the Governor: identity, policy, authz, guardrails, approvals,
               audit store, autonomy budget, CLI
policies/      classification.yaml · guardrails.yaml · budget/{demo,realistic}.yaml
benchmarks/    experiments 1-3, chart kit, HTML report generator
examples/      m2-m5 runnable stories + demo.py
docs/          architecture.md · design-decisions.md · stakeholder-briefing.md
```

## Non-goals (deliberately out of scope)

Multi-agent orchestration · a real MCP proxy (the gateway pattern generalizes; not built) ·
SPIFFE/SPIRE (the JWT flow simulates the short-lived-credential pattern) · production-grade
auth · RBAC UI · PII detection beyond the regex guardrail · Kubernetes · non-Anthropic model
providers beyond the policy seam · Grafana (a static report ships instead — see
design-decisions).

## License

MIT © 2026 Sandeep Bharadwaj Mannapur · built for LeadDev LDX3 New York 2026
