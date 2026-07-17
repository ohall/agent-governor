# Stakeholder briefing template: agentic governance

A fill-in-the-blank framework for explaining autonomous-agent governance to
legal, security, and executive stakeholders — people who demand
accountability without needing the technology. Each section is one
conversation: the question that stakeholder actually cares about, the
30-second answer, and the artifact from this system that proves it.

Copy this file, replace the ⟨placeholders⟩, delete what you don't need.

---

## The one-paragraph position (everyone)

> We run ⟨N⟩ autonomous agents in ⟨domain⟩. Every agent has a **named human
> owner**, operates at an explicit **autonomy level** it can lose
> automatically when its measured reliability drops, cannot touch
> ⟨production deploys / customer data / payments / permissions /
> external comms⟩ without an explicit human approval, and every action it
> takes is reconstructable after the fact: who authorized it, at what trust
> level, under which policy version, approved by whom. Autonomy here is
> earned, measured, and revocable.

---

## For legal / compliance

**Their question:** *"When the agent does something wrong, can we prove who
was accountable — and will that record survive discovery?"*

**The answer:** Accountability is structural, not reconstructed after the
fact. Every action record contains: the agent's identity and its named human
owner, the autonomy level at execution time, the policy version that
classified the action, the authorization path (autonomous / queued /
human-approved / blocked), the approver's name where a human decided, and
the agent's stated reasoning.

**The artifact:** run `warden audit <action_id>` on any action — it prints
the complete authorization story from the trace store alone. High-risk
categories (⟨data deletion, external communications, …⟩) *cannot* execute
without a named human's recorded approval, and an unanswered approval
auto-denies after ⟨30 minutes⟩ — silence is never consent.

**The sentence to reuse:** "Evidence isn't the same as accountability; our
records answer *under what governance regime was this action authorized*,
not just *what happened*."

---

## For security

**Their question:** *"What's the blast radius when — not if — an agent is
compromised or goes wrong?"*

**The answer:** Three independent bounds:
1. **Identity:** agents run on their own short-lived credentials
   (⟨10-minute⟩ tokens scoped to an explicit tool manifest) — never on a
   human's token or a shared service account. A stolen credential expires in
   minutes and can't reach tools outside the manifest.
2. **The three-tier trust model:** every action classifies by blast radius —
   read-only, reversible-write, or always-approve — *conditioned on the
   target* (deploying a sandbox ≠ deploying ⟨tier-1 payment systems⟩). The
   five always-approve categories hard-gate on a human at every normal
   autonomy level.
3. **Circuit breakers:** a burst of guardrail trips, or an attempt to use an
   always-approve action while already demoted, freezes the agent entirely —
   every subsequent call denied, event recorded, owner notified.

**The artifact:** the demo (`make demo`): watch a prompt-injected
instruction get blocked, watch automatic demotion, watch the freeze.

---

## For executives

**Their question:** *"How do we get the productivity without betting the
company — and how do we know it's working?"*

**The answer:** The same way we run reliable services: SLOs and error
budgets, applied to agent autonomy. Agents are scored continuously on
⟨six⟩ measured dimensions (task completion, tool-call success, recovery,
latency, guardrail trips, trace quality). Agents that perform earn more
autonomy — fewer human approvals, more throughput. Agents that degrade lose
autonomy *automatically*, before an incident, not after. Humans review the
exceptions, not every action — which is why this scales where
approve-everything governance doesn't.

**The numbers to show:** the reliability curve (an agent that's 95% right
per step fails ~40% of ten-step tasks — that's why per-action governance
matters), the current fleet's budget dashboard (`warden budget <agent>`),
and the demotion/promotion event history.

**The sentence to reuse:** "Observability tells you after; enforcement
stops it before. We bought the 'before'."

---

## The three commitments (close every briefing with these)

1. **No orphaned agents.** Every agent has a named human owner; ownership
   transfer is an explicit offboarding step.
2. **No silent escalation.** An agent's autonomy changes only through the
   measured budget loop or a recorded human decision — both audited, both
   notified.
3. **No unanswerable actions.** If we cannot reconstruct who authorized an
   action and under what regime, we treat that as a logging defect with the
   same severity as the action failing.
