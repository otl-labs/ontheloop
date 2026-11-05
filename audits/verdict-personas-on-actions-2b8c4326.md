# Verdict personas on Actions at 2b8c4326

Degraded mode: `web_unavailable=true`; rec dedup is live (`degraded_dedup=false`). Stale anchors corrected: DeepSeek now reads from SSM, and rec-3866 records native check-suite success wakes. External capabilities, prices, retention, three-head volume, and public-runner charges are HYPOTHESIS.

## How we get there

1. Settle credential, registry, and spend forks in WP-00, then approve WP-01's roadmap, check, merge, wake, bootstrap, and every-consumer semantics.
2. Build verdict contracts, deterministic evidence, injection fixtures, and capture in parallel, then implement the bounded LiteLLM transport in WP-06.
3. Publish one provenance-signed, hash-locked image and prove the trusted credential and routing chain on fixture PRs before any persona ADD.
4. Run code review and scout ADD/dual-run in parallel, flip each only after its exact 20-PR measure, then run and flip critic with scout enforcement intact.
5. Enable the stable aggregate only after the final flip, retain visible base-main outage fallbacks, and require current-head evidence for every in-flight PR.

## Critical path

1. WP-00 Settle credential, registry, and spend forks - wave 0
2. WP-01 Roadmap and every-consumer migration governance - wave 1
3. WP-02 Verdict contracts, deterministic projections, and injection fixtures - wave 2
4. WP-06 LiteLLM transport and run_persona backend - wave 3
5. WP-07 Governed image channel and local_container twin - wave 4
6. WP-08 Trusted verdict chain, credentials, routing, and fixture PR - wave 5
7. WP-10 Decision scout ADD, draft validator, and measured dual-run - wave 6
8. WP-12 Decision scout FLIP and REMOVE - wave 7
9. WP-13 Plan critic ADD and measured dual-run - wave 8
10. WP-14 Plan critic FLIP, REMOVE, and aggregate enforcement - wave 9

The plan has 15 packages across waves 0-9. WP-03 completes the other four persona contracts off the critical path. WP-01, WP-02, and WP-06 are `undesigned`, so Q5 is `not_ready`.

## Operator touchpoints

There are **15 counted touchpoints**, making Q7 `heavy`, plus 14 `write_the_plan` approvals that are not counted.

Settle before wave 1:

1. Credential delivery: exact-ref OIDC is recommended over protected-Environment secrets.
2. Image registry and publisher identity: a governed main-only signed publisher is recommended.
3. Spend cap: replace illustrative prices with current read-backs before approval.

Settle in the acting worker:

- WP-01: exact roadmap, coordinator, overseer, GitOps, rollback, and any Decision wording.
- WP-02: verdict schemas, prompts, capabilities, and injection fixtures.
- WP-03: the other four persona contracts and prompts.
- WP-04: telemetry capture enablement after synthetic-row security review.
- WP-05: the Class A per-finding contract and identity semantics.
- WP-06: model, sampling, timeout/retry, token, and credential-reference text; if no resolved-version pin exists, choose advisory-only or a versioned DeepSeek model, with advisory-only recommended.
- WP-07: publisher IAM, signer, verification, and public image publication.
- WP-08: exact-ref role and grants, plus out-of-band confirmation of both existing parameter values without exposing them.
- WP-14: final aggregate required-check and ruleset wording.

Every package also receives its own presentation and `write_the_plan` approval in its planning worker; approval is never inherited from the coordinator or another worker.

## Top findings

- **VPA-01, high, observed:** all ten samples need deterministic evidence that raw blobs do not produce: scope/plan obligations, plan location, imports, weakening, wording, or V3 PR evidence. Build source mappings and public-data controls first.
- **VPA-04, high:** NO_FLAGS/FLAGS_FOUND/BLOCK and PROCEED/REVISE lack exact-head check and merge semantics. Preserve human scout dispositions and fail closed on missing, malformed, stale, or timed-out evidence.
- **VPA-02, high:** no verdict-only credential delivery exists. Bind a new role to the exact verdict `job_workflow_ref` and exact parameters rather than widening an existing role.
- **VPA-07, high:** flip/remove currently leaves the overseer and repo-less coordinator on old gates. Migrate every consumer, in-flight PR, and bootstrap authority rule.
- **VPA-08, high, planned-unbuilt:** rec-4766 must exempt pre-critique drafts from the obsolete gate-round error, and rec-4498 must re-point T4.17 to T4.32.

