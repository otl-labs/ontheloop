# Audit: verdict personas on GitHub Actions via DeepSeek and LiteLLM (S1-S5)

Audited commit: `b3044d78` (main). Full record: `audits/verdict-personas-on-actions-b3044d78.yaml`.

**(0) Degraded mode:** `web_unavailable: true` (0 of 12 external lookups used; every external-fact row is HYPOTHESIS); `mcp__github__*` tools unavailable in this harness (Section 16 step 5 fallback used); hook canary waived by the operator for this harness; one stale anchor recorded (capabilities.yaml `boundary_patterns` `.claude/agents/` cited at line ~32, actually line 11; content verified).

**(1) How do we get there:**

1. Wave 0, operator only: settle the verdict-job credential path (WP-00) - pick Environment-vs-OIDC, publish the key out of band, human-gated `terraform/github` apply.
2. Wave 1: author the seven persona contracts (WP-01, closes T4.10a c1-c8).
3. Waves 2-3: LiteLLM transport (WP-02), digest-pinned image + named publish channel (WP-03), verdict chain + fixture run (WP-04), persona-run capture (WP-05).
4. Waves 4-5: add + dual-run each persona (WP-06 code_reviewer, WP-07 decision_scout, WP-08 plan_critic) plus shared flip mechanics (WP-09).
5. Waves 5-6: flip + remove per persona (WP-10, WP-11, WP-12), each flip only after its pre-registered N>=20 dual-run agreement is met.

**(2) Critical path** (6 WPs of 13 total, across 6 waves):

1. WP-01 (wave 1) - T4.10a persona contracts: seven contract-versioned YAMLs, validate.py enforcement, rendered prompts, run_persona port declaration
2. WP-02 (wave 2) - LiteLLM transport + run_persona litellm backend + MCP client
3. WP-04 (wave 3) - verdict chain, verdict identity, trigger-policy check, mixed-PR routing, GitHub-agnosticism, scout-input fingerprint, fixture run
4. WP-07 (wave 4) - T4.30 decision_scout ADD + DUAL-RUN
5. WP-08 (wave 5) - T4.29 plan_critic ADD + DUAL-RUN
6. WP-12 (wave 6) - T4.29 FLIP + REMOVE (closes the last REMOVE criterion)

**(3) Operator touchpoints:** 6 counted touchpoints plus one `write_the_plan` approval per plan WP. Settle before wave 1: the three WP-00 rows (credential mechanism fork, out-of-band key value, human-gated apply) and the spend approval (~USD 100-170/month, HYPOTHESIS estimate). Settle later, at their WP's `write_the_plan`: contract-text approvals for WP-01 and WP-09.

**(4) Top findings** (9 total: 2 novel, 3 planned-insufficient, 4 planned-unbuilt):

- **VPA-01** (high, S1): verdict-job LLM credential path is designed but undelivered and its Environment-vs-OIDC fork is unmade - no Terraform resource owns it. Fix: WP-00 (operator action), then WP-04.
- **VPA-03** (high, S1): the T4.13 prompt-injection threat model is sequenced after the verdict personas that read untrusted PR content. Fix: bundle the T4.13 verdict-persona slice into WP-04, before any add step.
- **VPA-04** (high, shared S2-S4): flip criteria name only /plan and /implement; the /overseer trampoline and the coordinator `standing_authorisation` still consume in-session verdicts. Fix: WP-09 rewords both contracts at flip.
- **VPA-02** (medium, shared S2-S4): no designed wake path for a check-run completion in a CC-web session (`subscribe_pr_activity` delivers failures, not successes; polling forbidden). Fix: WP-09 adds a verdict-wake signal to git-ops `wake_signals`.
- **VPA-08** (medium, S1): the LiteLLM telemetry producer rec-4028 is not named in any persona capture criterion although the per-finding rows key to its model_call rows. Fix: WP-05 absorbs rec-4028 with rec-4433/4434/4415.

**(5) Bundling recommendations:** bundle rec-4415, rec-4028, rec-4433, rec-4434 into WP-05; rec-4477 into WP-03; rec-4476 into WP-10; rec-4490 into WP-11; the T4.13 c1-c4 verdict-persona slice into WP-04; T4.17 c5 into WP-02. Sequence separately: T4.26 + rec-4506, rec-4566, the Decision 237 secrets-to-SSM cutover, repo-wide SHA pinning of existing workflows, T4.17 c1-c4. Defer: rec-4506, rec-4566, T4.14 fixtures.

