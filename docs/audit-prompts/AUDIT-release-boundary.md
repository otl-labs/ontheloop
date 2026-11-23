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
are leads of uneven quality, deliberately unranked; some are expected to be dismissed, and defects not listed here are
expected to be found.

Adjudicate every candidate to exactly one outcome, and record it where the mapping says:

| Adjudication | Where it goes |
|---|---|
| CONFIRMED defect, no owning roadmap item / Decision / open rec | `findings[]`, `roadmap_crossref.classification: novel` |
| Planned, and the owning item's remedy is insufficient | `findings[]`, `classification: planned-insufficient` |
| Planned, owning item's remedy is sufficient but unbuilt | `findings[]`, `classification: planned-unbuilt` |
| Planned and fully covered (built, or sufficient and correctly sequenced so no new action is needed) | `rejected_candidates[]`, naming the owning item |
| Not a defect | `rejected_candidates[]`, naming the compensating control or the reason |

A `deferred_post_mvp` status is never by itself a defect (Section 13). Whether a deferred item should be pulled
forward is answered in the `pull_forward_sequencing` block (Q4), not as a finding.

Candidates (C1-C20). Each is phrased as an observation plus the hypothesis to test:

- C1. The only recorded release classification in the repository is `binds` (repository / release / both) and
  `adopters` (bound / may_override / set_scale) on the nine `docs/contracts/north-star.yaml` principles. Hypothesis:
  decisions, contracts, checks, skills and config files have no equivalent machine-readable classification.
- C2. `docs/contracts/ports.yaml` `structural_assumptions` rows are recorded by hand as work meets them; the
  validator checks row shape and owning references. A literal-path census finds roughly 165 of roughly 834 tracked
  `scripts/` and `src/` Python files containing a quoted repository path. Hypothesis: the SA list is not a complete
  source for deriving the `otl init` specification (T4.24 c9).
- C3. `ports.yaml` sits within about 100 bytes of its byte budget; rec-5134 records an SA row deferred partly for
  size. Hypothesis: the contract size budget suppresses recording of structural assumptions.
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
- C10. The SLOC check's failure text names the budget file and "with justification" but no pointer to the Decision
  carrying the limit's rationale. Hypothesis: rule enforcement messages do not deliver rationale at the point of
  violation.
- C11. Persona contracts carry this repository's specifics in their prompt templates and in typed inputs computed
  from this repository's files (`project_context`, `review_guidance`); the rendered prompt is pinned by
  `rendered_prompt_sha256`. Hypothesis: no input slot or mechanism exists for adopter domain guidance, and adding one
  conflicts with the hash pin unless designed for.
- C12. `docs/contracts/instruction-architecture.yaml` lists layers 1-5 and consumers `claude_code_interactive`,
  `executor`, `repo_less_coordinator`, `coordinated_worker`. Hypothesis: neither run_persona personas nor adopter
  guidance has a layer or consumer row.
- C13. The executor leash `boundary_patterns` lists `.claude/agents/`, `CLAUDE.md` and `docs/contracts/personas/` but
  no `.claude/skills/` entry. Hypothesis: where adopter guidance or skills would live, the governed agent is not
  barred from editing its own instructions.
- C14. Skill entry files mix generic method with this repository's references (for example `planning` cites about
  24 Decision numbers and 34 repository paths; `audit-prompt` cites none). Hypothesis: shippable skill method and
  repository-only content are not separated.
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
  run via `run_persona`) are its successors and are a different surface.
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
<what you found>"), then continue.

Surfaces (state as of compose time; verify):

