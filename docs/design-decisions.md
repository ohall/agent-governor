# Design decisions

The honest ledger: every place we traded realism for reproducibility (or
scope for the deadline), written down so nobody has to discover it.

## Naming

**agent-governor / "Governor."** James Watt's 1788 centrifugal governor is a
mechanism that senses a machine's speed and automatically takes power away
before it destroys itself — precisely this project's job for AI agents. The
gateway package and CLI keep the working name `warden`.

## Honesty labels on benchmarks

Three experiments, three different epistemic standings — each labeled on the
chart itself so the caveat cannot drift away from the artifact:

1. **Exp 1 (p^n decay): measured.** Seeded sim runs scored against estate
   ground truth. The points track p^n because the math is real.
2. **Exp 2 (guardrail latency): calibrated + measured delta.** The three
   check latencies are synthetic sleeps calibrated to published ranges per
   check family (`policies/guardrails.yaml`). The sequential-vs-parallel
   *delta* and throughput difference are real orchestration behavior.
3. **Exp 3 (governance timeline): illustrative.** A scripted fault schedule
   showing the enforcement mechanism. Never quote it as a field failure rate.

## OpenTelemetry: SQLite is truth, OTel is interop

The SQLite trace store is the source of truth (`warden audit` must work on a
laptop with zero infrastructure). OTel export (`warden/otel.py`) mirrors
every action into real spans — GenAI semantic conventions plus `governor.*`
authorization attributes — to the console or any OTLP endpoint, because the
observability promise only lands when the trail appears in the backend your
org already trusts. OTLP transport is an extra (`agent-governor[otlp]`) to
keep the default install lean.

## Static HTML report instead of Grafana

A conference demo and a GitHub reviewer share one need: zero-setup results.
`make report` emits a single self-contained HTML file (base64-embedded
charts). Grafana + Prometheus would add the most fragile moving parts to the
highest-stakes moment (live stage, conference wifi) while making reviewers
run docker to see a chart. The gateway's audit store keeps every number a
dashboard would need; a `/metrics` exporter is a straightforward future step.

## In-memory shared state; Redis as a seam

The parallel guardrail pipeline coordinates through a `SharedState` protocol.
In-process asyncio doesn't need Redis to prove the latency property — the
delta is physics (sum vs max), not deployment. The protocol exists so a
Redis implementation slots in when checks span processes; adding the
dependency by default would break the "runs with zero setup" promise.

## Demo budget profile is compressed — and says so

`policies/budget/demo.yaml` (30-action window, demote on any breach) exists
so an audience can watch L4 → L3 → L2 → kill-switch in minutes. Every run
prints a banner naming the profile as compressed and non-production;
`realistic.yaml` (200 actions / 24h) is the reference configuration.

## Synchronous budget tick

Budget evaluation runs synchronously after each gateway action instead of on
a background thread. Determinism beats realism for a system whose outputs
are screen-recorded and re-run by reviewers: same inputs, same demotion at
the same action number, every time. A background evaluator is a deployment
choice the engine's API already supports.

## Benchmarks run the agent at L5 (labeled)

Reliability benchmarks (exp 1) register the sim agent at L5 Observer via an
explicitly named `BENCHMARK_MANIFEST` because approval pauses would corrupt
the measurement — those runs measure the *agent*, not the governance
friction. L5 remains demo/benchmark-only and is never a shipped default.

## Guardrails run on the autonomous path only

An action a named human explicitly approved skips the advisory checks: the
human is the higher authority, and that decision is itself audited
(`path=human_approved`, `decided_by=<name>`). Hard authorization (scope,
level, always-approve gates) is never skippable.

## Authorization precedes guardrails

An L1/L2 agent's queued action never reaches the guardrail pipeline — you
cannot trip a check with an action you were never authorized to attempt.
Consequence: guardrail trip *rate* is only meaningful at levels where the
agent still executes autonomously.

## Budget state over CLI, not HTTP (yet)

The spec sketched `/agents/{id}/budget`. Budget snapshots and level events
persist to the audit store, so `warden budget <agent>` answers the same
question cross-process without hosting a service. Mounting the same query
behind FastAPI is mechanical once warden runs as a standalone service.

## Timeout faults respond fast

mockops' TIMEOUT fault returns 504 after a configurable ~50ms delay rather
than a real 30s hang, so 4,800-task benchmark runs stay fast. The delay is
config (`MOCKOPS_FAULTS__TIMEOUT_DELAY_S`), not a claim about real timeout
behavior.

## Live LLM mode is a seam, not a milestone

Sim mode powers every reproducible number. Live Anthropic reasoning plugs
into the same LangGraph step loop and gateway interface; it ships as
credibility, not as the spine, and nothing in the benchmarks depends on it.
