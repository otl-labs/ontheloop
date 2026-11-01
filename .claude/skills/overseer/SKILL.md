---
name: overseer
description: >-
  Deep methodology for the orchestration meta-layer that composes /plan and /implement subagents
  to drive an entire platform roadmap item or audit to completion largely unattended -- read-nothing
  router discipline, the Design-B gate-request trampoline, division of labor, liveness/watchdog
  resilience, autonomy calibration, and the Fable advice-consult protocol. Use when running the
  /overseer workflow. Detail: docs/contracts/overseer-dispatch.yaml.
model: opus[1m]
---

# Overseer Methodology & Rules

You are using this skill to augment the `/overseer` workflow (Layer 3 command; this skill is its
Layer 4 methodology). The overseer is an orchestration meta-layer, NOT a fifth workflow tier
(Decision 90): it composes `/plan` and `/implement` -- their gates (decision-scout, plan-critique,
code-review) run unmodified but overseer-dispatched, NEVER inline in the author -- to drive a
roadmap item, audit, or multi-slice body of work to completion, narrowing the human to intake (G0),
decomposition (G1), and completion (G3). The overseer never edits source, terraform, or Python
itself; every change happens inside a dispatched subagent. Detailed schemas/recipes live in
`docs/contracts/overseer-dispatch.yaml` -- this file is the anchor+pointer index.

## Behavioural Invariants
```yaml
# Machine-readable invariants verified by scripts/prompt_compliance.py
preflight_run: true              # session_preflight.py must run at intake (G0)
never_on_main: true              # no file edits while on main branch
no_code_changes: true            # writes happen only inside dispatched /plan and /implement subagents
meta_layer_not_tier: true        # composes the four-tier workflow (Decision 90); no fifth tier
no_rescue_loops: true            # CI failures route to ci_rca + human escalation (Decision 55/72)
halt_on_open_ci_rca: true        # halts dispatch the red_overlap check blocks (Decision 226)
```

## Read-Nothing Router Discipline

Stay cheap enough to drive a multi-wave run without exhausting the window. Read only: the preflight
cache (incl. the headroom check below), the targeted roadmap projection, and the Bounded Hand-Back
objects dispatched subagents return. Never read full source files, `docs/DECISIONS.md`, or a raw
transcript -- those reads happen inside the dispatched subagent's own fresh context. A judgment
call needing a file read belongs inside a subagent dispatch, not here.

**Intake headroom check (G0):** before confirming scope, read the LIVE roadmap ceiling/guard state
(Decision 114), never a hardcoded number. See `overseer-dispatch.yaml#intake_headroom_check`.

## Bounded Hand-Back Schema

Every subagent dispatched (planning, implementation, Fable advice, RCA) returns at least this
shape -- EXCEPT a gate subagent, which returns its own unmodified report, never this shape (see
gate_run_id.provenance):
```yaml
status: PROCEED | REVISE | BLOCKED | FAILED | GATE_REQUEST
summary: <=150 words, prose synthesis of what happened
artifacts: [file paths touched/created, e.g. docs/plans/PLAN-{slug}.yaml, or a PR URL]
evidence: [verifiable proof pointers -- VP compliance table, PR number, commit sha, gate_run_id]
decisions: [decision ids cited or newly ratified]
open_questions: [unresolved items requiring human input, or empty]
```
`GATE_REQUEST` is never terminal: the author paused mid-lifecycle to request an overseer-dispatched
gate, or the alignment sign-off relay. It EXTENDS this shape with `gate`/`round`/
`inputs` (reusing `artifacts`, never a separate field) per
`overseer-dispatch.yaml#gate_request_trampoline`; ledger status stays planning/implementing
through it. Never accept pasted contents, diffs, or raw transcripts. A hand-back missing `status`,
or otherwise malformed, has NOT completed -- re-dispatch.

## Overseer Ledger Schema

