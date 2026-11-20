# AUDIT PROMPT: Telemetry field consolidation -- overlap, liveness, declaredness and agent findability of the telemetry warehouse's declared and stored fields

Execute this prompt exactly as written. It is self-contained: do not ask clarifying questions.
Every judgment call is assigned below; where something fails, take the named degraded path and
keep going.

## 1. TASK

Audit the declared and stored field set of this repository's agent-telemetry warehouse and say
what, if anything, should be merged, retired, derived at read or declared -- and what should stay
exactly as it is. Two concerns prompted this audit. (1) The operator considers the declared field
count excessive. (2) Planning agents twice proposed a new telemetry column for a fact that was
already stored (see the operator rulings of 2026-11-20 recorded on rec-4701 and rec-5079, and the
context of rec-5440). Those incidents produced three open recommendations: rec-5440, rec-5438 and
rec-5439 (the "data estate inventory", phases 1 and 2). This audit is the umbrella over them:
reuse them, assess whether they are sufficient, and extend them where your findings warrant it.
The contracts' primary readers are LLM agents, not humans. Judge size and findability by what an
agent pays to use the field set correctly (NS8, Section 6), not by how it reads to a person.

Surfaces (Section 4): the four stored telemetry tables (telemetry_sessions,
telemetry_observations, telemetry_agents, telemetry_transcripts), their shared envelope, the
free-form `metadata` field on telemetry_observations, and the read-derived relation
telemetry_persona_findings. Questions (full text in Section 7):
- Q1: overlap among written fields
- Q2: declared-but-unwritten fields
- Q3: facts stored but undeclared
- Q4: facts an agent cannot find
- Q5: size against practice, judged for an agent reader
- Q6: cost and timing of each change
- Q7: questions not asked

Deliverables: `audits/telemetry-field-consolidation-<sha>.yaml` and
`audits/telemetry-field-consolidation-<sha>.md` (`<sha>` per Section 5). These two are the ONLY
files you create or modify in the repository tree. Regenerating gitignored local caches per
Section 5 is expected and is not a breach; never commit them. You draft; the human disposes. You
change no contract, code, Decision, roadmap item, skill or recommendation, and you file no
recommendation.

## 2. CANDIDATE OBSERVATIONS vs VERDICTS -- THE EPISTEMIC CONTRACT

This prompt hands you FACTS (Section 10) and CANDIDATE hypotheses (below), never verdicts. ASSUME
NO CANDIDATE IS A REAL DEFECT UNTIL YOU TRACE IT. A run that merely confirms the candidates below
has failed. "The field set is fine as it is" is a full-credit outcome if you argue it.

Adjudicate each candidate, and each candidate you add, to exactly one outcome:
- CONFIRMED defect with no owner -> `findings`, `roadmap_crossref.classification: novel`.
- Owned by an open recommendation, an open roadmap criterion or an approved plan, but the owner's
  remedy is insufficient or not yet built -> `findings`, classification `planned-insufficient` or
  `planned-unbuilt`. Put the extension you propose in `proposed_change`.
- Owned, and fully covered by the owner's remedy -> `rejected_candidates`, naming the owner.
- Not a defect -> `rejected_candidates`, naming the design reason or the compensating control
  (see `control_property_match`, Section 14).

Coverage rule: every id C1-C16 appears exactly once, either in one finding's `candidate_ids` or as
one `rejected_candidates[].candidate`. One finding may cover several candidates. Number any
candidates you add C17 onward; the same rule applies to them.

Candidates. Anchors were taken at compose time; re-derive them per Section 4.

- C1 A model identifier is stored in five fields:
  - observations `model`, written on model_call points and on tool_call open rows (observations.py
    `_model`);
  - observations `requested_model` (model_call);
  - agents `model` (sessions.py `_agent_row`, via `first_model`);
  - transcripts `model`, the producing message's model; its semantics say "mirroring
    telemetry_observations.model";
  - sessions `model_primary`, set in sessions.py `build_sessions` via `first_model` to the model of
    the stream's first non-error assistant record. Its description says "The primary model that
    drove the session".
- C2 observations stores `tokens_cache_creation` and also `cache_creation_1h_tokens` and
  `cache_creation_5m_tokens`. Each split column is described as "a subset of tokens_cache_creation".
  The semantics say the pair's sum is "at most" the total, and that only each column's own bound
  is write-enforced; the sum leg is owned by rec-4408.
- C3 A byte size and a SHA-256 exist in two places:
  - on the tool_call close row: `output_bytes`, `output_sha256` and `output_lines`, over the RAW
    output (observations.py `_output_cols` and `_identity`);
  - on transcript rows of the same call: `content_bytes` and `content_sha256`, over the ONE stored
    payload after scrubbing. On an oversize omission row they cover the raw source bytes; on a
    withheld_by_policy row they are the empty-string digest and 0.
- C4 On transcript rows `entity_ref` is described as equal to `external_ref`
  (telemetry_transcripts.yaml `fields.entity_ref.governance_notes_local`). Compose-time sample:
  equal on 1,352 of 1,352 rows of one tree.
- C5 observations `exit_code` is declared_unwritten (owner `T4.32:c1`) and is described as
  "Process exit code for a model_call node". Separately:
  - a `metadata` key `exit_code` is written on hook process_event rows (observations.py
    `_hook_row`);
  - the same function derives `severity` from that exit code;
  - the `metadata` entry's semantics list `exit_code` among the fields "PROMOTED to typed columns
    ... and are NOT in this bag".
- C6 The keys listed in the `metadata` entry's semantics (by STEP, PHASE, PROCESS_EVENT,
  MODEL_CALL) and the keys the producer writes (by tool_call, turn, model_call, process_event) are
  different sets. rec-4733's title names two tool_call keys; its body also names the close row's
  `_result_meta` keys and `synthetic`.
- C7 tool_call close rows carry the `metadata` keys `is_error`, `interrupted`, `status` and
  `tool_denial_kind` (observations.py `_result_meta`) beside the typed `outcome`. `outcome_of`
  computes `outcome` from: is_error, the denial kind, pre-tool hook blocks, the interrupted flag,
  and matches on the result text.
- C8 27 fields are declared_unwritten across the four tables (compose-time count). Their owners:
  `held:feedback-loop-gate` (10), `rec-4599` (4), `rec-4600` (4), `rec-4601` (1), `rec-4711` (4)
  and `T4.32:c1` (4). Two approved, unbuilt plans would produce some of them:
  PLAN-merge-annotate-producer (six sessions fields, producer `ci_annotate`) and
  PLAN-litellm-telemetry-producer (observations `attempt`, `provider` and `persona_backend`,
  producer `litellm`).
- C9 `provider` is declared_unwritten on observations and on agents, with different listed values
  (observations: copilot_cli | copilot_sdk | github_models; agents: copilot_sdk | github_models |
  anthropic_max). `rec_id` is declared_unwritten on observations and on transcripts, and
  `rec_ids` on sessions.
- C10 The SHA-256 of a tool call's input is stored as transcripts `content_sha256` on rows whose
  `purpose` is tool_input. Those rows' `observation_id` is the model_call. The only declared
  observations-to-transcripts join (`_joins.yaml` `observation_id_join_observations_to_transcripts`)
  is on `observation_id`. The link to the tool_call is a shared `external_ref` prefix:
  `<record>#<block>` on the transcript row, plus `/tool_call_open` on the observation row (refs.py
  `role_ref` and `transcript_ref`). No join declares that link.
