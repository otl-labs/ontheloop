---
name: orient
description: Read-only orientation session. Surfaces in-progress/eligible work, CI-RCA triage, ranked what-to-work-on, and up to N disjoint /plan prompts with an overlap matrix and keystone-first sequencing. Chat reply only; writes nothing (one named probe exception -- Read-Only Contract).
---

# Orient Methodology

You are using this skill to augment the `/orient` workflow. This skill is **strictly read-only** (one named exception, in Read-Only Contract): it produces a chat reply only. No files, roadmap edits, recommendation writes, or decision writes.

Decisions cited: 90 (Four-Tier Workflow Architecture), 59 (prefer deterministic signals), 72 (RCA-as-Plan-Source), 76 (.claude/ canonical), 84 (closed boundary), 86 (no new prose-architecture docs), 88 (egress budget).

## Read-Only Contract

The `/orient` workflow produces **one deliverable: a chat reply**. It:
- Writes no files
- Makes no roadmap status edits
- Files no recommendations or decisions (Single Portal Invariant untouched)
- Issues no git commits or pushes
- Runs no subprocess except the CI-RCA relevance probe of `.claude/commands/orient.md` Step 3 -- read-only by that step's guard clause, writing no file, rec, or roadmap edit

Status flips remain the verification-earned closing step owned by `/implement` tier-item bookkeeping. Orient reports roadmap state AS AUTHORED -- it never promotes, infers, or corrects status.

## Inputs

| Input | Source | Load method |
|---|---|---|
| CI-RCA recs | `logs/.preflight-report.json` (`ci_rca_unresolved_recs`, `ci_rca_likely_resolved_recs`, alerts) | Read preflight cache; null list = UNAVAILABLE hard block |
| Eligible / in_progress items | `logs/.preflight-report.json` (`platform_roadmap.next_eligible`, `in_progress`, `strategic_pending`) | Read preflight cache |
| Blocked-on-CD annotations | `logs/.preflight-report.json` (`platform_roadmap.blocked_on_cd`) | Read preflight cache |
| Ratifiable CDs | `logs/.preflight-report.json` (`platform_roadmap.ratifiable_cds`) | Read preflight cache |
| Realized-but-pending CDs | `logs/.preflight-report.json` (`platform_roadmap.realized_but_pending_cds`) | Read preflight cache |
| Realization candidates | `logs/.preflight-report.json` (`platform_roadmap.realization_candidates`) | Read preflight cache |
| Gate evaluations | `logs/.preflight-report.json` (`platform_roadmap.gate_evaluations`) | Read preflight cache |
| Best-Practices signals | `logs/.preflight-report.json` (`convergence_health`, `data_quality`, `non_automatable_softcap_breached`, `terraform_pending`) | Read preflight cache |
| Roadmap detail (`files_in_scope`, `depends_on`) | `docs/ROADMAP-PLATFORM.yaml` | Typed-loader projection: `scripts.roadmap.platform_roadmap.load()` (pure-local, no warehouse I/O -- distinct from the banned `-m scripts.roadmap.platform_roadmap` module entrypoint), returning both a candidate-scoped projection (filtered to the ids already surfaced by the preflight cache) and a roadmap-wide `depends_index` (`{id: depends_on}`, cheap) for reverse-dependency lookups; see the orient command Step 2 for the literal runnable form. Full-file Read only as an error fallback if the extraction fails. |
| Recent main activity | `logs/.preflight-report.json` (`recent_main_commits`) | Read preflight cache |
| Decision reversal-conditions monitor | `logs/.preflight-report.json` (`decision_conditions`: `monitored[]`, `surfaced[]`, `malformed[]`) | Read preflight cache (`scripts.preflight.decision_conditions.preflight_bucket()`, SEQ-02) |
| Rec `acceptance` string (Step 3 probe) | `ci_rca_unresolved_recs[].acceptance` | Read preflight cache (no portal or reader call) |

**Read-from-preflight-cache constraint (Decision 88 egress budget; Decision 84 closed boundary):** `/orient` reads the preflight cache -- it must NOT trigger a warehouse reader fan-out. Do not call `bin/venv-python -m scripts.roadmap.platform_roadmap` or any DuckLake reader verb during orient. The preflight script is the only path that may refresh `logs/.preflight-report.json`.

**Full-projection requirement:** `/orient` requires the full preflight projection (`--roadmap-detail full`). If `platform_roadmap.gate_evaluations` is absent from the cached report, re-run preflight with `--roadmap-detail full` before proceeding (the orient command handles this check in Step 1).

### In_progress entry fields

