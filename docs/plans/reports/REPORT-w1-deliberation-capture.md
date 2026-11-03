# REPORT: W1 component 4 - deliberation capture (reasoning visibility, tokens and text; read-time metrics)

REPORT-ONLY (Decision 86 cl.2). Planning artefact: `docs/plans/PLAN-w1-deliberation-capture.yaml`.
Fixture rows: `pwi-deliberation-capture` in `docs/work-item-pilot/telemetry-feedback-loop.yaml`
(CD.45 pilot, provisional_v0). Nothing is built, filed, closed, flipped or ratified here.

## 0. Verdict

- The producer half of this component is already decided, and this item builds on it rather than
  re-deciding it. rec-4028's context records the operator's 2026-09-29 choice ("2a-1 + rec-4028"):
  telemetry_observations gains `reasoning_visibility` (full | summarized | omitted | redacted | none)
  and `reasoning_tokens` (<= tokens_output; zero or NULL when visibility is none) on model_call rows;
  the "2a-1 plan" moves the claude_code producer's mapping to parser_version 2; rec-4028 owns the
  LiteLLM mapping (DeepSeek `reasoning_content` -> full, thinking from the RESPONSE only, never from
  request messages). Slice 2a-1 landed it for claude_code on 2026-10-04 (#1405, 2553d69a). What no
  artefact holds is the read side: which metrics derive from those facts, and how a silent capture loss
  would be seen.
- 2a-1 is merged (#1405, 2553d69a; q1 closed). The contract now carries `reasoning_visibility` (the five
  values, required on model_call points) and `reasoning_tokens` (provider-reported, NULL when unreported,
  at_most tokens_output, NULL or 0 when none), and a write-time row-rule kernel projects all four rules and
  checks them before any SQL (src/telemetry/rules.py; VP 2). claude_code is at parser_version 2 and no longer
  writes metadata.thinking_tokens. Its mapping is the one this report assumed (VP 1): an empty signed block
  reads omitted, text reads summarized, redacted_thinking reads redacted, no reasoning reads none. 2a-1 is
  deploy-inert: the writer verb that applies the kernel to stored rows is slice 2a-2.
- On Claude Code the deliberation TEXT is not available by default; the token COUNT is. In this
  session's own transcript every thinking block (54 model_calls with thinking tokens) is empty and
  signed, so the producer writes zero thinking rows while 64% of output tokens were thinking (section 1).
  That matches the primary docs: `display` defaults to "omitted" on current models, and "No display
  setting returns the raw chain of thought". Text exists only on the LiteLLM lane (DeepSeek thinking
  mode, on by default), which T4.2 has not built. Whether to opt Claude Code into summaries is k1.
- Capture can fail silently in ways no single row shows: a display default that changes per model
  generation, LiteLLM's message logging switched off, a stream cut mid-flight, a mapping bug. The stored
  `reasoning_visibility` says what the producer saw; the thinking rows say what it stored. Their
  disagreement is this item's failure signal. It is a cross-table rule whose truth depends on the
  arrival order of two independent producers (R6), so Decision 210 cl.2(b) makes it a DQ monitor plus
  the read verb, never a write check. The row-local invariants, including that a classifying
  parser_version always writes a visibility, go to the writer (cl.1, cl.4), and the build waits for that
  enforcement; the read also counts a missing visibility after cutover as drift, so the rule does not rest
  on the writer alone. Rows written before a producer classified are a coverage count, not drift. One SQL
  statement, grouped by producer, parser_version, model and visibility, passes 16 of 16 vectors (VP 4).
- One item fits the clause-3 grain (kind task; three criteria; part_of T3.20, depends_on T2.36). The
  LiteLLM lane's arrival with rec-4028 and T4.2 is a maturity-ladder event, not a build dependency (section
  4 edges). The verb and the monitor share one failure mode and one SQL, so a split would spend the cap
  on rows with one failure signal.

## 1. Evidence (each row re-derivable; VP step in brackets)

