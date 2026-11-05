# Verdict personas on GitHub Actions via DeepSeek and LiteLLM: readiness audit (66ab746e)

Companion of `audits/verdict-personas-on-actions-66ab746e.yaml`, which is the system of record.

## 0. Degraded mode

No degraded mode: preflight and the rec reader ran, 11 of 12 external lookups were used, no anchor was stale. The one field withheld is `meta.model`, because the session harness forbids a model identifier in a pushed artifact.

## 1. How we get there

1. Rule on four forks and one spend ceiling before wave 1 (credential delivery, image registry, model selection, draft-push rule under the handoff gate), which collapses the verdict from `ready_with_forks` to ready.
2. Build the foundation in two parallel waves: persona contracts, the LiteLLM transport with the model-id correction, the verdict credential path, the fingerprint, the trigger-policy check, then the run_persona backend with its telemetry producer.
3. Publish the digest-pinned image and land the verdict chain with an input-projection module and a wake signal.
4. Add each persona advisory with a pre-registered measure, flag-conditional consumer text and a comparison tool; write the overseer and coordinator contracts flag-aware once.
5. Flip each persona by changing a flag default after twenty consecutive agreements, then remove the dual-run paths; the in-session subagents survive only as the visible fallback.

## 2. Critical path (10 work packages, 9 waves, 21 packages in all)

1. WP-00 Operator rulings (wave 0)
2. WP-01 Persona contracts, verdict slice (wave 1)
3. WP-07 run_persona litellm backend and LiteLLM telemetry producer (wave 2)
4. WP-11 Persona image, publish channel, local twin, agnosticism and digest checks (wave 3)
5. WP-12 Verdict chain, routing, input projection, check runs, wake, fixture run (wave 4)
6. WP-14 decision_scout add and dual-run (wave 5)
7. WP-15 plan_critic add and dual-run (wave 6)
8. WP-17 decision_scout flip (wave 7)
9. WP-19 plan_critic flip (wave 8)
10. WP-20 Plan-PR personas remove (wave 9)

The code_reviewer branch (WP-13, WP-16, WP-18) runs beside it and finishes two waves earlier. Only WP-11 on the path is not fully designed; its registry fork is one of WP-00's rulings.

## 3. Operator touchpoints

Eighteen counted acts, so the load is heavy, but the shape is light: four design forks and the spend ceiling can be settled before wave 1 in one message. The remaining thirteen are verbatim approvals the coordinator contract binds to the acting worker: one IAM approval and one hand apply of terraform/github for the verdict credential, nine governing-contract wordings (the persona contracts and the T4.10a rescope, the two compute-plane contracts, two Class A telemetry contracts, the inference and configuration rows, the flag-aware consumer contracts, the git-ops wake row, the handoff-gate wording, the alignment carrier), one public-surface approval for publishing the image, and one Decision 235 check on the draft-push rule. Twenty plan approvals are listed but not counted.

## 4. Top findings

- VPA-02 (high). No wake reaches a waiting session when a verdict check run completes; the chain designs none, and the repository forbids polling and self-wakes for CI waits. Fix inside the chain work package.
- VPA-01 (high). The flip criteria name only `/plan` and `/implement`; the overseer trampoline, the coordinator contract's merge rule, the lifecycle state machine and the alignment carriers are unnamed consumers. Write them flag-aware once.
- VPA-04 (high). The code_reviewer flip needs a draft PR before review, which the handoff evidence gate turns into a second 20-25 minute full tier per implementation. Rule on rec-4790's option (b) for draft pushes before wave 1.
- VPA-13 (medium). A workspace-less persona has no designed path to the non-PR context its skill reads today; the shell must materialise main's governance docs and five deterministic pre-computations, and the capability vocabulary needs a read tool.
- VPA-05 (medium). The primary role's selection names aliases DeepSeek discontinued on 2026-07-24, and no run records the served model; correct the ids and reset the dual-run window on a model change.

Of the other nine: the credential leg of T4.2 c9 has no owner (VPA-03), the telemetry producer rec-4028 is an unnamed prerequisite (VPA-08), T4.10a c1 blocks completion on deferred personas (VPA-09), the image channel's registry and provenance are undesigned (VPA-10), injection hardening sits behind the umbrella (VPA-11), the agreement measure lacks a reset rule (VPA-12), persona checks cannot be required checks until rec-4624 (VPA-07), and two are wording (VPA-06, VPA-14).

## 5. Bundling