Each entry in `platform_roadmap.in_progress` (preflight cache) carries:
- `open_criteria_count` -- count of criteria with status=open in the structured ledger
- `all_plans_actioned` -- true if no PLAN-*.yaml has closes_criteria pointing at still-open criteria
- `needs_followon_plan` -- true iff `open_criteria_count > 0` AND `all_plans_actioned` is true (follow-on `/plan` is the next action)
- `completion_blocked_on_cd` -- sorted list of pending CD ids gating this item's completion (via related_candidate_decisions / cd.gates / decision_required_before); empty when `bootstrap_completion_exempt` or no pending gating CD; non-empty with `open_criteria_count == 0` means the item is parked-gated (qualifies for complete but the decision has not ratified)

**Phase A (degradation branch, stale cache lacking the structured ledger):** if an in_progress entry lacks `open_criteria_count`, `all_plans_actioned`, or `needs_followon_plan`, infer them from the item's `exit_criteria[]` list and `progress_note` prose (exit_criteria entries not mentioned as done in the progress_note count as open; when ambiguous, count as open per the conservative bias). If `completion_blocked_on_cd` is missing entirely, treat it as unknown rather than empty -- do NOT infer parked-gated status from a missing key, and do not emit a closeout or follow-on `/plan` prompt until the cache is refreshed with `--roadmap-detail full` (safe default: a missing key is never silently "no gating CD"). Phase A is a fallback only; the structured ledger is the primary path (Decision 59).

**Parked-gated rule (canonical, single location -- Decision 93):** an in_progress item with `open_criteria_count == 0` AND a non-empty `completion_blocked_on_cd` is parked -- all code work is done but it cannot close because a pending candidate_decision gates its completion. Emit NO closeout or follow-on `/plan` prompt for a parked-gated item -- the gate is a pending decision, not pending code work. Every Deliverable Shape section below references this rule rather than restating it.

## Status-Trusted-Never-Inferred Rule

Trust roadmap `status` exactly as authored in `docs/ROADMAP-PLATFORM.yaml` (via the preflight cache or Step-2 projection). Never infer, promote, or correct status from commit activity, PR history, or file existence.

- **Activity-vs-label** (e.g., "a recent commit touched T-X.Y's scope but the label is still `not_started`"): surface as **neutral dispatch context** only -- useful for the operator's prioritization but never a correctness verdict.
- **Trust the label**: the T2.20 lesson is that a merged-but-unverified item is correctly `not_started`. Activity-inference leads to silently skipping the verification step that earns the status flip.
- Status flips require `/implement`'s tier-item bookkeeping gate. Orient has no authority to flip status.

## Tier Item Freshness Gate -- Reference

The single authoritative definition of the Tier Item Freshness Gate lives in
`docs/contracts/tier-item-lifecycle.yaml#tier_item_freshness_gate`. Orient uses the eligible candidates from the preflight cache as its input list. Freshness adjudication (the four checks: silent-completion, stale-reference, supersession, gating-decision) fires per-item inside `/plan` at commitment time, not during orientation.

Do not re-author the four checks here -- that would be drift by design. `/orient` references the contract; it does not duplicate it.

## Deliverable Shape

The orient deliverable is a structured chat reply with six sections, in order:

### 1. Status Digest

Compact table of tier_items currently `in_progress` or eligible (`not_started` with all depends_on satisfied). Source: `platform_roadmap.next_eligible` and `platform_roadmap.in_progress` from preflight cache.

```
| Tier Item | Status | Open Criteria | Phase | Notes |
|---|---|---|---|---|
| T-X.Y: <name> | in_progress | N open | <phase> | |
| T-X.Y: <name> | eligible | -- | <phase> | gated by CD.NN (related) [if in blocked_on_cd] |
```

**Open-criteria count for in_progress items**: read `open_criteria_count` directly from the preflight cache (primary path; see Inputs > In_progress entry fields). Falls back to Phase A prose inference only on a stale cache (see Inputs). Rank in_progress items fewest-open-criteria-first (closest-to-done) in this column so the operator immediately sees which item needs the least remaining work.

**Parked-gated items**: see Inputs > In_progress entry fields for the canonical rule. Surface a parked-gated item in the Status Digest as "parked: qualifies for complete, gated by CD.X" (list all gating CD ids). If any gating CD.X also appears in `ratifiable_cds` (see below), point at the ratification lane instead of leaving it a dead end: "parked, gated by CD.X -- CD.X is ratifiable (see Ratifiable CDs)". An in_progress item with zero open criteria AND an empty `completion_blocked_on_cd` is a legitimate `/implement` bookkeeping closeout candidate (Decision 90: `/plan` never flips status; status flips happen in `/implement`).

