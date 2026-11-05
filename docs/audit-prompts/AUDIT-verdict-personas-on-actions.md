# AUDIT PROMPT: Moving the gating subagents (decision-scout, plan-critique, code-review) onto GitHub Actions as DeepSeek-via-LiteLLM verdict personas -- readiness, sequencing, best practice and an executable work breakdown

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions.
Every judgment call is assigned below; where something fails, take the named degraded path and
keep going.

## 1. TASK

Audit the PATH from today's state to this goal: the three interactive gating subagents that
`/plan` and `/implement` dispatch in-session today -- decision-scout, plan-critique and
code-review -- run instead as GitHub Actions verdict jobs ("verdict personas") whose model calls
go to DeepSeek models through LiteLLM, and the in-session gates are flipped and removed (kept only
as the outage fallback). The design for that path already exists (roadmap items T4.10a, T4.32,
T4.28, T4.29, T4.30 and Decisions 205, 229, 173, 234); most of it is unbuilt. You assess whether
that design is complete, correctly ordered and in line with industry practice, and you produce
an ordered, dispatch-ready work breakdown that a repo-less coordinator session in a hosted
multi-session workspace ("Claude projects"; its rules are `docs/contracts/coordinator.yaml`) can
execute largely autonomously. Questions Q1-Q8 are in Section 7. Deliverables:
`audits/verdict-personas-on-actions-<sha>.yaml` and `audits/verdict-personas-on-actions-<sha>.md`
(`<sha>` per Section 5). The ONLY files you create or modify in the repository tree are those two
deliverables; regenerating gitignored local caches per Section 5 is expected and is not a breach
(never commit them). You draft; the human disposes. You change no audited surface, file no
recommendation, and run no persona.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS -- THE EPISTEMIC CONTRACT

This prompt hands you FACTS (Section 10) and CANDIDATE hypotheses (below). It never hands you a
verdict. ASSUME NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the
candidates below has failed. A candidate may turn out to be already owned, already covered, or
not a defect at all; each of those is a successful adjudication.

Adjudicate every candidate to exactly one of:

| Adjudication | Where it goes | `roadmap_crossref.classification` |
|---|---|---|
| CONFIRMED-defect, no owning item/rec | `findings[]` | `novel` |
| Owned by a roadmap item/rec whose remedy is insufficient | `findings[]` | `planned-insufficient` |
| Owned by a roadmap item/rec whose remedy is sufficient but unbuilt, AND the gap matters to sequencing or the work breakdown | `findings[]` | `planned-unbuilt` |
| Owned and fully covered, nothing to add | `rejected_candidates[]` | -- |
| Not a defect | `rejected_candidates[]`, naming the compensating control | -- |

Candidates (neutral; each is a hypothesis to test, not a claim):

- C1. T4.10a (persona contracts for all seven personas) is `not_started` and is the only
  `depends_on` of T4.32; whether the three verdict personas can proceed on a subset of T4.10a is
  not stated in the rows read at compose time.
- C2. The three gate skills are tool-using: they read repository files and run
  `bin/venv-python -m ...` scripts (Section 10). T4.2 c8 says the verdict job ingests the PR "only
  as API data (files and blobs at the head SHA) into a schema-validated input". How each persona
  obtains the non-PR context its skill reads today (decisions index and Decision sections, roadmap
  projections, plan scope files, project context) under that rule may be unspecified.
- C3. T4.10a c7 binds persona read capabilities to one MCP server (`scripts/agent_sdk/mcp_server.py`)
  "consumed by the litellm backend as an MCP client"; no litellm backend exists yet.
- C4. `docs/contracts/project-configuration.yaml` row `ports.run_persona.litellm.api_key` names the
  variable `ANTHROPIC_API_KEY`, while the primary inference role's current selection is
  DeepSeek-direct.
- C5. The DeepSeek key lives in AWS Secrets Manager; the GitHub Environments declared in
  `terraform/github/environments.tf` are `tf-gated-apply`, `tf-trusted-plan` and
  `deployment-approval-probe`; T4.2 c9 requires the LLM key to be reachable only from the verdict
  workflow ref. No verdict-job credential path was found at compose time.
- C6. Workflows reference actions by tag (for example `actions/checkout@v7`); T4.2 c8 requires
  SHA-pinned actions for the verdict job.
- C7. The tier-1 model ids are the aliases `deepseek/deepseek-chat` and
  `deepseek/deepseek-reasoner`, whose contract note says they remap upstream; whether a verdict run
  records or pins the resolved model version is not stated.
- C8. The dual-run step compares an in-session subagent (a Claude model in the interactive
  harness) with an Actions persona on DeepSeek; a disagreement can come from the model, the harness,
  the input projection, or the prompt. The agreement measure is required to be pre-registered but
  is not yet defined.
- C9. Each gate skill declares its own verdict vocabulary (decision-scout: NO_FLAGS / FLAGS_FOUND /
  BLOCK; code-review: PROCEED / REVISE); the mapping from each to a check-run conclusion is not
  stated in the rows read.
- C10. code-review findings are filed as recommendations by the CALLER through
  `scripts.ops_data_portal`; a verdict job filing recs would need warehouse writer access, which
  Decision 205 clause 1 permits as an output ("check run or rec") but no grant was found.
- C11. The persona telemetry prerequisites named by the persona items' CAPTURE criteria (rec-4433
  open, rec-4434 open, rec-4416 closed) and the LiteLLM telemetry producer (rec-4028, open) gate the
  add and dual-run steps.