| id | fact | anchor |
|---|---|---|
| e1 | Operator choice 2026-09-29: reasoning_visibility and reasoning_tokens on telemetry_observations (2a-1), claude_code mapping at parser_version 2 (2a-1), LiteLLM mapping and response-only capture (rec-4028); rec-4028 status open | rec-4028 context, 2026-09-29 entries (rec_by_id) |
| e2 | The claude_code producer (parser_version 2) sets reasoning_visibility by block shape and stores the reported thinking_tokens as reasoning_tokens; metadata no longer holds it [VP 1] | src/turn_capture/observations.py:175-183, :290-314 |
| e3 | A thinking block becomes a purpose=thinking row owned by the model_call only when its text is non-empty; a `redacted_thinking` block is a diagnostic only [VP 1] | src/turn_capture/transcripts.py:284-300 |
| e4 | The contract has four token columns plus reasoning_visibility and reasoning_tokens with required_when, accepted_values, at_most and null_or_zero_when; the row-rule kernel projects all four [VP 2] | docs/contracts/telemetry_observations.yaml:222-265, :419-434; src/telemetry/rules.py:213-241 |
| e5 | `provider` is never set by the claude_code producer, and its vocabulary is the retired copilot set (rec-4135 tracks the vocabulary) [VP 1, VP 2] | docs/contracts/telemetry_observations.yaml:307; src/turn_capture/observations.py:59 |
| e6 | Tier 1 still names `deepseek/deepseek-chat` and `deepseek/deepseek-reasoner` [VP 2], and its note says the aliases "remap to deepseek-v4-flash post-2026-07-24" | docs/contracts/inference-provider.yaml:355-357 |
| e7 | thinking is an accepted transcript purpose, owned by the model_call [VP 2] | docs/contracts/telemetry_transcripts.yaml:85, :134 |
| e8 | env R5(c) collapses one model_call seen by two producers to claude_code's row; transcript rows are not collapsed; env R8: a non-replayable producer emits no open marker | docs/contracts/telemetry-event-envelope.yaml:344-347, :359 |
| e9 | A transcript-less producer (LiteLLM) receives session_started_at from another producer of the tree, else defers | docs/contracts/telemetry-event-envelope.yaml:115-121 |
| e10 | Decision 209 cl.2(a): agent transcripts never leave the data plane; (b) only an allow-list crosses | docs/DECISIONS.md:75 |
| e11 | Decision 210 cl.1 row-local rules at the writer; cl.2 cross-row rules the write cannot decide are DQ monitors, alarm-not-gate | docs/DECISIONS.md:22-23 |
| e12 | T3.20 c3: an orphan/coverage DQ check for the turn-observation to transcript join, alarm-not-gate | docs/ROADMAP-PLATFORM.yaml T3.20 c3 |

External facts, checked against primary docs on 2026-10-03 (not VP steps; they can change):

- DeepSeek, Thinking Mode guide (api-docs.deepseek.com/guides/thinking_mode): "Thinking mode is enabled
  by default, with the default effort being high"; the chain of thought "is returned via the
  reasoning_content parameter, at the same level as content"; "for requests carrying the tools
  parameter, the reasoning_content must be fully passed back to the API in all subsequent requests ...
  If your code does not correctly pass back reasoning_content, the API will return a 400 error";
  without tools it "will be ignored".
- DeepSeek, Create Chat Completion reference: `usage.completion_tokens_details.reasoning_tokens`,
  "Tokens generated by the model for reasoning", a breakdown of completion tokens (so reasoning_tokens
  <= completion tokens holds).
- DeepSeek, V4 release note (2026-04-24) and Models page: "deepseek-chat & deepseek-reasoner will be fully
  retired and inaccessible after Jul 24th, 2026, 15:59 (UTC Time)"; the model name is now
  `deepseek-flash` (legacy `deepseek-v4-flash` still accepted) or `deepseek-v4-pro`. e6 is stale, and so is
  the contract's own remap note (:356-357): the old names are inaccessible, not remapped.
- Anthropic, Thinking overview (platform.claude.com/docs/en/build-with-claude/thinking): on Opus 5.5,
  Opus 5, Sonnet 5.5, Sonnet 5 and the Fable models "`display` defaults to `"omitted"`"; omitted
  blocks have "an empty `thinking` field" and a signature; summarized text "is a summary of Claude's
  full thinking process rather than the raw chain of thought", produced "by a different model";
  "No display setting returns the raw chain of thought"; you are "charged for the full thinking tokens".
- Anthropic, Extended thinking: `usage.output_tokens_details.thinking_tokens` "reports how many of the
  billed output tokens were internal reasoning" (so reasoning_tokens <= tokens_output holds).
- Claude Code, Model configuration (code.claude.com/docs/en/model-config): "Interactive sessions on the
  Anthropic API receive redacted thinking blocks by default, so set `showThinkingSummaries: true` in
  settings if you want the full summaries"; thinking cannot be turned off on Opus 5.5, Sonnet 5.5 or
  the Fable models. The docs say "redacted", but the stored blocks are type `thinking` with empty text
  (the API's omitted shape), not `redacted_thinking` (section 1). A mapping must key on block shape.

Measured (local only; no production catalog read, no provider call):

- This session's own CC-web transcript run through record_turn locally (counts only, not
  reproducible from the repository, at the time of measurement): 83 model_calls, 54 with
  thinking_tokens > 0, 39,396 of 62,008 output tokens thinking (64%), 0 purpose=thinking rows. Every
  thinking block in the transcript was empty and signed, and every model_call with a thinking block
  had thinking_tokens > 0 while every one without had 0. Under rec-4028's rule all 54 read omitted and
  the rest none.
- LiteLLM 1.102.1 against a mocked transport (VP 3; nothing leaves the process): a DeepSeek response's
  `reasoning_content` surfaces on the message and `reasoning_tokens` on usage. Re-sending the returned
  message echoes the reasoning back. A rebuilt history WITHOUT it gets a single-space placeholder when
  thinking is passed explicitly (a warning only), and nothing at all when thinking is left to
  DeepSeek's default, because LiteLLM's fill runs only for an explicit `thinking: enabled`. Per the
  DeepSeek guide the second case is a 400 on a tools request and the first silently blanks the chain.
  `supports_reasoning` is False for `deepseek-chat` and True for `deepseek-flash` in this version's
  bundled model map.