| Id | Surface | State |
|---|---|---|
| S1 | Core code: `scripts/`, `src/`, `bin/`, root packaging files | built |
| S2 | Contracts and configuration: `docs/contracts/`, `config/`, `docs/contracts/project-configuration.yaml` (settings registry), `docs/contracts/ports.yaml`; the tracked `ontheloop/` root, `ontheloop/platform.yaml`, gitignored `.ontheloop/` | registry built; `ontheloop/` roots designed-unbuilt (T4.24 c12, c14, c15) |
| S3 | Decision corpus and tiers: `docs/DECISIONS.md`, `docs/DECISIONS_ARCHIVE.md`, `docs/contracts/north-star.yaml`, `docs/contracts/decision-entry.yaml`; the Tier B release snapshot, Tier B/C id prefixes, the house-style profile | corpus built; Tier B snapshot, prefixes and profile designed-unbuilt (Decision 196 clause 8; T4.24 c7, c9) |
| S4 | Agent instruction surfaces: `AGENTS.md`, `CLAUDE.md`, `**/CLAUDE.md`, `.claude/skills/`, `.claude/commands/`, `.claude/hooks/`, `.claude/settings.json`, the `ontheloop` Claude Code plugin manifests, `docs/contracts/personas/`, `docs/contracts/executor-personas.yaml`, `docs/contracts/instruction-architecture.yaml` | built for this repository; adopter customisation undesigned (C15 to adjudicate) |
| S5 | Packaging and bootstrap: root `pyproject.toml`, `packages/`, the released-version rule (Decision 185 clause 3), its dependency on SIT/PROD (T2.57, T2.58), the three-repository topology (Decision 209 clause 3) | placeholders only; designed-unbuilt (T4.24 c1, c3, c5) |

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
- `split` -- the surface class mixes buckets; the block entry names each part and its bucket.

Surface classes for the `release_classification` block (K01-K28). Classify every one; `split` is expected for several:

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
`docs/PROJECT_CONTEXT.md` | K24 `.claude/skills/`, `.claude/commands/` | K25 `.claude/hooks/`, `.claude/settings.json`,
plugin manifests | K26 `.github/workflows/`, `.github/actions/` | K27 `terraform/`, `docker/`, `migrations/` | K28
`tests/`, `bin/`, root packaging and tool config (`pyproject.toml`, `requirements*`, `.importlinter`,
`.pre-commit-config.yaml`), `packages/`.

Classify at surface-class granularity. Do not classify file by file except inside the bounded samples of Section 11.
The operator did not settle whether `tests/` and `terraform/` belong in the classification; they are included as
whole classes (K27, K28), and you may argue in Q10 that either should be excluded or split further.

Out of scope (one line each; do not produce findings on them): the paid service and its control plane (Decisions 209
clause 2, 233); licensing and the Additional Use Grant (Decisions 171, 194; T4.24 c6); reducing this repository's own
agent-session cost (in flight elsewhere); the verdict-persona migration to GitHub Actions (T4.28-T4.30, T4.32);
Terraform and IaC changes; whether the content of any individual Decision is right.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repository rule, AGENTS.md): if your opening context has no `[hook_canary]` line from this
   repository's hooks, say "repo hooks not loaded" as your first status, write nothing, and end the turn. This is the
   one sanctioned stop in this prompt.
1. `git fetch origin main` then `git rev-parse --short origin/main`. This sha IS the audited tree; use it in the
   deliverable filenames, the branch name and `meta.audited_commit`. Then
   `git switch -c audit/release-boundary-<sha> origin/main` now, so everything you read is the audited tree. IF that
   branch already exists (locally per `git branch --list`, or on the remote per `git ls-remote --heads origin <name>`),
   append `-r2` (then `-r3`, ...) to the branch name only; the deliverable filenames keep `<sha>`. IF fetch fails:
   use `git rev-parse --short HEAD`, branch off `HEAD` instead, and record "base is local HEAD" in
   `meta.contract_notes`.
2. `bin/venv-python -m scripts.session.preflight --roadmap-detail full` -- writes `logs/.preflight-report.json`
   (gitignored; expected). Recommendations have NO local cache in this repository; rec dedup uses the live reader
   (step 3). IF this fails: record the error in `meta.contract_notes` and continue to step 3.
3. Rec dedup probe -- run it whatever step 2 did:
   `bin/venv-python -m scripts.recs_related --terms "otl init ontheloop root" --limit 5 --table`.
   IF this fails: do NOT abort -- set `meta.degraded_dedup = true`, mark every finding whose classification rests on
   a rec search `confidence: HYPOTHESIS` with `roadmap_crossref.dedup_hit_count: null`, dedup against the roadmap and
   `docs/DECISIONS.md` only, and use the rec descriptions in Section 10 as the only rec content. Proceed.
4. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record it in `meta.contract_notes` and read the YAML as text.
5. Census commands (re-derive every count quoted in this prompt with these; record your values in the evidence of
   the findings that use them):
   - Path-literal census, core code:
     `git grep -lE '"(docs/contracts|docs/DECISIONS\.md|docs/plans|docs/ROADMAP|config/)' -- 'scripts/*.py' 'src/*.py' | wc -l`
     against `git ls-files 'scripts/*.py' 'src/*.py' | wc -l`; per-package breakdown:
     `git grep -lE '"(docs/contracts|docs/DECISIONS\.md|docs/plans|docs/ROADMAP|config/)' -- 'scripts/*.py' 'src/*.py' | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn`.
   - Same census over `'tests/*.py'`.
   - Root-constant census:
     `git grep -lE '^(REPO_ROOT|ROOT|_ROOT|_REPO_ROOT|PROJECT_ROOT) *=' -- 'scripts/*.py' 'src/*.py' | wc -l`.
   - Skill reference census: for each `.claude/skills/*/SKILL.md`, count `grep -oE 'Decision [0-9]+'` and
     `grep -oE '(docs/contracts|scripts/|config/|docs/plans)'` matches.
   - Decision rationale census: `grep -c '^## Decision [0-9]' docs/DECISIONS.md` and, for each of `Problem`,
     `Intent`, `Rationale`, `Reversal conditions`, `grep -cE '^\*\*<marker>:?\*\*' docs/DECISIONS.md`.
   A literal match is a lead, not a defect: a quoted path in a check that enforces this repository's own house style
   may be correct `repository_only` or `optional_profile` behaviour (Q1 decides).
6. External lookups (Q9 only, and any Q7 claim about how a named tool layers agent instructions): you MAY use web
   search or fetch, at most 12 lookups in total (each search and each page fetch counts as one) -- do NOT exceed.
   Record each URL in the evidence field that uses it. A fact you could not look up is answered from your own
   knowledge and marked HYPOTHESIS. IF web access is unavailable: set `meta.web_unavailable = true`, answer from your
   own knowledge, and mark every checklist row and finding resting on an external fact `confidence: HYPOTHESIS`.

Repository-wide `validate` is advisory outside CI; running it is optional and never a gate (Section 16).

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

- Q1 Release classification. Classify each surface class K01-K28 into one bucket (Section 4) in the
  `release_classification` block. Then answer: is there an existing mechanism sufficient to record and enforce such a
  classification machine-readably (consider north-star.yaml `binds`/`adopters`, Decision 196 clause 5's trimming
  rule, `ports.yaml` structural assumptions, the settings registry, `.importlinter`, `config/agent/executor/
  capabilities.yaml`)? Where none is sufficient, propose one mechanism (shape, home, enforcing check). Verdict:
  `sufficient | partial | insufficient` (about the mechanism).
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
  every T4.24 criterion c1-c17 and for rec-4191, rec-4673, rec-5134, rec-5405 and rec-4391, and add any new slice you
  propose (id `NEW-<n>`). Each slice must be sized for one plan (one verifiable, revertible outcome; Decision 247).
  Verdict: `viable | viable_with_preconditions | not_viable`.
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
  style rules are enforced in code paths an adopter cannot switch off. Verdict: `sufficient | partial | insufficient`.