Bundle rec-4028 into the backend, rec-4433 and rec-4434 into their own capture packages, rec-4477 into the image package, rec-4490 into the fingerprint, rec-4476 into the reviewer flip, rec-4566 with the configuration rows, rec-4080 into the merge-gate predicate, T4.13 c3 into the contracts as a prompt clause, and the Decision 237 cutover for the inference keys into the credential package. Sequence T4.26 (and with it rec-4506's pull-forward) right after the chain, and SHA pinning of existing workflows as its own small plan. Defer T4.14 fixtures, rec-4624 and rec-4506's replay; reject rec-4415 for this path.

## 6. Q8 highlights

- Quality evidence before flip comes from the dual-run itself: deterministic agreement on structured outputs plus the operator's blind adjudication, which is Decision 231's grader shape.
- Keep code_reviewer first in start order; the plan-PR personas will flip later by construction because the critic waits on the scout and both need the fingerprint.
- Gate-changing packages are safe if, before the first flip, a PR touching a gate's skill runs that gate from origin/main's text; after the flip the Actions persona runs main's definition.
- Cost at current volume (434 merges in 30 days) is about USD 120 a month on deepseek-flash (range 100-350; tokens per run are hypotheses) and zero Actions dollars on a public repository.
- Per round the interactive loop is roughly neutral; the two material changes are the draft-PR requirement for the reviewer and the critic loop's many rounds, each now a push that needs a reliable wake.
- The routing rule's bookkeeping-field list held on every sampled implementation PR, so build PRs do not receive plan verdicts.

## 7. Coordinator intake

Verbatim copy of `coordinator_intake` from the YAML; the operator pastes it into the Claude-projects coordinator's memory or shared store.

```text
WORKSTREAM: verdict personas on GitHub Actions (decision_scout, plan_critic, code_reviewer) via DeepSeek through LiteLLM.
Rules: docs/contracts/coordinator.yaml (shared copy). Roadmap items T4.10a, T4.32, T4.28, T4.29, T4.30; Decisions 205, 229, 173, 234, 237, 224, 231.
(1) WORKSTREAMS in wave order
WP-00 Operator rulings (credential delivery, registry and provenance, model selection, draft-push rule, spend ceiling) | wave 0 | depends on nothing | waits on operator: yes
WP-01 Persona contracts, verdict slice (schema, validator, run_persona port, three contracts, rescope c1 to five) | wave 1 | depends on WP-00 | waits on operator: yes (contract text)
WP-02 rec-4433 persona key and definition hash on every producer | wave 1 | depends on nothing | waits on operator: yes (Class A text)
WP-03 LiteLLM transport, model-id correction, api_key rows per role, rec-4566 | wave 1 | depends on WP-00 | waits on operator: yes (contract text)
WP-04 Verdict credential path (OIDC role, SSM grants, persona-verdict Environment) | wave 1 | depends on WP-00 | waits on operator: yes (IAM, hand apply)
WP-05 Scout-input fingerprint and plan-edge extractor, rec-4490 | wave 1 | depends on nothing | waits on operator: no
WP-06 Trigger-policy check and maintainer-actor guards | wave 1 | depends on nothing | waits on operator: no
WP-07 run_persona litellm backend and LiteLLM telemetry producer (rec-4028) | wave 2 | depends on WP-01, WP-02, WP-03 | waits on operator: yes (contract text)
WP-08 rec-4434 per-finding rows | wave 2 | depends on WP-02 | waits on operator: yes (Class A text)
WP-09 Flag-aware gate consumers, flag rows, merge_gate predicate (rec-4080) | wave 2 | depends on WP-00, WP-03 | waits on operator: yes (contract text)
WP-10 Remaining T4.10a contracts (plan_agent, implement_agent) | wave 2 | depends on WP-01 | waits on operator: yes (contract text)
WP-11 Persona image, publish channel (rec-4477), local twin, agnosticism and digest checks | wave 3 | depends on WP-00, WP-07 | waits on operator: yes (public surface)
WP-12 Verdict chain, routing, input projection, check runs, wake, fixture run | wave 4 | depends on WP-04, WP-05, WP-06, WP-11 | waits on operator: yes (git-ops wording)
WP-13 code_reviewer add and dual-run | wave 5 | depends on WP-07, WP-08, WP-09, WP-12 | waits on operator: no (spend ceiling pre-approved)
WP-14 decision_scout add and dual-run | wave 5 | depends on WP-05, WP-07, WP-08, WP-09, WP-12 | waits on operator: no
WP-15 plan_critic add and dual-run | wave 6 | depends on WP-14 | waits on operator: no
WP-16 code_reviewer flip, rec-4476, draft-PR flow under the ruling | wave 6 | depends on WP-00, WP-13 and N=20 agreements | waits on operator: yes (git-ops wording, loosening check)
WP-17 decision_scout flip | wave 7 | depends on WP-14, WP-15 and N=20 | waits on operator: yes (contract text)
WP-18 code_reviewer remove | wave 7 | depends on WP-16 | waits on operator: no
WP-19 plan_critic flip | wave 8 | depends on WP-15, WP-17 and N=20 | waits on operator: no
WP-20 plan-PR personas remove | wave 9 | depends on WP-17, WP-19 | waits on operator: no
Critical path: WP-00, WP-01, WP-07, WP-11, WP-12, WP-14, WP-15, WP-17, WP-19, WP-20.
(2) WAITING ON OPERATOR (counted touchpoints; reply needed)
- Credential delivery for the verdict job: reply A (OIDC role reading SSM, recommended) or B (Environment secret).
- Image registry and provenance: reply A (GHCR public, attestation, hash-locked requirements, recommended) or B (ECR via OIDC).
- Model selection within the primary role: reply A (deepseek-flash for all, v4-pro as plan_critic escalation, recommended) or B (v4-pro for plan_critic now).
- Draft-PR pushes under the handoff evidence gate: reply A (fast-tier evidence for draft pushes, full tier at ready and merge, recommended) or B (two full-tier runs per implementation).
- Spend ceiling: reply yes to about USD 120 a month of DeepSeek spend at current volume (range 100-350), or a different cap.
- WP-04 IAM: approve the persona-verdict OIDC role and grants when the tf-gated-apply approval appears; reply approve in GitHub Actions.
- WP-04 admin: apply terraform/github by hand for the persona-verdict Environment when the worker posts the plan; reply applied with the plan summary.
- WP-01 contract text: approve the persona contracts and T4.10a c1 rescope wording pasted in the worker; reply approved.
- WP-02 Class A text: approve the telemetry_agents change; reply approved.
- WP-03 and WP-07 contract text: approve the inference-provider and project-configuration edits; reply approved.
- WP-08 Class A text: approve telemetry_persona_findings.yaml; reply approved.
- WP-09 contract text: approve the flag-aware wording of the four consumer contracts and the flag rows; reply approved.
- WP-12 contract text: approve the git-ops wake_signals row; reply approved.
- WP-11 public surface: approve publishing the persona image to the public registry; reply approved.
- WP-16 loosening check: confirm whether the fast-tier draft-push rule is a Decision 235 loosening of the handoff gate; reply not a loosening, or approved as one.
- WP-16 contract text: approve the git-ops.yaml handoff_evidence_gate wording for draft-PR pushes; reply approved.
- WP-17 contract text: approve project-alignment.yaml's pr_flow carrier change at the scout flip; reply approved.
- WP-10 contract text: approve the plan_agent and implement_agent contract texts; reply approved.
(3) STANDING-INSTRUCTION CANDIDATES
- A worker touching .claude/skills/<gate>/ or .claude/commands/ runs that gate's in-session subagent on the skill text from origin/main (git show), never the branch copy, until the gate's flag reads actions.
- A worker waiting on a verdict check run subscribes to the PR and ends its turn; it never polls; it confirms the verdict with get_check_runs before merging and passes expectedHeadSha.
- Gate verdicts are read by flag: gates.<persona>.verdict_source in project-configuration.yaml decides whether 'critique PROCEED' and 'code review PROCEED' mean the check run or the in-session subagent.
- Every persona plan records the served model string and definition hash it ran under; a change restarts the dual-run count and is reported, never absorbed.
- DeepSeek spend is reported per persona per week against the approved ceiling; a projected breach pauses the dual-run runs, never the in-session gates.
(4) WAVE-1 INTAKE BRIEFS
WP-01 | seed: /plan T4.10a verdict slice (Decisions 206, 229, 205 cl.2): author docs/contracts/executor-personas.yaml declaring run_persona(contract, workspace, backend) -> (result, events), backend in {litellm, claude_cli}, the capability vocabulary with litellm bindings and a read_input:<blob> capability for workspace-less personas; contracts for decision_scout, plan_critic and code_reviewer with input_schema (materialised head-tree snapshot, main's governance docs, the deterministic pre-computations each skill runs today), output_schema (each skill's verdict vocabulary), max_turns, max_wall_clock_seconds, max_revisions, failure_outputs; rendered system prompts with a recorded hash, each carrying T4.13 c3's quoted-content-is-data clause; validate.py rejects missing schemas, unknown capabilities, unbounded or dollar budgets and persona-to-persona capabilities; rescope T4.10a c1 to the five T4.2 personas. | files: docs/contracts/executor-personas.yaml, docs/contracts/personas/**, config/agent/executor/personas.yaml, scripts/checks/contracts/validate_persona_contracts*.py, scripts/persona_contracts/**, tests/checks/contracts/test_persona_contracts*.py, tests/persona_contracts/** | forks: non-PR context enters as shell-materialised input (default picked, mechanical: hermetic and credential-free per instruction-architecture.yaml); no operator fork.
WP-02 | seed: /plan rec-4433 (Decisions 199, 222, 231; T4.28 c3, T4.29 c2, T4.30 c2): settle the persona key column and its vocabulary for CC-web skills (code-review -> code_reviewer, plan-critique -> plan_critic, decision-scout -> decision_scout), derive it in the claude_code producer from the sub-agent's Skill tool_use, add one typed definition_hash column computed over the bytes the run loaded, retire the two-meaning metadata.prompt_hash, specify the litellm producer's values for WP-07; parser_version bump; Class A text approved verbatim. | files: docs/contracts/telemetry_agents.yaml, src/turn_capture/sessions.py, src/turn_capture/persona_identity.py, tests/turn_capture/test_persona_identity.py, tests/turn_capture/test_sessions*.py | forks: none.
WP-03 | seed: /plan PLAN-llm-client-litellm-transport (T4.32 c1 transport leg, T4.17 c5, Decisions 173, 122, 206 cl.3): scripts/llm/client.py routes every call through LiteLLM resolving provider and model per ROLE from inference-provider.yaml, refusing retired_providers and a same-vendor pair, with Decision 173 cl.4 cold-start assertions; correct litellm_tier_models tier_1 to deepseek/deepseek-flash (escalation deepseek/deepseek-v4-pro per the ruling) and the smoke test's model id; split ports.run_persona.litellm.api_key into one row per role with provider-derived variables; absorb rec-4566; retire or shim the Gemini CLI path; record the served model string on every result. | files: scripts/llm/**, scripts/smoke_test_inference_credentials.py, docs/contracts/inference-provider.yaml, docs/contracts/project-configuration.yaml, scripts/checks/contracts/validate_project_configuration.py, tests/checks/contracts/test_validate_project_configuration.py, tests/test_llm_client*.py, tests/llm/**, requirements-persona.in | forks: model selection -> recommendation, unapproved: A (deepseek-flash for all three, v4-pro as plan_critic's escalation).
WP-04 | seed: /plan verdict credential path (T4.2 c9, Decisions 205 cl.2, 237 cl.2, 223 cl.4; precedent terraform/personal/oidc_trusted_plan_role.tf): declare agent-platform-github-ci-persona-verdict trusted by the GitHub OIDC provider with StringEquals on aud, the persona-verdict Environment sub and job_workflow_ref .github/workflows/persona-verdict.yml@refs/heads/main; ssm:GetParameter on the DeepSeek and Anthropic /secret/ parameter ARNs and the telemetry writer URL invoke; the persona-verdict Environment (protected branches) in terraform/github with an import block; correct secret_parameters.tf's header. Trust CREATE routes to tf-gated-apply; terraform/github applies by hand. | files: terraform/personal/oidc_persona_verdict_role.tf, terraform/personal/secret_parameters.tf, terraform/github/environments.tf, terraform/github/imports.tf, scripts/checks/iam_tf/validate_secret_parameter_grants.py, tests/checks/iam_tf/test_persona_verdict_role.py, docs/contracts/deploy-paths.yaml | forks: credential delivery -> recommendation, unapproved: A (OIDC role reading SSM).
WP-05 | seed: /plan T4.32 c4 scout-input fingerprint (Decisions 229 cl.4, 217 cl.8; rec-4490): one extractor over the PlanDocument (amends, introduces, intent, scope paths, verification_tier, plan_type, cited and amended decision ids with content hashes, roadmap items and depends_on edges, approved text block hashes; approach text excluded) and a deterministic hash; a completeness test; interactive /plan's Decision Scout Gate re-dispatches on a fingerprint change. | files: scripts/roadmap/plan_fingerprint.py, scripts/roadmap/plan_edges.py, scripts/roadmap/plan_document.py, tests/roadmap/test_plan_fingerprint.py, tests/roadmap/test_plan_edges.py, .claude/skills/planning/SKILL.md, .claude/commands/plan.md, tests/test_planning_skill_scout_fingerprint.py | forks: none.
WP-06 | seed: /plan T4.2 c10 trigger-policy check (Decisions 205 cl.1, 229 cl.3, 184 cl.4): a registered check refusing any workflow invoking an LLM persona on schedule, repository_dispatch, pull_request_target or an unrestricted workflow_dispatch, refusing checks: write on pull_request-triggered workflows and any workflow starting a compute-plane persona, exempting claude.yml by name; red fixtures per branch; maintainer-actor guards on ci-rca.yml's dispatch path and the subagent-plan-review steps of reconcile.yml and terraform-apply-sandbox.yml. | files: scripts/checks/ci_guards/validate_persona_trigger_policy.py, tests/checks/ci_guards/test_validate_persona_trigger_policy.py, tests/checks/ci_guards/fixtures/persona_trigger_policy/**, .github/workflows/ci-rca.yml, .github/workflows/reconcile.yml, .github/workflows/terraform-apply-sandbox.yml | forks: none.
```
