# Verdict personas on GitHub Actions via DeepSeek and LiteLLM

Audit of `2b8c4326` against `docs/audit-prompts/AUDIT-verdict-personas-on-actions.md`. The machine-readable twin is
`audits/verdict-personas-on-actions-2b8c4326.yaml`; every number below is read from its `summary`.

**Degraded run.** Python execution and network access were denied by this harness, and no web tool exists in the
session. Section 5 step 2 (the roadmap preflight) did not run; step 3 (`recs_related`) did not run, so dedup is
roadmap-text-only (`meta.degraded_dedup: true`) and every unverified dedup claim is marked HYPOTHESIS; step 4's YAML
parse check did not run; the Section 16 pre-push `yaml.safe_load` gate could not run, so the file was hand-checked;
`scripts.validate --pre` was not run. `meta.web_unavailable: true` records the missing external check. No
model, persona, workflow or Terraform command was run against this repository.

## 1. How we get there

1. **Foundation, wave 1** - the seven persona contracts and their rendered prompts (WP-02), the digest-pinned image
   with its twin and conformance suite (WP-04), and the verdict job's credential home (WP-06).
2. **Transport, wave 2** - LiteLLM in `scripts/llm/client.py`, with tier membership resolved from
   `inference-provider.yaml` at call time and the `run_persona` litellm backend behind WP-02's port (WP-03).
3. **Chain, waves 3 and 4** - the secretless trigger, the `workflow_run` verdict job, the check run, the input
   projection and the two registered checks (WP-05), then the capture writer that makes a dual-run readable (WP-07).
4. **Add and measure, waves 5 and 6** - the three personas as advisory check runs (WP-08), then the pre-registered
   dual-run to N (WP-09).
5. **Flip and remove, waves 7 and 8** - the gates read their check runs (WP-10), then the routine in-session dispatch
   leaves `/plan` and `/implement`, leaving one visible fallback (WP-11).

## 2. Question verdicts

| Q | verdict | one line |
|---|---|---|
| Q1 | partial | contracts, image and chain are specified; three prerequisites (credential home, capture writer, corpus source) are unowned or unbuilt |
| Q2 | partial | the input projection admits PR files and blobs only; main's corpus, the PR body and the merge base have no designed source |
| Q3 | partial | readiness is blocked by four undesigned prerequisites, and X14 (the wake path) was missed entirely |
| Q4 | partial | the flip is measurable but not attributable; the wake path and migration of exempted surfaces are open |
| Q5 | ready_with_forks | 11 packages, 8 waves; two forks and one spend authorisation gate wave 1 |
| Q6 | partial | the exemption map exists but is incomplete; `.claude/workflows/**` is absent, 100+ tag-pinned action references remain |
| Q7 | moderate | 19 touchpoints: 9 decisions before the flips and 10 plan approvals, two of the nine being admin acts |
| Q8 | nine answers | recorded in full in the YAML; highlights in section 7 |

Maturity is `solid` at S1-S4 and `frontier` at S5.

## 3. The critical path

`WP-02 -> WP-03 -> WP-05 -> WP-07 -> WP-08 -> WP-09 -> WP-10 -> WP-11`, one package per wave from 1 to 8, with
wave 1 also carrying WP-04 and WP-06 in parallel. WP-04 sits off the critical path but blocks WP-05; WP-06 blocks WP-05 too
and is the only wave-1 package that cannot start without an operator answer. WP-01 is wave 0 and produces no code.

Six roadmap items are closed or advanced by these 11 packages: T4.10a c1-c8 (WP-02), T4.32 c2 and T4.2 c14
(WP-04), T4.32 c1 in two parts plus c3 and c4 and T4.2 c4, c8, c9, c10, c12, c13 (WP-03, WP-05), T4.2 c9's key
reachability (WP-06), T4.2 c3 (WP-07), T4.28 c1-c4 and T4.29-T4.30 c1-c3 (WP-08), c5 (WP-09), c6 (WP-10), c7 (WP-11).

## 4. What waits on the operator

19 operator touchpoints: 10 are "write the plan" approvals, 9 are decisions. Six of the nine settle before wave 1, as
do the plan approvals for WP-02, WP-04 and WP-06.

| # | decision | recommendation (unapproved) |
|---|---|---|
| O1 | credential delivery for the verdict job | an OIDC role bound to `job_workflow_ref`, reading the two `/secret/` parameters |
| O2 | who writes a verdict job's capture rows | the shell, under a short-lived OIDC identity (option A) |
| O3 | the IAM shape that follows from O1 | least-privilege on exactly two parameter names |
| O4 | monthly inference spend | about USD 40-70, HYPOTHESIS, dominated by the two plan personas |
| O5 | persona contract wording | present the exact text, get one yes |
| O6 | provider and model rows, per-persona model ids | defer to WP-03's plan |
| O7 | the Terraform change for O1 | human-gated local apply; it never auto-applies |
| O8 | the manual apply and the two key values | operator's own words naming the mutation |
| O9 | rewording the coordinator, overseer and alignment contracts | defer to WP-10's plan |

