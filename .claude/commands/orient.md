---
description: Read-only orientation session. Surfaces eligible work, CI-RCA triage, ranked what-to-work-on, and ready-to-paste /plan prompts. Produces a chat reply only; writes nothing. Run before /plan to choose what to work on next.
model: opus[1m]
---

# Orient Workflow

**Intent**: Consume the preflight cache and platform roadmap to produce a structured orientation deliverable: status digest, CI-RCA triage, ranked work list, and up to 5 disjoint `/plan` prompts with an overlap matrix and keystone-first sequencing. Writes nothing.

*Note: For the full orientation methodology (read-only contract, deliverable shape, overlap matrix spec, keystone sequencing, status-trusted-never-inferred rule), invoke your `orient` skill via the Skill tool.*

## Step 1: Confirm Preflight Cache

Check whether `logs/.preflight-report.json` exists, is recent (< 2 hours old), and contains a
`platform_roadmap.gate_evaluations` key. If any condition fails, refresh with the full projection:
`bin/venv-python -m scripts.session.preflight --roadmap-detail full`.

Full cache-only constraint, input-field semantics, and the in_progress field contract live in the
`orient` skill (Inputs section) -- invoke it via the Skill tool rather than re-deriving them here.

## Step 2: Load Inputs

Read from the preflight cache (`logs/.preflight-report.json`): `platform_roadmap.next_eligible`,
`strategic_pending`, `in_progress` (field semantics in the skill's Inputs > In_progress entry
fields), `blocked_on_cd`, `gate_evaluations`, `ratifiable_cds`, `realized_but_pending_cds`; `ci_rca_unresolved_recs`
(each entry carries `prior_deferrals`),
`ci_rca_likely_resolved_recs`, `ci_rca_liveness_alert`, `forward_fix_recursion_alert`,
`recent_main_commits`; and the Best-Practices signals `convergence_health`, `data_quality`,
`non_automatable_softcap_breached`, `terraform_pending`, `dependabot_stranded_prs`
(rendered from the cache only -- orient never shells out to `gh` to recompute it).

