# Verdict personas on GitHub Actions via DeepSeek and LiteLLM - readiness audit

Audited commit `2b8c4326` (base `main`). Companion machine-readable file: `verdict-personas-on-actions-2b8c4326.yaml`.

**Degraded mode:** `degraded_dedup: false` (the recommendation reader returned rows); `web_unavailable: true` (no search or fetch tool in this session, so every external fact - DeepSeek context window, tool calling, pricing, GitHub Actions pricing - is carried as HYPOTHESIS); **stale anchors:** none.

## 1. How do we get there

1. Wave 0 settles the two operator acts nothing else substitutes for: the verdict job's own credential identity, and the coordinator plus project-alignment text that says what "PROCEED" reads once a verdict is a check run.
2. Waves 1-2 build the foundation in one arc - persona contracts, LiteLLM transport and backend, capture prerequisites, image channel with its local twin - and every persona ADD waits on it.
3. Waves 3-6 add the missing middle: the verdict input projection (what a persona may read, and its size budget), the verdict chain with its wake comment, the injection hardening, then the code-reviewer and scout ADDs (advisory only, N never counted yet).
4. Waves 7-9 run both dual-run windows and flip code_reviewer then decision_scout, each behind its own per-persona rollback flag, each flip PR reviewed by the two gates it does not change.
5. Plan-critic flips last (waves 7-11): its criterion makes it fail "re-scout required" while the head has no scout verdict, so its ADD, dual-run, flip and remove all trail the scout's.

## 2. Critical path

Ten packages, from `execution_plan.critical_path` (waves in brackets):

1. WP-03 Persona contracts for the seven personas (wave 1)
2. WP-04 LiteLLM transport and run_persona backend (wave 2)
3. WP-06 Digest-pinned persona image, build-and-publish channel, local_container twin and conformance suite (wave 3)
4. WP-07 Verdict input projection and persona read surface (wave 4)
5. WP-08 Verdict chain (trigger, workflow_run job, verdict identity, trigger-policy and agnosticism checks, fixture run, wake) (wave 5)
6. WP-11 decision_scout ADD (wave 6)
7. WP-14 plan_critic ADD (wave 7)
8. WP-18 plan_critic dual-run window (wave 8)
9. WP-21 plan_critic FLIP (wave 10)
10. WP-22 plan_critic REMOVE (wave 11)

22 packages in total, waves 0-11 (`wave_count: 11`). The chain's last three links are single-package waves on purpose: WP-20 (scout REMOVE) must land before WP-21 (critic FLIP) because both own `plan.md` and the planning skill, and WP-22 closes the migration.

## 3. Operator touchpoints

Nine counted (`always_ask` or `design_fork`), plus one plan approval per plan. Eight settle before wave 1, so the fork answers can be collected in one reply:

| Settles before wave 1 | Act |
|---|---|
| yes | Approve the verdict-job identity - OIDC role bound to the verdict workflow ref (recommended) or an Environment restricted to main |
| yes | Apply the human-gated `terraform/github` root in the admin container |
| yes | Approve the coordinator wording (check-run reading plus PR-flow step order) |
| yes | Approve the project-alignment wording (persona carrier, PR flow live) |
| yes | Decide the three pre-registered agreement measures |
| yes | Decide the input projection: what a persona may read beyond the head-SHA blobs |
| yes | Approve `.claude/skills/` and `.claude/commands/` joining `boundary_patterns` (rec-4476) |
| yes | Authorise the inference spend - about $100/month at measured volume, Actions minutes $0 on a public repo |
| no | Set the tier-1 and tier-2 key values out of band (inside the acting worker, per the coordinator's approvals rule) |

## 4. Top findings

- **VPA-01 (critical)** - the schema-validated verdict input has no designed source for what the three personas actually read. `T4.2 c8` fixes the transport ("files and blobs at the head SHA") and `T4.10a c6` sets the workspace to none, but the skills also read the plan via a branch, `PROJECT_CONTEXT.md`, Decision sections, the roadmap, the archive's exemption list, scope files at current content, one-level imports and CI files. The only designed read surface is the MCP server, which projects warehouse verbs and serves none of this. Measured: plan scope reads of 88,982 B to 1,431,014 B, and changed-file sets up to 1,473,744 B.
- **VPA-04 (high)** - no credential delivery object and no owning Terraform root. Three Environments exist and none is for a verdict job; `terraform/github` is human-gated local apply; nothing reads the SSM parameter yet.
- **VPA-02 (high)** - no wake signal for a concluded verdict check run. `subscribe_pr_activity` has no success event, `ci.yml`'s signal-green comment covers its own workflow only, and CC-web has no sleep tool. A flip would trade a synchronous wait for an unbounded one.
- **VPA-11 (high)** - the coordinator contract cannot express a check-run gate: its lifecycle orders critique before the plan PR exists, and its merge authority merges on "critique PROCEED" without saying what that reads.
- **VPA-08 (high)** - the prompt-injection threat model (`T4.13`) hangs off the umbrella, so the personas read untrusted plans and diffs and gain blocking power before the hardening and its red-team fixture exist.

Also: VPA-03 (the credential row names `ANTHROPIC_API_KEY` for a DeepSeek-direct tier and cannot express the fallback) and VPA-05 (no verdict-to-check-run conclusion or name mapping). Counting invariant: 13 findings = 2 novel + 10 planned-insufficient + 1 planned-unbuilt. Maturity: S1-S4 solid, S5 frontier.

## 5. Bundling recommendations

- **Bundle** rec-4477 into the image package, rec-4433 + rec-4434 + **rec-4028** into the capture package (4028 is the LiteLLM producer and no persona criterion names it), rec-4476 into the contracts package (its acceptance is a `boundary_patterns` edit, which the flip cannot carry), and `T4.13` c1/c3/c4 into an explicit flip precondition rather than an umbrella afterthought.
- **Sequence separately** `T4.13 c2` (provenance tagging serves the rec-fed personas), rec-4415 (CI agent capture), rec-4490 (interactive re-scout reusing the same extractor) and repo-wide SHA pinning.
- **Defer** T4.26, rec-4506's pull-forward, T4.14 and rec-4566 - all off this path.

## 6. Q8 highlights

- **Quality evidence before a blocking flip:** replay 20 merged plan PRs and 20 merged code PRs whose defects are known from later recs and fix commits, and score the persona on whether it flags them. That is a real-outcome label, which Decision 231 permits; until it exists, advisory only.
- **Order:** code_reviewer ADD first, but the critic is the gate whose absence costs the most wall-clock, and it cannot start before the scout's verdict exists.
- **Bootstrap:** the flip PRs are reviewed by the very gate text they edit, and `boundary_patterns` covers `.claude/agents/` but not `.claude/skills/` or `.claude/commands/`.
- **Cost:** ~315M input tokens/month at measured volume, about $100/month, dominated by plan-critique; Actions minutes $0 on a public repository's standard runners (both pricing figures HYPOTHESIS).
- **Wall-clock:** after a flip without a wake, the session blocks forever; with the per-persona wake comment the wait becomes one comment-to-turn round trip.
- **Coverage hole:** a Decision amended on main after the scout ran does not re-scout, so a covered head SHA can carry a stale verdict.
- **`/implement`:** the one-full-tier rule needs restating for a check-run gate, because the commit and PR must move ahead of the review.
- **MCP:** none of the three personas needs the On The Loop MCP server - the capability they need is a repository-content read, which no surface provides.

## 7. Coordinator intake (verbatim)

```text
VERDICT PERSONAS ON ACTIONS -- WORKSTREAM INTAKE (audit verdict-personas-on-actions-2b8c4326)

WORKSTREAM (wave order)
WP-01 Verdict-job credential boundary | wave 0 | depends on nothing | waits on operator: yes
WP-02 Settle the operating contracts | wave 0 | depends on nothing | waits on operator: yes
WP-03 Persona contracts for the seven personas | wave 1 | depends on nothing | waits on operator: yes
WP-04 LiteLLM transport and run_persona backend | wave 2 | depends on WP-03 | waits on operator: yes
WP-05 Capture prerequisites | wave 1 | depends on nothing | waits on operator: no
WP-06 Image channel, local twin and conformance | wave 3 | depends on WP-03, WP-04 | waits on operator: no
WP-07 Verdict input projection and read surface | wave 4 | depends on WP-03, WP-04, WP-06 | waits on operator: yes
WP-08 Verdict chain and wake | wave 5 | depends on WP-01, WP-04, WP-06, WP-07 | waits on operator: yes
WP-09 Injection threat model and hardening | wave 6 | depends on WP-03, WP-08 | waits on operator: no
WP-10 code_reviewer ADD | wave 6 | depends on WP-05, WP-06, WP-08 | waits on operator: no
WP-11 decision_scout ADD | wave 6 | depends on WP-05, WP-06, WP-08 | waits on operator: no
WP-12 code_reviewer dual-run | wave 7 | depends on WP-10, WP-05 | waits on operator: no
WP-13 decision_scout dual-run | wave 7 | depends on WP-11, WP-05 | waits on operator: no
WP-14 plan_critic ADD | wave 7 | depends on WP-05, WP-06, WP-08, WP-11 | waits on operator: no
WP-15 Rec-content provenance | wave 2 | depends on WP-03 | waits on operator: yes
WP-16 code_reviewer FLIP | wave 8 | depends on WP-12, WP-02, WP-09 | waits on operator: yes
WP-17 decision_scout FLIP | wave 8 | depends on WP-13, WP-02, WP-09 | waits on operator: yes
WP-18 plan_critic dual-run | wave 8 | depends on WP-14, WP-05 | waits on operator: no
WP-19 code_reviewer REMOVE | wave 9 | depends on WP-16 | waits on operator: no
WP-20 decision_scout REMOVE | wave 9 | depends on WP-17 | waits on operator: no
WP-21 plan_critic FLIP | wave 10 | depends on WP-18, WP-17, WP-20, WP-02, WP-09 | waits on operator: yes
WP-22 plan_critic REMOVE | wave 11 | depends on WP-21 | waits on operator: no

WAITING ON OPERATOR (9 counted; reply needed in one line each)
1. WP-01/WP-08 IAM and security: approve a verdict-job identity - OIDC role bound to the verdict workflow ref (recommended) or an Environment restricted to main. Reply: "oidc" or "environment".
2. WP-01 manual/admin: apply the human-gated terraform/github root in the admin container. Reply: "applied" with the run id.
3. WP-01 manual/admin: set the tier-1 and tier-2 key values out of band. Reply: "set" (inside the WP-01 worker).
4. WP-02/WP-16/WP-17/WP-21 contract text: approve coordinator wording reading a verdict as a check-run conclusion, and the PR-flow step order. Reply: "approved".
5. WP-02/WP-17 contract text: approve the project-alignment wording naming the persona carrier, PR flow live. Reply: "approved".
6. WP-02/WP-12/WP-13/WP-18 design fork: the three agreement measures. Recommendation: adopt the audit's three, in one contract. Reply: "yes".
7. WP-07 design fork: what a persona may read beyond head-SHA blobs. Recommendation: add bounded read-only repository-content tools to the On The Loop MCP server, plus a per-persona projection budget that fails the verdict. Reply: "yes" or the alternative.
8. WP-03 contract text: approve boundary_patterns gaining .claude/skills/ and .claude/commands/. Reply: "approved".
9. WP-04/WP-16 spend: authorise about $100/month of inference (Actions minutes are free on a public repo). Reply: "yes".

STANDING-INSTRUCTION CANDIDATES (bind every worker)
1. A verdict job never calls the GitHub API, never reads GITHUB_TOKEN and never invokes gh; the shell is the only GitHub-aware layer.
2. The plan PR is the handoff: a plan is not approved until the verdict of record is on the PR head SHA.
3. A gate that has not concluded is never a pass; a missing, malformed or timed-out verdict is a failure.
4. Never model a verdict on a PR comment, label, review or the PR body; comments are wake signals only.
5. Each persona's flip flag has an off value that restores the in-session gate visibly, and no worker may flip a gate in the package that also builds it.

INTAKE BRIEF -- WP-03 (persona contracts)
Seed: Author the seven persona contracts under docs/contracts/personas/ with rendered prompts and content hashes, the executor-personas registry declaring run_persona and the capability vocabulary with litellm bindings, and the validate.py rejections; add .claude/skills/ and .claude/commands/ to boundary_patterns (rec-4476). Closes T4.10a c1-c8. Decisions 206, 117, 229 cl.2, 185 cl.3.
Files owned: docs/contracts/executor-personas.yaml, docs/contracts/personas/, config/agent/executor/personas.yaml, config/agent/executor/capabilities.yaml, scripts/validate.py, tests/checks/contracts/.
Fork: boundary_patterns edit -- recommendation, unapproved: include .claude/skills/ and .claude/commands/.

INTAKE BRIEF -- WP-04 (LiteLLM transport and backend)
Seed: Rewrite scripts/llm/client.py onto the in-process LiteLLM SDK reading tier membership from inference-provider.yaml; add scripts/personas/run_persona.py with the litellm backend; split the run_persona litellm credential into one row per provider role; pin litellm in the runtime lock. Discharges T4.2 c4. Decisions 173, 122 (CD.28), 206 cl.8.
Files owned: scripts/llm/client.py, scripts/personas/run_persona.py, docs/contracts/project-configuration.yaml, docs/contracts/inference-provider.yaml, requirements.txt, tests/llm/, tests/personas/.
Fork: credential-row rewording -- recommendation, unapproved: one api_key row per tier role, tier 1 naming the DeepSeek variable.

INTAKE BRIEF -- WP-05 (capture prerequisites)
Seed: Close rec-4433, rec-4434 and rec-4028 before any persona ADD: persona key and definition hash on every producer, one row per finding keyed to the agent run, and the LiteLLM CustomLogger success/failure producer feeding record_turn. Decisions 199, 221 cl.2, 231.
Files owned: src/turn_capture/, tests/turn_capture/, tests/telemetry/.
Forks: none.
```
