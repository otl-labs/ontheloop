# AUDIT PROMPT: The release boundary -- what an On The Loop release ships versus this repository's maintainer-only content, how adopters customise personas and decisions, and a pull-forward sequence

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions. Every judgment call is
assigned to you below; every anticipated failure has a named degraded path.

## 1. TASK

You are auditing how On The Loop separates what a release ships to adopters from this repository's maintainer-only
content, so that this repository (On The Loop's upstream repository) can consume On The Loop the same way an adopter
does -- "customer zero" -- and so that every improvement made here improves what adopters receive. Five surfaces are in
scope (Section 4): S1 core code, S2 contracts and configuration, S3 the decision corpus and its tiers, S4 agent
instruction surfaces, and S5 packaging and bootstrap. Much of the target state is designed but unbuilt (roadmap item
T4.24, `deferred_post_mvp`); the operator has decided to pull the separation work forward and wants this audit to
classify the surfaces, assess the existing mechanisms, and produce an ordered, plannable sequence. You answer ten
questions Q1-Q10 (Section 7): Q1 per-surface release classification; Q2 whether the recorded structural assumptions
can derive the `otl init` specification; Q3 target layout and boundary enforcement; Q4 pull-forward viability and
order; Q5 the three-repository topology; Q6 the released-version rule's timing; Q7 adopter customisation of personas
and skills; Q8 decisions that carry their rationale to adopters; Q9 an industry-practice rating; Q10 questions the
requester did not think to ask. Deliverables: `audits/release-boundary-<sha>.yaml` and
`audits/release-boundary-<sha>.md` (Section 14). The ONLY files you create or modify in the repository tree are those
two deliverables; regenerating gitignored local caches per Section 5 is expected and does not breach this (never
commit them). You draft; the human disposes.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS -- THE EPISTEMIC CONTRACT

This prompt hands you FACTS (Section 10) and CANDIDATE hypotheses (below). It hands you no verdicts. ASSUME NO
CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the candidates below has failed. Candidates
are leads of uneven quality, deliberately unranked; some are expected to be dismissed, and you should look for defects
not listed here -- but report only what you trace (precision wins over count; Section 17).

What a defect is, for this audit: (a) a property of the BUILT tree today that you can cite at file:line (or observe
by a command) and that works against the North Star (Section 6); or (b) an insufficiency in a RECORDED design (a
Decision clause, a roadmap criterion, a contract rule) such that building it as written would leave a gap. The mere
absence of something designed but deliberately unbuilt is NOT a defect: an unbuilt T4.24 criterion is a
`pull_forward_sequencing` row (Q4), never by itself a finding.

Adjudicate every candidate, and record it where the mapping says:

| Adjudication | Where it goes |
|---|---|
| Defect (a) or (b), no owning roadmap item / Decision / open rec | `findings[]`, `roadmap_crossref.classification: novel` |
| Defect whose owning item's remedy, as recorded, is insufficient | `findings[]`, `classification: planned-insufficient` |
| Defect (a), observable in the built tree today, whose owning item's remedy is sufficient but unbuilt | `findings[]`, `classification: planned-unbuilt` |
| No present defect: the owning design is sufficient and simply unbuilt | `rejected_candidates[]`: `why_dismissed` "deferred; owned by <item>"; its timing goes in `pull_forward_sequencing` |
| Planned and fully covered: the owning remedy is BUILT and works | `rejected_candidates[]`, naming the owning item |
| Not a defect | `rejected_candidates[]`, naming the compensating control or the reason |

A `deferred_post_mvp` status is never by itself a defect (Section 13).

A candidate may be partly confirmed and partly dismissed: then it appears in one or more findings for the confirmed
part AND in one `rejected_candidates` row for the dismissed part. Every candidate appears at least once. A defect you
find yourself that turns out to be fully covered goes in `rejected_candidates` with `candidate: "NEW: <short title>"`.

Candidates (C1-C25) are numbered with an upper-case C; they are unrelated to T4.24's lower-case criteria c1-c17. Each
is phrased as an observation plus the hypothesis to test. C21-C25 are counter-hypotheses that existing mechanisms
already suffice; adjudicate them with the same rigour, with the polarity reversed: a counter-hypothesis that HOLDS
goes to `rejected_candidates` (`why_dismissed` states what suffices; `control_property_match` is required); one that
is REFUTED becomes the subject of a finding.

- C1. The only recorded release classification in the repository is `binds` (repository / release / both) and
  `adopters` (bound / may_override / set_scale) on the nine `docs/contracts/north-star.yaml` principles. Hypothesis:
  decisions, contracts, checks, skills and config files have no equivalent machine-readable classification.
- C2. `docs/contracts/ports.yaml` `structural_assumptions` rows are recorded by hand as work meets them; the
  validator checks row shape and owning references. The Section 5 censuses find, as a floor, roughly 165 of roughly
  834 tracked `scripts/` and `src/` Python files quoting a `docs/`-or-`config/` repository path, and roughly 300 when
  path joins and other repository paths (`.claude/`, `AGENTS.md`, `logs/`, `terraform/`, `.github/`) are included.
  Hypothesis: the SA list is not a complete source for deriving the `otl init` specification (T4.24 c9).
- C3. `ports.yaml` sits within about 100 bytes of its byte budget; rec-5134 records an SA row deferred partly for
  size; rec-5274 (open) proposes a size relief valve for `ports.yaml`. Hypothesis: the contract size budget
  suppresses recording of structural assumptions.
- C4. Roughly 88 `scripts/`/`src/` files define their own module-level repository-root constant. Hypothesis: there is
  no single path-resolution seam through which repository-specific paths could be redirected to an `ontheloop/` root
  or a release-installed location.
- C5. T4.24 as a whole `depends_on: [T4.22, T4.23]`. Hypothesis: criteria c12, c14, c15, c16 and c17 do not
  technically depend on the local engine (T4.22) or the local ops-data adapter (T4.23), so the item-level dependency
  over-constrains a pull-forward.
- C6. T4.24 `files_in_scope` names `src/ontheloop/`, which does not exist; `packages/ontheloop-py` is a version 0.0.0
  namespace placeholder; the root `pyproject.toml` has no `[project]` table. Hypothesis: the release's own code
  location is undecided.
- C7. rec-4673 (open) proposes renaming `ontheloop/platform.yaml` to `ontheloop/project.yaml` before the first
  release. Hypothesis: creating the settings file during a pull-forward before the rename lands compounds the rename.
- C8. Decision 196 clause 4 ships Tier B "without this repository's amendment history"; `Rationale` is an optional
  marker in `docs/contracts/decision-entry.yaml`. Hypothesis: a Tier B snapshot would routinely lose the "why" an
  adopter or agent needs before superseding a decision.
- C9. The decision metadata envelope accepts `supersedes`; no field records what a superseding decision accepts in
  exchange. Hypothesis: an adopter's override is not required to acknowledge the rationale it overrules.
- C10. The SLOC check's output names Decision 43 (its section header and failure label, and the waiver text cites
  `decision-43`); its per-file over-budget message names the budget file and "with justification". It names neither
  Decision 128 (the raise guardrails) nor any reason text. Hypothesis: rule enforcement output does not deliver the
  rule's reason at the point of violation, only a Decision number.
- C11. Persona contracts carry this repository's specifics in their prompt templates, and declare typed inputs whose
  contract descriptions name this repository's files (`project_context` "The project context document, at main";
  `review_guidance` "Shell pre-computation, at main: deploy-paths.yaml's admin_out_of_band ..."). The input producers
  are not yet built: `scripts/personas/materialise.py` holds an empty `PROVIDERS` registry that "a later add plan"
  fills. The rendered prompt is pinned by `rendered_prompt_sha256`. Hypothesis: no input slot or mechanism exists for
  adopter domain guidance, and adding one conflicts with the hash pin unless designed for.
- C12. `docs/contracts/instruction-architecture.yaml` lists layers 1-5 and consumers `claude_code_interactive`,
  `executor`, `repo_less_coordinator`, `coordinated_worker`. Hypothesis: neither run_persona personas nor adopter
  guidance has a layer or consumer row.
- C13. The executor leash `boundary_patterns` lists `.claude/agents/`, `CLAUDE.md` and `docs/contracts/personas/` but
  no `.claude/skills/` entry. Hypothesis: where adopter guidance or skills would live, the governed agent is not
  barred from editing its own instructions.
- C14. Skill entry files mix generic method with this repository's references (for example `planning` carries about
  24 Decision citations, 16 distinct, and 34 repository path-prefix references by the Section 5 census, which counts
  occurrences and covers only four path prefixes). Hypothesis: shippable skill method and repository-only content are
  not separated.
