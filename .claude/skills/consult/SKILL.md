---
name: consult
description: 'Use when: you were dispatched as a consult. Callers consult on: a design fork, a disputed or second blocking critique finding, a NEW table or identity scheme or merge_key change, a load-bearing /implement under-specification, workflow adaptation, rec synthesis, or a convergence escalation the planning skill''s Escalate rule routes to it. A caller never invokes this skill in its own session: it dispatches a fresh-context subagent (Agent, model: "fable") whose first action invokes this skill.'
---

# consult

The advice consult. One home for the protocol and every trigger; it recommends and decides nothing.

## Dispatch (caller)
Dispatch a fresh-context subagent (`Agent`, `model: "fable"`, an agent type that has the Skill tool). Its brief names the
question and the facts. Its first action is to invoke this skill (persona attribution reads that first Skill call).

## Protocol (consult)
The consult reads (Read/Grep/Glob) but edits nothing, separating advice into "settled consensus" vs "contested" (where
practice diverges from this repo's convention). It recommends and decides nothing, except a convergence escalation the planning skill's Escalate rule routes to it, where it chooses only as that rule allows and its caller records the choice without rejecting it (Decision 287).

On a routed convergence escalation, answer each disputed finding holds, does not hold or cannot determine, citing file:line for each premise you read; apply plan-critique's blocking test (what reaches main if left unfixed, and which control fails first); do not read the plan's `gate_rounds` or `context`, the branch's commit messages or the plan PR. Hand back unruled, as "No ruling: <reason>; missing: <what would settle it>" (on a sign-off fork, add your recommendation), when the anchor or its cited evidence is unreadable, the finding turns on live state, runtime behaviour or cost the repo does not show, ruling needs a sign-off fork (a fix that would change approved scope, tier or a cited Decision), the brief discloses a preferred outcome, or the finding is that the plan weakens verification (Decision 235 cl.1), or the finding is a guard-input (5c) or category-consistency (12p) finding.

## Reconcile (caller)
Adopt / adapt / reject each point, recorded where the caller records the decision (the overseer ledger retires with the
overseer, Decision 247).

## Triggers
No trigger applies past a caller's STOP (implement-scope-boundary.yaml deviation_trigger branch b; Decision 55).
1. A major design decision or design fork in a plan.
2. A disputed blocking critique finding (planning skill Contested-residue consult): a third fresh reading of THIS plan
   against the repo's own Decisions, not industry practice; or any convergence escalation the planning skill's Escalate rule routes to it (oscillation, a third blocking finding on one plan row, a sixth-round REVISE).
3. A second blocking critique finding on one plan row (planning skill; in session only, never an escalation).
4. A NEW table, a NEW identity scheme or a merge_key change (data-modeling-standard.yaml design_time_walk step 8).
5. A load-bearing or novel in-scope under-specification in /implement (implement-scope-boundary.yaml deviation_trigger
   branch a).
6. Workflow adaptation: adapting your own workflow to a methodology gap.
7. Rec synthesis: reconciling related recs into one plan boundary, vs. sequencing already-atomic work.