- C11 telemetry_persona_findings declares 18 fields, all read-derived, in a table-shaped Class A
  contract (status draft) for a relation that no physical table holds.
- C12 Each of the four table contracts references the eleven envelope fields by `$ref`. Identity-key
  contracts (tenant-id, project-id, session-id, observation-id, parent-observation-id) are
  referenced the same way.
- C13 A `metadata` key `tool_use_id` is written on process_event rows (observations.py
  `_hook_row`, gate_events.py `_metadata`, hook_denials.py). `_hook_row` also sets the row's parent
  reference to that id when it names a tool use in the stream. A tool_call row's `entity_ref` is
  its toolUseId (telemetry_observations.yaml `fields.entity_ref.governance_notes_local`).
- C14 The claude_code producer writes transcripts `content_truncated` as false on every row
  (transcripts.py `_row`). Its contract calls it "A defect detector: a healthy producer writes
  false on every row".
- C15 agents `host_run_ref` embeds two run ids:
  - the GitHub Actions run id (`gha:<run_id>/...`), which `workflow_run_id` also stores;
  - a Lambda request id (`lambda:<request id>`), which the declared_unwritten `lambda_request_id`
    would store.

  The `host_run_ref` semantics say "the per-host typed columns (workflow_run_id,
  lambda_request_id) stay as typed join keys".
- C16 The producer serialises `metadata` with null-valued keys kept (`canonical_json` in
  transcripts.py; `_meta` in observations.py). Compose-time sample of one tree: the nine
  `_result_meta` keys are present on all 643 tool_call close rows, and `agent_id` held a value on
  9 of them.

## 3. READ FIRST -- DISAMBIGUATION TRAPS

- "174 fields" counts contract entries, not stored columns and not distinct facts. Envelope and
  identity keys recur on every table through `$ref`; read-derived entries are declared but never
  stored. The raw count is not a finding.
- Declared is not written. Each table contract's `realization` block separates `produced`
  (by producer key, or `write_boundary`) from `unproduced` (by owner).
- An entry with `derivation.timing: read` is computed by a reader verb. It has no physical column,
  so "drop the column" does not apply to it.
- telemetry_persona_findings is a contract for a relation computed at read. No physical table
  holds it.
- Overlap by name is not overlap by meaning. `model`, `outcome`, `severity`, `origin`, `provider`
  and `duration_seconds` appear on several contracts, for different entities or with different
  value lists. Conversely, one fact can sit under a generic column qualified by a discriminator
  value (`purpose`, `observation_type`, `event_kind`).
- `external_ref` and `entity_ref` are both record references. The envelope's `external_ref`
  description says "Not a caller-facing FK -- entity_ref is the field a row uses to participate
  as (or reference) an entity/FK key".
- `output_sha256` (observations) covers the RAW output bytes. `content_sha256` (transcripts)
  covers the ONE stored payload after scrubbing, except on omission rows (Section 2, C3). They have
  the same shape and different inputs.
- The name `input_sha256` occurs in src/persona_backends/events.py with an unrelated meaning.
  rec-5440 also uses it for a proposed column that was never built.
- `exit_code` has two meanings. The declared column is a model_call process exit code; the
  `metadata` key is a hook's exit code on a process_event row.
- A `metadata` key being present is not the same as it holding a value. The producer writes
  null-valued keys, so count NON-NULL values per key, never key presence alone.
- "Retire" has two mechanisms in this repository's own text:
  - rec-4599: drop the column, which needs a contract_version bump plus DDL;
  - rec-4711: "columns are never dropped; a retired column is declared unproduced forever".

  The operator states that rec-4711's sentence was an optimistic assumption, not a rule, and has
  no preferred mechanism. Weigh both per field (DD-C).
- `held:feedback-loop-gate` is an owner label checked by a regex. It is not a Decision and confers
  no immunity: every declared field is adjudicated.
- The reader's Class B contract (docs/contracts/ducklake_telemetry_reader.yaml) and the reader
  SQL are a second place a `metadata` key can be named. They are context, not audit surfaces.
- The ops_* tables (ops_recommendations, ops_decisions, ops_priority_queue and others) are OUT of
  scope.
- Line numbers drift. Every anchor below names a file and a YAML key path or function. Any line
  number is a compose-time hint only.

## 4. SCOPE

| Surface name (use these strings) | Source of truth | State |
|---|---|---|
| telemetry_sessions | docs/contracts/telemetry_sessions.yaml | built, in production |
| telemetry_observations | docs/contracts/telemetry_observations.yaml (all fields except `metadata`) | built, in production |
| observations_metadata | telemetry_observations.yaml `fields.metadata`, plus every key a producer writes into it | built, in production |
| telemetry_agents | docs/contracts/telemetry_agents.yaml | built, in production |
| telemetry_transcripts | docs/contracts/telemetry_transcripts.yaml | built, in production |
| envelope | docs/contracts/telemetry-event-envelope.yaml `contract.fields` | built |
| persona_findings | docs/contracts/telemetry_persona_findings.yaml | read-derived, status draft |

A finding whose subject spans surfaces uses `surface: shared`.

Context only (read; do not audit):
- the producers: src/turn_capture/, plus any other producer registered in
  config/telemetry/parser_versions.yaml at the audited commit;
- the reader: src/common/ducklake_event_read_verbs.py, ducklake_event_read_sql.py and
  ducklake_event_reads.py;
- docs/contracts/_joins.yaml, docs/contracts/telemetry-lexicon.yaml,
  docs/contracts/data-modeling-standard.yaml and docs/contracts/ducklake_telemetry_reader.yaml;
- scripts/checks/contracts/validate_telemetry_field_realization.py;
- config/structural_size_budgets.yaml;
- AGENTS.md section "Agent-First Repository".

Out of scope: the ops_* tables; DuckLake catalog internals; writer and reader deployment;
transcript text itself.