- C15. No Decision, roadmap criterion or open rec found at compose time addresses adopters customising persona prompts
  or skills to their domain. Hypothesis: this is an unowned design gap.
- C16. `.claude-plugin/marketplace.json` publishes the `.claude/` directory as a Claude Code plugin named `ontheloop`
  for this repository's project threads. Hypothesis: its relationship to the release (a distribution channel for
  skills and hooks, or repository-only) is undefined.
- C17. Decision 185 clause 3 binds the released-version rule at first_of(concurrency above 1, release ships). T2.57
  (SIT) is `in_progress` and T2.58 (promotion train; PROD receives only the artifact that ran on SIT) is
  `not_started`. Hypothesis: the trigger names no environment condition, though the bootstrap may depend on one.
- C18. Roughly 332 tracked `tests/` Python files contain a quoted repository path. Hypothesis: core tests depend on
  this repository's own content rather than on fixture projects, so they would not run against a release in an
  adopter repository.
- C19. SA-03 records that the rec portal's glossary lint enforces this repository's house style on rec text.
  Hypothesis: other house-style rules are enforced in code paths an adopter cannot switch off.
- C20. Decision 196 clause 6 leaves Tier B / Tier C id prefixes undecided; code and check labels cite bare Decision
  numbers (for example the SLOC check's failure label). Hypothesis: shipping Tier B requires rewriting citations
  across code.
- C21. Counter-hypothesis: Decision 196 clause 5's trimming rule ("a decision ships in B iff a shipped mechanism
  cites it") is already a machine-checkable test of Tier B membership once shipped mechanisms are known, so no new
  per-decision classification field is needed.
- C22. Counter-hypothesis: the settings registry, the SA rows, `.importlinter` and `boundary_patterns` together
  already form a partial classification and enforcement mechanism that can be extended rather than replaced.
- C23. Counter-hypothesis: the persona contract's typed-input design (inputs materialised by a shell, a pinned
  template) already provides the seam adopter guidance needs: guidance would be one more typed input, and the hash pin
  covers only the template, not input values.
- C24. Counter-hypothesis: the roughly 88 module-level root constants (C4) can be redirected cheaply through one shim
  module, so their number is not a material migration cost.
- C25. Counter-hypothesis: many of the roughly 332 test files that quote repository paths (C18) test checks that are
  correctly `repository_only` or `optional_profile`, so their coupling to this repository is right rather than a
  defect.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

- "Platform" and "product": `docs/contracts/glossary.yaml` (Decision 240) forbids these words unqualified in new
  governed text. Use its terms: On The Loop (the framework), core (the free part a release carries), release (a
  versioned cut adopters install), this repository / the upstream repository (On The Loop's source plus what only its
  maintainers use), operator deployment (On The Loop Labs' own running install), adopter (an organisation running a
  release), adopter product (what an adopter builds). Older records (DECISIONS.md, the roadmap, plans, audit prompts)
  use the old words; read them through the glossary's `read_as` rows. Identifiers keep their spelling
  (`ontheloop/platform.yaml`, `docs/ROADMAP-PLATFORM.yaml`, the `agent_platform` profile). Your two deliverables live
  in `audits/`, which the glossary lint does not scan, but use the glossary terms anyway for precision.
- "The ontheloop plugin" (`.claude-plugin/marketplace.json`, `.claude/.claude-plugin/plugin.json`) is a Claude Code
  plugin packaging this repository's hooks and skills for its own project threads. It is not the `ontheloop` release
  distribution namespace (`packages/ontheloop-py`, CLI `otl`). Whether it should become a release channel is Q10
  material, not an assumption.
- Tier A: `docs/contracts/north-star.yaml` is this repository's own Tier A file, AND its `binds: release|both`
  entries are the only recorded statement of what binds adopters. An adopter's Tier A is the adopter's own north
  stars (Decision 196 clause 2). Do not read north-star.yaml as either one alone.
- "Profile": the house-style profile (Decision 211 clause 2) is unrelated to AWS profiles (`agent_platform`,
  `clouds.aws.profile`). In this audit "profile" means the house-style profile unless stated.
- "Executor": the legacy Python executor is retired (Decision 247). Layer 5 of `instruction-architecture.yaml`
  (`config/agent/executor/prompts/`) belongs to it. The Decision 185 loop's personas (`docs/contracts/personas/`,
  run via `run_persona`) are its successors and are a different surface. Do NOT read "executor" in a file name as
  "legacy": `config/agent/executor/capabilities.yaml` and its `boundary_patterns` are live, read by current checks
  (verify by grepping `scripts/checks/` for `capabilities.yaml`), and C13 concerns that live list. Likewise "the
  executor runs an installed kernel" in Decision 185 clause 3 (Q6's anchor) means the Decision 185 loop, so Decision
  247's retirement does not make that rule moot.
- "Kernel", "engine" and "core": "core" (glossary) is everything a release carries for free. "Kernel" (Decision 185
  clause 3, T4.24 c3, T4.22 c5) is the loop's executing code that the released-version rule says must be installed,
  not loaded from the edited tree -- a part of the core. "Engine" in prose and the bucket name `core_engine` mean
  any code a release ships and runs; `core_engine` is a classification bucket, not a component name.
- "Tier A/B/C": in this audit, always Decision 196's decision tiers (A north stars, B decisions a release ships, C an
  adopter's own decisions). Decision 66 (`docs/DECISIONS.md` ~13829) uses "Tier A/B/C/D" for unrelated enforcement
  tiers of the rec write gateway; ignore that sense here.
- Identifier collisions inside this prompt: candidates C1-C25 (upper-case C) are not T4.24 criteria c1-c17
  (lower-case c, always written `T4.24 c<n>` or `T4.24:c<n>`); north-star principles P1-P7 (Section 6) are not method
  phases M1-M9 (Section 12); audit surfaces S1-S5 are not the prose-budget classes S1/S2/S4/S8 of
  `config/prose_budgets.yaml` (written here as "prose class S<n>"); surface classes K01-K29 are this prompt's own.
- Persona contracts versus skills: a persona contract (`docs/contracts/personas/*.yaml`) is rendered to a prompt and
  run through LiteLLM; a skill (`.claude/skills/*/SKILL.md`) is loaded by an interactive Claude Code session. A
  persona's `skill_tether` names the skill whose verdict rule it mirrors. They are two surfaces.
- SIT and PROD are environments of the operator deployment (environment-taxonomy.yaml; Decision 252), not adopter
  environments. Adopters have no SIT/PROD obligation.
- "init": `otl init` (adopter bootstrap, T4.24) is not `terraform init`, not `terraform/bootstrap/`, and not the SIT
  account bootstrap (rec-5027).
- The public adopter products monorepo (Decision 209 clause 3(2); Decision 220) is outside this repository and not
  readable from your session. Assess Q5 from this repository's side only; record that limit in the Q5 prose.
- Repository versus project: "project" (glossary) is one repository a deployment governs; this repository is the
  operator deployment's project.

## 4. SCOPE