**(6) Q8 highlights:** blocking-gate evidence before flip is the pre-registered dual-run agreement on N>=20 consecutive real PRs per persona, graded by code, never an LLM (Decision 231); code_reviewer can flip first, but the scout must precede the critic (T4.29 c1 fails "re-scout required" until the head's fingerprint has a scout verdict); the bootstrap is safe with one accepted residual - gate-changing WPs are reviewed by the incumbent in-session gate until that gate's flip, and rec-4476's self-review gap closes at flip; estimated cost ~USD 100-170/month at current volume (HYPOTHESIS); only the three gates migrate - the Step 10 report critique, /audit subagents, advisor consults and the project-alignment sign-off stay human/read-only.

**(7) Coordinator intake:**

```text
WP-00 Verdict-job credential path (Environment + key) | wave 0 | depends on nothing | waits on operator: yes (mechanism fork, secret value, human-gated apply)
WP-01 T4.10a persona contracts (seven YAMLs + validate.py + rendered prompts) | wave 1 | depends on nothing | waits on operator: yes (contract text approval at write_the_plan)
WP-02 LiteLLM transport + run_persona litellm backend | wave 2 | depends on WP-01 | waits on operator: no
WP-03 Persona image + publish channel + local_container twin | wave 2 | depends on WP-01 | waits on operator: yes (spend)
WP-04 Verdict chain + identity + trigger policy + routing + fingerprint + fixture | wave 3 | depends on WP-00, WP-02, WP-03 | waits on operator: yes (spend)
WP-05 Persona-run telemetry capture (rec-4028, rec-4433, rec-4434, rec-4415) | wave 3 | depends on WP-02 | waits on operator: no
WP-06 T4.28 code_reviewer ADD + DUAL-RUN | wave 4 | depends on WP-04, WP-05 | waits on operator: yes (spend)
WP-07 T4.30 decision_scout ADD + DUAL-RUN | wave 4 | depends on WP-04, WP-05 | waits on operator: yes (spend)
WP-08 T4.29 plan_critic ADD + DUAL-RUN | wave 5 | depends on WP-07 | waits on operator: yes (spend)
WP-09 Shared flip mechanics (flags, wake signal, overseer/coordinator text) | wave 4 | depends on WP-04 | waits on operator: yes (contract text + spend)
WP-10 T4.28 FLIP + REMOVE | wave 5 | depends on WP-06, WP-09 | waits on operator: yes (spend)
WP-11 T4.30 FLIP + REMOVE | wave 5 | depends on WP-07, WP-09 | waits on operator: yes (spend)
WP-12 T4.29 FLIP + REMOVE | wave 6 | depends on WP-08, WP-09 | waits on operator: yes (spend)
Waiting on operator (counted touchpoints):
1. WP-00 design fork: credential delivery mechanism - reply: "environment" or "oidc" (recommendation: environment restricted to protected branches)
2. WP-00: set the tier-1 LLM key value out of band - reply: done + which home
3. WP-00: human-gated local apply of terraform/github - reply: applied + Environment name
4. WP-03..WP-12: approve persona-run spend ~USD 100-170/month (HYPOTHESIS estimate) - reply: "approved" or a cap
5. WP-01: approve persona contract + executor-personas.yaml text at write_the_plan - reply: "yes" to the pasted text
6. WP-09: approve flip-mechanics contract text at write_the_plan - reply: "yes" to the pasted text
Standing-instruction candidates (bind every worker):
1. Never run terraform apply; terraform/github is human-gated local apply only.
2. Never set, read or print a secret value; name credentials by name only (Decision 206 cl.2).
3. Verdict personas run only on GitHub-originated events, never pull_request_target (Decision 205).
4. The in-session gates remain the gate until their persona's flip flag is on; never skip a gate (Decision 229 cl.4).
5. File no recs from a verdict job; the caller files findings from the check-run content (Decision 84, T4.10a c7).
Wave-1 intake brief (WP-01):
Plan seed: see execution_plan WP-01 plan_seed (T4.10a c1-c8, Decisions 93/117/185/199/206/229).
File ownership: docs/contracts/executor-personas.yaml, docs/contracts/personas/, config/agent/executor/personas.yaml, scripts/validate.py, tests/checks/contracts/.
Forks: none (all design choices settled by T4.10a c1-c8); contract text approval is the operator's write_the_plan gate.
```