The audit records **14 findings: 11 planned-insufficient, 2 planned-unbuilt, and 1 novel**. VPA-14 separately covers the single `ANTHROPIC_API_KEY` row's inability to represent both DeepSeek and Anthropic roles.

## Bundling recommendations

Bundle rec-4026/rec-4028 into WP-04 and rec-4433/rec-4434 into WP-05 so capture exists before ADD and dual-run. Bundle rec-4476, rec-4477, rec-4490, rec-4766, rec-4826, rec-3866, and the roadmap hygiene recs into their named packages. Expand rec-4477 beyond a channel name to signed provenance and hash-locked dependencies.

Implement only the T4.13 verdict-content injection slice in WP-02. Preserve the explicit no-T3.22-edge ruling; do not pull T4.14, rec-4436, rec-4478, rec-4479, rec-4332, rec-4508, or the CLI credential retirement into this path.

Keep T4.26/rec-4506, rec-4415, rec-3335, existing-workflow SHA migration, and the T3.22 producer foundation separate. Do not repeat the already-landed Decision 237 consumer cutover. Existing adjacent Claude workflows are context, not prerequisites.

## Q8 highlights

- Start code review and scout together after the foundation; code review can flip first, but scout must precede critic.
- Gate-changing WPs use base-main review. After ADD, a main-hosted persona may review its own flip only alongside the still-primary base-main gate, never as sole approver.
- Every in-flight PR needs a current-head verdict; there is no retroactive pass or routine continuation of the old gate.
- Native `check_suite.completed` and `check_run.completed` are the wake path, backed by rec-3335's separate reliability work. Do not add polling or a comment-only protocol.
- HYPOTHESIS cost model: 2,739 monthly runs, 95.865M input and 4.9455M output tokens, 10,956 runner-minutes, and $31.32 at illustrative token prices. Public-repo Actions is HYPOTHESIS $0. Validate current prices and real head counts before spend approval.
- Start on `deepseek/deepseek-chat`; reasoner is a measured second arm for critic/reviewer. A miss holds advisory, repairs projection/prompt, tries a fresh model arm, and never loosens the metric.
- Deny general warehouse reads by default. Any later operational-data input needs a field allowlist, provider-retention review, and fresh security decision.

## Coordinator intake