Trust nothing quoted in this prompt. Obtain every file, line, count and size by reading the repository; re-derive
from the repo and record any anchor that does not resolve in `meta.stale_anchors` (string: "<anchor as quoted> ->
<what you found>"), then continue. A "~N" line anchor resolves if the named content is within 10 lines of N; a count
quoted with "about" or "roughly" holds if within 10 percent.

Surfaces (state as of compose time; verify):

| Id | Surface | State |
|---|---|---|
| S1 | Core code: `scripts/`, `src/`, `bin/` | built |
| S2 | Contracts and configuration: `docs/contracts/`, `config/`, `docs/contracts/project-configuration.yaml` (settings registry), `docs/contracts/ports.yaml`; the tracked `ontheloop/` root, `ontheloop/platform.yaml`, the `.ontheloop/` runtime root | registry built; tracked `ontheloop/` and `ontheloop/platform.yaml` designed-unbuilt (T4.24 c12, c14, c15); `.ontheloop/` untracked and partly in use: `.gitignore` ignores only `/.ontheloop/maps/` (the default folder `docs/contracts/visual-grammar.yaml` names), an ignore that lasts "until T4.24 c12 creates and ignores that whole root" |
| S3 | Decision corpus and tiers: `docs/DECISIONS.md`, `docs/DECISIONS_ARCHIVE.md`, `docs/contracts/north-star.yaml`, `docs/contracts/decision-entry.yaml`; the Tier B release snapshot, Tier B/C id prefixes, the house-style profile | corpus built; Tier B snapshot, prefixes and profile designed-unbuilt (Decision 196 clause 8; T4.24 c7, c9) |
| S4 | Agent instruction surfaces: `AGENTS.md`, `CLAUDE.md`, `**/CLAUDE.md`, `.claude/skills/`, `.claude/commands/`, `.claude/hooks/`, `.claude/settings.json`, the `ontheloop` Claude Code plugin manifests, `docs/contracts/personas/`, `docs/contracts/executor-personas.yaml`, `docs/contracts/instruction-architecture.yaml` | built for this repository; no design for adopter customisation found at compose time (C15 to adjudicate) |
| S5 | Packaging and bootstrap: root packaging and tool config (`pyproject.toml`, `requirements*`), `packages/`, the released-version rule (Decision 185 clause 3), its dependency on SIT/PROD (T2.57, T2.58), the three-repository topology (Decision 209 clause 3) | placeholders only; designed-unbuilt (T4.24 c1, c3, c5) |

Release classification buckets (the vocabulary of Q1; pinned):

- `core_engine` -- code a release ships and runs; never copied into an adopter's repository.
- `release_schema_defaults` -- schemas, default values, Tier B decisions and the definitions of their enforcers that
  a release ships read-only, pinned to its version (Decision 196 clauses 4-5).
- `init_scaffold` -- content `otl init` generates into an adopter's tracked `ontheloop/` or gitignored `.ontheloop/`,
  authored by the adopter or generated then human-confirmed (Decision 211 clause 5); never copied verbatim from this
  repository's own instance.
- `optional_profile` -- house-style content a release ships that an adopter may decline through Tier C `supersedes`
  records (Decision 211 clause 2).
- `repository_only` -- this repository's maintainer-only content (glossary `this_repository`): its CI, Terraform,
  roadmap, plans, audits, its own Tier A and Tier C entries, operator deployment configuration.
- `split` -- the surface class mixes buckets; the block entry's `mechanism` names each part and its bucket.

Surface classes for the `release_classification` block (K01-K29). Classify every one; `split` is expected for several.
Every tracked path belongs to exactly one class. Where classes overlap, the most specific (longest matching) path
wins, with one exception: every `CLAUDE.md` file belongs to K23 wherever it sits. A path matched by no K01-K28 entry
belongs to K29.

K01 `scripts/checks/` | K02 `scripts/validate.py` and `scripts/checks/registry.py` dispatch | K03 `scripts/ops_portal/`,
`scripts/ops_data_portal.py`, `scripts/ops/` | K04 `scripts/roadmap/` | K05 `scripts/personas/`,
`scripts/persona_contracts/` | K06 `scripts/llm/` | K07 `scripts/session/`, `scripts/preflight/`, `scripts/postflight/` |
K08 `scripts/ci/`, `scripts/ci_rca/` | K09 `scripts/telemetry_capture/`, `scripts/telemetry_verify/`,
`scripts/telemetry_fact_index/` | K10 `scripts/sync/`, `scripts/recs_related/` | K11 `scripts/build_lambda*.py`,
`scripts/lambda_manifest.py` | K12 remaining `scripts/` modules (one entry; name notable members) | K13 `src/common/`,
`src/lambdas/`, `src/data/` | K14 `src/persona_backends/`, `src/persona_hosts/` | K15 `src/telemetry/`,
`src/telemetry_client/`, `src/turn_capture/`, `src/sensitive_values/`, `src/row_rules/`, `src/schemas/` | K16
`docs/contracts/*.yaml` excluding personas, north-star, glossary | K17 `docs/contracts/personas/` and
`docs/contracts/executor-personas.yaml` | K18 `docs/DECISIONS.md`, `docs/DECISIONS_ARCHIVE.md` | K19
`docs/contracts/north-star.yaml` | K20 `docs/contracts/glossary.yaml` | K21 `docs/ROADMAP-PLATFORM.yaml`, `docs/plans/`,
`docs/audit-prompts/`, `audits/` | K22 `config/` | K23 `AGENTS.md`, `CLAUDE.md`, `**/CLAUDE.md`,
`docs/PROJECT_CONTEXT.md` | K24 `.claude/skills/`, `.claude/commands/`, `.claude/agents/` | K25 `.claude/hooks/`,
`.claude/settings.json`, plugin manifests | K26 `.github/` (workflows, actions and the rest) | K27 `terraform/`,
`docker/`, `migrations/` | K28 `tests/`, `bin/`, root packaging and tool config (`pyproject.toml`, `requirements*`,
`.importlinter`, `.pre-commit-config.yaml`), `packages/` | K29 every other tracked path (one entry; name notable
members, for example `src/` packages not listed in K13-K15, other `docs/` files, root files such as `README.md`, the
licence files and `EVALUATION-PROMPTS.yaml`). K29's entry also classifies the untracked, partly used `.ontheloop/`
runtime root, the one untracked path in this block.

Classify at surface-class granularity. Do not classify file by file except inside the bounded samples of Section 11.
The operator did not settle whether `tests/` and `terraform/` belong in the classification; they are included as
whole classes (K27, K28), and you may argue in Q10 that either should be excluded or split further.

Surface assignment of a finding: every finding names exactly ONE primary surface S1-S5 (the surface whose change fixes
it); there is no `shared` value. A finding that spans surfaces names the primary one and lists the others in `gap`.

Out of scope (one line each; do not produce findings on them): the paid service and its control plane (Decisions 209
clause 2, 233); licensing and the Additional Use Grant (Decisions 171, 194; T4.24 c6 -- still give it a
`pull_forward_sequencing` entry, `at_first_release`, without assessing it); reducing this repository's own
agent-session cost (in flight elsewhere); the verdict-persona migration to GitHub Actions (T4.28-T4.30, T4.32);
Terraform and IaC changes; whether the content of any individual Decision is right; recommending a hard context-size
budget for adopter personas (the operator has directed visibility only; there is no Decision for this direction).

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repository rule, AGENTS.md): if your opening context has no `[hook_canary]` line from this
   repository's hooks, say "repo hooks not loaded" as your first status, write nothing, and end the turn. This is the
   one sanctioned stop in this prompt.