For the ad-hoc lane (Section 6's trailing subsection, below): `followon_recs`,
`open_critical_recs`, `priority_queue` and `recs_read_status` -- the last lets the renderer tell a
DEGRADED recommendation pull from a genuinely empty lane rather than reading an unknown Critical
population as a silent zero.

For `files_in_scope` (overlap matrix) and `depends_on` (keystone computation), use the typed-loader
projection -- pure-local `scripts.roadmap.platform_roadmap.load()` import, no warehouse I/O, distinct from
the banned `-m scripts.roadmap.platform_roadmap` module entrypoint. Keystone fan-out is a reverse query, so
the extraction also emits a roadmap-wide `{id: depends_on}` index (cheap) alongside the
candidate-scoped projection -- forward-only visibility over the ~9 candidates cannot answer it.
Fall back to a full-file Read of `docs/ROADMAP-PLATFORM.yaml` on error:
```bash
bin/venv-python -c "import json; from scripts.roadmap.platform_roadmap import load; data=load('docs/ROADMAP-PLATFORM.yaml'); ids={i['id'] for k in ('next_eligible','in_progress','blocked_on_cd') for i in json.load(open('logs/.preflight-report.json')).get('platform_roadmap',{}).get(k,[])}; proj=[t.model_dump(include={'id','files_in_scope','depends_on','related_candidate_decisions'}) for t in data.tier_items if t.id in ids]; depends_index={t.id: t.depends_on for t in data.tier_items}; print(json.dumps({'candidates': proj, 'depends_index': depends_index}))"
```

## Step 3: Invoke the Orient Skill and Emit the Deliverable

Apply the `orient` skill methodology to produce the six-section chat deliverable:
1. Status Digest
2. CI-RCA Triage
3. Momentum & Direction
4. Best-Practices Health Check
5. Ranked What-to-Work-On
6. /plan Prompts with Overlap Matrix

See the skill's Deliverable Shape section for the full spec of each, except Section 2's CI-RCA triage rendering and Section 6's ad-hoc lane trailing subsection, both specified below.

### CI-RCA Triage rendering (deliverable Section 2)

| Preflight signal | Classification | Operator action |
|---|---|---|
| `ci_rca_unresolved_recs` null (`recs_read_status` != `ok`) | **HARD BLOCK -- UNAVAILABLE** | Print "CI-RCA UNAVAILABLE"; never render it as clear or zero. The next `/plan` blocks at commitment unless the human overrides. |
| `ci_rca_unresolved_recs` non-empty | **HARD BLOCK** | List each rec (id, priority, title, its `prior_deferrals` line -- see below). The next `/plan` blocks overlapping work, and all work once a red is past its SLO (`scripts.ci_rca.red_overlap`, Decision 226); orient surfaces it. A High rec is Track A: route it to `docs/contracts/red-main-recovery.yaml` `procedure.fast_lane_repair`, never a zero-th `/plan` prompt. |
| `ci_rca_likely_resolved_recs` non-empty | **SOFT PROMPT** | "LIKELY RESOLVED -- verify and close." Close-then-stamp: `bin/venv-python -m scripts.ops_data_portal --update-rec <id> --status closed --resolution '...'`, then `bin/venv-python -c "from scripts.ops_portal.ci_rca_lifecycle import stamp_fixed_by_sha; stamp_fixed_by_sha('<id>', '<merge-sha>')"` per `docs/contracts/ci-rca-lifecycle.yaml` `closure_dependency.manual_route`. |
| `ci_rca_liveness_alert` non-null | **HARD ALERT** | Main CI red >30 min with no rec. Triage immediately. |
| `forward_fix_recursion_alert` non-null | **HARD ALERT** | 3+ ci-rca recs targeting same file in 24h. Triage immediately. |

If HARD BLOCK recs exist, note them prominently at the top of this section.

**`prior_deferrals` render**: each `ci_rca_unresolved_recs` entry carries `prior_deferrals` (`count`, `plan_slugs`, `owner_named`). Beneath that rec's listing render one line: "deferred N times (plans: a, b, c[, +M more]; owner named: X | none)".

**N >= 3 close-or-plan trigger**: once a rec's `prior_deferrals.count` reaches 3 or more, it has been deferred at least three times without closure -- do not defer it again by reflex.

1. Side-effect guard clause, checked BEFORE anything runs: read the rec's `acceptance` string from its `ci_rca_unresolved_recs[].acceptance` preflight-report entry; it is probeable only when that `acceptance` string is read-only -- a grep, a read-only inspection, or a pytest selector. A pytest selector is read-only only after BOTH `-p no:cacheprovider` AND `--randomly-seed=0` are appended to its pytest segment: `pyproject.toml`'s `addopts` carries `--randomly-seed=last`, and pytest-randomly asserts the cacheprovider plugin is present (`hasattr(config, "cache")`) whenever the seed resolves to the literal string `last` -- so `-p no:cacheprovider` alone hard-errors (`INTERNALERROR`) on every pytest acceptance command in this repo. Overriding the seed to a concrete value sidesteps that assertion (pytest-randomly only touches `config.cache` when the option equals `last`) while the run still writes no `.pytest_cache`. Anything else is surfaced verbatim, unprobed, with the /plan prompt below.
2. The bounded, timed probe (commits sourced from the preflight cache's `recent_main_commits` -- cache only, no fresh shell command):
   ```
   bin/venv-python -c "
   import json, time
   from scripts.rec_relevance import evaluate_rec_relevance
   report = json.load(open('logs/.preflight-report.json'))
   rec = <the rec dict from ci_rca_unresolved_recs, acceptance taken from the report entry>
   start = time.monotonic()
   verdict, evidence = evaluate_rec_relevance(
       rec, run_acceptance_probe=True, acceptance_timeout=120,
       recent_commits=report['recent_main_commits'],
   )
   elapsed = time.monotonic() - start
   timed_out = elapsed >= 0.95 * 120
   print(verdict, evidence, elapsed, timed_out)
   "
   ```
3. Three-way routing rule on the printed result:
   - `verdict == "satisfied"` -> the SOFT PROMPT close-then-stamp route above, naming `stamp_fixed_by_sha`.
   - `timed_out` true -> report "relevance probe did not complete (timed out at 120s)"; this is NOT evidence the rec is still unresolved -- surface the `acceptance` string verbatim and emit the /plan prompt marked inconclusive.
   - any other verdict -> name the verdict and emit the /plan prompt.

### Ad-hoc lane rendering (deliverable Section 6, trailing subsection)

Rendered AFTER the in_progress follow-on prompts, the eligible-item prompts and the overlap
matrix (Section 6's own spec, in the skill) -- additive to and NEVER consuming the five roadmap
`/plan` prompt slots. The ad-hoc prompts sit OUTSIDE the overlap matrix and the keystone ranking:
they are rec-grain and carry no `files_in_scope`/`depends_on` to project (Decision 90; audit
B1-R4 (b) "never competing for the five roadmap slots"). The lane renders id/title/parent only --
relevance is judged by `/plan`'s Recommendation Relevance Gate
(`docs/contracts/recommendation-relevance.yaml#planning_gate_protocol`) when a prompt is RUN; no
relevance verdict is computed or cached at preflight (Decision 55 surfacing-only, Decision 103).

Cache-only: render from this Step's `followon_recs`, `open_critical_recs` and `priority_queue`
plus `recs_read_status` -- never a rec-cache read or a DuckLake reader call. An absent key means
the preflight cache predates this lane -- say so and recommend re-running preflight.

**Omission / degraded rule**: omit the whole subsection when `followon_recs`, `open_critical_recs`
and `priority_queue` are all empty AND `recs_read_status` is `ok`. When `recs_read_status` is
anything else, render the subsection as DEGRADED naming that status instead of omitting it --
mirroring the skill's `decision_conditions` degradation clause: a degraded read must never present
as an empty lane (Decision 55). This DEGRADED guarantee covers the two REC keys only. The queue
bucket is a declared residual: the preflight report carries no independent queue-read status
(`priority_queue_source` derives from `creds_status` alone), so a failed per-table
`ops_priority_queue` warm pull renders as an empty queue, indistinguishable from a genuinely empty
one. Do not widen the trigger to `creds_status != ok` to mask this -- creds down already implies
`recs_read_status != ok`, so that disjunct is redundant and would not close this per-table window.

**Cap and order**: at most 3 prompts TOTAL across the three buckets, ordered open Critical recs ->
ready follow-on recs -> priority-queue head. Three or more open Critical recs displace the
follow-on and queue buckets entirely, so an empty follow-on bucket reads as displacement, never as
"nothing ready". A rec present in both `open_critical_recs` and `followon_recs` renders once --
its Critical entry wins. Within the open-Critical bucket, oldest-created-first is a deterministic
TIE-BREAK under the cap, not an urgency rank -- the bucket order itself (Critical first) carries
the urgency semantic.

Render (within the total cap of 3):
```
/plan rec-NNNN: <title> (open Critical, source=<source>)
/plan rec-NNNN: follow-on of PLAN-<parent_plan> -- <title>
/plan <rec_id>: <rationale>   # priority-queue head
```

Output the deliverable to the chat. This is the sole output of `/orient`.

**Write nothing.** No files created or modified. No recommendations filed. No roadmap edits. Sole exception: the Step 3 relevance probe, admitted only under that step's read-only guard clause and run with pytest's cache plugin disabled -- it creates no tracked file, no rec and no roadmap edit.

STOP. The orient session is complete.