Vocabulary:
- A FACT is one meaning a reader could ask for (for example, "the model that answered this
  call").
- A FIELD is one entry under a contract's `fields:` key.

Field kinds: apply these in order to every field entry; the first match wins.
1. `read_derived`: the entry has `derivation.timing: read`.
2. `bookkeeping`: the entry is listed under `realization.produced.write_boundary`, OR its `$ref`
   targets telemetry-event-envelope.yaml or an identity-key contract (tenant-id.yaml,
   project-id.yaml, session-id.yaml, observation-id.yaml, parent-observation-id.yaml).
3. `declared_unwritten`: the entry is listed under `realization.unproduced`.
4. `stored_written`: every other entry.

There is also a fifth kind, `hidden_key`: every key a producer writes into `metadata`, whether
found in code or in rows. No metadata key has a contract field of its own. Whether the `metadata`
entry's semantics text or another contract names a key goes in that row's `evidence`, not in its
kind. Note that agents `agent_type` takes its `$ref` from source-lineage.yaml (a registry key), so
it is stored_written under this rule.

Compose-time snapshot at commit ea07baa7 (bookkeeping / stored_written / declared_unwritten /
read_derived):

| Contract | Counts |
|---|---|
| sessions | 15 / 10 / 12 / 7 |
| observations | 16 / 24 / 8 / 1 |
| agents | 16 / 10 / 5 / 5 |
| transcripts | 17 / 8 / 2 / 0 |
| persona_findings | 0 / 0 / 0 / 18 |
| total | 64 / 52 / 27 / 31 = 174 |

That is 174 entries, 116 distinct names, and 25 names on more than one contract.

Trust nothing quoted here, and expect drift:
- Main moves while this prompt waits, and other agents keep building telemetry work. Obtain every
  file, line, count and size by reading the file or the rows at the audited commit.
- Your own recount is authoritative. Put it in `meta.field_counts`. Record every anchor or count
  in this prompt that does not resolve or does not match in `meta.stale_anchors`, giving the
  prompt's value and the value you found.
- Work that may have landed by the time you run (adjudicate what you find, not what this prompt
  expected):
  - PLAN-merge-annotate-producer (producer `ci_annotate`; six sessions fields leave
    `held:feedback-loop-gate`);
  - PLAN-litellm-telemetry-producer (producer `litellm`; observations `attempt`, `provider` and
    `persona_backend` leave `unproduced`; MODEL_CALL `metadata` gains the keys `loss` and `error`);
  - status changes to any recommendation cited here.

  Record the producer keys registered at the audited commit in `meta.producers_registered`.

## 5. SETUP

Run these first. NEVER abort on a failure -- take the named degraded path.

0. Hook canary (repo rule, AGENTS.md). If your opening context has no `[hook_canary]` line from
   this repo's hooks: say "repo hooks not loaded" as your first status, write nothing, and end the
   turn; the operator resolves it. This is the one sanctioned stop in this prompt.
1. Derive the base:
   - Run `git fetch origin main`, then `git rev-parse --short origin/main`. This sha IS the
     audited tree; use it in both deliverable filenames, the branch name and
     `meta.audited_commit`.
   - Now run `git switch -c audit/telemetry-field-consolidation-<sha> origin/main`, so that
     everything you read is the audited tree.
   - IF that branch already exists, locally (`git branch --list`) or on the remote
     (`git ls-remote --heads origin <name>`), append `-r2` (then `-r3`, ...) to the branch name
     only. The filenames keep `<sha>`.
   - IF the fetch fails: use `git rev-parse --short HEAD`, branch off `HEAD` instead, and record
     "base is local HEAD" in `meta.contract_notes`.
2. Run `bin/venv-python -m scripts.session.preflight --roadmap-detail full`. It writes
   `logs/.preflight-report.json` (gitignored; expected). Recommendations have NO local cache in
   this repository. IF preflight fails: record the error class in `meta.contract_notes` and
   continue.
3. Recommendation reads. Run these whatever step 2 did:
   - Search: `bin/venv-python -m scripts.recs_related --terms "<keywords>" --table --limit 25`.
   - Bodies, by id:

     ```
     bin/venv-python - <<'EOF'
     from src.common.ducklake_reader_client import fetch_recs_by_ids, make_reader
     rows, missing = fetch_recs_by_ids(make_reader(), ["rec-4733", "rec-5440"])
     for r in rows:
         print(r["id"], r["status"], r["title"]); print(r["context"]); print(r["acceptance"])
     print("missing:", missing)
     EOF
     ```

     A `mcp__ducklake-reads__recs_by_ids` tool, if your session has one, is equivalent.
   - IF either path fails:
     - set `meta.degraded_dedup = true`;
     - set every `roadmap_crossref.confidence` to HYPOTHESIS and `dedup_hit_count` to null;
     - dedup against `docs/DECISIONS.md` and `docs/ROADMAP-PLATFORM.yaml` only;
     - use the rec summaries in Section 13 as the only rec content.
4. Roadmap parse check:
   `bin/venv-python -c "import yaml; d=yaml.safe_load(open('docs/ROADMAP-PLATFORM.yaml')); print(len(d['tier_items']))"`.
   IF it errors, record that in `meta.contract_notes` and read the YAML as text.
5. Telemetry reader: the first call of the empirical pass (Section 11) is your reachability probe.
   IF it raises:
   - print only the exception class name (an AWS error message can contain an account id);
   - set `meta.degraded_empirical = true` and tag every finding `evidence_kind: static`;
   - open the `.md` report with that fact;
   - any finding that needs stored rows to be CONFIRMED stays HYPOTHESIS.
6. External lookups (Q5 only): you MAY use web search or fetch, at most 8 lookups in total (each
   search and each page fetch counts as one) -- do NOT exceed. Use them to confirm current
   practice: the OpenTelemetry semantic-convention authoring guidance and GenAI conventions, the
   Open Data Contract Standard property fields, and Langfuse's observation data model.
   - Record each URL in the `evidence` field that uses it.
   - Label anything answered from memory "recalled, unverified" and mark it HYPOTHESIS.
   - IF web access is unavailable: set `meta.web_unavailable = true` and proceed from Section 10's
     compose-time quotes plus your own knowledge, labelled as above.
7. Subagent capability for DD-B (Section 9): check whether your harness offers a tool that starts
   a fresh subagent with no shared context. IF it does not: set `meta.degraded_probe = true` and
   take DD-B's degraded path.

Repo-wide validate is advisory outside CI. Running it is optional and never a gate (Section 16).

## 6. NORTH STAR

These are the bars you judge each surface against. NS4, NS6, NS7 and NS8 are judgment-bearing,
not absolute: argue them; do not pattern-match them.

- NS1 One fact, one home. A fact is stored once, or its copies are declared as copies with a
  reason.
- NS2 Derive, do not store. A value computable from other stored rows is computed at read
  (Decision 199; data-modeling-standard.yaml rule `derived-state`).
- NS3 Stored means declared. Every stored fact has a contract entry the writer can enforce
  (Decision 210).
- NS4 Findable by meaning. An agent asking for a fact in plain words locates where it lives and
  reads it correctly.
- NS5 Declared means live or owned. An unwritten field has a live owner and a reason to stay.
- NS6 New facts get typed columns, not new `metadata` keys. The observations `metadata` entry
  names promoted fields that "are NOT in this bag".
- NS7 Consolidation pays for itself. Removing or merging a production column has a real cost
  (Section 10, G8). Recommend a change only when its benefit exceeds that cost.
- NS8 The reader is an agent. Judge a field set by what an LLM agent pays to use it correctly:
  - the files and bytes it must read to locate a fact;
  - the tool calls it spends;
  - the rate of wrong or missed answers, including proposing a column for a fact already stored.

  Human navigability and a "too many columns" feel are not costs in themselves. Industry practice
  (most of it written for human readers) is evidence of a cost only where its rationale transfers
  to an agent reader; say which practices transfer and which do not. A large declared set that
  agents parse cheaply and correctly is not excessive. A small one they misread is. See AGENTS.md
  "Agent-First Repository" and config/structural_size_budgets.yaml's contracts class
  `actual_purpose`.

## 7. THE QUESTIONS

- Q1 Overlap among written fields. For every `stored_written` field: is it independently
  necessary, a copy of another stored field, or computable from other stored fields (apply the
  Section 11 counterfactual)? Verdict: lean | some_redundancy | substantial_redundancy.
- Q2 Unwritten fields. For every `declared_unwritten` field: should it be produced, kept reserved
  under a live owner, or retired -- and by which mechanism (DD-C)? No field is exempt. Verdict:
  justified | partly_justified | unjustified.
- Q3 Undeclared facts. Which keys does each registered producer write into `metadata` (DD-A)?
  Which carry a fact no contract field declares; which duplicate a declared column or a typed
  column's inputs; which does any code read? `metadata` is the only JSON-carrying column
  (scripts/telemetry_verify/compare.py `JSON_COLUMNS`); confirm that, and name any other
  free-form text column that carries structured facts. Verdict: declared | partly_declared |
  undeclared.
- Q4 Findability. Which stored facts would an agent fail to find, or find wrongly, from the
  contracts alone? Consider:
  - a generic column qualified by a discriminator value;
  - a link not declared in `_joins.yaml`;
  - like facts placed on different tables;
  - a fact held only in `metadata` or only in transcript content.

  Measure this with DD-B, and assess rec-5440, rec-5438 and rec-5439 for sufficiency. Verdict:
  findable | partly_findable | not_findable.
- Q5 Size against practice. Is this field set lean, proportionate or excessive for agent and
  LLM-call telemetry read by agents? Assess the EXTERNAL CHECKLIST property by property, rating
  each met | partial | missed with evidence, AND judging for each whether its rationale transfers
  to an agent reader (yes | partly | no):
  - E1 One enforced specification for every field (schema-registry practice).
  - E2 An explicit reuse-before-create rule for new fields. OpenTelemetry "How to write semantic
    conventions", section "Defining attributes": "Reuse existing attributes when possible."
  - E3 Each field carries a plain meaning, synonyms or examples. Open Data Contract Standard
    property fields: description, businessName, synonyms, examples.
  - E4 Every supported join and derivation path is declared (column-level lineage practice).
  - E5 The keys of free-form attribute bags are declared in the specification (OpenTelemetry
    attribute-registry practice).
  - E6 Shared bookkeeping is specified once and referenced, not re-specified per table.
  - E7 A value is not stored beside the inputs it is computed from, unless it is declared as a
    deliberate copy.
  - E8 Unused declared fields are pruned or marked deprecated (ODCS property field `deprecated`).

  You may also compare coverage with the OpenTelemetry GenAI conventions and Langfuse; label
  anything recalled. Verdict: lean | proportionate | excessive.
- Q6 Cost and timing. For each merge, retire, derive_at_read and declare verdict, cover four
  things:
  - what the change costs here (Section 10, G8 and G9);
  - which stored history it affects;
  - whether it has ever held a value (DD-C);
  - whether to do it alone now, batch it with the next column change on that table, or leave it.

  Set `timing` on each such field_verdicts row. The Q6 verdict is the timing you recommend for
  the highest-leverage change set as a whole: consolidate_now | batch_with_next_column_change |
  leave.
- Q7 Questions the requester did not think to ask. Answer AND extend these seeds:
  - (a) Is the four-table split itself a cause of cross-table copies?
  - (b) Should a read-derived relation carry a table-shaped contract?
  - (c) Do rec-4701, rec-4704, rec-5079 and rec-5250 ask for facts already stored or derivable?
    rec-4701 and rec-5079 already carry 2026-11-20 rulings of "no new column"; test those rulings'
    premises. Note that sessions `workflow` already records the first workflow-skill name
    (sessions.py `_workflow`, `_skill_workflow`).
  - (d) What should one line of a "fact index" contain, keyed by fact rather than column? Compare
    rec-5438's planned row ("one row per table.column and discriminator value").
  - (e) Does writing null-valued `metadata` keys cost an agent reader or a query anything?
  - (f) Would rec-5440, rec-5438 and rec-5439 together, as written, have prevented both incidents?
    What is missing?
  - (g) Which practices in Q5 or Section 6 do NOT transfer to an agent reader, and what
    agent-specific practice, if any, replaces them?

## 8. RUBRIC

Rate each Section 4 surface on each dimension: strong | adequate | weak | absent | n/a. n/a is
correct and costless where a dimension does not structurally apply (for example VD5 on
persona_findings). Never manufacture a rating or a finding to fill a cell.

- VD1 Necessity -- each stored field is needed by a reader, a rule or an identity (Q1, Q2).
- VD2 Non-redundancy -- no fact is stored twice or beside its own inputs without a declared reason
  (Q1, Q6).
- VD3 Declaredness -- every stored fact, including `metadata` keys, has a contract entry (Q3).
- VD4 Agent findability -- an agent locates and correctly reads a fact by its meaning at low cost,
  as measured by DD-B (Q4, Q5).
- VD5 Liveness -- declared fields are written, or owned by a live owner with a reason (Q2).
- VD6 Consistency -- one concept has one name, type, value list and meaning across tables (Q1,
  Q5).

## 9. DEEP-DIVES

DD-A Metadata key census (feeds Q1, Q3, C5-C7, C13, C16).
1. From code: list every `metadata` key each registered producer writes, by (observation_type,
   event_kind). For claude_code, cover observations.py `_tool_rows`, `_result_meta`,
   `_turn_open_row`, `_turn_close_rows`, `_turn_rows` and `_hook_row`, plus gate_events.py and
   hook_denials.py. For each key, give the function that writes it.
2. From rows (Section 11 sample): per (observation_type, event_kind, parser_version) and key,
   count rows where the key is present and rows where it is non-null.
3. Per key, establish:
   - the fact it carries;
   - whether a declared column carries the same fact or is computed from it (Section 11
     counterfactual);
   - whether any code reads it. Search `json_extract` and `metadata` reads under src/ and scripts/;
     at compose time only `attribution_mcp_server` was found read;
   - whether the `metadata` entry's semantics, the reader contract or an open rec names it.
4. Each key gets one field_verdicts row (kind `hidden_key`).

DD-B Agent findability probe (feeds Q4, Q5, Q7, VD4; operationalises NS8).
- Dispatch exactly ONE fresh subagent with no shared context. Give it repository read access only.
  Forbid it: the telemetry warehouse, the web, this prompt file, the `audits/` directory, and
  `logs/`.
- Give it the eight questions below verbatim, in plain words. You may replace at most two with
  questions your own findings make more decisive, keeping them in plain words with no table,
  column or key names. Never pass it the answer key.
- Ask it, for each question, where the fact is stored: table and column; or table, `metadata` key
  and discriminator value; or "derived at read by <verb>"; or "not stored". Also ask for the
  searches it ran, the files it opened, approximate bytes read, and its total tool calls. Cap its
  report at about 1,200 words.
- Grade each answer against your own field table as correct | partly | wrong | not_found.
- Record everything in `findability_probe`.
- Bounds: one subagent, at most eight questions, no re-dispatch.
- Degraded path (`meta.degraded_probe = true`): for each question, estimate statically the
  minimal read path an agent would take with grep-first search (files, bytes, likely wrong turns);
  mark the VD4 ratings and dependent findings HYPOTHESIS.

The eight questions:
1. "Where is the SHA-256 of the exact input a tool call was given?"
2. "Where is the name of the skill a Skill tool call invoked?"
3. "Where is the exit code of a hook run?"
4. "Where are the model that actually served an LLM call and the model that was requested?"
5. "How would you tell that a tool returned byte-identical output twice in one session?"
6. "Where is the GitHub Actions run id of an agent run hosted in CI?"
7. "Where are an LLM call's cache-write tokens split by cache lifetime?"
8. "Where is the hash of the persona definition a verdict-persona run loaded?"

Compose-time answer key, for grading only. Verify each answer before grading; never give it to
the subagent.
1. transcripts `content_sha256` on the `purpose` tool_input row, linked to the tool_call by the
   `external_ref` prefix (C10).
2. Only inside the tool_input transcript content, which production does not serve. sessions
   `workflow` holds the first workflow-skill name of a session.
3. observations `metadata.exit_code` on hook process_event rows. The `exit_code` column is
   unwritten.
4. observations `model` and `requested_model` on model_call rows.
5. Equal observations `output_sha256` on tool_call close rows in one session.
6. agents `workflow_run_id`, also embedded in `host_run_ref`.
7. observations `cache_creation_1h_tokens` and `cache_creation_5m_tokens`.
8. agents `definition_sha256`.

DD-C Retire and merge evidence (feeds Q2, Q6). For each declared_unwritten field and each field
you consider for merge or retire, determine `ever_non_null` from:
- (i) its `populated_by` and `amendment_log`;
- (ii) parser_versions.yaml history reasons that name it;
- (iii) `git log -S '<field name>' --oneline -- src/ scripts/telemetry_capture` (at most one such
  search per field) for any producer that ever wrote it;
- (iv) the realization check, which refuses an unproduced field that is non-null in any golden
  row (rule R5);
- (v) sampled rows across parser versions.

Set `ever_non_null: no` only when (i)-(iv) are all negative and (v) found no value. Set `yes`
when any source shows a value. Otherwise set `unknown`. Sampled absence alone is `unknown`.

Then weigh the operator's stated factors for `retire_mode`:
- a column that never held a value and is redundant or unneeded has little reason to survive
  (`drop_column`);
- a column that held values whose meaning is now superseded may keep its history
  (`unproduced_forever`);
- a hidden key a producer should stop writing is `stop_writing`.

Cost each choice (Q6).

## 10. GROUNDING MAP

This map spends your cognition on judgment, not grep. Verify each item before relying on it; the
facts are neutral and carry no verdict. Compose-time snapshot: commit ea07baa7, 2026-11-20.

G1 Contracts (docs/contracts/). Line numbers are hints.

- telemetry_sessions.yaml (`fields:` ~29, `realization:` ~473):
  - `model_primary` "populated_by: the claude_code producer at the open event";
  - eight fields carry `populated_by: "unproduced: held:feedback-loop-gate (no producer writes
    it)"`.
- telemetry_observations.yaml (`fields:` ~30, `realization:` ~473):
  - `fields.metadata` (~459): semantics list keys by STEP, PHASE, PROCESS_EVENT and MODEL_CALL,
    and say the promoted fields are NOT in the bag;
  - `fields.exit_code`, `fields.provider`: description lists copilot_cli | copilot_sdk |
    github_models;
  - `fields.requested_model`: "model stays the id that served the call";
  - `fields.cache_creation_1h_tokens` and `fields.cache_creation_5m_tokens`;
  - `fields.output_sha256`: "identifies unscrubbed bytes";
  - `fields.cost_usd`: read-derived from the four token classes;
  - `fields.cost_usd_reported`: declared_unwritten.
- telemetry_agents.yaml (`fields:` ~34, `realization:` ~410):
  - `fields.provider`: copilot_sdk | github_models | anthropic_max;
  - `fields.workflow_run_id`;
  - `fields.lambda_request_id`;
  - `fields.host_run_ref` (prefix grammar; "the per-host typed columns ... stay as typed join
    keys");
  - `fields.definition_sha256`;
  - a `run_shapes` block follows `realization`.
- telemetry_transcripts.yaml (`fields:` ~32, `realization:` ~337):
  - `fields.entity_ref.governance_notes_local` ("Equals external_ref for a transcript row");
  - `fields.observation_id.governance_notes_local` names each purpose's node: the turn for
    prompt and system, the model_call for thinking, response and tool_input, the tool_call for
    tool_result;
  - `fields.purpose`, `fields.content_omitted_reason`, `fields.content_sha256`,
    `fields.content_truncated`.
- telemetry_persona_findings.yaml: header comment lines 1-3 say "no physical table holds it"; it
  has no `realization` block.
- telemetry-event-envelope.yaml:
  - `contract.fields` holds 11 entries;
  - the `external_ref` description holds "Not a caller-facing FK";
  - R7 states a replayable producer's bump obligation: re-parse the whole root tree from the
    start.
- _joins.yaml: the joins between telemetry tables key on session_id, parent_session_id,
  observation_id and parent_observation_id, plus tenant and project joins. No join keys on
  `external_ref`.
- data-modeling-standard.yaml:
  - rule `derived-state`: materializing a derived field is owned by T2.52 c1;
  - `design_time_walk` has nine steps, and none asks whether the fact is already stored.
- telemetry-lexicon.yaml (Class D): `lexicon.terms` holds canonical terms. rec-5438 plans concept
  search words there.

G2 Write-time control. scripts/checks/contracts/validate_telemetry_field_realization.py, rules
R1-R8 (module docstring):
- every stored column is credited to a producer, `write_boundary` or exactly one unproduced owner;
- R4: a produced field is non-null in the producer's golden rows;
- R5: no unproduced field is non-null in golden rows;
- R7: a criterion owner must be an open roadmap criterion. A rec owner's liveness is not checked;
  rec-4606 owns that.

Its scope is the four tables' stored columns. A compose-time search of scripts/checks/ found no
check that validates `metadata` keys.

G3 Producer (src/turn_capture/).
- observations.py: `OBSERVATION_COLUMNS`, `_row`, `_meta` (`canonical_json` of keyword arguments,
  nulls kept), `_model`, `outcome_of`, `_result_meta`, `_identity`, `_output_cols`, `_tool_rows`,
  `_turn_open_row`, `_turn_close_rows`, `_turn_rows`, `_hook_row` (sets `severity` from the exit
  code: 2 -> error, 0 or none -> info, otherwise warning).
- gate_events.py `_metadata`; hook_denials.py (process_event metadata).
- sessions.py: `first_model`, `_workflow`, `_skill_workflow`, `build_sessions`, `_agent_row`.
- host_run.py `top_level_agent_rows`.
- transcripts.py: the module docstring ("Content is scrubbed BEFORE content_sha256"),
  `canonical_json`, and `_row` (withheld branch, oversize branch, digest of the stored payload).
- refs.py: `role_ref`, `transcript_ref`, `turn_entity_ref`.

`metadata` keys found in the claude_code code at compose time:

| Row | Keys |
|---|---|
| tool_call open | caller_type, attribution_mcp_server |
| tool_call close | tool_denial_kind, permission_decision_source, is_error, interrupted, persisted_output, persisted_output_size, agent_id, is_async, status |
| synthetic tool_call close | synthetic |
| turn open | is_meta, prompt_source, turn_origin |
| turn close | response_count, tool_call_count |
| model_call | request_id, stop_reason, block_count |
| hook process_event | hook_event, hook_name, attachment_type, exit_code, duration_ms, tool_use_id, command, stderr_head |
| gate process_event | marker_version, tool_use_id |
| hook-denial process_event | hook_event, marker_version, tool_use_id |

config/telemetry/parser_versions.yaml: `producers.claude_code.current` is 11, and each history
entry gives a reason.

G4 Reader (src/common/).
- ducklake_event_read_verbs.py: `EVENT_READS` holds 17 verbs; `MAX_WINDOW_DAYS = 7` and
  `MAX_TREE_ROWS = 2000`.
- `session_tree_rows` and `telemetry_root_sessions` are verification-only (`agent_facing=False`).
- `session_tree_rows` returns, per row, every compared column plus `created_timestamp`.
- ducklake_event_reads.py `compared_columns`: every stored column except `created_timestamp`,
  `producer_version` and the representation-only columns (`content`, `content_uri`). So
  `producer_version` and content are not in tree rows.
- Compose-time search: the only `metadata` key read by repository code is
  `attribution_mcp_server` (ducklake_event_read_sql.py, `json_extract_string`;
  scripts/telemetry_verify/derive_usage.py).

G5 Two incidents (rec bodies as read 2026-11-20). rec-4701 and rec-5079 each carry an operator
ruling dated 2026-11-20 of "no new column", because the fact is already stored:
- the skill name in the tool_input transcript content;
- the input digest as tool_input `content_sha256`, joined by the `external_ref` prefix. The rec
  records 5,945 of 5,945 tool calls matched across 20 root sessions of 2026-11-19.

rec-5440 states the root causes: the design walk never asks whether a fact exists, and the
contracts point the tool_input join at the model_call.

G6 Compose-time empirical seeds (one tree, a root session of 2026-11-19, counts only):
- 2,111 observation rows and 1,352 transcript rows, all at parser_version 11;
- 643 tool_call open rows, each matching exactly one tool_input transcript row by `external_ref`
  minus the `/tool_call_open` suffix;
- 643 tool_input rows, each with an `observation_id` that resolves to a model_call;
- 28 distinct `metadata` keys present.

Key-present versus non-null examples:

| Key | Present | Non-null |
|---|---|---|
| agent_id | 643 | 9 |
| attribution_mcp_server | 643 | 36 |
| exit_code | 145 | 85 |
| interrupted | 643 | 142 |
| persisted_output | 643 | 2 |

The window 2026-10-20..2026-10-26 returned 12 root sessions; its oldest tree was parser_version
7. Production rows therefore span parser versions 7-11.

G7 Size facts. In-scope contract bytes:

| Contract | Bytes |
|---|---|
| sessions | 67,824 |
| observations | 56,369 |
| envelope | 53,867 |
| agents | 48,895 |
| transcripts | 33,549 |
| persona_findings | 16,025 |

That is about 276 KB in total. In config/structural_size_budgets.yaml, the contracts class has
`limit: 500` effective lines and `max_bytes: 49152`, and its `actual_purpose` reads "the densest
agent-consumed field semantics in the repository". `byte_budgets` grandfathers the envelope,
observations and sessions at exactly the sizes above; per that file's comment, the roster
"Ratchets DOWN only", and a raise needs a Decision (Decision 275). So the envelope, observations
and sessions have zero byte headroom, and agents has 257 bytes below `max_bytes`. Adding a
declaration costs bytes; removing one frees them.

G8 Cost chain of a column change:
- Wording of a Class A semantic change is approved by the operator on GitHub (workflow
  `.github/workflows/approved-text-approval.yml`, Decision 246).
- A producer change is a `parser_version` bump with a golden corpus (parser_versions.yaml;
  envelope R7). Re-parse reaches only trees the producer can still read; stored rows are never
  updated (Decision 199).
- A production column change is an operator admin step (`provision_telemetry_catalog`, Decision
  230 cl.4; Decision 216 cl.3). While it is pending, docs/contracts/deploy-paths.yaml's TELEMETRY
  PRODUCTION COLUMN HOLD makes the deploy skip the production telemetry writer and reader.
- On the smoke catalog a nullable column add is a CI step (Decision 222 cl.1).
- Decision 216's telemetry-schema-churn reversal condition fired on 2026-11-16; rec-5214 owns the
  cl.3 re-decision.
- Per rec-4599, there is no removal change class: dropping a column needs a contract_version bump
  plus DDL plus a migration Decision. Dropping loses the stored values.

G9 Governing Decisions (headers in docs/DECISIONS.md):

| Decision | Subject |
|---|---|
| 95 | four-table model |
| 199 | event journal, derived at read |
| 207 | grain at write |
| 210 | rules enforced at write |
| 216 | one owner per metadata schema |
| 222 | smoke column add |
| 230 | production telemetry catalog |
| 236 | production serves no transcript content |
| 239 | masked content reader |
| 246 | wording approved on GitHub |
| 275 | contract byte budget |

G10 External sources (verified at compose time; re-verify per Section 5 step 6):
- opentelemetry.io/docs/specs/semconv/how-to-write-conventions/ ("Reuse existing attributes when
  possible.");
- opentelemetry.io/docs/specs/semconv/general/naming/ ("Two attributes, two metrics, or two
  events MUST NOT share the same name.");
- bitol-io.github.io/open-data-contract-standard/latest/schema/ (property fields description,
  businessName, synonyms, examples, deprecated, authoritativeDefinitions).

## 11. EMPIRICAL PASS

Read stored rows only through the reader's named verbs; there is no caller SQL. This entry point
ran at compose time:

```
bin/venv-python - <<'EOF'
from scripts.telemetry_capture.read_route import make_routed_reader
from scripts.telemetry_verify.rows import read_tree, decode_rows
r = make_routed_reader()
roots = r.named("telemetry_root_sessions", day_from="2026-11-14", day_to="2026-11-20")
rows = decode_rows(read_tree(r.named, roots[0]["session_id"]))   # {table_name: [row dicts]}
print(len(roots), {t: len(v) for t, v in rows.items()})
EOF
```

Bounds -- do NOT exceed:
- at most 3 calls to `telemetry_root_sessions`, each window at most 7 UTC days;
- at most 12 trees read in total.

Choose three windows: the oldest that returns rows (2026-10-20..2026-10-26 did at compose time),
a middle one, and the latest. Take trees from at least 3 distinct days and at least 3 parser
versions. Include a top-level CI run tree (a root whose `host_run_ref` is non-null) if any window
returns one, and a `litellm` or `ci_annotate` tree if those producers are registered.

Privacy -- you may print only:
- counts and null rates;
- column names and `metadata` key names;
- values of declared enums (observation_type, event_kind, purpose, origin, outcome, severity,
  output_capture, reasoning_visibility, usage_source, content_omitted_reason, trigger,
  billing_shape, producer, parser_version);
- model ids;
- values of these `metadata` keys only: caller_type, status, tool_denial_kind,
  permission_decision_source, stop_reason, hook_event, attachment_type, marker_version, is_error,
  interrupted, is_async, is_meta, prompt_source, turn_origin.

Never print ids, refs, hashes, branch names, commands, stderr, error text or any other free text.
Do NOT call the content reader (scripts.telemetry_content or any transcript-content verb); no
question here needs transcript text.

Counterfactual test, applied to every overlap, copy and derivability candidate as an operation:
"If field A were deleted, could every sampled row's A be recomputed exactly from the fields I
claim it duplicates or derives from?" Count:
- rows where it could;
- rows where it could not;
- rows where either side is null.

A candidate with any could-not rows is not a copy. Say what the difference carries (for example
scrubbing, a persisted sidecar, an API-error record, a later model switch).

Tag each finding `evidence_kind: observed` (counted on sampled rows) or `static` (contract or code
only). At equal severity, an observed finding outranks a static one.

## 12. METHOD

- P1 Read the six in-scope contracts' field entries and realization blocks. Build the field table
  with each field's kind (Section 4 rule) and fill `meta.field_counts`.
- P2 Trace each stored_written field to the producer code that sets it, and each read_derived
  field to its verb.
- P3 DD-A step 1: `metadata` keys from code.
- P4 Empirical pass (Section 11) within its bounds, including DD-A step 2. Apply the
  counterfactual to every overlap candidate.
- P5 DD-B findability probe.
- P6 DD-C evidence for every declared_unwritten field and every merge or retire candidate.
- P7 Adjudicate C1-C16 and any candidate you add. Rate the rubric.
- P8 Dedup every finding (Section 13).
- P9 Fill `field_verdicts`, answer Q1-Q7, and write the report. Compute maturity LAST.

## 13. DEDUP DISCIPLINE

Before filing any finding, search these ownership surfaces and record the search terms and the
hit count on the finding:
- `rg` over `^## Decision` headers in `docs/DECISIONS.md`;
- a `yaml.safe_load` projection of `tier_items` and `candidate_decisions` in
  `docs/ROADMAP-PLATFORM.yaml` (T3.20, T3.22, T3.3, T3.4, T4.32 and T2.52 own nearby work);
- `scripts.recs_related --terms`;
- the approved plans in `docs/plans/` named in Section 4.

A hit means a sufficiency assessment (planned-insufficient or planned-unbuilt) or a
`rejected_candidates` entry, never a fresh discovery. A finding with no recorded negative search
is HYPOTHESIS.

Owners already known (summaries as read 2026-11-20; re-read the bodies per Section 5 step 3):

| Owner | Scope |
|---|---|
| rec-4599 | produce or retire sessions steps_total, failure_phase, coverage_before, coverage_after |
| rec-4600 | produce or retire observations cost_usd_reported, attempt, acceptance_passed, time_lost_seconds |
| rec-4601 | produce or retire transcripts token_count |
| rec-4711 | produce or retire agents lambda_request_id, findings_count, recs_created, queue_entries_written |
| rec-4606 | check that unproduced owners are live; record the feedback-loop hold in the repo |
| rec-4733 | declare tool_call `metadata` keys (open keys, `_result_meta` keys, synthetic) |
| rec-4408 | write-enforce the cache-split sum and model_call-only columns |
| rec-5079 | key friction repeats on the stored tool-input digest, joined at read |
| rec-5250 | count exact re-fetches from `output_sha256` |
| rec-4701 | skill name on Skill tool_call rows (ruled: no new column now) |
| rec-4704 | names and sizes of loaded context |
| rec-5270 | the ci_annotate producer, built by PLAN-merge-annotate-producer |
| rec-5396 | the litellm producer, built by PLAN-litellm-telemetry-producer |
| rec-5214 | re-decide Decision 216 cl.3 |

Data estate inventory -- this audit may reuse and extend these:

| Owner | Scope |
|---|---|
| rec-5440 | phase 1: an existence-first step in the design walk; settle the tool_call-to-tool_input link |
| rec-5438 | phase 2: a generated fact index, a local lookup command, concept search words in the lexicon |
| rec-5439 | each reader verb's output columns listed in describe |

Decision-ratified design choices. These are NOT immune, but challenge one only as a Decision
revisit, never as a plain defect. Such a finding uses `change_type: rescope`, names the Decision
and its reversal condition, and argues why the condition holds. Each may also be weighed as a
cost in Q5 and Q7.
- Read-derived fields are declared but not stored (Decision 199; rule `derived-state`).
- Envelope fields recur on every table by reference (Decision 199).
- Tables are append-only; nothing is updated in place (Decision 199).
- `producer`, `event_id` and `parser_version` form the grain key (Decision 207).
- The ordinary reader serves no transcript content (Decision 236); text is read masked
  (Decision 239).
- A withheld row stores the empty-string digest (telemetry_transcripts.yaml `content_sha256`,
  write-enforced).

## 14. OUTPUT

`audits/telemetry-field-consolidation-<sha>.yaml`:

```
audit:
  meta:
    audited_commit: <sha>
    base_branch: main
    model: <your self-reported model name, free text>
    methodology_version: 1
    scope_surfaces: [telemetry_sessions, telemetry_observations, observations_metadata,
                     telemetry_agents, telemetry_transcripts, envelope, persona_findings]
    producers_registered: []
    field_counts: {bookkeeping: 0, stored_written: 0, declared_unwritten: 0, read_derived: 0,
                   hidden_key: 0}
    degraded_dedup: false
    degraded_empirical: false
    degraded_probe: false
    web_unavailable: false
    trees_sampled: 0
    days_sampled: []
    parser_versions_sampled: []
    contract_notes: ""
    stale_anchors: []
  question_answers:
    - {q: Q1, verdict: lean|some_redundancy|substantial_redundancy, basis: [finding ids], prose: ""}
    - {q: Q2, verdict: justified|partly_justified|unjustified, basis: [], prose: ""}
    - {q: Q3, verdict: declared|partly_declared|undeclared, basis: [], prose: ""}
    - {q: Q4, verdict: findable|partly_findable|not_findable, basis: [], prose: ""}
    - {q: Q5, verdict: lean|proportionate|excessive, basis: [], prose: "",
       external_checklist:
         - {property: E1, rating: met|partial|missed, transfers_to_agent_reader: yes|partly|no,
            surfaces_missed: [], evidence: ""}}
    - {q: Q6, verdict: consolidate_now|batch_with_next_column_change|leave, basis: [], prose: ""}
    - {q: Q7, answers: [{question: "", answer: "", basis: []}]}
  findability_probe:
    dispatched: true
    questions:
      - {n: 1, question: "", answer_given: "", grade: correct|partly|wrong|not_found,
         correct_location: "", files_opened: 0, bytes_read_approx: 0, note: ""}
    tool_calls_total: 0
    summary: ""
  per_surface_assessment:
    - {surface: "", maturity: lean|sound|cluttered|tangled, strengths: "", top_gaps: [finding ids]}
  rubric_ratings:
    - {surface: "", dimension: VD1, rating: strong|adequate|weak|absent|n/a, evidence: "", note: ""}
  field_verdicts:
    - {table: "", field: "", kind: bookkeeping|stored_written|declared_unwritten|read_derived|hidden_key,
       verdict: keep|merge|derive_at_read|reserve|retire|declare, overlaps_with: ["table.field"],
       ever_non_null: yes|no|unknown, retire_mode: drop_column|unproduced_forever|stop_writing|n/a,
       timing: consolidate_now|batch_with_next_column_change|leave|n/a,
       evidence: "", cost: "", finding: <finding id or null>, confidence: CONFIRMED|HYPOTHESIS}
  findings:
    - {id: TFC-01, surface: <a Section 4 surface name or shared>, question: Q1, dimension: VD1,
       candidate_ids: [], title: "", evidence: "", evidence_kind: static|observed,
       current_behavior: "", ideal_behavior: "", gap: "", compensating_controls_considered: "",
       change_type: add|rescope|enforce|unify|persist|clarify|retune_gate, proposed_change: "",
       acceptance: "", severity: critical|high|medium|low, severity_rationale: "",
       confidence: CONFIRMED|HYPOTHESIS,
       roadmap_crossref: {classification: novel|planned-insufficient|planned-unbuilt, item_ids: [],
                          dedup_search_terms: [], dedup_hit_count: 0, confidence: CONFIRMED|HYPOTHESIS,
                          note: ""},
       effort: XS|S|M|L, depends_on: [finding ids],
       sequencing: {safe_to_queue_now: true|false, blocked_behind: [finding or owner ids], note: ""}}
  rejected_candidates:
    - {candidate: "C<n>: <short label>", why_dismissed: "", compensating_control: "",
       control_property_match: "", decision_or_item_id: ""}
  summary: {total_findings: 0, novel_count: 0, planned_insufficient_count: 0,
            planned_unbuilt_count: 0, fields_keep: 0, fields_merge: 0, fields_derive_at_read: 0,
            fields_reserve: 0, fields_retire: 0, fields_declare: 0, top_improvements: [finding ids],
            highest_leverage_change: <finding id>, maturity_by_surface: {<surface>: <level>}}
```

The `external_checklist` example shows E1. Write one entry per property E1-E8. Every enum shown
as `a|b|c` takes exactly one value; a `question` or `dimension` value is one of Q1-Q7 or VD1-VD6.

Rules:
- `field_verdicts` rows:
  - one row for EVERY stored_written field, EVERY declared_unwritten field and EVERY hidden_key
    you find; bookkeeping and read_derived fields get a row only when a finding concerns them.
  - Verdict meanings:
    - keep: needed as is;
    - merge: fold into `overlaps_with`;
    - derive_at_read: stop storing it and compute it in a verb;
    - reserve: unwritten, keep it, and name the live owner in `evidence`;
    - retire: stop declaring or writing it;
    - declare: a hidden_key that should be declared, either in the `metadata` entry's key list or
      as a typed column (say which in `cost`).
  - `cost`, `timing` and `ever_non_null` are required whenever `verdict` is merge,
    derive_at_read, retire or declare. `retire_mode` is required when `verdict` is retire, and is
    `n/a` otherwise. `timing` is `n/a` for keep and reserve.
- `surfaces_missed` is non-empty if and only if `rating` is missed. It names the Section 4
  surfaces where the property is missed.
- COUNTING INVARIANT:
  - `findings[]` is the SOLE enumerated list;
  - `total_findings = len(findings) = novel_count + planned_insufficient_count +
    planned_unbuilt_count`;
  - fully covered candidates live in `rejected_candidates`, NOT in findings;
  - `rubric_ratings`, `question_answers`, `findability_probe` and `field_verdicts` are systems of
    record referenced FROM findings, never re-counted;
  - `top_improvements` and `highest_leverage_change` MUST be finding ids;
  - the six `fields_*` counts sum to `len(field_verdicts)`.

  Also meet the C1-C16 coverage rule (Section 2).
- `control_property_match` is REQUIRED whenever a compensating control is the reason for a
  dismissal. Name the property the control exercises, cite where it operates (mechanism or
  file:function), and state why it would FAIL if the defect were real.
- CONFIRMED requires the behaviour traced to file and function, or counted on sampled rows;
  anything less is HYPOTHESIS.

`audits/telemetry-field-consolidation-<sha>.md`: at most 1,500 words, in plain language for a
reader who has not seen the YAML.
1. If `meta.degraded_empirical` is true, open with that fact.
2. Lead with the answer to "is the field set excessive?" under NS8: what an agent pays, not how
   many columns there are.
3. Then give a table of every merge, retire, derive_at_read and declare verdict, with its cost,
   timing, `ever_non_null` and `retire_mode`.
4. Then the findability probe's result in two or three sentences.
5. Then what you sampled and what you could not check.

## 15. SEVERITY + MATURITY

Assign severity AFTER judgment, by defect class, never from this prompt's framing:
- critical: a reader can obtain a wrong-but-trusted answer because two stored copies of one fact
  disagree; or a stored fact escapes write enforcement in a way that corrupts a derived measure.
- high: a redundancy, an undeclared fact or a misleading declaration that materially misleads
  agent planning or reading, for example one that would lead an agent to propose a column for a
  stored fact, or to read the wrong field. To be high, the compensating controls must be ones you
  judged insufficient.
- medium: a redundancy, ambiguity or inconsistency with a clear fix.
- low: naming or wording.

A compensating control lowers severity only if it PROPERTY-MATCHES: it exercises the same property
and would fail if the defect were real.

Maturity per surface is computed LAST, top-down; the first match wins:
- lean: 0 open critical or high findings on that surface, AND the surface appears in no
  `surfaces_missed` list in Q5's `external_checklist`;
- sound: 0 critical AND at most 1 high;
- cluttered: at most 1 critical;
- tangled: otherwise.

The top rating stays reachable where you argued a property-matched compensating control; this
prompt's framing does not foreclose it.

## 16. COMMIT / PR MECHANICS

1. The base was derived ONCE in Section 5 step 1, and you are on
   `audit/telemetry-field-consolidation-<sha>` created from it. This is a deliberate exception to
   the harness-assigned session branch: the audit needs a clean two-file diff off the audited
   base, and `audit/` is a registered extra push destination
   (docs/contracts/git-ops.yaml `extra_destination_prefixes`). The branch base MUST equal
   `meta.audited_commit`; the branch name carries any `-rN` suffix that step 1 added.
2. Pre-push gate:
   - the YAML parses:
     `bin/venv-python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" audits/telemetry-field-consolidation-<sha>.yaml`;
   - the counting invariant and the C1-C16 coverage rule hold;
   - the report is within its cap (`wc -w` on the `.md` is at most 1,500).

   Running `bin/venv-python -m scripts.validate --pre` is OPTIONAL. If you run it and an unrelated
   failure appears, record it in `meta.contract_notes` before committing; never fix it.
3. `git add` exactly the two deliverables, then run
   `git commit -m "audit: telemetry field consolidation"`, appending any attribution trailer lines
   your harness instructs for commits.
   - Commit with the session's configured identity and signing: do not override the author, do not
     disable or force signing, do not amend.
   - If a pre-commit hook rejects the commit, fix the cause inside your two deliverables and commit
     again; never bypass the hook.
4. Run `git push -u origin HEAD` as a tool call on its own, with no pipe, redirect or second
   command (a repo hook rejects other forms). On a network error, retry up to 4 times with whatever
   backoff your harness allows. IF the push still fails, or fails for a non-network reason: do not
   open a PR. End the turn with a final message naming the branch, the commit sha, the exact error
   and the `summary:` block.
5. Open the PR via `mcp__github__create_pull_request`:
   - owner `otl-labs`, repo `ontheloop`, base `main`, head your branch;
   - ready for review, NOT draft;
   - title `audit: telemetry field consolidation (four telemetry tables, envelope, metadata keys)`;
   - body: a 2-3 sentence lede, plus the `summary:` block in a yaml fence, plus any PR footer your
     harness instructs.

   IF the tool is unavailable or the call fails: end the turn with a final message naming the
   pushed branch, the sha, the error, and the intended title and body.

   Then END THE TURN. Do not poll, do not merge, do not subscribe, do not self-approve. The human
   disposes of the PR.

## 17. GUARDRAILS

- Write boundary (closed list): the ONLY repository files you create or modify are
  `audits/telemetry-field-consolidation-<sha>.yaml` and
  `audits/telemetry-field-consolidation-<sha>.md`. You do NOT edit any contract, code, Decision,
  roadmap item, skill or plan. You do NOT file or update a recommendation.
- Warehouse access is read-only, through named verbs, inside the Section 11 bounds. No content
  reader. The DD-B subagent gets no warehouse access.
- This repository is public (Decision 101). Never write any of the following in either
  deliverable or in printed output:
  - an AWS account id, an ARN, a secret or an internal hostname;
  - any stored row value beyond what Section 11 allows.

  From recommendation bodies, cite rec ids and paraphrase. Never copy message ids, artifact URLs
  or non-repository file paths into a deliverable.
- Precision over volume. Fewer than about 6 surviving findings is a valid result; state it plainly
  and do not pad. `field_verdicts` will be long by design: it is a classification, not a list of
  findings.
- No verdict inheritance. The operator's "excessive" is a concern to test, not a conclusion to
  deliver. NS8 cuts both ways: a large field set an agent parses cheaply and correctly is not
  excessive.
- If an anchor does not resolve, record it in `meta.stale_anchors` and downgrade dependent items to
  HYPOTHESIS. Never invent; never abort.