- C12. The persona image build-and-publish channel (rec-4477, open) gates every persona item's
  add step.
- C13. The interactive session awaits a verdict check run; how a CC-web session is woken by a
  check-run completion (as opposed to its own CI) is not stated in the rows read.
- C14. T4.13 (prompt-injection threat model) depends on T4.2, the umbrella, so it is sequenced
  after the verdict personas that read untrusted PR content.
- C15. LiteLLM is pinned only in `requirements-dev.txt` and imported only by
  `scripts/smoke_test_inference_credentials.py`; `scripts/llm/client.py` is a Gemini-CLI transport.
  Whether the persona image uses the LiteLLM Python SDK in-process or a LiteLLM proxy server is not
  stated in the rows read.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

1. "subagent-plan-review" / `.github/actions/subagent-plan-review` (used by `reconcile.yml`,
   `terraform-apply-sandbox.yml`, `subagent-review-smoke.yml`) is the merge-time TERRAFORM plan
   reviewer, not plan-critique. It is an adjacent LLM-on-Actions surface (owned by T4.26) and
   context/bundle-candidate only; it is NOT one of the three gates in scope.
2. "plan" means a `docs/plans/PLAN-*.yaml` work plan unless the text says Terraform plan.
3. "persona" means an AI role (Decision 232), never a human user archetype. "Verdict persona" is
   one of decision_scout, plan_critic, code_reviewer (their roadmap and contract names); the skill
   names are decision-scout, plan-critique, code-review. Map them; do not treat them as six things.
4. "Claude projects" means the hosted multi-session workspace whose coordinator cannot read the
   repository (`docs/contracts/coordinator.yaml` header). It is NOT GitHub Projects, NOT the
   `.claude/` directory, and NOT the repo's own `/overseer`. The work breakdown you produce is
   shaped for that coordinator's lifecycle (Section 14).
5. "code-review" is the repo skill `.claude/skills/code-review/SKILL.md`, not Claude Code's
   built-in `/code-review` or `/security-review`, and not `claude.yml` (the operator's first-party
   `@claude` workflow, a reasoned exemption under Decision 184 clause 4 / T4.2 c10).
6. "Tier 1" / "primary" are ROLES (Decision 173); DeepSeek-direct is the primary role's CURRENT
   SELECTION, held only in `docs/contracts/inference-provider.yaml`
   `model_id_formats.litellm_tier_models`. Tier 2 (Anthropic-direct) is the warm-fetched fallback
   role.
7. "Gate" today = an in-session subagent that blocks the interactive workflow. During dual-run the
   Actions check run is ADVISORY (CD.12 alarm-not-gate); it gates only after its item's flip
   criterion. Do not rate an advisory check run as a missing gate.
8. "LiteLLM" may mean the Python SDK (a library, pinned in `requirements-dev.txt`) or the LiteLLM
   proxy server. Name which one any finding or package concerns.
9. T4.25 is `reserved`; its criteria were re-homed into T4.28-T4.30 (Decision 229 clause 4). Do
   not audit T4.25 as live scope.

## 4. SCOPE

Surfaces (rate each in the rubric):

| Surface | Contents | State |
|---|---|---|
| S1 foundation | T4.10a (persona contracts), T4.32 (LiteLLM transport, run_persona litellm backend, digest-pinned image + channel, local_container twin, verdict chain, plan-PR routing, scout-input fingerprint), the inference-provider tier model and the credential path to Actions | designed, unbuilt |
| S2 decision_scout | T4.30 + `.claude/skills/decision-scout/SKILL.md` + `/plan` scout gate | designed, unbuilt (in-session gate built) |
| S3 plan_critic | T4.29 + `.claude/skills/plan-critique/SKILL.md` + `/plan` critique gate | designed, unbuilt (in-session gate built) |
| S4 code_reviewer | T4.28 + `.claude/skills/code-review/SKILL.md` + `/implement` Code Review Protocol | designed, unbuilt (in-session gate built) |
| S5 adjacent LLM-on-Actions | `ci-rca.yml`, the subagent-plan-review composite action, `claude.yml`, T4.26, rec-4506 | built (on `claude -p` + OAuth token); BUNDLE-CANDIDATE CONTEXT ONLY |

Vocabulary:
- add / dual-run / flip / remove: the four strangler steps of Decision 229 clause 4, as written in
  each persona item's criteria.
- verdict chain: the secretless `pull_request` workflow chained by `workflow_run` to a trusted
  verdict job (T4.2 c8).
- work package (WP): one unit the coordinator dispatches to one planning worker, producing one
  merged plan and (usually) one build (Section 14).
- always_ask classes: the closed list in `docs/contracts/coordinator.yaml` `always_ask.items`
  (re-read it; Section 14 pins short names for them).

Out of scope (one line each): plan_agent/implement_agent/Fargate/T4.15/T4.31 beyond their edges to
the verdict personas; the loop host (T4.1, T4.19-T4.22); scheduled agents (T4.12); the
user-owned CLI backend (T4.27) and sandbox host (T4.34) except as bundle candidates; re-deciding
which provider fills the primary role (Section 13 do-not-flag).

