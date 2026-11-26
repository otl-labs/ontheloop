---
name: consult
description: 'Use when: you were dispatched as a consult. Callers consult on: a design fork, a disputed or second blocking critique finding, a NEW table or identity scheme or merge_key change, a load-bearing /implement under-specification, workflow adaptation, rec synthesis, or a convergence escalation routed to it. A caller never invokes this skill in its own session: it dispatches a fresh-context subagent (Agent, the consult-class model per project.capability_class.classes) whose first action invokes this skill.'
---

# consult

The advice consult. One home for the protocol and every trigger; it recommends and decides nothing outside the Convergence section.

## Dispatch (caller)
Dispatch a fresh-context subagent (`Agent`, `model:` the consult class's model, an agent type that has the Skill tool).
The consult class's model is project-configuration.yaml row `project.capability_class.classes`,
`defaults.local.consult.claude_code_model` (`ontheloop/platform.yaml` once it exists); read it, never inherit the parent's:
`bin/venv-python -c "import yaml;r=yaml.safe_load(open('docs/contracts/project-configuration.yaml'))['settings'];print(next(x for x in r if x['id']=='project.capability_class.classes')['defaults']['local']['consult']['claude_code_model'])"`.
Its brief names the question and the facts. Its first action is to invoke this skill (persona attribution reads that first Skill call).

## Protocol (consult)
The consult reads (Read/Grep/Glob) but edits nothing, separating advice into "settled consensus" vs "contested" (where
practice diverges from this repo's convention). It recommends and decides nothing, except a convergence escalation routed to it, where it chooses only as the Convergence section allows and its caller records the choice without rejecting it (Decision 287).

## Reconcile (caller)
Adopt / adapt / reject each point, recorded where the caller records the decision (the overseer ledger retires with the
overseer, Decision 247).

## Convergence (Decision 287)
Route (caller): for the plan-critique gate alone (the Decision Scout and Report Critique gates escalate to the human), a convergence escalation (any trigger in the planning skill's Escalate rule but the last) goes to a consult, which may only rule whether each disputed finding holds (where it holds: fix it, disposition fixed; where it does not: write its evidence at the finding's anchor; it stays disputed) and choose one more round, once per plan. In place of the Escalate rule's presentation, its brief gives each disputed finding (for a trigger with none, the findings at the escalation's anchors) verbatim with its tag and anchor, quotes each anchor's field text, and gives both positions unattributed, with their cited evidence; it withholds the round history, the trigger and every recommendation. It decides once per finding: a later escalation at an anchor it decided, any escalation after its one more round, the last trigger, a fork on taste, cost or intent, a finding that the plan weakens verification (Decision 235 cl.1), and every other choice go to the human. A consult that errors, returns no ruling or cannot verify a finding hands the escalation back to the human; a partial hand-back is the human's whole, and the consult's rulings there are advice and decide no anchor. Record the row's `escalation_choice` and `escalation_decider: consult`. Tell the human of a consult's choice at once and list each in the plan PR body; they may override it until the plan PR merges, recorded on the consult's row as `overrides_consult: true` with their `override_choice`, which then counts as the row's choice. An override made after a later round is also the human's answer on the latest plan-critique row (`escalated: true`, that `escalation_choice`, `escalation_decider: operator`), and the loop follows it from there; after the series' final PROCEED, record it on the consult's row alone, and an override there other than accept-with-deferral reopens the loop under that choice. Ruling (consult): answer each disputed finding holds, does not hold or cannot determine, citing file:line for each premise you read; apply plan-critique's blocking test (what reaches main if left unfixed, and which control fails first); do not read the plan's `gate_rounds` or `context`, the branch's commit messages or the plan PR. Hand back unruled, as "No ruling: <reason>; missing: <what would settle it>" (on a sign-off fork, add your recommendation), when the anchor or its cited evidence is unreadable, the finding turns on live state, runtime behaviour or cost the repo does not show, ruling needs a sign-off fork, the brief discloses a preferred outcome, or the finding is a guard-input (5c) or category-consistency (12p) finding, a finding that the plan weakens verification (Decision 235 cl.1) or a fix that would change approved scope, tier or a cited Decision. On a second blocking finding on one plan row (in session only) the consult only advises. Revisit after 10 contested escalations a consult decided (every row with `escalation_decider: consult`): return them to the human if the human overrode (`overrides_consult`) or re-escalated (a later escalated row in that plan that the human decided) 4 or more.

## Triggers
No trigger applies past a caller's STOP (implement-scope-boundary.yaml deviation_trigger branch b; Decision 55).
1. A major design decision or design fork in a plan.
2. A disputed blocking critique finding (planning skill Contested-residue consult): a third fresh reading of THIS plan
   against the repo's own Decisions, not industry practice; or any convergence escalation routed to it (the Convergence section).
3. A second blocking critique finding on one plan row (planning skill; in session only, never an escalation).
4. A NEW table, a NEW identity scheme or a merge_key change (data-modeling-standard.yaml design_time_walk step 8).
5. A load-bearing or novel in-scope under-specification in /implement (implement-scope-boundary.yaml deviation_trigger
   branch a).
6. Workflow adaptation: adapting your own workflow to a methodology gap.
7. Rec synthesis: reconciling related recs into one plan boundary, vs. sequencing already-atomic work.