- Q9 Industry practice. Rate the designed and built state against the external checklist below, property by
  property, in `external_checklist` (rating `met | partial | missed`; `partial` requires an argued, property-matched
  compensating control in evidence). Verdict: `at_practice | partially_at_practice | below_practice`.
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
  render.py`), which inputs are computed from this repository's files (find the producers under `scripts/personas/`),
  and where an adopter-guidance input would attach. State what the hash pin covers and what it does not.
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
- `docs/contracts/personas/code_reviewer.yaml`: inputs `project_context` (~19), `review_guidance` (~25, computed from
  this repository's deploy-paths and tests guidance), `skill_tether` (~122), `rendered_prompt_sha256` (~127),
  `prompt_template` (~129). Rendered prompts in `docs/contracts/personas/rendered/` are roughly 10-16 KB each.
- `config/agent/executor/capabilities.yaml`: `boundary_patterns` (~4), includes `.claude/agents/` (~32) and
  `docs/contracts/personas/` (~46).
- `config/prose_budgets.yaml`: byte budgets on ambient instruction prose (S1, S2, S4, S8 classes).
- `scripts/checks/sloc/sloc_limits.py`: `validate_sloc_limits` (~81); over-budget message (~110); failure label
  "SLOC limits (Decision 43)" (~133).
- `.claude-plugin/marketplace.json`: plugin `ontheloop`, source `./.claude`, "for project threads, which do not load
  .claude/settings.json".
- `packages/ontheloop-py/pyproject.toml`: name `ontheloop`, version `0.0.0`, "Reserved namespace placeholder".
- `.importlinter`: contracts govern first-party layering within `src` and `scripts`.

Open recs at compose time (verify status with the reader; content summarised neutrally):
- rec-4191 (open, Medium): seed `structural_assumptions` from hard-coded repository-path and AWS-identity assumptions
  in `scripts/executor/` and `src/`; acceptance is at least 8 SA rows.
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

- Census files: at most 15 files from the core-code path census, stratified across at least 6 packages, at least 5
  from `scripts/checks/`. Per file, apply the counterfactual: "If this repository's `docs/`, `config/` and decision
  files moved under `ontheloop/` and the location were supplied through configuration, would this file still work
  without an edit?" Record each file's bucket (Section 4).
- Decisions: at most 10, chosen to include at least 4 house-style decisions (from 43, 86, 102, 127, 128, 240) and at
  least 3 whose clauses would ship in Tier B under the trimming rule. Per decision: does it carry Problem or
  Rationale; would its reason survive a Tier B snapshot that drops amendment history; does any enforcer message point
  to it.
- Check failure messages: at most 8 checks; record whether the failure text names the rule's reason or the Decision
  carrying it.
- Skills: all 9 `.claude/skills/*/SKILL.md` via the Section 5 census; read in full at most 2.
- Persona contracts: all 5 under `docs/contracts/personas/`, reading inputs and `prompt_template` only.

## 12. METHOD

- P1 Read: Sections 2-4 and 10; resolve anchors.
- P2 Trace: DD-A to DD-D.
- P3 Classify: fill `release_classification` (Q1).
- P4 Empirical: Section 11 samples.
- P5 Adjudicate: every candidate C1-C20 to findings or `rejected_candidates`, plus any new findings.
- P6 Rate: rubric cells; external checklist (Q9).
- P7 Dedup: Section 13 before any finding is final.
- P8 Sequence: fill `pull_forward_sequencing` (Q4) from surviving findings and dependencies.
- P9 Synthesize LAST: question answers, maturity, summary, report.

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
- Public-content boundary (Decision 101).
- GitHub as approval authority; no identity system in the core (Decision 233 clause 4).
- Glossary names (Decision 240).
- A hard context-size budget for adopter personas: excluded by operator direction; recommend visibility only.
- Persona contracts' and the loop kernel's place in `boundary_patterns` (Decision 185 clause 3).

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
        # seeds (a)-(e) plus at least two of your own
  per_surface_assessment:
    - {surface: S1, maturity: frontier|strong|solid|nascent, strengths: "", top_gaps: [<finding ids>]}
    # one per S1..S5
  rubric_ratings:
    - {surface: S1, dimension: VD1, rating: strong|adequate|weak|absent|n/a, evidence: "file:line|item-id", note: ""}
    # one per surface x dimension (5 x 8 = 40 rows)
  release_classification:
    K01: {verdict: core_engine|release_schema_defaults|init_scaffold|optional_profile|repository_only|split,
          mechanism: "", what_changes: "", cost: XS|S|M|L, rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    # one entry per K01..K28; for split, mechanism names each part and its bucket;
    # what_changes = what must move or change for the class to reach its target; cost = effort to get there
  pull_forward_sequencing:
    T4.24:c1: {verdict: now|after_prerequisite|after_sit_prod|at_first_release|already_met|not_applicable,
               order: <int or null>, blocked_behind: [<criterion, rec, finding or item ids>],
               slice: "", rationale: "", confidence: CONFIRMED|HYPOTHESIS}
    # one per T4.24:c1..c17, rec-4191, rec-4673, rec-5134, rec-5405, rec-4391, and NEW-<n> slices you propose;
    # order is a 1-based global execution order across verdicts now and after_prerequisite (null otherwise);
    # slice = the one-plan outcome, phrased so /plan can take it
  findings:
    - id: RB-01
      surface: S1|S2|S3|S4|S5|shared
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
      proposed_change: ""
      acceptance: ""             # a check a planner could turn into a verification step
      severity: critical|high|medium|low
      severity_rationale: ""
      confidence: CONFIRMED|HYPOTHESIS
      roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt, item_ids: [],
                         dedup_search_terms: [], dedup_hit_count: 0, note: ""}
      effort: XS|S|M|L
      depends_on: [<finding ids>]
      sequencing: {safe_to_queue_now: true|false, blocked_behind: [<finding or roadmap ids>], note: ""}
  rejected_candidates:
    - {candidate: C<n>, why_dismissed: "", compensating_control: "", control_property_match: "",
       decision_or_item_id: ""}
  summary:
    total_findings: 0
    novel_count: 0
    planned_insufficient_count: 0
    planned_unbuilt_count: 0
    top_improvements: [<finding ids>]
    highest_leverage_change: <finding id>
    maturity_S1: <value>
    maturity_S2: <value>
    maturity_S3: <value>
    maturity_S4: <value>
    maturity_S5: <value>
```

Invariants (state them to yourself and hold them):
- COUNTING INVARIANT: `findings[]` is the SOLE enumerated list of defects. `total_findings = len(findings) =
  novel_count + planned_insufficient_count + planned_unbuilt_count`. Fully covered candidates live in
  `rejected_candidates`, NOT findings. `rubric_ratings`, `question_answers`, `release_classification` and
  `pull_forward_sequencing` are systems of record referenced FROM findings, never re-counted. `top_improvements` and
  `highest_leverage_change` MUST be finding ids.
- Every candidate C1-C20 appears exactly once: as the subject of at least one finding (cite it in the finding's
  `title` or `gap` as "(C<n>)") or as one `rejected_candidates` row.
- `control_property_match` is REQUIRED whenever a compensating control is the reason for dismissal: name the property
  the control exercises, where it operates (mechanism or file:line), and why the control would FAIL if the defect were
  real.
- CONFIRMED requires the behaviour traced to file:line or an observed sampled artifact; anything less is HYPOTHESIS.
- Every `release_classification` key K01-K28 and every listed `pull_forward_sequencing` key is present.

Write `audits/release-boundary-<sha>.md`, prose, at most 1500 words, the layer the operator reads first:
1. A five-to-eight sentence synopsis: the answer to "what does a release ship, what stays here, and what do we do
   first".
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
- frontier = 0 open critical or high findings on the surface AND every Q9 `external_checklist` row relevant to the
  surface rated `met` or `partial` (never `missed`). A row is relevant to a surface if its evidence cites that
  surface; state relevance in the row's evidence.
- strong = 0 critical AND at most 1 high.
- solid = at most 1 critical.
- nascent = otherwise.
The top rating remains reachable where you argued a property-matched compensating control; this prompt's framing
does not foreclose it. Designed-unbuilt state is rated as found: a surface whose target exists only as a design is
not penalised for being unbuilt where a deferral is deliberate, but its findings count.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1 and you are on `audit/release-boundary-<sha>` created from it. This
   is a deliberate exception to the harness-assigned session branch rule (`docs/contracts/git-ops.yaml` agent branch
   prefixes): the audit needs a clean two-file diff off the audited base. The branch base MUST equal
   `meta.audited_commit`; the branch name carries any `-rN` suffix Section 5 step 1 added.
2. Pre-push gate: the YAML parses
   (`bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/release-boundary-<sha>.yaml`),
   the counting invariant and the candidate-coverage rule hold, every K01-K28 key is present, and the report is within
   its cap (`wc -w` on the `.md` <= 1500). Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL (advisory
   outside CI); if you run it and an unrelated failure appears, record it in `meta.contract_notes` before committing
   -- never fix it.
3. `git add` exactly the two deliverables, then `git commit -m "audit: release boundary review"` (append any
   attribution trailer lines your harness instructs for commits) using the session's configured identity and signing
   (do not override the author, do not disable or force signing, do not amend). If a pre-commit hook rejects the
   commit, fix the cause inside your two deliverables and commit again; never bypass the hook.
4. Run `git push -u origin HEAD` as a tool call on its own (no pipe, redirect or second command). On a network error
   retry up to 4 times with backoff. IF the push still fails, or fails for a non-network reason: do not open a PR;
   end the turn with a final message naming the branch, the commit sha, the exact error and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request` (owner `otl-labs`, repo `ontheloop`, base `main`, head your
   branch, ready for review, NOT draft), title
   `audit: release boundary between the core and this repository (S1-S5)`, body = a 2-3 sentence lede plus the
   `summary:` block in a yaml fence, plus any PR footer your harness instructs. IF the tool is unavailable or the
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
