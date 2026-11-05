# Audit: verdict personas on GitHub Actions (DeepSeek via LiteLLM) - S1-S5

Audited commit: b3044d78 (main). Full record: `audits/verdict-personas-on-actions-b3044d78.yaml`.

Degraded mode: web_unavailable (no web tool; every external fact is HYPOTHESIS), no mcp__github__* tools in this harness (the PR is opened by a human per Section 16 step 5), no stale anchors.

## 1. How do we get there

1. Slice T4.10a to the three verdict personas (contracts, rendered prompts, run_persona port) so the path stops waiting on four non-verdict contracts (VPA-01).
2. Build the foundation as three plans: capture (close rec-4433/4434/4028), the LiteLLM transport, then the digest-pinned image channel with its local twin (T4.32 c1-c2).
3. Land the verdict chain (secretless trigger, thin shell, identity, routing, fingerprint, check runs, wake comment) using the operator's wave-0 credential ruling (T4.32 c3-c4, T4.2 c8-c13).
4. ADD each persona advisory, capturing from run one, with pre-registered agreement measures; dual-run N >= 20 per persona (T4.28-T4.30 add criteria).
5. Flip per persona on measured agreement (rewording the coordinator and overseer contracts first), then remove the in-session dispatches; the per-persona flag rows roll back visibly.

## 2. Critical path (10 of 16 work packages; waves 0-10)

1. WP-00 (wave 0) Operator rulings: credential mechanism, spend, T4.10a slice, model selection
2. WP-01 (wave 1) T4.10a verdict-persona slice: contracts, rendered prompts, run_persona port
3. WP-02 (wave 2) Capture foundation: close rec-4433, rec-4434, rec-4028; admit the verdict producer
4. WP-04 (wave 3) Persona image channel (rec-4477) + local_container twin + conformance suite
5. WP-05 (wave 4) Verdict chain: trigger, shell, identity, routing, fingerprint, checks
6. WP-06 (wave 5) decision_scout ADD + dual-run start
7. WP-08 (wave 6) plan_critic ADD + dual-run start
8. WP-12 (wave 8) plan_critic FLIP
9. WP-14 (wave 9) decision_scout REMOVE
10. WP-15 (wave 10) plan_critic REMOVE

Off-path: WP-03 transport (wave 2), WP-07 code_reviewer ADD (wave 5), WP-09 consumer flip mechanics (wave 5), WP-10 code_reviewer FLIP (wave 6), WP-11 decision_scout FLIP (wave 7), WP-13 code_reviewer REMOVE (wave 7). Q5 verdict: ready_with_forks - WP-04 carries the one needs_operator_fork (image registry and signing); no WP is undesigned.

## 3. Operator touchpoints

Eight counted touchpoints (always_ask or design fork); four settle in the WP-00 session before wave 1: the verdict-job LLM-key delivery mechanism (recommendation: OIDC role bound to the verdict workflow ref reading the Decision 237 SSM parameter), the monthly spend (order $10-40 DeepSeek tokens at ~1,500 verdict runs, $0 Actions minutes on public-repo standard runners; HYPOTHESIS), the T4.10a slice amendment wording, and the per-persona model selection (recommendation: chat/reasoner/reasoner). Later: image registry + signing (WP-04), the key value set out of band (WP-05), the verdict producer identity grant (WP-02/WP-06), and the coordinator/overseer contract rewording (WP-09). Four write_the_plan approvals (WP-01, WP-10, WP-11, WP-12) are listed but not counted.

## 4. Top findings (11 total: 6 novel, 3 planned-insufficient, 2 planned-unbuilt)

- VPA-09 (high, novel): no wake mechanism exists for verdict check-run completion; every flip step's central wait would strand on the watcher's hours cadence or poll. Add the completion wake comment with the verdict chain (WP-05).
- VPA-10 (high, planned-insufficient): T4.13's prompt-injection hardening is sequenced after the umbrella completes, leaving the untrusted-content personas unhardened through dual-run and flip. Pull its verdict-persona legs into the ADDs.
- VPA-11 (high, planned-insufficient): the flip criteria name only /plan and /implement; the /overseer trampoline and the coordinator contract would keep dispatching in-session gates after a flip. Land WP-09's rewording before the first flip.
- VPA-01 (medium, novel; highest leverage): T4.10a's seven-contract scope blocks the three verdict personas on four non-verdict contracts. Slice it; everything else waits on this.
- VPA-08 (medium, novel): rec-4433, rec-4434 and rec-4028 are named capture prerequisites that no item owns closing. WP-02 absorbs them.