## 5. Top findings

17 findings: 2 novel, 13 planned-insufficient, 2 planned-unbuilt. Six are high, 11 medium, none critical.

| id | what | why it matters |
|---|---|---|
| VPA-04 | no item, file or apply path carries the inference key to the verdict job | nothing on the path runs at all, and the nearest shortcut is a repo-level secret any `pull_request` job can read |
| VPA-03 | no wake path exists for a green verdict, and a PR comment is barred as a verdict channel | the flip criteria say "loop on the check run" with nothing to loop on |
| VPA-06 | the flip criteria name `/plan` and `/implement` only, and leave the plan's gate bookkeeping unowned | the coordinator can keep merging on a session-asserted PROCEED after the gate is "removed" |
| VPA-05 | nothing writes the per-finding rows the dual-run must read | the measurement step feeds on rows with no producer |
| VPA-01 | the input projection names no source for main's governance corpus | a digest-pinned image can answer from a stale corpus and look trustworthy |

The sixth high is VPA-02: the projection cannot carry the PR body, so the V3 post-deploy evidence check is either
falsely red or silently dropped on exactly the PRs that ship infrastructure.

## 6. Bundling

The disposition table carries 23 candidates: 9 bundled, 6 sequenced separately, 3 deferred, 5 rejected.

Bundled because the criterion is the package's own acceptance: rec-4028 (into WP-03), rec-4433/4434 (WP-07), rec-4476
(WP-10), rec-4490 (WP-05), rec-4477 (WP-04), T4.13 c3 (WP-08), T4.17 c1 and c5 (WP-03). Sequenced separately because
they would swamp a gate PR: T4.26, rec-4566, Decision 237's secrets cutover, the repo-wide SHA-pinning sweep, T4.13
c1/c2/c4, and T4.36 c5 - the last one before WP-02 renders, since it is editing two of the three source skills now.
Deferred: rec-4506, rec-4415, T4.14's fixtures. Rejected with the control that covers each: C3, C6, C15, C16, C17.

## 7. Q8 highlights

- **Quality before a blocking flip** is provable only as agreement with a stronger judge on this repo's own artifacts:
  25 PRs per persona, zero missed Criticals, zero false PROCEEDs, plus a replay set labelled by outcome rather than
  judgement.
- **Flip order**: `code_reviewer` first, then the scout and the critique together - T4.29 and T4.30 own the same two
  files and one `/plan` re-sequencing, and the critique fails with "re-scout required" when the head has no scout
  verdict.
- **Bootstrap is safe only with two mitigations**: dispatch the flip PR's gate from an `origin/main` worktree, and add
  `.claude/skills/` plus `.claude/commands/` to `boundary_patterns` before the first flip.
- **A check run blocks nothing until its name is a required status check**, which is a ruleset edit with no owner on
  this path. The check name is therefore a fixed interface, and "required" is a separate operator act at the flip.
- **Cost**: about USD 40-70 a month at 3 pushes per PR over roughly 205 code and 208 plan PRs; Actions minutes are
  free for a public repository.

## 8. coordinator_intake