1. Base, names, branch -- in this order:
   a. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree and
      `meta.audited_commit`. IF fetch fails: use `git rev-parse --short HEAD` as the sha and `HEAD` as the base in
      step d, and record "base is local HEAD" in `meta.contract_notes`.
   b. Deliverable names: `audits/release-boundary-<sha>.yaml` and `.md`. IF either already exists at the base
      (`git ls-tree origin/main audits/`), use `release-boundary-<sha>-r2` (then `-r3`, ...) for both files and record
      the suffix in `meta.contract_notes`. Every later mention of the deliverables means these names.
   c. Branch name: `audit/release-boundary-<sha>`. IF it already exists (locally per `git branch --list`, or on the
      remote per `git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...). The branch suffix and the
      deliverable suffix are independent.
   d. `git switch -c <branch name> origin/main` now, so everything you read is the audited tree. This prompt is the
      operator's explicit instruction to create this branch; see Section 16 step 1. IF the switch is refused: run
      `git switch --detach origin/main`, read from there, record "branch creation refused" in `meta.contract_notes`,
      and at Section 16 take the step-1 refusal path.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes `logs/.preflight-report.json`
   (gitignored; expected). Use it in phase M1 as context only: its roadmap section gives current item statuses to
   compare with Section 10, and its CI-RCA section tells you whether `main` is red (record a red `main` in
   `meta.contract_notes`; it does not change your task). Recommendations have NO local cache in this repository; rec
   dedup uses the live reader (step 3). IF this fails: record the error in `meta.contract_notes` and continue to
   step 3.
3. Rec dedup probe -- run it whatever step 2 did:
   `bin/venv-python -m scripts.recs_related --terms "otl init ontheloop root" --limit 5 --table`.
   IF this fails: do NOT abort -- set `meta.degraded_dedup = true`, mark every finding whose classification rests on
   a rec search `confidence: HYPOTHESIS` with `roadmap_crossref.dedup_hit_count: null`, dedup against the roadmap and
   `docs/DECISIONS.md` only, and use the rec descriptions in Section 10 as the only rec content. Proceed.
4. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record it in `meta.contract_notes` and read the YAML as text.
5. Census commands (re-derive every count quoted in this prompt with these; record your values in the evidence of
   the findings that use them). Every census here is a FLOOR, not an exact count: it misses paths built in other
   ways (f-strings, constants defined in another module). Say "at least" when you report a census value.
   - Path-literal census, core code (narrow; `docs/` and `config/` literals):
     `git grep -lE '"(docs/contracts|docs/DECISIONS\.md|docs/plans|docs/ROADMAP|config/)' -- 'scripts/*.py' 'src/*.py' | wc -l`
     against `git ls-files 'scripts/*.py' 'src/*.py' | wc -l`; per-package breakdown:
     `git grep -lE '"(docs/contracts|docs/DECISIONS\.md|docs/plans|docs/ROADMAP|config/)' -- 'scripts/*.py' 'src/*.py' | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn`.
   - Broad census, core code (adds path joins and other repository paths; report the union):
     `{ git grep -lE '"(docs/contracts|docs/DECISIONS\.md|docs/plans|docs/ROADMAP|config/|\.claude/|AGENTS\.md|CLAUDE\.md|logs/|terraform/|\.github/)' -- 'scripts/*.py' 'src/*.py'; git grep -lE '"(docs|config|logs|audits)" */|/ *"(contracts|plans|DECISIONS\.md|agent)"' -- 'scripts/*.py' 'src/*.py'; } | sort -u | wc -l`.
   - Same narrow census over `'tests/*.py'`.
   - Root-constant census:
     `git grep -lE '^(REPO_ROOT|ROOT|_ROOT|_REPO_ROOT|PROJECT_ROOT) *=' -- 'scripts/*.py' 'src/*.py' | wc -l`.
   - Skill reference census: for each `.claude/skills/*/SKILL.md`, count `grep -oE 'Decision [0-9]+'` occurrences
     (and distinct values with `| sort -u | wc -l`) and `grep -oE '(docs/contracts|scripts/|config/|docs/plans)'`
     occurrences. These four prefixes are a floor; a skill with zero matches may still name other repository paths
     (`AGENTS.md`, `audits/`, `bin/venv-python`), so read before calling any skill free of repository content.
   - Decision rationale census: `grep -c '^## Decision [0-9]' docs/DECISIONS.md` and, for each of `Problem`,
     `Intent`, `Rationale`, `Reversal conditions`, `grep -cE '^\*\*<marker>:?\*\*' docs/DECISIONS.md` (the pattern
     matches both `**Rationale:**` and `**Rationale**:`).
   A literal match is a lead, not a defect: a quoted path in a check that enforces this repository's own house style
   may be correct `repository_only` or `optional_profile` behaviour (Q1 decides).
6. External lookups (Q9 only, and any Q7 claim about how a named tool layers agent instructions): you MAY use web
   search or fetch, at most 12 lookups in total (each search and each page fetch counts as one) -- do NOT exceed.
   Record each URL in the evidence field that uses it. A fact you could not look up is answered from your own
   knowledge and marked HYPOTHESIS. IF web access is unavailable: set `meta.web_unavailable = true`, answer from your
   own knowledge, and mark every checklist row and finding resting on an external fact `confidence: HYPOTHESIS`.

Repository-wide `validate` is advisory outside CI; running it is optional and never a gate (Section 16). The full
validation tier that AGENTS.md runs before a code handoff does not apply to this audit: your deliverables are two
data files under `audits/`, not code; do not run it.

## 6. NORTH STAR

These principles are the bar you judge each surface against. They are judgment-bearing, not absolute: argue each
rating; do not pattern-match.

- P1 The release is the interface (customer zero). This repository consumes On The Loop only through what a release
  ships plus its own tracked `ontheloop/` content and configuration -- no private path, literal or shortcut an
  adopter could not have. Improvements land in the release, so customer zero's friction is every adopter's friction.
- P2 Generate, never copy. `otl init` derives an adopter's content from shipped schemas, defaults and the adopter's
  own repository (Decision 211 clauses 1, 3, 5), never by copying this repository's instance.
- P3 One classification, recorded and enforced. Each artifact's release classification is machine-readable,
  collocated with its subject where practical, and enforced by a check that fails when crossed (AGENTS.md
  agent-first rules; NS.4).
- P4 Customise by layering, never by forking. Adopter changes are separate, explicit layers (Tier C `supersedes`,
  configuration, guidance) that survive a release upgrade (Decision 196 clause 4).
- P5 Rules carry their why. An adopter or agent meeting a rule can reach the reason for it, and an override is an
  informed act that names what it gives up. Operator position: without the why, a limit such as the 500-SLOC budget
  reads as a barrier to remove.
- P6 Migrate incrementally. Each move is one verifiable, revertible outcome (Decision 247); no big-bang
  restructure.
- P7 Context economy. Ambient context stays small and stable; the rest is loaded on demand or retrieved when needed
  (NS.4 detail: the balance between removing context an agent does not need and letting it retrieve what it does).
  The operator wants context size visible to adopters, not enforced as a hard requirement.

This repository's own north stars (`docs/contracts/north-star.yaml`) that bind both this repository and releases are
also in force; NS.1 (ports), NS.4 (the repo is for agents; adopters `may_override`), NS.6 (improvement is proven),
NS.8 (autonomy is earned) and NS.9 (humans hold approval authority) are the most relevant.

## 7. THE QUESTIONS

Answer every question in its own `question_answers` entry (Section 14). Verdict enums are pinned.

- Q1 Release classification. Classify each surface class K01-K29 into one bucket (Section 4) in the
  `release_classification` block. Then answer: is there an existing mechanism sufficient to record and enforce such a
  classification machine-readably (consider north-star.yaml `binds`/`adopters`, Decision 196 clause 5's trimming
  rule, `ports.yaml` structural assumptions, the settings registry, `.importlinter`, `config/agent/executor/
  capabilities.yaml`)? Where none is sufficient, propose one mechanism (shape, home, enforcing check); P3's
  collocated-and-checked shape is one option, not a required answer. Also name any class you classified
  `core_engine`, `release_schema_defaults` or `optional_profile` that carries operator deployment content (for example
  an account-specific setting, an admin-only path list, or this repository's own hook settings), since a release
  would then carry it. Verdict: `sufficient | partial | insufficient` (about the mechanism).
- Q2 Init-specification source. T4.24 c9 derives the `otl init` specification from `ports.yaml`
  `structural_assumptions`; Decision 224 adds the settings registry. Is that pair a sufficient and complete source?
  Consider how rows are recorded, what the validator checks, the path census, the root-constant census, the byte
  budget, and rec-4191's seeding scope. Verdict: `sufficient | partial | insufficient`.
- Q3 Target layout and boundary enforcement. Propose the target layout: where core code lives for a release, where
  this repository's own instance content lives (`ontheloop/`, `.ontheloop/`), where a shipped Tier B snapshot and
  shipped schemas live, and how core code finds each (one path-resolution seam or another mechanism). Then assess
  current enforcement: is any check today able to fail when core code reads a repository-only file other than through
  configuration? Verdict (about current enforcement): `sufficient | partial | insufficient`.
- Q4 Pull-forward viability and order. The operator has decided to pull the separation forward while the rest of
  T4.24 stays deferred. Is a pull-forward viable now, and in what order? Fill the `pull_forward_sequencing` block for
  every T4.24 criterion c1-c17 and for rec-4191, rec-4673, rec-5134, rec-5274, rec-5405 and rec-4391, and add any new
  slice you propose (id `NEW-<n>`). Each slice must be sized for one plan (one verifiable, revertible outcome; Decision
  247). Mapping rules:
  - A T4.24 criterion whose roadmap status is `met` is `already_met`.
  - A rec is judged on the INTENT its context states, not on its acceptance command: `already_met` only when that
    intent is achieved on the audited tree. An acceptance that already passes is evidence, not proof -- apply the
    counterfactual "would this acceptance have failed before the rec's work was done?"; if not, it passes vacuously,
    so say so in `rationale` and judge the intent. (rec-4191's acceptance grep already returns 12 at compose time; its
    stated intent is to seed SA rows from `scripts/executor/` and `src/`, which Q2 assesses.) Running a rec's
    acceptance command is optional; a command that names a test file that does not exist yet is not run.
  - A `closed` rec whose intent is achieved is `already_met`; a `closed` rec whose intent is not achieved gets a
    `NEW-<n>` slice for the residue and its own key is `not_applicable`; a `declined` or `superseded` rec is
    `not_applicable`; a `failed` rec is judged as if `open`.
  - Work that stays deferred beyond this pull-forward is `stays_deferred` (name its trigger in `blocked_behind`).
  - T4.24 c6 is `at_first_release` (out of scope, Section 4).
  - IF a rec's status or content cannot be read (Section 10 fallback), judge from Section 10's summary and set that
    entry's `confidence: HYPOTHESIS`.
  `pull_forward_sequencing` is the AUTHORITATIVE sequencing record: each finding names the slice key that fixes it in
  `sequencing.slice_key`, and the two must not disagree. Verdict: `viable | viable_with_preconditions | not_viable`.
- Q5 Repository topology (operator instinct: keep Decision 209 clause 3 -- this public repository holds the core; a
  public adopter products monorepo depends on On The Loop as an external dependency; a private monorepo holds the paid
  service). Does that topology hold given your Q1-Q3 answers, and what must the separation deliver before the
  adopter products monorepo can consume a release as a genuine external adopter rather than through this repository's
  internals? Verdict: `instinct_holds | instinct_holds_with_conditions | instinct_does_not_hold`.
- Q6 Released-version rule timing (operator instinct: adopt the rule -- this repository's loop runs an installed,
  released kernel, never the tree it edits -- only once SIT and PROD exist, because bootstrapping it earlier would be
  painful). Is Decision 185 clause 3's trigger complete as written? What bootstrap path (which artifact this
  repository runs, promoted how) makes the rule tractable, and what must exist first? Verdict:
  `instinct_holds | instinct_holds_with_conditions | instinct_does_not_hold`.
- Q7 Adopter customisation of personas and skills. Adopters will need personas and skills adapted to their domain
  (example: an adopter building Blender add-ons needs its own review and planning conventions). Assess a layered
  model -- a fixed persona frame (role, schemas, verdict rule, capabilities, budget, safety rules); shipped base
  method; adopter guidance (project-wide, path-scoped, installable domain packs); adopter-defined personas conforming
  to the persona contract schema -- against the existing seams (typed inputs, `rendered_prompt_sha256`,
  `skill_tether`, `boundary_patterns`, instruction-architecture layers, the plugin). Answer: which parts of today's
  persona templates and skills are `repository_only`; how adopter guidance enters without breaking verdict parsing,
  hash pinning or upgrades; whether adopter guidance files must be protected from the governed agent editing them;
  how instruction changes are evaluated (T4.14 is the offline persona evaluation lane); and how adopters see the
  context size each persona loads (advisory visibility only; a hard budget is excluded by operator direction).
  Verdict (about readiness of current design): `sufficient | partial | insufficient`.
- Q8 Decisions that carry their why. Operator position: house style, the 500-SLOC budget included, ships as Tier B
  decisions an adopter may supersede, and adopters and their agents need the rationale so that overriding is an
  informed act. Assess: does the designed Tier B snapshot carry each decision's rationale (Decision 196 clause 4
  drops amendment history; Rationale is optional); how should a superseding Tier C record acknowledge the rationale
  and the risk it accepts (compare Tier A's required `failure_signal`); how should rationale reach an agent at the
  moment a rule blocks it (enforcer messages pointing to the reason) without front-loading context; and which house
  style rules are enforced in code paths an adopter cannot switch off -- answer this last part from the Section 11
  check sample plus SA-03 only (no wider sweep; name further suspects in Q10). Verdict:
  `sufficient | partial | insufficient`.
- Q9 Industry practice. Rate the BUILT state against the external checklist below, property by property, in
  `external_checklist`: `met` = built and working; `partial` = partly built, or the built state reaches the property
  through an argued, property-matched compensating control (name it in evidence); `missed` = not built. A property
  that exists only as a recorded design is `missed`; name the design and its owning item in evidence, and judge the
  design's sufficiency in the relevant question, not in the rating. The same rule applies to rubric cells (Section 8):
  rate what is built; a design-only target rates `absent`. Verdict: `at_practice | partially_at_practice |
  below_practice`. Each row is
  relevant to the surfaces fixed here (used by Section 15's maturity rule; do not change them): E1 S1, S2; E2 S2;
  E3 S2, S5; E4 S5; E5 S3, S4; E6 S4; E7 S3; E8 S3; E9 S1, S4; E10 S1; E11 S5; E12 S4.
  External checklist (E1-E12; you may cite external sources within the Section 5 lookup cap):
  E1 Engine/project separation: a shipped engine reads the adopter's project through configuration and never copies
  itself into it (dbt Core and a dbt project; Terraform core and configuration).
  E2 One schema-validated project configuration file with defined precedence, plus a read-only doctor or validate
  command (`dbt debug`, `terraform validate`).
  E3 Customer zero through the public interface only: the vendor's own use goes through the same install and
  configuration path as adopters'.
  E4 Self-hosting bootstrap: the tool that changes itself runs a previous released build (compiler bootstrap, for
  example Rust building with the prior release).
  E5 Overlays, not forks: adopter customisation is an override layer that survives upgrades (Kustomize overlays,
  Helm values, shareable lint configs extended and overridden), never an ejected copy.
  E6 Layered, path-scoped, on-demand agent instructions (Claude Code's CLAUDE.md hierarchy and skills, GitHub
  Copilot's path-specific instruction files, Cursor rules with globs, AGENTS.md).
  E7 Rationale at the point of violation: an enforcement failure links the rule's documented reason (linters linking
  rule documentation; architecture decision records with context and consequences).
  E8 Justified suppression: overriding a rule requires a recorded reason (suppression comments that require a
  description; ADR supersession).
  E9 Runtime extension discovery against a stable interface (Terraform providers; Python entry-point plugins).
  E10 A conformance suite per extension point (Kubernetes conformance; Terraform provider acceptance tests).
  E11 A declared release manifest: what ships is enumerated, not inferred from the tree (package-data and file
  allow-lists).
  E12 Visibility of agent context cost per invocation (advisory telemetry an adopter can read).
- Q10 Questions the requester did not think to ask. Answer each seed below AND add at least two of your own:
  (a) Should `tests/` split along the release boundary so core tests run against fixture projects (C18)?
  (b) Should the `ontheloop` Claude Code plugin become the adopter distribution channel for skills and hooks, or stay
  repository-only (C16)?
  (c) Does the ordering of rec-4673 (settings file rename) relative to creating the file matter (C7)? The operator is
  neutral; answer on evidence.
  (d) How do Tier B/Tier C id prefixes interact with bare Decision numbers cited in code and check labels (C20)?
  (e) Should `terraform/` and `tests/` be inside or outside the release classification at all?
  (f) What is a release's stable interface (CLI verbs, contract schemas, check ids, persona contract shape), and how
  do an adopter's `ontheloop/` content and Tier C `supersedes` records migrate when a shipped schema changes?
  `contract_version` and `semantic_break` in contract amendment logs are a candidate existing mechanism.
  (g) The decision corpus has a warehouse half: `ops_decisions` is rebuilt from `docs/DECISIONS.md`
  (`ops_data_portal --backfill-decisions-md`, AGENTS.md), with read caches `logs/.decisions-index.jsonl` and
  `docs/decisions-index.json`. How does a release locate and rebuild these for an adopter, and does Tier B appear in
  an adopter's `ops_decisions`?
  (h) GitHub Actions is the verdict plane in every mode (Decision 184 clause 3), and this repository's gates live in
  `.github/workflows/` and `.github/actions/` (K26). How does an adopter receive the CI gates: reusable workflows or
  composite actions pinned to a release, or copies scaffolded by `otl init`? Which choice keeps P2 and P4?

## 8. RUBRIC

Rate every surface S1-S5 on every dimension: `strong | adequate | weak | absent | n/a`. `n/a` is correct and costless
where a dimension does not structurally apply; never manufacture a rating or a finding to fill a cell.

| Dim | Name | What is rated | Served questions |
|---|---|---|---|
| VD1 | Boundary recorded | Each artifact's release classification is recorded machine-readably | Q1, Q9 |
| VD2 | Boundary enforced | A check fails when the boundary is crossed (counterfactual: would it fail if the boundary were crossed?) | Q1, Q3, Q9 |
| VD3 | Configuration indirection | Repository-specific facts reach core code through the settings registry or one resolution seam, not literals | Q2, Q3 |
| VD4 | Upgrade-safe customisation | Adopter customisation survives a release upgrade without merge or fork | Q7, Q8, Q9 |
| VD5 | Rationale travels | The why reaches an adopter or agent with the rule, at the point it matters | Q8, Q9 |
| VD6 | Dogfooding fidelity | This repository uses only interfaces an adopter would | Q5, Q6, Q9 |
| VD7 | Incremental migratability | The surface can move to its target in small, revertible slices | Q4, Q6 |
| VD8 | Context economy | Ambient load small and stable; adopter additions bounded and visible | Q7, Q9 |

## 9. DEEP-DIVES

- DD-A Path coupling to init specification (feeds Q2, Q3, Q4). Trace three core paths end to end from where a
  repository path is named to where the file is opened: one from `scripts/checks/`, one from `scripts/roadmap/`, one
  from `src/`. For each, record whether the path also appears in `ports.yaml` `structural_assumptions` or in the
  settings registry, and what would have to change for it to resolve from an `ontheloop/` root or a release-installed
  location. Generalise to the population with the census.
- DD-B Rationale chain (feeds Q8). Follow one house-style rule from its enforcing check to an adopter: the 500-SLOC
  budget (`scripts/checks/sloc/sloc_limits.py`, `config/sloc_budgets.yaml`, Decisions 43, 102, 128, NS.4 detail).
  Record: what an agent sees when the check fails; where the reason is written; whether that reason would be in a
  Tier B snapshot under clause 4; and what an adopter's superseding record would have to say. Repeat briefly for one
  non-SLOC house-style rule of your choice (for example the glossary lint, SA-03).
- DD-C Persona instruction assembly (feeds Q7). For `docs/contracts/personas/code_reviewer.yaml`, trace how the
  rendered prompt is built (`docs/contracts/executor-personas.yaml` `rendered_prompt` rules, `scripts/persona_contracts/
  render.py`), which declared inputs name this repository's files in their contract descriptions, and where an
  adopter-guidance input would attach. The input producers are NOT built yet: `scripts/personas/materialise.py`
  holds an empty `PROVIDERS` registry that a later plan fills, so assess the declared design, not a producer. Verify
  that claim once with `git grep -l review_guidance -- scripts src .github` (expect no producer) and search no
  further. State what the hash pin covers and what it does not.
- DD-D Bootstrap path (feeds Q4, Q6). Using Decision 185 clause 3, Decision 252, T2.57, T2.58 and T4.22 c5 ("Loads the
  installed kernel, never the target repo tree"), describe the concrete sequence by which this repository would stop
  running its own working tree, and the earliest point each step is possible.

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Every anchor was read at compose time; verify each before
relying on it, and record any that does not resolve in `meta.stale_anchors`. Facts are stated without verdicts.

Decisions (`docs/DECISIONS.md`; line of the header or clause at compose time):
- Decision 184 (header ~6937): this repository is the core; cloud services are adapters behind ports.
- Decision 185 clause 3 (~6911): the released-version rule "binds at first_of(concurrency above 1, free-tier package
  ships) (T4.24)"; the kernel path, rendered ASL and persona contracts join `boundary_patterns`.
- Decision 196 (header ~5977): clause 4 (~6006) Tier B "ships as a static snapshot pinned to the package version,
  without this repository's amendment history"; customisation is a Tier C decision with `supersedes`. Clause 5
  (~6007) trimming rule: a decision ships in B iff a shipped mechanism cites it. Clause 6 (~6008) id prefixes fixed
  before the first release tag. Clause 8: execution rides T4.24.
- Decision 209 clause 3 (~5046): three monorepos -- this public repository (core), one public monorepo of
  adopter products importing releases, one private monorepo (paid service and closed-code adopter products); "No other
  repository is created speculatively."
- Decision 211 (header ~4896): clause 2 (~4922) house style is an optional profile that still binds this repository;
  declining it scaffolds Tier C `supersedes` records. Clause 5 (~4925) the `ontheloop/` and `.ontheloop/` layout.
  Clause 6: `otl init` passes on three repositories the operator did not write.
- Decision 212 (header ~4835): clause 8 (~4867) `ontheloop/platform.yaml` names the adapter per port; `otl doctor`.
- Decision 220 clause 5 (~4405): the core never imports adopter product code; its tests never need that tree.
- Decision 224 (header ~4101): clause 4 (~4129) local first; clause 7 (~4132) this repository adopts the homes once
  T4.24 creates `ontheloop/`.
- Decision 233 (header ~3401), Decision 240 (header ~2861), Decision 252 (header ~1935; SIT stood up now, PROD
  receives only the artifact that ran on SIT), Decision 128 (header ~10492; SLOC raise guardrails), Decision 275
  (header ~421; contracts budgeted in bytes).

Roadmap (`docs/ROADMAP-PLATFORM.yaml`):
- T4.24 (~line 7740): status `deferred_post_mvp`, `depends_on: [T4.22, T4.23]` (~7750), `files_in_scope:
  [pyproject.toml, src/ontheloop/, LICENSE]`, criteria c1-c17 (c9 ~7780, c12 ~7789, c14 ~7795).
- T4.22 (~7625) local engine, c5 "Loads the installed kernel, never the target repo tree". T4.23 (~7661) ops-data
  local adapter. T4.14 (~6876) offline persona evaluation. T3.14 (~5573) context budget as a tracked metric,
  measurement only. T2.57 (~8842) SIT, `in_progress`. T2.58 (~8870) promotion train, `not_started`.

Contracts and config:
- `docs/contracts/glossary.yaml`: terms `core` (~49), `this_repository` (~61), `release` (~69), `adopter` (~89).
- `docs/contracts/north-star.yaml`: semantics `binds` (~35) and `adopters` (~40); NS.4 (~127) detail names the 500-SLOC
  limit and the optional house-style profile.
- `docs/contracts/ports.yaml` (about 513 lines, about 50,160 bytes): `ports` (~120), `coupling_findings` (~240),
  `structural_assumptions` (~364; 16 rows SA-01..SA-16 at compose time; semantics ~112: "recorded as T2-T4 work
  meets it"). Byte budget `ports.yaml: 50251` in `config/structural_size_budgets.yaml` (~222).
- `scripts/checks/contracts/validate_ports_contract.py`: validates shape, uniqueness and owning references of
  coupling and structural-assumption rows.
- `docs/contracts/project-configuration.yaml`: `settings` (~98), 22 rows at compose time, most `this_repository.status:
  open` with `owning_item: T4.24:c16`.
- `docs/contracts/decision-entry.yaml`: `required_markers` (~79: Status, Date, Decision); optional markers (~86:
  Problem, Intent, Rationale, Reversal conditions, Related, Warehouse ID); envelope `optional_fields` (~337) includes
  `supersedes`.
- `docs/contracts/instruction-architecture.yaml`: `layers` (~96), `consumers` (~155).
- `docs/contracts/executor-personas.yaml`: persona contract shape; `rendered_prompt` rules (~205).
- `docs/contracts/personas/code_reviewer.yaml`: inputs `project_context` (~19, described "The project context
  document, at main"), `review_guidance` (~25, described as a "Shell pre-computation, at main" of deploy-paths.yaml's
  admin-only roots and sections of the tests guidance), `skill_tether` (~122), `rendered_prompt_sha256` (~127),
  `prompt_template` (~129). Rendered prompts in `docs/contracts/personas/rendered/` are roughly 10-16 KB each.
- `scripts/personas/materialise.py` (~20): `PROVIDERS: dict[str, Provider] = {}`; its docstring says "A later add
  plan registers its persona's own fields in PROVIDERS." No file under `scripts/`, `src/` or `.github/` computes
  `review_guidance` at compose time.
- `.gitignore` (~117): `/.ontheloop/maps/`; `docs/contracts/visual-grammar.yaml` (~196-200) names `.ontheloop/maps/`
  as a default folder.
- `config/agent/executor/capabilities.yaml`: `boundary_patterns` (~4), includes `.claude/agents/` (~32) and
  `docs/contracts/personas/` (~46).
- `config/agent/executor/capabilities.yaml` is read by live checks, for example
  `scripts/checks/contracts/validate_persona_contracts.py` and `scripts/checks/executor/validate_executor_boundary.py`.
- `config/prose_budgets.yaml`: byte budgets on ambient instruction prose (prose classes S1, S2, S4, S8).
- `scripts/checks/sloc/sloc_limits.py`: `validate_sloc_limits` (~81); section header "=== SLOC limits (Decision 43)
  ===" (~83); over-budget message (~110); unregistered-file message citing `decision-43` (~119-121); failure label
  "SLOC limits (Decision 43)" (~133). Decision 43 is "Directed Growth Governance" (`docs/DECISIONS.md` ~14026).
- `.claude-plugin/marketplace.json`: plugin `ontheloop`, source `./.claude`, "for project threads, which do not load
  .claude/settings.json".
- `packages/ontheloop-py/pyproject.toml`: name `ontheloop`, version `0.0.0`, "Reserved namespace placeholder".
- `.importlinter`: contracts govern first-party layering within `src` and `scripts`.

Open recs at compose time (content summarised neutrally). Verify each status with the ducklake-reads named verb
`recs_by_ids` (MCP tool `mcp__ducklake-reads__recs_by_ids`, one call with the comma-joined ids). IF that tool is
unavailable: `bin/venv-python -m scripts.recs_related --terms "<title words>" --table` returns OPEN recs only, so a
rec it returns is open and a rec it does not return has unknown status -- treat that rec's sequencing entry as
`confidence: HYPOTHESIS`.
- rec-4191 (open, Medium): seed `structural_assumptions` from hard-coded repository-path and AWS-identity assumptions
  in `scripts/executor/` and `src/`; acceptance is
  `test "$(grep -c '^  - id: SA-' docs/contracts/ports.yaml)" -ge 8` plus
  `bin/venv-python -m pytest tests/checks/contracts/test_validate_ports_contract.py::test_real_contract_passes -q`. At compose time
  that grep returns 12 (block-form rows only; 4 further rows are flow-form).
- rec-5274 (open, Low): give `docs/contracts/ports.yaml` a real size relief valve instead of flow-style compaction
  near its cap.
- rec-4673 (open, Medium): rename `ontheloop/platform.yaml` to `ontheloop/project.yaml` before the first release.
- rec-5134 (open, Low): record the merge gate's read of `project-configuration.yaml` as an SA row; deferred because
  `ports.yaml` was at budget.
- rec-5405 (open, Low): record the friction classifier's repository-specific rules as an SA row.
- rec-4391 (open, Low): import-linter contract forbidding the core from importing the showcase adopter product.
- rec-3718 (open, Medium): legacy Layer 5 executor prose has no byte budget.
- rec-4558, rec-4141 (open, Low): multi-writer enforcement; paid-service metadata allow-list (out of scope; for
  dedup only).

## 11. EMPIRICAL PASS

Tag every finding `evidence_kind: static` (read from code or docs) or `observed` (from a sampled artifact or a command
you ran). At equal severity, observed findings outrank static ones. Hard bounds -- do NOT exceed any:

- Census files: at most 15 files from the core-code broad census, stratified across at least 6 packages (a package
  is the first two path components, `scripts/<dir>` or `src/<dir>`; all root-level `scripts/*.py` modules count as
  one package), at least 5
  from `scripts/checks/`. Per file, apply the counterfactual: "If this repository's `docs/`, `config/` and decision
  files moved under `ontheloop/` and the location were supplied through configuration, would this file still work
  without an edit?" Record each file's bucket (Section 4) in `empirical_samples` (Section 14).
- Decisions: at most 10, chosen to include at least 4 house-style decisions (from 43, 86, 102, 127, 128, 240) and at
  least 3 whose clauses would ship in Tier B under the trimming rule. Per decision: does it carry Problem or
  Rationale; would its reason survive a Tier B snapshot that drops amendment history; does any enforcer message point
  to it.
- Check failure messages: at most 8 checks; record whether the failure text names the rule's reason or the Decision
  carrying it.
- Skills: every `.claude/skills/*/SKILL.md` (9 at compose time; "every" governs if the count differs) via the
  Section 5 census; read in full at most 2.
- Persona contracts: all 5 under `docs/contracts/personas/`, reading inputs and `prompt_template` only.

## 12. METHOD

- M1 Read: Sections 2-4 and 10; resolve anchors; skim the preflight report (Section 5 step 2).
- M2 Trace: DD-A to DD-D.
- M3 Classify: fill `release_classification` (Q1).
- M4 Empirical: Section 11 samples.
- M5 Adjudicate: every candidate C1-C25 to findings and/or `rejected_candidates`, plus any new findings. Their
  `roadmap_crossref` classifications are provisional until M7.
- M6 Rate: rubric cells; external checklist (Q9).
- M7 Dedup: Section 13 before any finding is final.
- M8 Sequence: fill `pull_forward_sequencing` (Q4) from surviving findings and dependencies.
- M9 Synthesize LAST: question answers, maturity, summary, report.

## 13. DEDUP DISCIPLINE

Before filing any finding, search the ownership surfaces: the roadmap (`tier_items[]` criteria text and
`candidate_decisions[]`), `docs/DECISIONS.md` headers and clauses, and open recs
(`bin/venv-python -m scripts.recs_related --terms "<terms>" --table`, paging with `--exclude` -- it takes the
comma-joined rec ids the previous page printed as `next_exclude` -- at most 3 pages per finding). Record
`dedup_search_terms` and `dedup_hit_count`. A "hit" is a returned item whose text you judge to OWN the defect (its
acceptance or criterion would fix it), not merely any returned row; record the raw returned total in
`roadmap_crossref.note`. `dedup_hit_count` counts owning hits only. A hit means a sufficiency assessment
(`planned-insufficient` / `planned-unbuilt`) or a `rejected_candidates` row, never a fresh discovery. A `novel`
finding requires a recorded negative search (`dedup_hit_count: 0` on the terms you list); without one it is
HYPOTHESIS.

DO NOT FLAG (deliberate constraints; you may assess how they are implemented or carried, never whether they should
exist):
- The three-tier decision model and Tier B read-only per release (Decision 196).
- House style as an optional profile that still binds this repository (Decision 211 clause 2).
- The 500-SLOC file budget and prose byte budgets (Decisions 128, 127). How their rationale is carried IS in scope.
- The three-repository topology (Decision 209 clause 3; Decision 220). Only Q5's verdict may argue it; never a
  finding.
- `deferred_post_mvp` statuses (Decision 93). Pull-forward is the `pull_forward_sequencing` block's job.
- Licence and Additional Use Grant (Decisions 171, 194).
- Whether this repository is public, and what Decision 101 lets it publish. (Whether operator deployment content
  would ride into a release IS in scope: Q1 and Section 15's critical class.)
- GitHub as approval authority; no identity system in the core (Decision 233 clause 4).
- Glossary names (Decision 240).
- Persona contracts' and the loop kernel's place in `boundary_patterns` (Decision 185 clause 3).
- (Scope, not a Decision: a hard context-size budget for adopter personas is out of scope by operator direction,
  Section 4; recommend visibility only.)

## 14. OUTPUT

Write `audits/release-boundary-<sha>.yaml`:

```yaml
audit:
  meta:
    audited_commit: <origin/main short sha>
    base_branch: main
    model: <your self-reported model name, free text>
    methodology_version: 1
    scope_surfaces: [S1, S2, S3, S4, S5]
    degraded_dedup: false        # true per Section 5 step 3
    web_unavailable: false       # true per Section 5 step 6
    contract_notes: ""           # setup failures, base fallback, unrelated validate failures
    stale_anchors: []            # "<anchor as quoted> -> <what you found>"
  question_answers:
    - {q: Q1, verdict: sufficient|partial|insufficient, basis: [<finding ids>], prose: ""}
    - {q: Q2, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q3, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q4, verdict: viable|viable_with_preconditions|not_viable, basis: [], prose: ""}
    - {q: Q5, verdict: instinct_holds|instinct_holds_with_conditions|instinct_does_not_hold, basis: [], prose: ""}
    - {q: Q6, verdict: instinct_holds|instinct_holds_with_conditions|instinct_does_not_hold, basis: [], prose: ""}
    - {q: Q7, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - {q: Q8, verdict: sufficient|partial|insufficient, basis: [], prose: ""}
    - q: Q9
      verdict: at_practice|partially_at_practice|below_practice
      basis: []
      prose: ""
      external_checklist:
        - {property: E1, rating: met|partial|missed, evidence: ""}
        # one row per E1..E12; partial requires an argued property-matched compensating control in evidence
    - q: Q10
      answers:
        - {question: "", answer: "", basis: [<finding ids>]}
        # seeds (a)-(g) plus at least two of your own
  per_surface_assessment:
    - {surface: S1, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
    # one per S1..S5
  rubric_ratings:
    - {surface: S1, dimension: VD1, rating: strong|adequate|weak|absent|n/a, evidence: "file:line|item-id", note: ""}
    # one per surface x dimension (5 x 8 = 40 rows)
  release_classification:
    K01: {verdict: core_engine|release_schema_defaults|init_scaffold|optional_profile|repository_only|split,
          mechanism: "", what_changes: "", cost: XS|S|M|L, rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    # one entry per K01..K29. mechanism: for split, each part and its bucket; for any other verdict, how the
    # class reaches its target (move into a release package, read through configuration, ship as profile, stay put).
    # what_changes = what must move or change for the class to reach its target; cost = effort to get there
  pull_forward_sequencing:
    T4.24:c1: {verdict: now|after_prerequisite|after_sit_prod|at_first_release|stays_deferred|already_met|not_applicable,
               order: <int or null>, blocked_behind: [<criterion, rec, finding or item ids>],
               slice: "", rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    # one per T4.24:c1..c17, rec-4191, rec-4673, rec-5134, rec-5274, rec-5405, rec-4391, and NEW-<n> slices;
    # verdict mapping rules for met criteria and closed/declined recs: Q4 text;
    # order is a 1-based global execution order across verdicts now and after_prerequisite (null otherwise);
    # slices that can run in parallel may share an order value;
    # slice = the one-plan outcome, phrased so /plan can take it
  findings:
    - id: RB-01
      surface: S1|S2|S3|S4|S5      # the ONE primary surface (Section 4)
      question: Q1..Q10
      dimension: VD1..VD8
      title: ""
      evidence: "file:line|item-id"
      evidence_kind: static|observed
      current_behavior: ""
      ideal_behavior: ""
      gap: ""
      compensating_controls_considered: ""
      change_type: add|rescope|enforce|unify|persist|clarify|retune_gate
      # add = new mechanism; rescope = change an item's scope; enforce = add a check for an existing rule;
      # unify = merge duplicate sources; persist = record something only held in prose or code; clarify = wording;
      # retune_gate = change a threshold or trigger
      proposed_change: ""
      acceptance: ""             # a check a planner could turn into a verification step
      severity: critical|high|medium|low
      severity_rationale: ""
      confidence: CONFIRMED|HYPOTHESIS
      roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt, item_ids: [],
                         dedup_search_terms: [], dedup_hit_count: 0, note: ""}
      effort: XS|S|M|L           # XS under an hour; S one small plan; M one full plan; L needs splitting into plans
      depends_on: [<finding ids>]
      sequencing: {slice_key: <pull_forward_sequencing key>, safe_to_queue_now: true|false,
                   blocked_behind: [<finding or roadmap ids>], note: ""}
      # safe_to_queue_now = the operator could file it as a rec and plan it today without waiting on anything
  rejected_candidates:
    - {candidate: C<n> | "NEW: <short title>", why_dismissed: "", compensating_control: "", control_property_match: "",
       decision_or_item_id: ""}
  empirical_samples:             # system of record for the Section 11 samples; never counted as findings
    - {kind: census_file|decision|check_message|skill|persona_contract, subject: "<path or id>",
       result: "<the per-sample answer Section 11 asks for>", bucket: <Section 4 bucket or null>, note: ""}
  summary:
    total_findings: 0
    novel_count: 0
    planned_insufficient_count: 0
    planned_unbuilt_count: 0
    top_improvements: [<finding ids>]   # min(3, total_findings) to 5 ids, highest leverage first
    highest_leverage_change: <finding id>
    degraded: ""                        # "none" unless any of: a meta flag is true, stale_anchors is non-empty,
                                        # or contract_notes records a base, branch or setup fallback; then list them
    maturity_S1: <value>
    maturity_S2: <value>
    maturity_S3: <value>
    maturity_S4: <value>
    maturity_S5: <value>
```

Invariants (state them to yourself and hold them):
- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects. `total_findings = len(findings) =
  novel_count + planned_insufficient_count + planned_unbuilt_count`. Fully covered candidates live in
  `rejected_candidates`, NOT findings. `rubric_ratings`, `question_answers`, `release_classification`,
  `pull_forward_sequencing` and `empirical_samples` are systems of record referenced FROM findings, never re-counted. `top_improvements` and
  `highest_leverage_change` MUST be finding ids. Order `top_improvements` by leverage; where two candidates for a
  place have equal severity and leverage, the `observed` one ranks first.
- Every candidate C1-C25 appears at least once: in one or more findings (cite it in the finding's `title` or `gap` as
  "(C<n>)"), in one `rejected_candidates` row, or in both when partly confirmed and partly dismissed (Section 2).
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal: name the property
  the control exercises, where it operates (mechanism or file:line), and why the control would FAIL if the defect were
  real.
- CONFIRMED requires the behaviour traced to file:line or an observed sampled artifact; anything less is HYPOTHESIS.
  For a `release_classification` entry, CONFIRMED means you read at least one file of the class (or its sampled
  files) and the bucket follows from what you read; otherwise HYPOTHESIS.
- Keep every free-text `evidence` field to about 40 words; a rubric cell with no finding behind it carries one line.
- Every `release_classification` key K01-K29 and every listed `pull_forward_sequencing` key is present.

Write `audits/release-boundary-<sha>.md`, prose, at most 1500 words, the layer the operator reads first:
1. A five-to-eight sentence synopsis: the answer to "what does a release ship, what stays here, and what do we do
   first". If `summary.degraded` is not "none", the synopsis's first sentence says which degraded paths ran.
2. One line per question Q1-Q9 with its verdict.
3. The proposed first five slices from `pull_forward_sequencing`, in order, one line each.
4. The highest-leverage change and why.
5. "Open questions for the operator": at most 6, each one sentence, each a decision only the operator can make (for
   example direction on Q5/Q6 conditions or Q7/Q8 design choices), each naming the finding or block entry it rests
   on.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never inherited from this prompt's framing:

- critical: the separation as built or designed would ship operator secrets or private data in a release; or would
  let a governed agent edit its own enforcer or instructions in an adopter deployment; or would produce a release
  that silently applies this repository's own repository-bound rules to adopters as if release-bound (a
  wrong-but-trusted outcome).
- high: a gap that would force adopters to fork or restructure their repository (against Decision 211 clauses 1 and
  6), or break adopter customisation on upgrade, or make this repository depend on a private path an adopter cannot
  have -- AND whose compensating controls you judged insufficient.
- medium: a missing record, redundancy, ambiguity or inconsistency with a clear fix.
- low: clarity or wording.

A compensating control lowers severity or justifies dismissal only if it PROPERTY-MATCHES: it exercises the same
property AND would fail if the defect were real (apply the counterfactual to the control). A control that could not
catch the break does neither.

Maturity: compute LAST, per surface, top-down, first match wins:
- frontier = 0 critical or high findings whose `surface` is this surface AND every Q9 `external_checklist` row
  relevant to the surface (the fixed mapping in Q9) rated `met` or `partial` (never `missed`).
- strong = 0 critical AND at most 1 high.
- solid = at most 1 critical.
- nascent = otherwise.
Count every finding whose `surface` is this surface, of every classification (`novel`, `planned-insufficient` and
`planned-unbuilt` alike). Deliberate deferral is handled before this point, by the Section 2 rule that unbuilt-ness
alone is never a finding; it does not discount findings here. The top rating remains reachable where you argued a
property-matched compensating control; this prompt's framing does not foreclose it.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on the `audit/release-boundary-<sha>` branch (with any
   `-rN` suffix) created from it. This prompt is the operator's explicit instruction to create that branch; AGENTS.md's
   harness-branch rule governs routine sessions, not this one. The `audit/` branch is SANCTIONED, not a conflict for
   Section 17 to resolve: `docs/contracts/git-ops.yaml` registers
   `extra_destination_prefixes: ["audit/"]` (its `handoff_evidence_gate` block), the governing rule for audit
   deliverables, which need a clean two-file diff off the audited base. The branch base MUST equal
   `meta.audited_commit`; the branch name carries any `-rN` suffix Section 5 step 1 added. IF creating or pushing the
   `audit/` branch is refused by a hook or the git remote: keep the commit local, do not open a PR, and end the turn
   with a final message naming the local branch, the commit sha, the exact refusal and the `summary:` block, so the
   human can push it. (The same block registers `audits/*` as exempt from the pre-push validation-evidence hook, so
   the two deliverables need no validation run to push.)
2. Pre-push gate: the YAML parses
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/release-boundary-<sha>.yaml`),
   the counting invariant and the candidate-coverage rule hold, every K01-K29 key is present, and the report is within
   its cap (`wc -w` on the `.md` <= 1500). Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory
   outside CI); if you run it and an unrelated failure appears, record it in `meta.contract_notes` before committing
   -- never fix it.
3. `git add` exactly the two deliverables, then `git commit -m "audit: release boundary review"` (append any
   attribution trailer lines your harness instructs for commits) using the session's configured identity and signing
   (do not override the author, do not disable or force signing, do not amend). If a pre-commit hook rejects the
   commit, fix the cause inside your two deliverables and commit again; never bypass the hook.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command). On a network error
   retry immediately, up to 4 more attempts (no sleep needed). IF the push still fails, or fails for a non-network reason: do not open a PR;
   end the turn with a final message naming the branch, the commit sha, the exact error and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`, base `main`, head your
   branch, ready for review, NOT draft), title
   `audit: release boundary between the core and this repository (S1-S5)`, body = a 2-3 sentence lede (its first
   sentence states any degraded path, per `summary.degraded`) plus the `summary:` block in a yaml fence, plus any PR
   footer your harness instructs. IF the tool is unavailable or the
   call fails: end the turn with a final message naming the pushed branch, the sha, the error and the intended title
   and body, so the human can open it. Then END THE TURN -- do not poll, do not merge, do not subscribe, do not
   self-approve. The human disposes of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): `audits/release-boundary-<sha>.yaml` and `audits/release-boundary-<sha>.md`. Nothing
  else in the tree is created, modified or deleted. Do not file recs, edit contracts, edit the roadmap, or open any
  other PR; recommendations live in your findings and blocks.
- Read-only everywhere else: no deploys, no Terraform, no writes through `scripts.ops_data_portal`.
- Fewer than about 8 surviving findings is a valid result -- state it; do not pad. Precision over volume: one traced
  finding is worth more than five plausible ones.
- Do not restate this prompt's candidates as conclusions; every verdict needs your own trace.
- Stay inside the Section 11 sampling caps and the Section 5 lookup cap.
- If an instruction here conflicts with a repository hook or harness rule, the hook or rule wins; record the conflict
  in `meta.contract_notes` and continue on the nearest compliant path.