## 5. Bundling recommendations

Bundle: the capture recs (rec-4433/4434/4028, rec-4415) into WP-02; rec-4477 into WP-04; rec-4476 into WP-10 (its closure is T4.28 c6); rec-4490 into WP-11; T4.13's verdict-persona legs into the ADD WPs (WP-06/07/08); SHA pinning of the existing workflows into WP-05; T4.17 c1/c5 into WP-02/WP-03. Sequence separately: T4.26 and rec-4506 (their own lane; the OAuth token expiry 2027-05-31 is the watch-signal), and the Decision 237 secrets-to-ssm cutover (a WP-05 prerequisite only under the OIDC ruling). Defer: rec-4566 and the T4.14 fixtures (deferred_post_mvp; the dual-run rec-4434 rows are the interim regression signal).

## 6. Q8 highlights

- Cost: ~1,500 verdict runs/month at current volume (436 first-parent merges in 30 days; 3 runs per PR assumed, HYPOTHESIS); order $10-40/month in DeepSeek tokens, $0 Actions minutes on public-repo standard runners (pricing unverified).
- Scout before critic is structurally forced (shared plan.md/planning SKILL.md files plus the re-scout-required coupling); code_reviewer may flip first safely.
- Bootstrap is safe: the verdict job always runs main's definition, so no persona gates its own flip PR; the residual window is the rec-4476 self-review gap, which closes at WP-10.
- In-flight branches keep gates: advisory check runs re-trigger on the next push, and the per-persona flag rolls back to the in-session subagent visibly.
- DeepSeek quality for a blocking gate is unproven either way; the pre-flip evidence is the dual-run record scored against escape ground truth, not an LLM grader (Decision 231).
- One out-of-scope candidate noted: /plan Step 10's REPORT-ONLY deliverable critique becomes a cheap contract instance after WP-08; the /audit verification subagents stay in-session.

Maturity: S1 strong; S2-S4 nascent; S5 frontier.

## 7. Coordinator intake