```yaml
WORKSTREAM: verdict personas on GitHub Actions via DeepSeek and LiteLLM (audit 2b8c4326, 11 packages, 8 waves).

(1) PACKAGES IN WAVE ORDER
WP-01 Settle the credential-delivery and capture-identity forks and authorise spend | wave 0 | depends on nothing | waits on operator: yes
WP-02 Persona contracts, rendered prompts and the contract validator (T4.10a) | wave 1 | depends on nothing | waits on operator: yes
WP-03 LiteLLM transport, tier membership and the run_persona litellm backend | wave 2 | depends on WP-02 | waits on operator: yes
WP-04 Digest-pinned persona image, build channel, local_container twin and conformance suite | wave 1 | depends on nothing | waits on operator: no
WP-05 Verdict chain, identity, plan-PR routing, scout-input fingerprint, two checks, fixture run | wave 3 | depends on WP-03, WP-04, WP-06 | waits on operator: no
WP-06 Verdict job credential home and the two inference keys | wave 1 | depends on WP-01 | waits on operator: yes
WP-07 Persona capture wiring (persona key, definition hash, per-finding rows) | wave 4 | depends on WP-01, WP-05 | waits on operator: yes
WP-08 ADD the three verdict personas as advisory check runs | wave 5 | depends on WP-05, WP-07 | waits on operator: no
WP-09 DUAL-RUN pre-registration and the advisory comparison to N | wave 6 | depends on WP-08 | waits on operator: no
WP-10 FLIP the three gates and reword the two extra consumers | wave 7 | depends on WP-09 | waits on operator: yes
WP-11 REMOVE the routine in-session dispatch | wave 8 | depends on WP-10 | waits on operator: yes

(2) WAITING ON THE OPERATOR (nine counted items; reply needed in one line each)
O1 (WP-01) Credential delivery. Reply A or B. Recommendation, unapproved: B (an OIDC role bound to job_workflow_ref reading the two /secret/ parameters), because it leaves no long-lived key in a GitHub scope and reuses the ssm adapter the secrets port already has implemented.
O2 (WP-01) Capture writer. Reply A or B. Recommendation, unapproved: A (the shell writes capture rows under a short-lived OIDC role), because the alternative empties capture exactly when no session is watching.
O3 (WP-01, WP-06) IAM shape that follows from O1. Reply yes, or name a narrower scope.
O4 (WP-01) Monthly inference spend: about USD 40-70 (HYPOTHESIS). Reply yes, or name a ceiling.
O5 (WP-02) Persona contract wording. Reply yes to the exact text pasted at presentation.
O6 (WP-03) Inference-provider and project-configuration row wording, including per-persona model ids. Reply yes to the pasted text.
O7 (WP-06) The Terraform change creating the Environment or role. Reply yes to the pasted plan.
O8 (WP-06) The manual step: apply terraform/github locally and set the two key values out of band. Reply with your words naming the mutation.
O9 (WP-10) Rewording of coordinator.yaml, overseer-dispatch.yaml and project-alignment.yaml. Reply yes to the pasted text.
Nothing else waits on you until WP-09 reports its numbers.

(3) STANDING-INSTRUCTION CANDIDATES
S1 Every worker reads the shared coordinator.yaml copy before its first dispatch and never edits that copy.
S2 A verdict persona runs from main only; no worker retargets a persona job at a branch definition.
S3 No worker writes an inference key value or its parameter path into a plan, PR body, memory or the shared store; name the environment or role instead.
S4 A check run whose conclusion is not success is never read as PROCEED; until its own flip merges, the in-session gate remains the gate of record.
S5 Every gate-flip PR is reported to the coordinator with the check-run name and the head SHA it reviewed.

(4) INTAKE BRIEFS FOR WAVE-1 PACKAGES
WP-02 (T4.10a). Plan seed: "IMPLEMENTATION plan closing T4.10a c1-c8 under Decisions 206, 117, 185, 229 cl.2 and 93. Author the seven persona contracts (input_schema, output_schema, capabilities, max_turns and max_wall_clock_seconds, max_revisions, failure_outputs), the rendered prompt artefacts with per-version content hashes, the run_persona port declaration with backend in {litellm, claude_cli}, and one registered conformance check that rejects a missing schema, an unknown capability, a harness tool name, an unbounded budget, a dollar budget or a persona-starting capability. Register it in scripts/validate.py with a mirror test. Do not implement any backend." Files owned: docs/contracts/executor-personas.yaml, docs/contracts/personas/, config/agent/executor/personas.yaml, scripts/validate.py, scripts/checks/contracts/validate_persona_contracts.py, tests/checks/contracts/test_validate_persona_contracts.py. Forks: none operator-facing; mechanical default picked - write the input_schema so the non-PR corpus arrives as an explicit pinned field rather than an implicit image read, because the corpus source is VPA-01.
WP-04 (image and conformance). Plan seed: "IMPLEMENTATION plan for T4.32 c2 and T4.2 c14 under Decisions 205 cl.2, 212 cl.1-2 and 234 cl.2. Build the digest-pinned persona image, its build-and-publish channel (rec-4477) with a hash-locked dependency set, the local_container persona_compute_host adapter as the port oracle, and the gating conformance suite that runs one schema-validated input through run_image credential-free in PR CI. Add a fixture persona. Assert one digest is referenced by the Actions shell, the twin and the compute-plane descriptor." Files owned: .github/workflows/persona-image-publish.yml, container/persona/, requirements-lock.txt, scripts/personas/local_container.py, tests/personas/test_conformance.py. Forks: mechanical defaults picked - SHA-pin every action in the new publish workflow (T4.2 c8's rule applied to the only new workflow on this path), and lock the dependency set with hashes because requirements-dev.txt carries none today (X6).
WP-06 (credential home). Plan seed: "IMPLEMENTATION plan for T4.2 c9's key reachability under Decisions 237, 223 cl.2, 173 and 224. Implement WP-01's chosen option: either a github_repository_environment with custom_branch_policies plus a branch_pattern matching the verdict workflow ref, or an OIDC role trusting the Actions token issuer with job_workflow_ref bound to the verdict workflow and ssm:GetParameter on exactly the two inference-key parameter names. Two keys, one per tier role, read by name. Never a repository-level Actions secret." Files owned: terraform/github/environments.tf, terraform/github/variables.tf, terraform/github/repo.tf, terraform/personal/verdict_oidc.tf, docs/contracts/secret-material-handling.yaml. Forks: operator fork O1 unapproved (recommendation: option B, the OIDC role); every other choice is mechanical and defaults are picked at presentation. Do not start until O1, O3 are answered.
```