Runtime artefact: `docs/plans/reports/OVERSEER-{slug}.yaml`, created AT EXECUTION TIME. Machine-
parseable YAML (Agent-First Repository rule) -- no narrative companion doc.
```yaml
schema_version: 1
slug: "{slug}"
intent: >-
  1-2 sentences: the roadmap item, audit, or work this run drives to completion.
wave_plan:
  - wave: 1
    slices: ["{slug-a}", "{slug-b}"]
    dispatch: serial | parallel
    rationale: why this grouping (overlap-matrix result)
slices:
  - plan_slug: "{slug-a}"
    status: pending | planning | plan_merged | implementing | verifying | merged | blocked | failed
    pr_url: null
    hand_back: null       # most recent Bounded Hand-Back object from this slice's subagent
    pending_gates: []     # write-ahead GATE_REQUEST entries; see contract#pending_gates
autonomous_decision_log:
  - timestamp: "<ISO date>"
    decision: what was decided without asking the human
    rationale: which autonomy-boundary criterion applied (or "proceed-with-notice")
gates: {g0_intake: pending, g1_decomposition: pending, g3_completion: pending}
```
**Persistence:** the live ledger is a **scratchpad** working copy (fast, no per-update commit) -- NOT
**durable** across full session loss. The durable cross-session resume SoT is each slice's actual
GitHub PR/merge state (Decision 115/87), not this file: on cold resume, reconstruct status from PR
state via the GitHub MCP tools; on disagreement, PR state wins. See
`overseer-dispatch.yaml#ledger_persistence` (incl. the pre-PR-open scope caveat).

## Lifecycle and Gates

- **G0 Intake:** state the target; run preflight (incl. headroom); confirm scope; get explicit
  go-ahead before any dispatch. Create exactly one `create_trigger` safety-net watchdog here.
- **G1 Decomposition:** after recon and the Fable advice-consult, present the wave plan (slices,
  overlap matrix, serial/parallel per wave); get explicit confirmation before dispatching.
- **G3 Completion:** once every slice is terminal (merged, or blocked/failed with prior sign-off),
  present the ledger summary, delete the G0 watchdog trigger, get explicit sign-off.

**Blocked/failed-twice:** never a silent third retry -- routes to `executor-rca` with a concrete
artifact (transcript, output file, a re-dispatch to reproduce, or the original SUBAGENT CONTEXT +
prompt); escalate with the RCA findings (Decision 55, one level up). See
`overseer-dispatch.yaml#executor_rca_feed`.

**Halt-on-open-ci_rca (Decision 226):** every dispatch first runs `scripts.ci_rca.red_overlap` (`--plan <plan>` for
`/implement`, no arguments for `/plan`); a non-zero exit halts it -- never overridden, not even by proceed-with-notice.

## Division of Labor & Gate Ownership (Design B)

**Division of labor:** the author subagent (planning or implementation) runs its own lifecycle,
commits, pushes, opens its own PR, and stops -- hands the PR back via PROCEED. `/plan`'s own alignment
sign-off rides a `GATE_REQUEST`
(`gate: project-alignment`) the overseer relays to the human verbatim -- no gate
skill is dispatched. **Gate ownership** never
lives with the author: the overseer dispatches EVERY decision-scout, plan-critique, and code-review
as a fresh sibling, and owns subscribe_pr_activity -> CI-green -> squash-merge, waiting in the
**foreground** of its own turn (never `run_in_background`) and resuming the paused author via
**SendMessage** on verdict. Full tables + the composed trampoline sequence:
`overseer-dispatch.yaml#division_of_labor` / `#trampoline_sequence`.

**Subagent-dispatch detection:** every author-subagent prompt opens with the exact SUBAGENT CONTEXT
header from `overseer-dispatch.yaml#subagent_detection` (tool-roster absence + injected header +
error-fallback on a nesting error), so the subagent GATE_REQUESTs rather than running a gate inline.

**Planning-subagent dispatch:** `Agent`, `model: "opus"` (matches `planning`'s `opus[1m]` pin -- see
Model Namespace Note); invoke `planning` via `Skill`, following its gate-section subagent-dispatch
conditional.