```
WORKSTREAM: verdict personas on GitHub Actions (DeepSeek via LiteLLM). Base commit b3044d78 on main.
You cannot read the repository: every brief below is self-contained; your workers hold clones.

(1) WORKSTREAM LINES (wave order):
WP-00 Operator rulings: credential mechanism, spend, T4.10a slice, model selection | wave 0 | depends on: nothing | waits on operator: yes
WP-01 T4.10a verdict-persona slice: contracts, rendered prompts, run_persona port | wave 1 | depends on WP-00 | waits on operator: yes (write_the_plan)
WP-02 Capture foundation: close rec-4433, rec-4434, rec-4028; admit the verdict producer | wave 2 | depends on WP-01 | waits on operator: yes (producer grant)
WP-03 LiteLLM transport + run_persona litellm backend | wave 2 | depends on WP-01 | waits on operator: no
WP-04 Persona image channel + local_container twin + conformance suite | wave 3 | depends on WP-02, WP-03 | waits on operator: yes (registry fork)
WP-05 Verdict chain: trigger, shell, identity, routing, fingerprint, checks | wave 4 | depends on WP-03, WP-04 | waits on operator: yes (key value out of band)
WP-06 decision_scout ADD + dual-run start | wave 5 | depends on WP-02, WP-05 | waits on operator: no
WP-07 code_reviewer ADD + dual-run start | wave 5 | depends on WP-02, WP-05 | waits on operator: no
WP-09 Consumer flip mechanics: reword coordinator + overseer contracts | wave 5 | depends on WP-05 | waits on operator: yes (wording)
WP-08 plan_critic ADD + dual-run start | wave 6 | depends on WP-05, WP-06 | waits on operator: no
WP-10 code_reviewer FLIP (closes rec-4476) | wave 6 | depends on WP-07, WP-09 + dual-run agreement | waits on operator: yes (write_the_plan)
WP-11 decision_scout FLIP (closes rec-4490) | wave 7 | depends on WP-06, WP-09, WP-10 + agreement | waits on operator: yes (write_the_plan)
WP-13 code_reviewer REMOVE | wave 7 | depends on WP-10 | waits on operator: no
WP-12 plan_critic FLIP | wave 8 | depends on WP-08, WP-11 + agreement | waits on operator: yes (write_the_plan)
WP-14 decision_scout REMOVE | wave 9 | depends on WP-11, WP-12, WP-13 | waits on operator: no
WP-15 plan_critic REMOVE | wave 10 | depends on WP-12, WP-14 | waits on operator: no
Parallel-within-wave rule: only the waves' listed WPs share a wave, and their file sets are disjoint; never run two WPs that both touch .claude/commands/plan.md or .claude/skills/planning/SKILL.md.

(2) WAITING ON OPERATOR (reply needed):
1. Verdict-job LLM-key delivery: reply "oidc" or "environment". Recommendation, unapproved: oidc (key stays AWS-side per the secrets-to-ssm decision; the environment path copies it to GitHub).
2. Monthly spend: reply "approved" or a cap. Estimate: DeepSeek tokens order $10-40/month at ~1,500 verdict runs; Actions minutes $0 on public-repo standard runners. Figures are hypotheses (pricing unverified this session).
3. T4.10a slice amendment: the exact roadmap wording is pasted at WP-01's presentation; reply "write the plan" there. Recommendation, unapproved: approve the slice (three verdict contracts first, the other four on the remainder).
4. Per-persona model selection: reply "chat/reasoner/reasoner" (scout/critic/reviewer) or your own triple. Recommendation, unapproved: chat/reasoner/reasoner.
5. Image registry + signing (at WP-04): reply "ecr" or "ghcr". Recommendation, unapproved: ecr.
6. LLM key value set out of band (at WP-05): reply "done" after setting it in the ruled home.
7. Verdict producer identity grant (at WP-02/WP-06): reply "approved" naming the grant text pasted in the acting worker.
8. Coordinator + overseer contract rewording (at WP-09): reply "write the plan" with the exact wording pasted there.

(3) STANDING-INSTRUCTION CANDIDATES (bind every worker):
- Never edit a file outside your work package's file ownership; a gap you find is a recommendation or a plan-defect report, never an inline fix.
- Every gate round (scout, critique, code review) runs fresh-context; never run a gate inline in your own session.
- Never poll for CI or check-run state: subscribe, end the turn, and let the wake comment or the watcher resume you.
- Record every measured figure with the command that produced it; re-measure before disputing one.
- Report partial results as partial; never drop real end-to-end testing for environment limits.

(4) WAVE-1 INTAKE BRIEF (WP-01):
Plan seed: plan the T4.10a verdict-persona slice per Decision 229 and the operator's slice ruling:
contract-versioned YAML for decision_scout, plan_critic and code_reviewer (input_schema, output_schema,
persona capabilities, max_turns AND max_wall_clock_seconds, max_revisions, failure_outputs per T4.10a c1
as amended by Decision 206 cl7), validate.py rejection rules (c2), rendered system prompts under
docs/contracts/personas/ with per-version content hashes (c5), executor-personas.yaml declaring
run_persona(contract, workspace, backend) with the capability vocabulary and litellm bindings (c6), the
one MCP server binding for read capabilities (c7), hub-and-spoke contract rejection (c8). Amend T4.10a
c1/c5 to record the slice. Governing Decisions: 205, 206, 229. File ownership:
docs/contracts/executor-personas.yaml, docs/contracts/personas/{decision_scout,plan_critic,code_reviewer}.yaml,
config/agent/executor/personas.yaml, scripts/validate.py, docs/ROADMAP-PLATFORM.yaml (slice amendment only).
Forks: input-projection shape (one-shot pre-assembled input vs MCP read loop for the scout's bounded
retrieval) -- mechanical, default picked: MCP read loop for decision_scout, one-shot projection for
plan_critic and code_reviewer; settle on advisor evidence and list it as settled. The slice amendment
wording is an operator fork: recommendation, unapproved: approve the slice.
```