TRUST-NOTHING CLAUSE: obtain every file, line, size, status and count by reading the file -- trust
no number quoted here; re-derive from the repo and record any non-resolving anchor in
`meta.stale_anchors`, downgrading anything resting on it to HYPOTHESIS.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree;
   use it in the deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/verdict-personas-on-actions-<sha> origin/main` now, so everything you read
   is the audited tree. IF fetch fails: use `git rev-parse --short HEAD`, branch off `HEAD`
   instead, and record "base is local HEAD" in `meta.contract_notes`.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes
   `logs/.preflight-report.json` (gitignored; expected). Recommendations have NO local cache in
   this repo; rec dedup uses the live reader (step 3).
   - IF this fails (creds/egress down): set `meta.degraded_dedup = true` and continue.
3. Rec dedup probe: `bin/venv-python -m scripts.recs_related --terms "litellm verdict persona" --limit 5 --table`.
   - IF this fails, or `meta.degraded_dedup` is already true: do NOT abort -- set
     `meta.degraded_dedup = true`, mark every `roadmap_crossref` confidence HYPOTHESIS (set the
     finding's `confidence: HYPOTHESIS`) and `dedup_hit_count: null`, and dedup against the roadmap
     and `docs/DECISIONS.md` only, proceeding.
4. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record it in `meta.contract_notes` and read the YAML as text.
5. External lookups (Q2, Q3, Q6 only): you MAY use web search/fetch, at most 12 lookups in total --
   do NOT exceed -- solely to confirm current external practice and current DeepSeek / LiteLLM
   capabilities (tool calling, structured output, context window, model versioning, data
   retention). Record each URL in the evidence field that uses it. IF web access is unavailable:
   set `meta.web_unavailable = true`, answer from your own knowledge, and mark every checklist row
   and finding that depends on an external fact `confidence: HYPOTHESIS`.

Repo-wide `validate` is advisory outside CI; do not run it as a gate (Section 16).

## 6. NORTH STAR

Principles every rating references. NS1-NS3 are hard rules from Decisions; NS4-NS7 are
judgment-bearing -- a bar you judge each surface against, not a checklist to pattern-match.

- NS1 Verdict integrity (Decision 205 clauses 1-2): an LLM verdict runs only on a GitHub-originated
  event, reads a pinned SHA, never runs on `pull_request_target`, runs the default branch's
  definition, fails rather than skips, and posts a check run under an identity the executor cannot
  forge.
- NS2 One inference lane (Decision 205 clause 4, CD.28, Decision 173): every API-key model call
  goes through LiteLLM; provider identity is configuration; the fallback role is a different vendor.
- NS3 Strangler migration (Decision 229 clause 4): add, dual-run (advisory), flip, remove; a
  per-persona rollback flag; the in-session gate survives only as a visible outage fallback; never a
  skipped gate.
- NS4 Thin shell, portable persona (T4.2 c8/c13): persona logic lives in the image, never in
  workflow YAML; moving hosts is a shell change.
- NS5 Evidence before authority: a gate gains blocking power only after measured agreement, with
  capture from day one (persona key, definition hash, per-finding rows).
- NS6 Autonomous executability: a coordinator with no repo access can dispatch the work in
  dependency order, and the operator is asked only for always_ask items and real forks.
- NS7 Industry-grade CI/LLM security and reliability (Section 7 Q3 external checklist).

## 7. THE QUESTIONS

Each question has its own answer slot (`question_answers[]`). Verdict enums are pinned per question.

- Q1 PREREQUISITE COMPLETENESS. Is the designed dependency chain (T4.10a -> T4.32 -> T4.30, T4.29,
  T4.28, with the recs their criteria name) complete and correctly ordered to reach the goal in
  Section 1 -- every prerequisite named, no circular or missing edge, nothing that silently blocks
  on a deferred or toxic item (T4.1, T4.9a, T4.15)? Enumerate the full prerequisite set you find,
  including any not yet named in the roadmap. Verdict: `sufficient | partial | insufficient`.
- Q2 DEEPSEEK-VIA-LITELLM FITNESS. Can the three personas, as their skills work today, run on the
  primary role's current DeepSeek models through the designed LiteLLM transport and run_persona
  litellm backend? Cover: tool calling and the MCP read surface (C2, C3), context-window fit for
  each persona's inputs, structured output, model-version pinning (C7), timeouts/retries/fallback to
  the tier-2 role, credential naming (C4), and what a persona must change (prompt, input
  projection) versus what stays the same. Verdict: `sufficient | partial | insufficient`.
- Q3 INDUSTRY PRACTICE (EXTERNAL CHECKLIST). Rate the designed verdict chain property by property
  against the external checklist below. Each row goes into `external_checklist` with rating
  `met | partial | missed` judged against the DESIGN (criteria text) plus anything already built;
  `partial` requires an argued property-matched compensating control in `evidence`. Verdict:
  `sufficient | partial | insufficient`.
  - X1 No untrusted code with secrets: no `pull_request_target`; secretless `pull_request` plus
    `workflow_run` split (GitHub Security Lab "pwn request" guidance).
  - X2 Least-privilege `GITHUB_TOKEN`: `permissions: {}` default, per-job minimum grants.
  - X3 Third-party actions pinned to full commit SHA (OpenSSF Scorecard Pinned-Dependencies).
  - X4 LLM key scoped to the trusted ref: GitHub Environment with branch restriction, or OIDC
    short-lived cloud credentials bound to the workflow ref; no long-lived key reachable from PR code.
  - X5 Persona image digest-pinned and built by a governed channel with provenance/signing (SLSA
    build provenance, e.g. Sigstore/cosign attestation).
  - X6 Python dependencies for the image locked with hashes.
  - X7 Untrusted PR content treated as data: prompt-injection mitigations (OWASP Top 10 for LLM
    applications, LLM01), no write-capable tools, structured output validated against a schema.
  - X8 Fail-closed: a missing, malformed or timed-out verdict is a failure, never a pass or a skip;
    stable required-check names.
  - X9 Shadow / dual-run before enforcement with a pre-registered agreement metric and a rollback
    switch.
  - X10 Reproducibility: pinned model version, recorded prompt/definition hash, fixed sampling
    settings where the provider allows.
  - X11 Provider resilience and cost bounds: per-call timeout, bounded retries, fallback to an
    independent provider, per-run token cap.
  - X12 Observability: per-run trace of model, tokens, latency, cost and verdict (e.g. OpenTelemetry
    GenAI semantic conventions or equivalent).
  - X13 Concurrency hygiene: superseded head SHAs cancelled; job `timeout-minutes` set.
  - X14 Runner egress control (an egress allowlist such as a harden-runner step, or equivalent).
  - X15 Data governance: what is sent to the third-party model provider is limited to public
    repository content; no secrets or operational data in prompts; provider retention terms known.
  - X16 Offline regression set for prompt/model changes before they go live.
- Q4 STRANGLER SOUNDNESS. Is add -> dual-run (N >= 20, pre-registered agreement) -> flip -> remove
  a sound and measurable migration for each persona, given C8 (model family change), C9 (verdict
  vocabularies), C13 (how the interactive session waits) and the fallback rule? Propose the
  agreement measure each persona's dual-run plan should pre-register. Verdict:
  `sufficient | partial | insufficient`.
- Q5 EXECUTION READINESS FOR CLAUDE PROJECTS. Produce the `execution_plan` block (Section 14): an
  ordered set of work packages a repo-less coordinator can dispatch under
  `docs/contracts/coordinator.yaml` with no further design work, each with dependencies, file
  ownership (disjoint files run in parallel), always_ask classes triggered, design state and a
  paste-ready `/plan` seed. The question's verdict rates how much of the path is dispatchable now:
  `ready | ready_with_forks | not_ready`.
- Q6 BUNDLING. What should be bundled into the work packages along the way, sequenced separately,
  or deferred? Assess at least: T4.26 (ci-rca and subagent-plan-review onto the LiteLLM lane) and
  rec-4506; rec-4415, rec-4028, rec-4433, rec-4434; rec-4476; rec-4490; rec-4477; rec-4566; SHA
  pinning of existing workflows; T4.13 c1-c4; T4.17 c1 and c5; T4.14 fixtures. Record each in
  `bundle_dispositions`. Verdict: `sufficient | partial | insufficient` (is the design's own
  bundling adequate).
- Q7 OPERATOR TOUCHPOINTS. List every point on the path that needs the operator (always_ask items:
  IAM, spend with a monthly estimate, Decision or governing-contract text, public-surface files,
  manual/admin steps, secret values set out of band, loosened tests) and every open design fork the
  coordinator cannot settle on advisor evidence alone. Verdict: `minimal | moderate | heavy`.
- Q8 QUESTIONS THE REQUESTER DID NOT THINK TO ASK. Answer each seed below AND add your own:
  - Is DeepSeek's quality on long, tool-heavy repository review adequate for a BLOCKING gate, and
    what evidence would show it before flip (without an LLM grader, Decision 231)?
  - Should code_reviewer flip before the plan-PR personas given the operator's start order, or does
    plan-PR routing (T4.32 c4) make the scout first?
  - What happens to in-flight plans and branches at each flip?
  - What is the monthly Actions-minutes and DeepSeek token cost at current PR volume?
  - Does moving gates off the session change the interactive session's wall-clock loop materially?

## 8. RUBRIC

Rate every surface S1-S5 on every dimension: `strong | adequate | weak | absent | n/a`. `n/a` is
correct and costless where a dimension does not structurally apply; never manufacture a rating or
a finding to fill a cell. S5 is rated only on VD3, VD4 and VD6; mark its other cells `n/a`.

| Dim | Name | Serves |
|---|---|---|
| VD1 | Design completeness: criteria exist, are testable, cover the goal | Q1, Q5 |
| VD2 | Dependency and sequencing correctness | Q1, Q5, Q6 |
| VD3 | Trust boundary and credential isolation | Q3, Q7 |
| VD4 | Provider/model fitness and resilience | Q2, Q3 |
| VD5 | Migration safety: dual-run, rollback, fallback | Q4 |
| VD6 | Observability and evaluation capture | Q3, Q4 |
| VD7 | Autonomous executability by a repo-less coordinator | Q5, Q7 |

## 9. DEEP-DIVES

- DD-A Input projection per persona (feeds Q2, Q4): for each of the three skills, list every file,
  command and external read it performs today (from its SKILL.md and the dispatching command), and
  state for each how the Actions persona would obtain it under T4.2 c8's API-data rule and T4.10a
  c7's MCP surface: in the schema-validated input, via an MCP read capability, from the image's
  baked copy of `main`, or not at all. Mark any read with no designed source.
- DD-B Credential path (feeds Q3, Q7): trace how the DeepSeek key would reach a verdict job: secret
  home today, candidate delivery (Environment secret vs OIDC role reading Secrets Manager), which
  Terraform root would own it (`terraform/github` vs `terraform/bootstrap` vs `terraform/personal`),
  and which steps are always_ask.
- DD-C Flip mechanics (feeds Q4, Q5): for each persona, the exact interactive command/skill text
  that changes at flip (anchors in Section 10), the rollback flag's home
  (`docs/contracts/project-configuration.yaml`, Decision 224), and the visible fallback path.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify each anchor before relying on it;
anchors are as of compose time and may have moved.

Roadmap (`docs/ROADMAP-PLATFORM.yaml`, `tier_items[]`):
- T4.2 (line ~5844): umbrella, `not_started`; criteria c8 (verdict chain), c9 (verdict identity and
  credentials), c10 (trigger-policy check; names `ci-rca.yml` and the subagent-plan-review step in
  `reconcile.yml` and `terraform-apply-sandbox.yml` as needing maintainer-actor guards), c12
  (mixed-PR routing), c13 (GitHub-agnosticism, one image digest), c14 (compute-host conformance).
- T4.10a (line ~6448): `not_started`, depends on T1.12 (`complete`); c1 contracts for seven
  personas; c5 rendered system prompts under `docs/contracts/personas/`; c6
  `docs/contracts/executor-personas.yaml` declares run_persona; c7 MCP read capabilities; c8 hub and
  spoke.
- T4.32 (line ~7974): `not_started`, effort XL, depends on T4.10a; c1 foundation, c2 image channel
  (rec-4477), c3 fixture run, c4 scout-input fingerprint; `files_in_scope` names
  `scripts/llm/client.py`, `scripts/personas/`, `.github/workflows/persona-verdict-trigger.yml`,
  `.github/workflows/persona-verdict.yml`; `decomposition_hints` lists three atomic plans.
- T4.28 (line ~7818) code_reviewer, T4.29 (~7858) plan_critic, T4.30 (~7894) decision_scout: each
  `not_started`, depends on T4.32, with ADD / CAPTURE / DUAL-RUN (N at least 20, pre-registered) /
  FLIP (per-persona flag, project-configuration row) / REMOVE criteria.
- T4.13 (~6647) depends on T4.2; T4.14 (~6693) `deferred_post_mvp`; T4.17 (~7146) depends on T4.2;
  T4.21 (~7376) `not_started`; T4.25 (~7624) `reserved`; T4.26 (~7652) `deferred_post_mvp`, depends on
  T4.2; T4.27 (~7689) and T4.34 (~8064) `deferred_post_mvp`.
- Candidate decisions CD.12 (alarm not gate), CD.17, CD.27, CD.28 (`candidate_decisions[]`).

Decisions (`docs/DECISIONS.md`, locate with `rg -n "^## Decision N:"`): 205 (event origin, verdict
integrity, credential lane, plans in git), 229 (persona-first migration; clause 4 strangler; its
2026-11-02 update on in-session gates), 173 (tiers are roles), 122 (CD.28 ratified: DeepSeek-direct
tier 1), 206, 212, 217, 224, 231 (persona evaluation offline, never an LLM grader), 234 (clause 3:
a verdict persona on someone else's PR uses a team API key through LiteLLM), 235, 241.

Contracts and config:
- `docs/contracts/inference-provider.yaml`: `tier_model` (roles); `model_id_formats.litellm_tier_models`
  (around line 389): tier_1 provider DeepSeek-direct, models `deepseek/deepseek-chat`,
  `deepseek/deepseek-reasoner`, note on upstream alias remap; tier_2 Anthropic-direct.
- `docs/contracts/project-configuration.yaml` rows `ports.run_persona.litellm.model`,
  `ports.run_persona.litellm.api_key` (variable `ANTHROPIC_API_KEY`), `ports.run_persona.claude_cli.credential`.
- `docs/contracts/ports.yaml`: `persona_compute_host` port; coupling PC-08, PC-09.
- `docs/contracts/coordinator.yaml`: repo-less coordinator rules -- `lifecycle`, `standing_authorisation`,
  `always_ask`, `templates.handoff_note`, `coordinator_operations.parallelism`.
- `docs/contracts/instruction-architecture.yaml` `gate_subagent_retrieval` (around line 195): the
  scout's triage source is the committed `docs/decisions-index.json`, chosen so the gate is
  credential-free and hermetic.
- `config/agent/executor/capabilities.yaml` `boundary_patterns`: lists `.claude/agents/` (line ~32);
  `.claude/skills/` and `.claude/commands/` are absent (rec-4476).
- Absent at compose time: `docs/contracts/executor-personas.yaml`, `docs/contracts/personas/`,
  `scripts/personas/`, any `persona-verdict*.yml` workflow.

Code and workflows:
- `scripts/llm/client.py` (393 lines): docstring states it routes to the Gemini CLI transport and
  that the LiteLLM tier model lands with T4.2.
- `scripts/smoke_test_inference_credentials.py` line 24 `import litellm`; `--provider deepseek`.
- `requirements-dev.txt` line ~139 `litellm==1.106.2`.
- `.github/workflows/subagent-review-smoke.yml` line 18 `actions/checkout@v7` (tag reference).
- `.github/workflows/ci-rca.yml` line ~434, `reconcile.yml` lines ~379/~476,
  `terraform-apply-sandbox.yml` line ~358: `claude -p` / composite action on
  `secrets.CLAUDE_CODE_OAUTH_TOKEN`. `.github/workflows/claude.yml` line ~38
  `anthropics/claude-code-action@v1`.
- `terraform/personal/inference_credentials.tf`: Secrets Manager secret
  `agent-platform-deepseek-api-key` (value set out of band, Decision 37).
- `terraform/github/environments.tf`: environments `tf-gated-apply`, `tf-trusted-plan`,
  `deployment-approval-probe`.
- Gate dispatch today: `.claude/skills/planning/SKILL.md` "Decision Scout Gate" (line ~248) and
  plan-critique dispatch (line ~404); `.claude/skills/implement/SKILL.md` "Code Review Protocol"
  (line ~138), dispatching `subagent_type: "general-purpose"`; `.claude/commands/plan.md` line ~63;
  `.claude/commands/implement.md` line ~70.
- Skill reads: `.claude/skills/decision-scout/SKILL.md` (reads `docs/decisions-index.json`,
  targeted `docs/DECISIONS.md` sections, runs `bin/venv-python -m scripts.roadmap.scope_projection`;
  verdict NO_FLAGS | FLAGS_FOUND | BLOCK, line ~96); `.claude/skills/plan-critique/SKILL.md` (reads
  the plan, `docs/PROJECT_CONTEXT.md`, targeted roadmap and Decision sections, scope files, runs
  `scripts.roadmap.plan_obligations`); `.claude/skills/code-review/SKILL.md` (runs
  `scripts.roadmap.find_plan`, `scripts.extract_imports`, reads `docs/DECISIONS_ARCHIVE.md` "Code
  Review Exemptions"; verdict PROCEED | REVISE, line ~219; caller files findings via
  `scripts.ops_data_portal`, line ~238).

Recommendations (read with `scripts.recs_related` or the ducklake-reads `recs_by_ids` verb):
rec-4433 (persona key + definition hash, open), rec-4434 (per-finding rows, open, depends on
rec-4433), rec-4416 (top-level agent run shape, closed), rec-4028 (LiteLLM telemetry producer,
open), rec-4332 (resume journal, open), rec-4415 (CI agent capture, open), rec-4476 (self-review
gap, open), rec-4477 (image channel, open), rec-4478 (Decision 67 supersession, open), rec-4490
(deterministic scout fingerprint in interactive /plan, open), rec-4043 (separate admin principal,
open; T4.2 c15), rec-4506 (ci-rca model choice by replay and pulling T4.26 forward), rec-4566.

## 11. EMPIRICAL PASS

Static design review dominates this audit; the empirical pass is small and bounded.

- Sample <= 5 recently merged plan files in `docs/plans/` (by `git log` date on `main`) -- do NOT
  exceed -- and for each compute the inputs DD-A says the decision_scout and plan_critic personas
  would need; record whether any input lacks a designed source.
- Sample <= 5 recently merged code PRs (`git log --merges` or squash commits on `main`) -- do NOT
  exceed -- and record diff size (files, lines) against what a single DeepSeek context window can
  hold with the code-review skill's required reads.
- Counterfactual per sample: "If the in-session gate were deleted today and only the designed
  Actions persona existed, would this sample have received a verdict over the same inputs?" A
  sample answering no is an observed gap.
- Tag each finding `evidence_kind: static | observed`; at equal severity, observed outranks static.
- Do NOT call any model, run any persona or dispatch any workflow.

## 12. METHOD

- P1 Read: Section 10 anchors, the three skills, the five roadmap items, Decisions 205/229/234 in
  full, `docs/contracts/coordinator.yaml` in full.
- P2 Trace: adjudicate C1-C15.
- P3 Deep-dives DD-A, DD-B, DD-C.
- P4 Empirical pass (Section 11).
- P5 Rate the rubric (Section 8) and the external checklist (Q3).
- P6 Dedup every finding (Section 13).
- P7 Build `execution_plan` and `bundle_dispositions` (Section 14).
- P8 Synthesize: answer Q1-Q8, then compute severity and maturity LAST (Section 15).

## 13. DEDUP DISCIPLINE

Before filing any finding, search the ownership surfaces: the roadmap (`tier_items[]` criteria
text and `candidate_decisions[]`), `docs/DECISIONS.md` headers and clauses, and open recs
(`bin/venv-python -m scripts.recs_related --terms "<terms>" --table`, paging with `--exclude` as
needed, at most 3 pages per finding). Record `dedup_search_terms` and `dedup_hit_count` on the
finding. A hit means a sufficiency assessment (`planned-insufficient` / `planned-unbuilt`) or a
`rejected_candidates` row -- never a fresh discovery. A finding without a recorded negative
search is HYPOTHESIS.

DO NOT FLAG (deliberate constraints; you may assess how they are implemented, never whether they
should exist):
- DeepSeek-direct as the primary role's selection and Anthropic-direct as fallback (Decision 122,
  Decision 173, CD.28). Operational risks of the selection belong in Q2/Q8 prose, not as a finding
  to change provider.
- Verdict personas on Actions only on GitHub-originated events, never `pull_request_target`
  (Decision 205 clauses 1-2).
- Personas never invoke personas; the coordinator dispatches each (Decision 229 clause 2).
- The add / dual-run / flip / remove order and the in-session fallback (Decision 229 clause 4).
- No LLM grader for persona evaluation (Decision 231).
- IMPLEMENTATION-only plans and the Decision 67 freeze as currently read (Decision 229 clause 1
  interim reading; rec-4478).
- Weakening verification is human-gated (Decision 235).
- Public-repository content boundary (Decision 101).
- `claude.yml` exemption (Decision 184 clause 4; T4.2 c10).
- `deferred_post_mvp` statuses (Decision 93): a deferral is not a defect; pulling an item forward is
  a `bundle_dispositions` recommendation for the operator, not a finding.
- The 500-SLOC file budget (Decision 128).

## 14. OUTPUT

Write `audits/verdict-personas-on-actions-<sha>.yaml`:

```
audit:
  meta: {audited_commit: <sha>, base_branch: main, model: <your self-reported name>,
         methodology_version: 1, scope_surfaces: [S1, S2, S3, S4, S5],
         degraded_dedup: false, web_unavailable: false, contract_notes: "", stale_anchors: []}
  question_answers:
    - {q: Q1, verdict: sufficient|partial|insufficient, basis: [<finding ids>], prose: ""}
    - {q: Q2, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q3, verdict: sufficient|partial|insufficient, basis: [], prose: "",
       external_checklist: [{property: X1..X16, rating: met|partial|missed, evidence: ""}]}
    - {q: Q4, verdict: sufficient|partial|insufficient, basis: [], prose: "",
       agreement_measures: [{persona: decision_scout|plan_critic|code_reviewer, measure: "", n: <int >= 20>}]}
    - {q: Q5, verdict: ready|ready_with_forks|not_ready, basis: [], prose: "see execution_plan"}
    - {q: Q6, verdict: sufficient|partial|insufficient, basis: [], prose: "see bundle_dispositions"}
    - {q: Q7, verdict: minimal|moderate|heavy, basis: [], prose: "see execution_plan.operator_touchpoints"}
    - {q: Q8, answers: [{question: "", answer: "", basis: []}]}
  per_surface_assessment:
    - {surface: S1..S5, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
  rubric_ratings:
    - {surface: S1..S5, dimension: VD1..VD7, rating: strong|adequate|weak|absent|n/a,
       evidence: "file:line|item-id", note: ""}
  execution_plan:
    critical_path: [<WP ids in order>]
    packages:
      - wp_id: WP-01
        title: ""
        kind: plan_and_build|plan_only|operator_action
        roadmap_closes: ["T4.32:c1", ...]     # item:criterion ids; [] if none
        recs_absorbed: [rec-NNNN, ...]
        depends_on: [WP ids]
        wave: <int, 1 = startable now>
        files_owned: [<paths or globs>]       # packages in one wave must be disjoint
        always_ask: [iam_security|spend|decision_or_contract_text|public_surface|manual_admin|non_ci_deploy|loosened_tests]
        design_state: designed|needs_operator_fork|undesigned
        open_forks: [""]                      # each a one-line decision_prompt with a marked recommendation
        plan_seed: ""                         # paste-ready /plan prompt, <= 120 words, names items, recs and Decisions
        acceptance_signal: ""                 # the one read-back proving the package done
        effort: XS|S|M|L|XL
        basis: [<finding ids>]
    operator_touchpoints:
      - {wp_id: "", item: "", always_ask: <class from the enum above>, monthly_cost_estimate: "" }
  bundle_dispositions:
    - {candidate: "<item or rec id>", disposition: bundle|sequence_separately|defer|reject,
       into_wp: "<WP id or empty>", rationale: "", cost: "", confidence: CONFIRMED|HYPOTHESIS}
  findings:
    - {id: VPA-01, surface: S1..S5|shared, question: Q1..Q8, dimension: VD1..VD7,
       title, evidence: "file:line|item-id|url", evidence_kind: static|observed,
       current_behavior, ideal_behavior, gap, compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate,
       proposed_change: "", acceptance: "", severity: critical|high|medium|low,
       severity_rationale, confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt,
                          item_ids: [], dedup_search_terms: [], dedup_hit_count: <int|null>, note: ""},
       effort: XS|S|M|L, depends_on: [finding ids],
       sequencing: {safe_to_queue_now: true|false, blocked_behind: [finding, WP or roadmap ids], note: ""}}
  rejected_candidates:
    - {candidate, why_dismissed, compensating_control, control_property_match, decision_or_item_id}
  summary: {total_findings, novel_count, planned_insufficient_count, planned_unbuilt_count,
            top_improvements: [finding ids], highest_leverage_change: <finding id>,
            maturity_S1: <value>, maturity_S2: <value>, maturity_S3: <value>,
            maturity_S4: <value>, maturity_S5: <value>, wp_count: <int>, wave_count: <int>}