**Ratifiable CDs** (candidate-decision-ratification lane, Decision 105): read `platform_roadmap.ratifiable_cds` from the preflight cache -- pending CDs carrying a truthy `realization_evidence` (set when someone has noticed the CD's gated work is realized/live). This is distinct from `blocked_on_cd`/parked-gated handling above, which is item-centric; this is CD-centric and surfaces even when no item is currently parked on the CD. List each as:
```
Ratifiable CDs: CD.6 (realized: <first ~80 chars of realization_evidence>) | CD.34 (realized: ...)
```
A CD appearing here is a candidate for a `/plan` session that drafts its ratifying Decision text (see `docs/contracts/candidate-decision-ratification.yaml#lane_steps.plan.draft`) -- ratification itself never happens in `/orient` (read-only) or without human sign-off. Do NOT surface a pending CD with no `realization_evidence` here even if it looks plausibly realized -- absence of the field means nobody has corroborated it yet (Decision 55: no unilateral judgement calls in a read-only surface).

**Realized-but-pending CDs** (close-audit-ulf-02 amendment, building on Decision 105): read `platform_roadmap.realized_but_pending_cds` from the preflight cache -- pending CDs whose free-text `detail` carries a `[Realized` prose marker but which have NOT (yet) been given a structured `realization_evidence` value. This is a lower-confidence, "needs corroboration/ratification-review" tier that sits BELOW the Ratifiable CDs list above: a prose annotation is not the same as someone deliberately corroborating the CD as ready (Decision 55). List each as:
```
Realized-but-pending (needs corroboration): CD.2 (hint: <realized_hint>) | CD.21 (hint: ...)
```
`/orient` ranks these as candidates for a human-confirmed `/plan` ratification session -- it never ratifies them itself (read-only, same discipline as Ratifiable CDs). Preserve the "do NOT surface a pending CD with no `realization_evidence` in the RATIFIABLE list" rule above unchanged: a CD promoted out of this list into Ratifiable CDs only once `realization_evidence` is actually set (typically at ratification time, when the prose marker is folded into the structured field).

**Realization candidates** (derived, lowest-confidence; audit PCD-01, building on Decision 105): read `platform_roadmap.realization_candidates` from the preflight cache -- pending CDs with no `realization_evidence` and no `[Realized` prose marker, whose every gated tier_item (or tier, via the same tier-shortcut gate-resolution used elsewhere) is `complete` or non-blocking (`reserved`/`deferred_post_mvp`), and whose detail does NOT match "fully superseded by CD.NN" (that prose belongs to the supersession lane, not here). This is a third tier, strictly disjoint from and BELOW Realized-but-pending CDs above: candidates for evidence-writing in a human-confirmed `/plan` session, never a status /orient computes or writes itself. List each as:
```
Realization candidates (derived): CD.4 (gates: ...) | CD.5 (gates: ...)
```
`/orient` stays read-only here exactly as elsewhere in this section -- it never writes `realization_evidence`; a human-confirmed `/plan` session is what would draft the evidence text (and, separately, the ratifying Decision, per `docs/contracts/candidate-decision-ratification.yaml#lane_steps.plan.draft`).

**Blocked-on-CD annotation**: for each item in `platform_roadmap.blocked_on_cd`, add a "gated by CD.NN" note in the Notes column including the relationship type (`gates`, `related`, or `decision_required_before`) and whether the item carries `bootstrap_completion_exempt: true` (in which case it may start/complete despite the pending CD). An item can be eligible-to-start while still annotated as gated-by-CD; the annotation informs planning, it is not a hard block on eligibility.

Omit items with status `complete`, `reserved`, or blocked (depends_on not satisfied).

**Gate-evaluation summary** (below the status table): one line per cross-tier gate from `platform_roadmap.gate_evaluations`:
```
Cross-tier gates: G.1 pass | G.8 fail | G.9 fail | G.10 fail
  G.8 deferred reason: <reason> [only shown when verdict is deferred]
```
Deferred gates include the reason string so the operator understands which runtime field is unresolved.

**Decisions past review date / reversal conditions fired** (audit SEQ-02, Decision 133 follow-on): read `decision_conditions` from the preflight cache (`monitored[]`, `surfaced[]`, `malformed[]` -- see Inputs). This bucket is surfacing-only; it never gates a merge (only the separate `validate_reversal_stanzas` --pre check gates, and only on stanza well-formedness). Render (illustrative format placeholder only -- decision ids/states below are not live data; Decision 133's own repo_state predicates are both `predicate: null` today, so it cannot currently render FIRED):
```
Reversal conditions: Decision NNN FIRED (alpha-readiness) | Decision 108 REVIEW DUE (review_by 2026-05-01)
  MALFORMED: Decision 77 -- unclosed 'yaml reversal-conditions' fence
```
Rank `surfaced[]` fired-first, then manual-review-due (the bucket is already sorted this way -- preserve order, do not re-sort). Render each `malformed[]` entry loudly on its own line naming the decision id and the error -- a malformed stanza is a data-quality signal the operator should not miss, even though it does not block anything here. Omit the whole block only when `monitored`, `surfaced`, and `malformed` are all empty (print nothing, not even a "(none)" placeholder -- unlike the Status Digest table, this is an optional addendum, not a fixed-shape table row). If `decision_conditions` carries an `error` key (the resilient `preflight_bucket()` degraded), surface "Reversal-conditions monitor degraded: <error>" instead of the ranked list.

### 2. CI-RCA Triage

Source: `ci_rca_unresolved_recs`, `ci_rca_likely_resolved_recs`, `ci_rca_liveness_alert`, `forward_fix_recursion_alert`, `convergence_health` from preflight cache. Decision 72 surfacing obligation: all open ci-rca recs are visible here so the operator knows the state before opening `/plan`.

**Convergence-health surfacing (CD.35 Wave 6 / T2.35):** Check `convergence_health` in the preflight report. Surface at the top of this section when it indicates a problem:

| `convergence_health` condition | Triage action |
|---|---|
| `status == "red"` and `red_age_hours` > 6 OR `stuck_approvals` > 0 | **STALE PIPELINE ALERT** -- Surface red_age_hours, unapplied_backlog, stuck_approvals count. An open tf_convergence_stale rec should exist; if it does, point the operator to it. Recovery: approve the pending gated-apply run in GitHub Actions, or run terraform-apply-sandbox workflow_dispatch with acknowledge_red_commit naming the red commit SHA. |
| `status == "red"` and `red_age_hours` <= 6 | **PIPELINE RED (recent)** -- note it; not yet escalated. |
| `status == "unknown"` | S3 read failed -- note as informational; may indicate transient credential issue. |
| `status == "green"` or `convergence_health` is null | No action needed. |

Classification rows, the `prior_deferrals` render and the close-then-stamp route have ONE home: `.claude/commands/orient.md` Step 3 ("CI-RCA Triage rendering"). Render this section from there; restate no row here.

### 3. Momentum & Direction

**Inferred neutral dispatch context -- not a status verdict (Status-Trusted-Never-Inferred Rule; see above).**

Source: `recent_main_commits` from the preflight cache (`logs/.preflight-report.json`). Do not issue a `git log` Bash call -- cache only (Decision 88 egress budget; Decision 84 closed boundary).

Group the recent commits by conventional-prefix slug (`feat`/`plan`/`roadmap`/`scope`) and map each slug to the tier_item it advanced using the Step-2 roadmap projection (Inputs > Roadmap detail). Emit a one-line trajectory read describing which area of the platform saw recent activity.

**Degradation rule**: when the slug->tier_item mapping is ambiguous (e.g., the commit prefix does not match any tier_item slug or multiple items share a prefix pattern), skip the inferred mapping and emit the raw commit list (sha, date, subject) without any inferred tier_item association.

**Scope constraint**: do NOT resurface parked-gated or deferred items that the Status Digest excludes. This section describes recent commit activity, not future eligibility.

### 4. Best-Practices Health Check

**Deterministic-signal-only checklist (Decision 59). No LLM free-association of best-practices -- evaluate ONLY the fixed signals listed below. No new warehouse reads, no DuckLake reader calls.**

Render as a table: practice -> preflight signal -> PASS/WATCH/GAP.

| Practice | Preflight signal | PASS/WATCH/GAP threshold |
|---|---|---|
| Terraform converged | `convergence_health.status` | PASS if `green`; WATCH if `red` and `red_age_hours` < 6; GAP if `red` and `red_age_hours` >= 6 or `stuck_approvals` > 0 |
| Data quality coverage | `data_quality.last_run.verdict` | PASS if `PASS`; WATCH if `DEGRADED` or `SKIP`; GAP if `FAIL`, `HARD_GATE`, `ERROR` or null/absent |
| CI-RCA liveness | `ci_rca_unresolved_recs` empty (null = UNAVAILABLE) AND `ci_rca_liveness_alert` null | PASS if both clear; GAP if either non-empty or non-null |
| Rec backlog (soft cap) | `non_automatable_softcap_breached` | PASS if false; GAP if true |
| Terraform pending | `terraform_pending` | PASS if false or absent; WATCH if true |
| Dependency backlog | `dependabot_stranded_prs` | PASS if `stranded` and `quota_saturated` both empty; WATCH if all `stranded` < 14d; GAP if any >= 14d or `quota_saturated` non-empty |

If a signal is absent from the preflight cache, mark it UNKNOWN rather than inferring a verdict. Do not issue any read to resolve UNKNOWN.

### 5. Ranked What-to-Work-On

Prioritized work list from the Status Digest:

1. **CI-RCA first**: HARD BLOCK recs appear as item 0 -- they block overlapping work. Route each per the command's Section 2 row.
2. **In_progress follow-on planning (ranked fewest-open-criteria-first)**: in_progress items have momentum and are typically the lowest-activation-cost next step. Rank them fewest-open-criteria-first (closest-to-done). For each, determine which of the three cases applies:
   - **Parked-gated**: see Inputs > In_progress entry fields for the canonical rule -- no prompt is emitted for these items.
   - **Mid-implementing** (a PLAN-*.yaml was authored and is in-flight but not yet acted on): suggest `/implement PLAN-{slug}.yaml` for that item.
   - **All authored plans actioned / no plan yet** (the common case -- the last plan was implemented and the item still has open criteria): emit a follow-on `/plan <item-id>: <item-name>` prompt. This is the default action for in_progress items. Read `needs_followon_plan` directly from the preflight cache (primary path); degrade to determining mid-implementing status from docs/plans/ and the progress_note only on a stale cache (see Inputs > In_progress entry fields).
3. **Keystone-first within eligible**: items that unblock the largest downstream depends_on fan-out appear before items with fewer downstream dependents. Fan-out is a reverse query -- count, for each candidate id, how many entries in the Step-2 `depends_index` (Inputs > Roadmap detail) list that id in their `depends_on`; the candidate-scoped projection alone cannot answer this (it only carries the candidates' own forward `depends_on`). A keystone is an item whose completion enables the largest set of currently blocked items.
4. **Strategic pending**: list separately at the bottom, noted "blocked by executor freeze (CD.17 reversal required)".

Format: numbered list with a one-line rationale per item citing the keystone/momentum/block reasoning.

### 6. /plan Prompts with Overlap Matrix

Up to 5 ready-to-paste `/plan` prompts (one per eligible non-blocked item), ordered keystone-first. Emit each `/plan` prompt in its own fenced code block (one paste-ready command per block). The overlap matrix renders as plain text outside any code block, with a one-line "safe to parallelize" note beneath it.

**Overlap matrix** -- before finalizing prompts, compute pairwise overlap between items. Two items overlap if they share at least one `files_in_scope` path, share a `related_candidate_decisions` cd_id, or one is in the other's `depends_on` chain. Non-overlap on all three dimensions = safe to parallelize.

Present the matrix:
```
Overlap matrix:
  T-X.Y vs T-A.B: [file1.py, file2.py]   <- cannot parallelize
  T-X.Y vs T-C.D: none                    <- safe to parallelize
```

**Keystone-first sequencing**: order prompts so items that unblock the most downstream work appear first. Note explicitly which pairs are safe to run in parallel sessions.

If a HARD BLOCK Critical rec exists, prepend a zero-th prompt:
```
/plan ci-rca: resolve rec-NNNN (<brief title>)
```

**Follow-on prompts for in_progress items** (ranked fewest-open-criteria-first, before eligible items):
```
/plan <item-id>: follow-on -- <item-name> (<N> open criteria remaining)
```
Exceptions -- do NOT emit a `/plan` prompt when:
- The item is **parked-gated**: see Inputs > In_progress entry fields for the canonical rule; surface it in the Status Digest only.
- The item is **mid-implementing** (a PLAN-*.yaml with closes_criteria names a still-open criterion, or the progress_note attests a plan was authored but not yet run): suggest the implement action instead:
```
/implement docs/plans/PLAN-{slug}.yaml   # mid-implementing: plan exists but is un-actioned
```

Then one prompt per eligible (not_started) item, ordered keystone-first:
```
/plan <item-id>: <item-name>
```

Ad-hoc lane (non-blocking, cap 3): open Critical recs, ready follow-on recs, priority queue -- rendering rule in the orient command Section 6 trailing subsection.

## Scope

v1: platform roadmap only. Product-roadmap orientation is deferred.