- The SQL in section 2 passes 16 of 16 vectors on DuckDB 1.5.4 (VP 4). Thirteen mutants were run once by
  hand and each is caught: counting thinking rows instead of their existence (15/16), counting any
  transcript purpose (15/16), treating redacted as text-bearing (15/16), treating summarized as text-free
  (14/16), summing output tokens of calls with no reported count (13/16), an inner join (6/16), dropping
  the legacy arm (14/16), counting a NULL row with no text as text_missing (14/16), classing every NULL as
  legacy (15/16), legacy ignoring the parser_version (15/16) or the producer (15/16), and dropping
  parser_version (15/16) or model (15/16) from the grain.

## 2. Design (what this item stages)

Inputs, both after the shared env R4/R5 dedupe the reader-verbs item stages (W1-2, its report section 2), so
partition binding, generation retirement and the model_call collapse are inherited, not restated:
`{calls}` is one session's model_call rows (observation_id, producer, parser_version, model,
tokens_output, reasoning_tokens, reasoning_visibility, as 2a-1 lands them). The monitor binds the same
statement over many sessions, and a non-replayable producer can change version mid-session (v13), so
parser_version is in the grain rather than recovered from a session's own versions. `{thinking}` is the
same session's transcript rows, projected to observation_id and purpose only. The verb never selects `content` or
`content_uri`. Text-bearing visibilities are full and summarized; omitted, redacted and none hold none.

```sql
WITH t AS (
  SELECT DISTINCT observation_id FROM {thinking} WHERE purpose = 'thinking'
),
c AS (
  SELECT m.*, t.observation_id IS NOT NULL AS has_text,
         CASE
           WHEN m.reasoning_visibility IS NULL AND m.producer = 'claude_code' AND m.parser_version < 2 THEN 'legacy'
           WHEN m.reasoning_visibility IS NULL THEN 'visibility_missing'
           WHEN m.reasoning_visibility IN ('full', 'summarized') AND t.observation_id IS NULL THEN 'text_missing'
           WHEN m.reasoning_visibility NOT IN ('full', 'summarized') AND t.observation_id IS NOT NULL THEN 'text_unexpected'
         END AS call_class
  FROM {calls} m LEFT JOIN t USING (observation_id)
)
SELECT producer, parser_version, model, reasoning_visibility,
       count(*) AS calls,
       count(*) FILTER (WHERE call_class = 'legacy') AS legacy_calls,
       count(*) FILTER (WHERE reasoning_tokens > 0) AS reasoning_calls,
       coalesce(sum(reasoning_tokens), 0) AS reasoning_tokens,
       coalesce(sum(tokens_output) FILTER (WHERE reasoning_tokens IS NOT NULL), 0) AS counted_output_tokens,
       count(*) FILTER (WHERE has_text) AS text_calls,
       count(*) FILTER (WHERE call_class = 'text_missing') AS text_missing,
       count(*) FILTER (WHERE call_class = 'text_unexpected') AS text_unexpected,
       count(*) FILTER (WHERE call_class = 'visibility_missing') AS visibility_missing
FROM c GROUP BY producer, parser_version, model, reasoning_visibility
```