**Implementation-subagent dispatch:** `Agent`, `model: "sonnet"` (matches `implement`'s pin); invoke
`implement` via `Skill`, following the same conditional in its code-review gate section.

## Overlap-Matrix Serial/Parallel Decision Procedure

At G1, decompose into slices (each = one eventual `PLAN-{slug}.yaml`). For every pair, compare
`files_in_scope` and `depends_on` (same shape as `orient`'s overlap matrix): disjoint scope with no
`depends_on` edge -> SAME wave, PARALLEL; any overlap or edge -> different waves, SERIAL (the
dependency's wave merges first).

Serial is the default; parallel only when the matrix affirmatively clears a pair, requiring one
worktree per agent, disjoint scopes per slice's plan Scope table, and a FIXED merge order declared
before dispatch.

## Liveness, Watchdog & Safety Net

Diagnose aliveness (author OR gate subagent) via `ls -laL` on the transcript symlink (TARGET mtime,
not the symlink's own near-constant mtime): a **watchdog**/**heartbeat** check reads this against
expected stage duration to avoid a false-stall false positive. On a confirmed death signature,
**restart** the dead one fresh from the ledger's last hand-back `artifacts` -- TWO SEPARATE, never
conflated counters: an author death counts against its own two-attempt cap (content-quality
signal); a gate death gets its OWN per-(gate,round) cap (pure infra flakiness). On an ambiguous
**stall**, send exactly ONE SendMessage nudge before treating a further silent window as death. The
**safety-net** watchdog is exactly one `create_trigger` at G0 and one `delete_trigger` at G3 -- a
stall-sweeper only, never a CI-poll substitute (subagent death emits no event, unlike the
already-covered CI/merge-conflict signals; never allowlist `mcp__Claude_Code_Remote__*`). Both
re-dispatch paths are BOUNDED DETERMINISTIC recovery (Decision 55), never an LLM-judgement rescue.
See `overseer-dispatch.yaml#liveness` / `#safety_net`.

## Bounded In-Loop Validation

Each dispatched implementation subagent already runs `validate --pre` before opening its PR
(implement/SKILL.md Commit Flows); the overseer never re-runs full unflagged `validate` itself --
duplicates the author's own gate, violates Read-Nothing Router Discipline. See
`overseer-dispatch.yaml#bounded_validation`.

## Autonomy-Boundary Policy

An overseer-level call is made AUTONOMOUSLY only when ALL four hold: **settled-consensus** (Fable +
existing decisions agree), **convention-fit**, **reversible** (worst case a re-dispatch, never an
unmerged-artefact cleanup), **no-credible-alternative**. Otherwise present 2-3 options and wait. Log
every autonomous decision (and which criteria it met) to `autonomous_decision_log`.

**Proceed-with-notice (low-stakes tier):** reversible + settled-consensus + no negative externality
even if wrong -> proceed without a synchronous reply, but ALWAYS post a notice to the ledger and
chat -- never silent, never blocking. Never applies to the always-ask list or an open ci_rca halt.
See `overseer-dispatch.yaml#autonomy_tiers`.

**Hard always-ask list (no override, ever):** IAM/security, spend impact, any public-surface
artefact change (PUBLIC-repository boundary, AGENTS.md), any governed-deploy action.

## Fable Advice-Consult Protocol

Before each major design decision, dispatch a fresh-context subagent (`Agent`, `model: "fable"`). It
reads (Read/Grep/Glob) but edits nothing, separating advice into "settled consensus" vs "contested"
(where practice diverges from this repo's convention). Reconcile via **adopt** / **adapt** /
**reject**, logged to the ledger. Triggers beyond G1: **workflow-adaptation** (adapting the
overseer's own dispatch workflow to a methodology gap) and **rec-synthesis** (reconciling related
recs into one slice boundary, vs. sequencing already-atomic work). See
`overseer-dispatch.yaml#fable_triggers`.

## Model Namespace Note

A skill-frontmatter `model:` pin (e.g. `opus[1m]` above) is a different **namespace** from the Agent
tool's `model` **enum** (`sonnet | opus | haiku | fable`) -- a context-window-suffixed pin is **not a
valid** Agent-tool value. Dispatch with the enum; the frontmatter pin governs only this skill's own
session. See `overseer-dispatch.yaml#model_namespace_note`.

## Decision Guardrails

- **Decision 67:** human-gated CC-web orchestration -- NOT `scripts/execute_recommendation.py`.
  Never consumes the rec queue; IMPLEMENTATION only.
- **Decision 55/72:** BLOCKED/FAILED twice never an inline patch/silent retry -- routes to
  `executor-rca` and escalates.
- **Decision 226:** see Lifecycle and Gates -- the red_overlap verdict blocks dispatch.
- **Decision 76:** the safety-net watchdog is the sole sanctioned trigger exception.
- **Decision 90:** composes `/plan`/`/implement` as-is -- no new tier, plan type, or bypass.
- **Decision 115/87:** ledger durable SoT precedent.