```

always_ask enum mapping (short name -> `docs/contracts/coordinator.yaml` `always_ask.items`):
iam_security = "IAM and security changes"; spend = "Spend"; decision_or_contract_text = "Decision
text and governing contract text"; public_surface = "Public-surface files"; manual_admin =
"Manual or admin steps outside CI" (includes setting a secret value out of band); non_ci_deploy =
"Deploys not run by the repo's reviewed CI channel"; loosened_tests = "Loosened tests or
assertions".

execution_plan rules:
- Every WP is one planning worker's scope under the coordinator lifecycle (intake -> /plan ->
  alignment sign-off -> critique -> plan PR -> handoff -> build -> close_out). Size each to one
  IMPLEMENTATION plan; split by concern where a roadmap item's `decomposition_hints` does.
- Every open roadmap criterion on the path to the goal (T4.10a, T4.32, T4.28-T4.30 criteria, plus
  any T4.2 criterion those items discharge) appears in exactly one WP's `roadmap_closes`, or is
  named in `summary`-adjacent prose of Q5 as deliberately left out with a reason.
- `design_state: undesigned` means a design decision no Decision or criterion has made; such a WP
  must list the fork in `open_forks`.
- `plan_seed` follows the repo's `/plan` conventions: names the roadmap items and criteria, the
  recs to bundle, the governing Decisions; contains no secret, account id or hostname.

COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects; `total_findings =
len(findings) = novel_count + planned_insufficient_count + planned_unbuilt_count`; fully covered
candidates live in `rejected_candidates`, never in findings; `rubric_ratings`,
`question_answers`, `execution_plan` and `bundle_dispositions` are systems of record referenced
FROM findings and never counted as findings; `top_improvements` and `highest_leverage_change` MUST
be finding ids. `wp_count = len(execution_plan.packages)`; `wave_count` = the highest `wave`.

`control_property_match` is REQUIRED whenever a compensating control is the reason for a
dismissal: name the property the control exercises, cite where it operates (file:line or
mechanism), and state why it would FAIL if the defect were real.

CONFIRMED requires the behavior traced to file:line or an observed sampled artifact; anything less
is HYPOTHESIS. A design gap is CONFIRMED when the absence is traced (the criteria text read and the
missing element shown absent from every owning item searched).

Write `audits/verdict-personas-on-actions-<sha>.md`, <= 1500 words, the layer the operator and the
coordinator read first: (1) a 5-line answer to "how do we get there", (2) the critical path as a
numbered list of WP titles with waves, (3) operator touchpoints, (4) top findings, (5) bundling
recommendations, (6) Q8 highlights. It restates no finding counts beyond what `summary` holds.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never inherited from this prompt's framing:
- critical: the designed path can produce a wrong-but-trusted verdict that gates a merge (e.g. a
  persona that passes on missing input, a credential reachable from PR-controlled code), or the
  path cannot reach the goal at all as designed.
- high: a gap that materially weakens a gate's guarantee or blocks autonomous execution of the path,
  AND whose compensating controls you judged insufficient.
- medium: redundancy, ambiguity, ordering inefficiency or a missing bundle with a clear fix.
- low: clarity or wording.

Compensating controls must PROPERTY-MATCH: the control exercises the same property AND would fail
if the defect were real (apply the counterfactual to the control). A control that cannot catch the
break neither lowers severity nor justifies dismissal.

Maturity, computed LAST per surface, top-down, first match wins:
- frontier = 0 open critical or high findings on the surface AND every Q3 `external_checklist` row
  rated met or partial (none missed). For S5, the checklist condition reads only X1-X4 and X11.
- strong = 0 critical AND <= 1 high.
- solid = <= 1 critical.
- nascent = otherwise.

The top tier remains reachable when you argued a property-matched compensating control; this
prompt's framing does not foreclose it. Rating a designed-but-unbuilt surface: rate the DESIGN
(criteria text) for VD1-VD5 and VD7; rate VD6 on design plus what capture already exists.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on
   `audit/verdict-personas-on-actions-<sha>` created from it. This is a deliberate, documented
   exception to the `claude/*` session-branch convention: the audit needs a clean two-file diff off
   the audited base. The branch base MUST equal `meta.audited_commit`.
2. Pre-push gate: both deliverables parse cleanly
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/verdict-personas-on-actions-<sha>.yaml`)
   and the counting invariant holds. Repo-wide validation is advisory outside CI; if an unrelated
   `validate --pre` failure appears, record it in `meta.contract_notes` -- never fix it.
3. `git add` exactly the two deliverables. Commit with
   `git -c user.name=Claude -c user.email=noreply@anthropic.com commit --no-gpg-sign -m "audit: verdict personas on Actions readiness"`;
   if a signing configuration or hook rejects `--no-gpg-sign`, commit without that flag and note it
   in `meta.contract_notes` only if a later amend is impossible (do not amend). Then
   `git push -u origin HEAD`; on a network error retry up to 4 times (2s, 4s, 8s, 16s).
4. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`,
   base `main`, ready for review, NOT draft), title
   `audit: verdict personas on GitHub Actions via DeepSeek and LiteLLM (S1-S5)`, body = a 2-3
   sentence lede plus the `summary:` block in a yaml fence. Then END THE TURN -- do not poll, do not
   merge, do not subscribe, do not self-approve. The human disposes of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): the ONLY repository files you create or modify are
  `audits/verdict-personas-on-actions-<sha>.yaml` and `audits/verdict-personas-on-actions-<sha>.md`.
  You do NOT edit the roadmap, Decisions, contracts, skills, commands or workflows; do NOT file or
  update a recommendation; do NOT run a persona, call a model, dispatch a workflow or apply
  Terraform; do NOT set or read secret values.
- Never write an AWS account id, ARN, ExternalId, credential, secret value or internal hostname in
  either deliverable (public repository, Decision 101).
- Precision over volume. Fewer than ~8 surviving findings is a valid result -- state it plainly; do
  not pad. `execution_plan` and `bundle_dispositions` will be long by design; they are
  classifications, not findings.
- A run that merely restates the candidates in Section 2 has failed. Every finding survives its
  dedup search (Section 13) and, where sampled, its counterfactual (Section 11).
- If an anchor does not resolve, record it in `meta.stale_anchors` and downgrade dependent items to
  HYPOTHESIS -- never invent, never abort.