Response (Decision 88; Decision 209 cl.2a): the rows above and the drift counts; never text. The
deliberation share is a consumer's division, `reasoning_tokens / counted_output_tokens`, over calls whose
count was reported (a NULL count is never estimated; rec-4028 forbids LiteLLM's local token counter).

deliberation_drift_share is `(text_missing + text_unexpected + visibility_missing) / (calls -
legacy_calls)`, per producer and parser_version: sum each numerator and the denominator across model and
visibility first, then divide; never average per-row shares (an omitted row can only show
text_unexpected). It is undefined (never 0) when no call is classified. The monitor's run record carries
`classified_calls` (calls - legacy_calls) and a verdict of drift, clean or none; a legacy-only run reports
`none`, never `clean` (v07, v14; c2's third test node).

`legacy_calls` counts only rows written before their producer classified: claude_code below parser_version
2, the 2a-1 version (config/telemetry/parser_versions.yaml, merged in #1405; the build binds the cutover to
that history entry, never a literal, per Decision 210 cl.3: a cutover set too high is silent, one set too
low is loud). It is a coverage figure, never drift, and excluded from every maturity trigger. 2a-1's
required_when admits no NULL visibility on any model_call point, and no telemetry writer was live before it
(the contracts' TARGET-STATE notes), so no legacy row is expected in the store; the arm stays as defence
(inferred, not measured). Any other NULL visibility (litellm at any version, claude_code
from 2a-1 on) is `visibility_missing` and counts as drift (v15).

Contract declaration (data-modeling-standard.yaml rule derived-state, Decision 199; the build's edit, not
this PR's). Every read-derived telemetry metric is declared in its Class A contract with `derivation:
{timing: read, derived_by: reader_verb:..., realized: false}` (telemetry_observations.yaml:290,
telemetry_sessions.yaml:219-252). The build declares, before or with the verb: `deliberation_class` on
telemetry_observations, a model_call-grain read-derived field (legacy, text_missing, text_unexpected,
visibility_missing or NULL for clean), which is the SQL's per-call `call_class`; and session-grain totals on
telemetry_sessions with `derived_by: reader_verb:session_deliberation_rollup`, named per that contract's
`*_total` roll-up convention (classified_calls_total, legacy_calls_total, deliberation_drift_calls_total,
reasoning_tokens_total, counted_output_tokens_total) and added to its roll-up reconciliation note. The
roll-up verb returns grouped rows, not a per-call class, so `deliberation_class` names a per-call verb (or
a per-call projection of the roll-up verb) as its derived_by, as `model_call_cost_estimate` does for cost;
the build records that choice beside q4. 2a-1 merged without these fields, so both the model_call field
and the session fields ride with the verb's build plan. The per-(producer, parser_version, model,
visibility) roll-up is multi-row per session and fits no single Class A field; where its shape is declared
is q4. Decision 210 cl.3 also binds the SQL's literals to these sources: the text-bearing set (full,
summarized) is read from the 2a-1 contract's visibility vocabulary and the cutover from
parser_versions.yaml, never restated. A value outside the vocabulary would read text_unexpected only if it
stored a thinking row, and clean otherwise; the writer's accepted-set rule (cl.4, in the build gate) is
what stops it, as the writer-side paragraph below says.

Writer-side, not here (Decision 210 cl.1 and cl.4, rec-4024 slice 2a; the rules are the 2a-1 contract's):
reasoning_visibility in its accepted set; reasoning_visibility NOT NULL on model_call rows at a
classifying parser_version (litellm: every version; claude_code: from the 2a-1 version), which is
conditional requiredness and never demoted to DQ; reasoning_tokens <= tokens_output; and reasoning_tokens
zero or NULL when visibility is none. 2a-1 (#1405) put all four in the contract and in the write-time
row-rule kernel (src/telemetry/rules.py; VP 2); its required_when is unconditional on model_call points, so
the classifying-version condition above is stricter than needed and harmless. The read relies on all four:
without them an out-of-set value (`Full`, `summarised`, empty), `none` with reasoning_tokens > 0 (a
producer that saw reasoning and dropped it) and reasoning_tokens > tokens_output all read clean. 2a-1 is
deploy-inert, so the build of this verb waits for the writer verb (slice 2a-2) to apply the kernel to every
write (plan constraint). For the NULL case only, the read adds
defence in depth: `visibility_missing` makes a producer which stops writing visibility read as drift, not
as legacy. The other three have no read-side arm; read-side arms for them are a fork for the operator, not
taken here.

Vectors (hand-written for this report; VP 4 runs every one). `calls` rows are `[observation_id,
producer, parser_version, model, tokens_output, reasoning_tokens, reasoning_visibility]` (claude_code 1 is
pre-2a-1, 2 is the 2a-1 version rec-4028 names), `thinking` rows are `[observation_id, purpose]`, and
`expected` rows follow the SELECT column order. The vectors are post-dedupe input: the env R4/R5 dedupe
itself is pinned by W1-2's own vectors, not re-proved here.

```yaml
vectors:
  - id: v01-omitted-claude-code-counts-only
    calls: [[m1, claude_code, 2, claude-opus-5-5, 371, 52, omitted], [m2, claude_code, 2, claude-opus-5-5, 112, 0, none]]
    thinking: []
    expected:
      - [claude_code, 2, claude-opus-5-5, omitted, 1, 0, 1, 52, 371, 0, 0, 0, 0]
      - [claude_code, 2, claude-opus-5-5, none, 1, 0, 0, 0, 112, 0, 0, 0, 0]
  - id: v02-summarized-with-text
    calls: [[m1, claude_code, 2, claude-opus-4-6, 300, 120, summarized]]
    thinking: [[m1, thinking]]
    expected: [[claude_code, 2, claude-opus-4-6, summarized, 1, 0, 1, 120, 300, 1, 0, 0, 0]]
  - id: v03-deepseek-full-with-text
    calls: [[d1, litellm, 1, deepseek-flash, 40, 31, full]]
    thinking: [[d1, thinking]]
    expected: [[litellm, 1, deepseek-flash, full, 1, 0, 1, 31, 40, 1, 0, 0, 0]]
  - id: v04-full-text-lost
    calls: [[d1, litellm, 1, deepseek-flash, 40, 31, full]]
    thinking: []
    expected: [[litellm, 1, deepseek-flash, full, 1, 0, 1, 31, 40, 0, 1, 0, 0]]
  - id: v05-two-thinking-rows-count-once
    calls: [[d1, litellm, 1, deepseek-flash, 40, 31, full]]
    thinking: [[d1, thinking], [d1, thinking]]
    expected: [[litellm, 1, deepseek-flash, full, 1, 0, 1, 31, 40, 1, 0, 0, 0]]
  - id: v06-omitted-but-text-stored
    calls: [[m1, claude_code, 2, claude-opus-5-5, 371, 52, omitted]]
    thinking: [[m1, thinking]]
    expected: [[claude_code, 2, claude-opus-5-5, omitted, 1, 0, 1, 52, 371, 1, 0, 1, 0]]
  - id: v07-legacy-row-is-coverage-not-drift
    calls: [[m1, claude_code, 1, claude-opus-5-5, 371, null, null]]
    thinking: []
    expected: [[claude_code, 1, claude-opus-5-5, null, 1, 1, 0, 0, 0, 0, 0, 0, 0]]
  - id: v08-response-row-is-not-thinking
    calls: [[m1, claude_code, 2, claude-opus-4-6, 300, 120, summarized]]
    thinking: [[m1, response]]
    expected: [[claude_code, 2, claude-opus-4-6, summarized, 1, 0, 1, 120, 300, 0, 1, 0, 0]]
  - id: v09-orphan-thinking-row-ignored
    calls: [[m1, claude_code, 2, claude-opus-5-5, 112, 0, none]]
    thinking: [[zz, thinking]]
    expected: [[claude_code, 2, claude-opus-5-5, none, 1, 0, 0, 0, 112, 0, 0, 0, 0]]
  - id: v10-deepseek-thinking-disabled
    calls: [[d1, litellm, 1, deepseek-flash, 25, 0, none]]
    thinking: []
    expected: [[litellm, 1, deepseek-flash, none, 1, 0, 0, 0, 25, 0, 0, 0, 0]]
  - id: v11-redacted-holds-no-text
    calls: [[m1, litellm, 1, claude-sonnet-5, 200, 64, redacted]]
    thinking: []
    expected: [[litellm, 1, claude-sonnet-5, redacted, 1, 0, 1, 64, 200, 0, 0, 0, 0]]
  - id: v12-full-with-unreported-count
    calls: [[d1, litellm, 1, deepseek-flash, 40, null, full], [d2, litellm, 1, deepseek-flash, 50, 20, full]]
    thinking: [[d1, thinking], [d2, thinking]]
    expected: [[litellm, 1, deepseek-flash, full, 2, 0, 1, 20, 50, 2, 0, 0, 0]]
  - id: v13-parser-versions-group-apart
    calls: [[d1, litellm, 1, deepseek-flash, 40, 31, full], [d2, litellm, 2, deepseek-flash, 40, 31, full]]
    thinking: [[d1, thinking]]
    expected:
      - [litellm, 1, deepseek-flash, full, 1, 0, 1, 31, 40, 1, 0, 0, 0]
      - [litellm, 2, deepseek-flash, full, 1, 0, 1, 31, 40, 0, 1, 0, 0]
  - id: v14-legacy-row-with-text-is-not-drift
    calls: [[m1, claude_code, 1, claude-opus-4-6, 300, null, null]]
    thinking: [[m1, thinking]]
    expected: [[claude_code, 1, claude-opus-4-6, null, 1, 1, 0, 0, 0, 1, 0, 0, 0]]
  - id: v15-missing-visibility-after-cutover-is-drift
    calls: [[d1, litellm, 1, deepseek-flash, 40, 31, null], [m1, claude_code, 2, claude-opus-5-5, 371, 52, null]]
    thinking: []
    expected:
      - [litellm, 1, deepseek-flash, null, 1, 0, 1, 31, 40, 0, 0, 0, 1]
      - [claude_code, 2, claude-opus-5-5, null, 1, 0, 1, 52, 371, 0, 0, 0, 1]
  - id: v16-two-models-group-apart
    calls: [[m1, claude_code, 2, claude-opus-5-5, 371, 52, omitted], [m2, claude_code, 2, deepseek-flash, 40, 31, omitted]]
    thinking: []
    expected:
      - [claude_code, 2, claude-opus-5-5, omitted, 1, 0, 1, 52, 371, 0, 0, 0, 0]
      - [claude_code, 2, deepseek-flash, omitted, 1, 0, 1, 31, 40, 0, 0, 0, 0]
```

## 3. Settled / contested / risk / open

Settled (consistent with a Decision, a contract, an operator choice or measured; s1-s3 in the fixture):

- s1 The producer facts are decided (e1) and, for claude_code, merged (#1405): reasoning_visibility and
  reasoning_tokens on model_call, the claude_code mapping in 2a-1, the LiteLLM mapping in rec-4028. This
  item checks them against the primary docs and the measurement above and finds them consistent: both
  providers report reasoning tokens as a subset of output tokens, Claude Code's empty signed blocks read
  omitted, and capture from the response only avoids LiteLLM's placeholder (VP 3).
- s2 Data plane only. Thinking text is agent transcript (Decision 209 cl.2a). The verb reads visibility,
  counts and transcript purpose, never content, so only derived counts are candidates for rec-4141's
  allow-list; the visibility vocabulary and the counts are a closed set, the model ids are not.
- s3 The drift rule is cross-table and fails Decision 210 cl.2(b): one call can carry thinking rows from two
  independent producers (R6), so whether a call "has text" depends on which producer's rows have arrived.
  That makes it a DQ monitor, alarm-not-gate, with T3.20 c3's observation-to-transcript join check as its
  home; the verb runs the same SQL. Its row-local half (a classifying parser_version writes a visibility) is
  the writer's (cl.1, cl.4); the read counts its breach as visibility_missing, drift over classified calls
  beside text_missing and text_unexpected. 16/16 vectors (VP 4). The batching premise is not relied on:
  W1-1's runner (#1384, unmerged) writes per-table batches, and the telemetry project reports that slice
  2a-2 writes one transaction per request with the sessions batch last (env R7; relayed 2026-10-04, not in
  the 2a-1 plan read here); within one producer's request cl.2(a) could hold, never across producers
  (section 5).

Contested (evidence on both sides; k1-k3 in the fixture, k4 report-only; parked for the operator):

- k1 Claude thinking text. Claude Code stores empty signed blocks by default, so deliberation text never
  reaches telemetry from the only live producer. Options: (a) counts only, no settings change; (b) set
  `showThinkingSummaries: true` in `.claude/settings.json` for every session; (c) set it only for
  sessions an RCA will read. For (b)/(c): the summary costs no extra billed tokens (thinking is billed
  in full either way) and is the only Claude text there is. Against: it is a summary written by a
  different model, so it says what a summarizer saw, not how the agent reasoned; it adds content rows
  and blob reads; and it changes every session's request. Untested here: whether the setting reaches
  CC-web and headless sessions at all (q-level, would be measured first). Recommended: (a) until an RCA
  consumer names a need; visibility then flips to summarized for those models and the drift rule
  follows with no change.
- k2 LiteLLM-lane durability. DeepSeek's chain of thought exists in the live response and in the
  persona's own message history, which a tools request must carry anyway (the 400 rule above). A
  callback producer that fails to write loses it; Decision 84 I-4 bars an outbox. Options: (a) make the
  LiteLLM producer replayable (env R7) from the persona's durable checkpoint (CD.27), with an open marker;
  (b) keep it non-replayable (env R8) and count each lost write in the producer's diagnostics, as rec-4028
  item (4) already does for a stream cut mid-flight; (c) an outbox, barred. Recommended: (b) now, and
  (a) as a T4.2 design input once the checkpoint exists. A design choice for rec-4028 and T4.2; no
  precedent.
- k3 Who reads chain-of-thought text. Options: (a) metrics only; text is retained in the data plane
  for a forensic read by session (no verb); (b) a per-call text verb (egress on every read, Decision
  88); (c) LLM labels over CoT (spends; would mirror rec-4032's materialization trigger). Recommended:
  (a). No consumer has named a need, and DeepSeek reasoning can exceed 64 KiB and spill to the blob
  port, so every read is a blob fetch.
- k4 Read-side arms for the three writer rules other than NULL visibility (an out-of-set value; `none`
  with reasoning_tokens > 0; reasoning_tokens > tokens_output). Options: (a) writer only, with the build
  waiting for all four rules (taken here); (b) also add `visibility_unknown` and `none_with_reasoning`
  arms as drift, one vector each. Recommended: (a); (b) is a cheap add at build time if the read must
  stand without the writer. rec-4028's LiteLLM mapping makes `none` with tokens > 0 unreachable for a
  correct producer. Not in the fixture (its contested list holds three rows); parked for the operator.

Risk (known loss modes, not choices). R1-R6 and R9 are this report's ids; a rule of the event envelope
(telemetry-event-envelope.yaml) is always written env R<n>:

- R1 2a-1 merged, not live. 2a-1 (#1405) is deploy-inert: until slice 2a-2's writer verb ships, no
  telemetry row is stored, so the verb has nothing to read and the drift share is undefined. Once it ships,
  every claude_code row is parser_version 2 and classified. Owner: the telemetry project (rec-4024 slice
  2a-2).
- R2 Bytes are not deliberation. Claude text is empty or a summary by another model, so any metric over
  thinking-row `content_bytes` measures the summarizer. Only reasoning_tokens measures deliberation;
  the verb sums no bytes.
- R3 Pass-back degradation (T4.2's, named here because capture must never feed it). If the persona
  rebuilds history from anything but the provider message, LiteLLM blanks the chain (explicit
  thinking) or the call 400s (default thinking) (VP 3). rec-4028 item (2) already takes thinking from
  the response only; the new fact for T4.2 is that LiteLLM's fill does not run under DeepSeek's
  default-on thinking.
- R4 Stale tier ids. e6 names models DeepSeek retired on 2026-07-24, and LiteLLM's bundled map marks
  `deepseek-chat` as non-reasoning, so a cost or capability lookup keyed on it is wrong. The contract's
  note (inference-provider.yaml:356-357, "Aliases remap to deepseek-v4-flash post-2026-07-24") is wrong
  as well: DeepSeek says the names are "fully retired and inaccessible". Both the ids and the note need
  the fix. Owner: T4.2 and inference-provider.yaml (Decision 173 cl.2 makes it a contract edit). Named,
  not filed.
- R5 Grouping key. `provider` is NULL on claude_code rows (e5), so the verb groups by producer and model.
  A Claude Code session pointed at DeepSeek's Anthropic-format endpoint would land under claude_code
  with a DeepSeek model id; grouping by model keeps it apart. Decision 206's persona_backend (litellm |
  claude_cli) is on model_call rows, and outcome-parity reads group by (agent_name, persona_backend). Once
  claude_cli personas (T4.27) share the claude_code producer with interactive sessions, the two can carry
  different visibility mixes, so the build should consider persona_backend in the grain; q2's outcome join
  needs it either way.
- R6 One call, two producers. env R5(c) keeps claude_code's model_call row, but both producers' thinking rows
  survive; the verb tests existence per observation_id (v05). This is also why the drift rule cannot be
  a write check (s3): neither producer's write can see the other's rows. The existence test is per call,
  not per producer: if claude_code says summarized and only litellm's thinking row survived, the call
  reads clean and claude_code's lost copy is hidden on that call (it still shows as text_missing on
  claude_code's single-producer calls). Carrying producer in the thinking projection would close it for
  the monitor; not staged here.
- R9 `display: "updates"`, settled by 2a-1. The Anthropic thinking page documents a beta in which
  reasoning blocks come back empty and separate progress-update blocks carry text. The 2a-1 contract's
  semantics now class that text as summarized ("Claude thinking text, which can include user-facing
  progress updates"), so the drift rule needs no new value; a share over summarized calls then mixes
  summaries and progress text, which matters only if k1 opts into summaries.
- Goodhart. deliberation share is the obvious efficiency lever, and lowering effort lowers it while making
  outcomes worse. It is diagnostic only: no consumer ranks, alarms or files a rec on it alone, and T3.3
  reads it only beside an outcome (acceptance_passed, cost per verified merge). The drift share cannot be
  gamed by a wildcard the way a rule set can. A producer that stops writing visibility (NULL) is rejected
  at write (cl.1) and, until that enforcement exists or if it regresses, read as visibility_missing drift.
  An out-of-set value, `none` with reasoning tokens, or tokens above output have no read-side arm: they
  rest on the writer, which the build gate waits for. The remaining lever is a producer that misclassifies
  consistently, which section 4 assigns to the producer conformance tests and c3's review.

Open (q1-q3 in the fixture, q4 report-only; q1 closed by the 2a-1 merge, the rest open):

- q1 Where is the 2a-1 plan? Closed: it merged on 2026-10-04 (#1405, 2553d69a), and VP 1-2 now read the
  merged producer, contract and kernel.
- q2 Does deliberation predict outcome? One session spent 64% of output tokens thinking; nothing joins
  that to an outcome yet, so T3.3 must not alarm on a deliberation share until a measurement shows it
  means something.
- q3 Who roots a LiteLLM-only persona tree? rec-4028 has the executor loop propagate session_ref, the root
  session_started_at and project_ref (e9), but a non-replayable producer emits no open marker (e8), so a
  persona run with no Claude Code transcript has no telemetry_sessions open row, and W1-2's session verbs
  cannot see it. The deliberation verb is unaffected (it reads model_call rows by session_id).
- q4 Where is the multi-row roll-up's shape declared? The per-call class and the session totals have Class
  A homes (section 2), but the per-(producer, parser_version, model, visibility) rows do not. Options: a
  response-shape entry in the reader registry, or a declared verb-response contract. For W2 and the
  verb's build plan; not in the fixture (its open list holds three rows).

## 4. Consideration register (as authored in the fixture)

- planes: data_plane.
- failure_signal: stored visibility disagrees with stored text (full or summarized with no thinking row; a
  thinking row where none was declared) or is missing after the producer's cutover. Metric
  `deliberation_drift_share` over classified calls, per producer and parser_version (both in the SQL's
  grain; legacy rows excluded). Source: the verb's response and the T3.20 c3 monitor. Why this and not a
  count or latency: every way capture breaks here is silent (a display default flips, logging is switched
  off, a stream is cut, a mapping regresses), and each shows up as a disagreement between the producer's own
  classification and what it stored. What it cannot see: a producer that misclassifies and stores
  consistently (omitted with no text when text was available). That is the producer conformance tests' job
  (rec-4028's acceptance node `test_reasoning_visibility_matrix` and 2a-1's), and c3's review.
- maturity: starts at read_all, meaning the operator reviews every drift row. The ladder is kept per
  producer, so the claude_code lane (which under k1 (a) can show text_unexpected but never text_missing)
  cannot promote the LiteLLM lane. Every trigger counts that producer's classified work only, so
  legacy-only history (every session today, R1) cannot promote a rung on absent evidence. read_all ->
  sampled at >= 20 consecutive reviewed sessions with >= 1 classified model_call and zero drift rows;
  sampled -> spot_check at 0 drift rows across a full window of the last 200 sampled classified
  model_calls (fewer than 200 never promotes); spot_check -> anomaly_triggered at >= 30 consecutive days
  with deliberation_drift_share defined and <= 0.01 for every parser_version present that day. At
  anomaly_triggered the monitor alarms when a producer's daily share exceeds 0.01 (seed alarm threshold).
  Rung events that return a producer to read_all: a new producer, a parser_version bump (2a-1 is one), and
  a change in its visibility mix (k1 flipping to summarized). The rung events and the alarm threshold are
  report-only (the fixture's Maturity schema carries only the adjacent forward pairs); all are seed values
  for the maturity-ladder component to challenge.
- verification: c1 (the verb on DuckLake passes every vector and selects no content column), c2 (the
  monitor flags each drift kind, text_missing, text_unexpected and visibility_missing, stays silent on
  clean vectors, and reports verdict none with classified_calls 0 on a legacy-only run, never clean), c3
  (the operator's read_all review before T3.3 or T3.4 reads the metrics). Each execution command names at
  least two distinct test node ids (c2 three), so one thin test cannot satisfy it (pytest exits non-zero
  on a missing node id). c1 names its nodes without `-m integration`, so an unmarked node cannot be
  deselected, and its tests fail rather than skip when DuckLake is unavailable. All open.
- rollback: drop the verb and the monitor; nothing is stored, so every metric re-derives on the next
  read.
- edges: part_of T3.20 (its c3 join check is the monitor's home and its turn-grain rows carry the
  Claude Code facts); depends_on T2.36 (the DuckLake write and read path the verb runs on; it does not
  own row-local rule enforcement, which is rec-4024 slice 2a and a plan build gate, not an edge, since no
  tier id owns it). No edge to T4.2: T4.2 depends on T3.4, which depends on T3.3, and this item feeds both
  (c3, q2, the Goodhart line, the T3.4 note in section 5), so the edge would order it after its own
  consumers and hold the claude_code lane behind the LiteLLM one. Nothing in c1 or c2 needs T4.2: both are
  vector tests over synthetic rows and the SQL is producer-generic. The LiteLLM lane arrives as a new
  producer when rec-4028 lands with T4.2, which is a rung event that starts that producer at read_all; k2,
  R3 and R4 keep the T4.2 pointers in prose.

## 5. Boundary notes for W2 synthesis

- Capture producer wiring (W1-1, #1384): its boundary note calls deliberation a transcript-less producer
  that must receive project_ref and session_started_at. True for the LiteLLM lane only; the Claude Code
  half rides W1-1's own rows. On merge W2 should add `pwi-deliberation-capture depends_on
  pwi-capture-producer-wiring`; the evaluator's L4 refuses an edge to a pilot item absent from this
  branch's fixture, so it is not added here. A parser_version bump for 2a-1 re-parses every tree under
  W1-1's runner (env R7), so its warm-pass cost (W1-1 k1) applies once per tree at the bump.
- Reader verbs (W1-2, #1390): this verb composes on its shared dedupe and needs its multi-table binding
  (calls plus transcripts). W2 should add `depends_on pwi-telemetry-reader-verbs`.
- Friction classifier (W1-3, #1394): reasoning text is content, like W1-3's k3 signatures. If W2 adopts
  read-side content regex (W1-1 P1), CoT would be its largest input; this report recommends it stays out
  (k3 (a)).
- Back-validation (T3.4): a delta in deliberation share across a fix is valid only at one parser_version
  and one model; a model change moves it with no behaviour change.
- Batching (W1-1 runner vs rec-4024 slice 2a-2): W1-1 step 3 writes per-table batches; the telemetry
  project reports that 2a-2 writes one transaction per request with the sessions batch last (env R7),
  replacing rec-4024 item (1)'s per-turn-flush wording. W2 reconciles W1-1 with it. Either way the drift
  rule stays a monitor (s3 rests on cl.2(b), R6); only the single-producer case would become
  write-decidable.
- Cost/egress: reasoning tokens are already inside tokens_output and are never priced separately (both
  providers' docs, section 1), so the cost verb must not add them twice. The verb reads two narrow
  column sets; the CoT bytes it skips are the egress k3 (b) would add.
- Allow-list (rec-4141): the visibility vocabulary, the counts and the drift share are candidates; model
  ids and every text are not.
- Goodhart register: the deliberation-share rule above is this item's entry.

## 6. Not done here (and why)

- No verb, monitor, test, contract or producer code: REPORT-ONLY by brief.
- No telemetry_observations amendment: the fields are 2a-1's, already merged (#1405); the stale tier ids
  (R4) are T4.2's contract edit.
- No `.claude/settings.json` change for k1, and no LiteLLM or DeepSeek call: a live call spends.
- No rec filed, updated or closed (Decision 67). rec-4024, rec-4026, rec-4028, rec-4029, rec-4135 and
  rec-4141 are named as owners only.
- No T3.20, T2.36, T4.2, T3.3 or T3.4 criterion text or status change.