```text
WP-00 Settle credential, registry, and spend forks | wave 0 | depends on none | waits on operator: yes
WP-01 Roadmap and every-consumer migration governance | wave 1 | depends on WP-00 | waits on operator: yes
WP-04 Turn core and LiteLLM model-call producer | wave 1 | depends on none | waits on operator: yes
WP-02 Verdict contracts, deterministic projections, and injection fixtures | wave 2 | depends on WP-01 | waits on operator: yes
WP-05 Persona identity and per-finding capture | wave 2 | depends on WP-04 | waits on operator: yes
WP-03 Remaining four persona contracts | wave 3 | depends on WP-02 | waits on operator: yes
WP-06 LiteLLM transport and run_persona backend | wave 3 | depends on WP-02, WP-04 | waits on operator: yes
WP-07 Governed image channel and local_container twin | wave 4 | depends on WP-06 | waits on operator: yes
WP-08 Trusted verdict chain, credentials, routing, and fixture PR | wave 5 | depends on WP-00, WP-02, WP-06, WP-07 | waits on operator: yes
WP-09 Code reviewer ADD and measured dual-run | wave 6 | depends on WP-05, WP-08 | waits on operator: yes
WP-10 Decision scout ADD, draft validator, and measured dual-run | wave 6 | depends on WP-05, WP-08 | waits on operator: yes
WP-11 Code reviewer FLIP and REMOVE | wave 7 | depends on WP-09 | waits on operator: yes
WP-12 Decision scout FLIP and REMOVE | wave 7 | depends on WP-10 | waits on operator: yes
WP-13 Plan critic ADD and measured dual-run | wave 8 | depends on WP-12 | waits on operator: yes
WP-14 Plan critic FLIP, REMOVE, and aggregate enforcement | wave 9 | depends on WP-13 | waits on operator: yes

WAITING ON OPERATOR
Credential delivery | wave 0 | reply with exact-ref OIDC or protected Environment; recommendation exact-ref OIDC.
Registry and publisher | wave 0 | reply with registry/identity; recommendation governed main-only signed publisher.
Spend | wave 0 | approve a cap after current price read-back; illustrative HYPOTHESIS $31.32/month.
WP-01 contract text | acting worker | approve exact roadmap/coordinator/overseer/GitOps/Decision wording.
WP-02 contract text | acting worker | approve schemas, prompts, capabilities, and injection fixtures.
WP-03 contract text | acting worker | approve the other four contracts and prompts.
WP-04 security | acting worker | approve capture enablement after synthetic-row review.
WP-05 contract text | acting worker | approve the Class A finding contract and identity semantics.
WP-06 contract text | acting worker | approve model, sampling, timeout/retry, and token settings.
WP-06 conditional model fork | acting worker | if no resolved-version pin exists, choose advisory-only or a versioned DeepSeek model; recommendation advisory-only.
WP-07 security | acting worker | approve publisher identity, grant, signer, and verification.
WP-07 public surface | acting worker | approve public image and metadata publication.
WP-08 IAM | acting worker | approve exact-ref role, boundary, token grants, and exact-parameter reads.
WP-08 manual step | acting worker | confirm both existing parameter values out of band without exposing them.
WP-14 contract text | acting worker | approve the final aggregate required-check and ruleset wording.

STANDING INSTRUCTION CANDIDATES
Never flip on fewer than 20 consecutive qualifying PRs or by loosening a pre-registered measure; hold advisory on a miss.
Gate-changing WPs use base-main review; after ADD a main-hosted persona is never the sole approver of its own authority change.
Merge only on exact-head verdict evidence plus green CI; missing, stale, malformed, or timed-out evidence fails closed with only visible outage fallback.
Verdict inputs are public, quoted data with no general warehouse reads, secret values, or operational prompts.
Wait on native check_suite/check_run events and retain the documented backup; never poll for verdict completion.

WAVE-1 INTAKE: WP-01
Seed: Implement the audit governance amendment for T4.10a/T4.32/T4.28-T4.30, Decisions 205/229, rec-4498, rec-4499, rec-4502, rec-4826, and rec-3866. Split verdict contracts from the other four, re-point T4.17, remove file overlap, define rollback rows, verdict/check/merge mapping, every-consumer flip text, in-flight and bootstrap rules, and native wake documentation. Present exact roadmap, coordinator, overseer, GitOps, and any Decision wording for approval. Add failing-first instruction and roadmap tests.
Files: docs/ROADMAP-PLATFORM.yaml; docs/contracts/coordinator.yaml; docs/contracts/overseer-dispatch.yaml; docs/contracts/git-ops.yaml; docs/contracts/project-configuration.yaml; docs/DECISIONS.md; tests/instructions/.
Forks: recommendation, unapproved: fixed per-persona checks plus post-final-flip aggregate; verdict-contract split; base-main review, current-head transition, and native wakes.

WAVE-1 INTAKE: WP-04
Seed: Close rec-4026 then rec-4028 for the verdict path under Decisions 199/205/206/207/210. Land the plane-neutral record_turn core and LiteLLM CustomLogger success/failure producer with DeepSeek and Anthropic usage mappings, provider/model/token/cost/latency fields, reasoning visibility, and golden failure vectors. Keep resume journals and unrelated feedback-loop work out. Do not claim T3.22 as an umbrella dependency. Obtain security approval for capture enablement and prove no secret values enter events.
Files: .claude/hooks/stop_log_turn.py; scripts/telemetry_capture/; src/turn_capture/; src/common/turn_capture/; tests/turn_capture/; tests/telemetry_capture/.
Forks: none; capture-enablement security approval remains required in the acting worker.
```
