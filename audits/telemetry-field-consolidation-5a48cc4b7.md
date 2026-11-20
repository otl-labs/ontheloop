# Telemetry field consolidation audit

Audited commit 5a48cc4b7 (main, 2026-11-20). The companion file
`audits/telemetry-field-consolidation-5a48cc4b7.yaml` holds a verdict for every stored field and the full findings.

## Is the field set excessive?

No. It is proportionate in size, under-declared in two places and over-declared in one.

The headline count of 174 is contract entries, not columns. 64 are bookkeeping held once and referenced from each
table, 31 are values computed at read with no column, and 27 are declared but never written. That leaves 52 written
facts across four tables, about what a journal of sessions, turns, tool calls, model calls and payloads needs. A
further 29 facts are written as keys inside the `metadata` JSON column and appear in no count.

What matters for an agent reader is what it pays to use the set correctly. Two fresh agents with repository access
only were asked nine questions. All nine answers were correct. The price was high: about 258 KB read over 52 tool
calls to place eight facts, and about 330 KB over 69 tool calls to plan one change. Four of the eight facts could be
answered from contracts alone; the other four needed the producer's source code.

So the cost is not the number of fields. It is:

- **Homes that are in no contract.** 27 of the 28 `metadata` keys that carry a fact are named nowhere, and the link
  from a tool call to its input row exists only as a naming convention in code.
- **Prose weight.** Amendment history is 32% of the 276 KB of contract text. The 27 unwritten fields are 8%.
- **A few declarations that say the wrong thing**, listed below.

| Question | Verdict | Basis |
|---|---|---|
| Overlap among written fields | some redundancy | 4 of 53 fields are copies or recomputable |
| Unwritten fields | unjustified | 15 of 27 should retire or have no live owner |
| Undeclared facts | undeclared | 27 of 28 fact-carrying keys are named in no contract |
| Findability | findable | 6 of 6 trap items correct |
| Size against practice | proportionate | as argued above |
| Timing | consolidate now | for declarations; column removals batch |

Eleven findings follow from this: two high, eight medium, one low. Four are new and seven already have an owner
whose remedy this audit extends or decides.

High:

- **TFC-03. The link from a tool call to its input is undeclared.** The stored input digest sits on the transcript
  row, which points at the model call. A tool call reaches it only through a shared prefix of `external_ref`, a field
  the envelope describes as not a caller-facing key. The link held on 5,328 of 5,328 sampled tool call rows across
  parser versions 7 to 11, which meets rec-5440's precondition. rec-5440 should also amend the envelope sentence and
  the join description, and cover the merge commit, which is stored the same way.
- **TFC-08. `agents.trigger` is wrong for background runs the harness started on its own.** It is set from the spawn
  request, while the producer shapes the run from the spawn result. 8 of 32 distinct sampled sub-agent runs say
  `subagent_sync` and ran in the background. No test case has that shape. No reader uses the column yet.

Medium:

- **TFC-01.** Register all 29 `metadata` keys in a sibling contract with a check against the golden rows, and stop
  writing 11 that repeat stored facts. rec-4733 covers 12 keys, has no check and targets a contract with no byte
  headroom.
- **TFC-02.** A hook's exit code is stored as a `metadata` key while the contract says that key is not there and
  offers an unwritten `exit_code` column with another meaning. Write the existing column instead; no schema change
  is needed.
- **TFC-04.** The withheld content policy empties a tool output's digest on the transcript row but leaves the raw
  digest, size and line count on the tool call row. rec-4985 covers text only. This was not seen in sampled rows; it
  was reproduced on a test case.
- **TFC-05.** `sessions.model_primary`, `agents.model` and `transcripts.model` repeat a model call's model. The
  first two hold the first call's model, not a "primary" one. No reader uses them.
- **TFC-06 and TFC-07.** 14 unwritten fields should retire, and six unwritten fields have no owner that names them.
- **TFC-09.** Every read-derived field is flagged `realized: false`, including 12 that are served.
- **TFC-10.** The planned existence step would not have stopped either incident: both authors knew where the fact
  was, and both proposals began before any plan. The rulings are recorded only on the recommendations; the
  repository still points to a typed-column plan.

Low:

- **TFC-11.** Four descriptions state behaviour the producer does not implement.

## Change table

| Fields | Verdict | Timing | Ever non-null | Retire mode | Cost |
|---|---|---|---|---|---|
| sessions: plan_slug, failure_phase, steps_total, scope_drift_files, coverage_before, coverage_after | retire | batch | no | drop_column | One version bump, DDL and migration Decision per table, with a production deploy hold. Frees 4.9 KB in a contract with no headroom. |
| observations: acceptance_passed, time_lost_seconds, rec_id | retire | batch | no | drop_column | Same. Frees 2.3 KB in a contract with no headroom. |
| agents: provider, findings_count, lambda_request_id | retire | batch | unknown | drop_column | Same. A handler deleted before these tables existed wrote the names. |
| transcripts: token_count, rec_id | retire | batch | no | drop_column | Same. |
| sessions.model_primary | derive at read | batch | yes | n/a | Reword now; drop with the sessions batch. |
| sessions.execution_attempt, agents.model | derive at read | leave | yes | n/a | A column change for no reader. |
| transcripts.model | merge | leave | yes | n/a | Same. |
| 18 metadata keys, among them stop_reason, hook_event, duration_ms, turn_origin, tool_denial_kind, exit_code | declare | now | yes | n/a | Registry and check only: no parser bump and no schema change. exit_code moves to its typed column at the next parser bump. |
| 8 metadata keys: is_error, interrupted, persisted_output, persisted_output_size, agent_id, is_async, status, synthetic | merge | batch | yes (synthetic unknown) | n/a | Stop writing at the next parser bump; is_async and status only after trigger is corrected. |
| metadata response_count, tool_call_count | derive at read | batch | yes | n/a | Stop writing; exact from stored rows on 415 of 415. |
| metadata tool_use_id | retire | batch | yes | stop_writing | The parent link already carries it. |

"Batch" means with the next production column change on that table, or with the next parser version bump for a
metadata key. One parser bump can carry TFC-02, TFC-04, TFC-08 and the key removals. Thirteen other unwritten fields
stay reserved: twelve have a live owner, and `exit_code` gets one through TFC-02.

## Findability probe

The design probe reproduced the incident's setup and the agent did not repeat the mistake: it found the stored
digest and proposed a read-side join with no new field. The lookup probe scored 6 of 6 on trap items and 2 of 2 on
controls, which is a trap-set rate and not a sample of the whole field set. Each is one run of one capable model,
and the result is about cost rather than failure: correct answers at roughly 30 KB of reading per fact.

## What was sampled, and what was not

Sampled: 30,169 stored rows from 12 session trees on 12 days between 2026-10-20 and 2026-11-19, parser versions 7 to
11, including two CI-hosted runs. Every overlap claim above was tested by asking whether the field could be
recomputed exactly from the fields it appears to duplicate.

Not checked on stored rows: withheld or oversize transcript rows, synthetic tool call closes, and any row from a
producer other than `claude_code`, the only one registered. `producer_version` and transcript text are not returned
by the reader verb used. Those cases rest on code and the test corpus. The Langfuse comparison was dropped because
its page could not be fetched.
