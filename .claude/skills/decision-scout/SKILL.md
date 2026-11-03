---
name: decision-scout
description: "Use when: scope a proposed plan against active decisions, surface decision-contradiction flags before plan commitment, find related decisions a plan should cite. Mandatory project-alignment gate in /plan; runs in a fresh-context subagent so the bounded triage load stays off the planning agent."
---

## Intent

Given a proposed plan approach, surface every active decision that is relevant -- to cite, to contradict, as related work, or a spirit-alignment concern (SPIRIT, Phase 2) -- plus any roadmap tier_item its Scope touches (ROADMAP, step 8b). Triage is BOUNDED (Phase 1 step 1): the committed `docs/decisions-index.json` projection plus targeted reads of shortlisted entries -- the corpus never loads wholesale; only the structured summary returns.

A BLOCKING gate before `/plan` Step 6. A superficial scan that misses a contradiction is worse than not running -- the parent and human both trust this output to be exhaustive.

### Why a subagent, and why bounded

See `docs/contracts/instruction-architecture.yaml` block `gate_subagent_retrieval` (isolation_rationale, bounded_retrieval).

### Lambda / portal migration contract (T1.5 c1 owns this; rec-2774)

See the same contract's `gate_subagent_retrieval.substrate_swap_contract`; T1.5 c1 owns the swap.

---

## Steps

### Phase 1: Load Inputs (MANDATORY)

1. **Triage source.** Read `docs/decisions-index.json` (provenance/hermeticity rationale: `gate_subagent_retrieval.triage_source_provenance`). Derive **M** = count of `live: true` entries -- the live-file header count, excluding the archive and distinct from the max decision number (numbering gaps). Each live entry's `title`, `triage_excerpt` (<=320 chars, Intent/Problem/Context/Decision fallback order; `triage_excerpt_source` names which; a small terse-historical band carries none), `currency`, and `category_tags` (deterministic artifact/process tags, e.g. `lambda`/`terraform`/`iam`/`secrets`/`deploy`/`egress`) is the Phase 2 signal -- never a whole-file load here.

2. Read the caller's input brief, which is mandated to include:
   - **Intent** (1-2 sentences from `/plan` Step 3 clarification)
   - **Proposed approach** (paragraph from `/plan` Step 3-5 synthesis)
   - **Scope file list** (from `/plan` Step 4 Identify Affected Files)
   - **Verification Tier** (V1 / V2 / V3, from `/plan` Step 5)
   - **Explicitly cited decision IDs** (any decisions the human or planning agent has already referenced)

If any of these inputs are absent in the prompt, return immediately with `Verdict: BLOCK` and a one-line note: "Caller did not provide [missing input]. Re-dispatch after [step] completes."

### Phase 2: Triage Each Decision

3. **Shortlist.** FIRST, mechanically: derive the approach's own tag set (`lambda` = creates/touches a Lambda; `terraform`/`iam` = touches a terraform/ or IAM surface; `secrets` = reads/stores a credential or authenticates externally; `deploy` = ships code/infra by any channel; `egress` = reads Neon/warehouse; `decisions-corpus`/`prose-docs` = edits governance docs) and shortlist EVERY live entry whose `category_tags` intersects it -- a mechanical set-intersection, never per-entry judgment (many decisions govern by ARTIFACT TYPE, not topic keyword). THEN classify every REMAINING entry PROVISIONALLY via title + `triage_excerpt`:
   - **CITE** -- governs the approach; the plan MUST reference it.
   - **CONTRADICT** -- the approach violates an active decision.
   - **RELATED** -- in the neighbourhood, not mandatory.
   - **IRRELEVANT** -- discard. Tag-matched + non-IRRELEVANT + any SPIRIT candidate (step 8) =
     the final SHORTLIST.

4. **Targeted read.** For every shortlisted entry, locate its heading (`rg -n "^## Decision N:" docs/DECISIONS.md`) and Read only that section (offset/limit to the next heading), never the source file wholesale. Confirm or refine the provisional classification against the full text; a SPIRIT quote (step 8) may be any verbatim sentence from that section, not only the excerpt.

5. For each CONTRADICT, attach a severity:
   - **BLOCK** -- the approach cannot proceed without violating the decision. Plan must pivot.
   - **WARN** -- partial conflict; a small refactor or explicit deferral note can resolve.
   - **NOTE** -- edges close to the decision's domain but does not violate it; surface for judgement.

6. **Managed-service-native check (Decision 100 / Decision 75):** flag CONTRADICT WARN when a plan vendors client tooling or custom scripts to replicate a capability the managed service already exposes natively (e.g. pg_dump/pg_restore instead of Neon branching). Fires even if previously recorded as a "human decision" -- that does not exempt it. Decision 100 extends Decision 75 to ALL managed services.

7. **Currency filter.** Branch on the typed `currency`, never status prose. `superseded_compacted` is the ONLY value that demotes to RELATED, noted "superseded by Decision M, awareness only". `superseded_pointer` is NEVER filtered and NEVER severity-reduced: triage exactly as `current`, annotated "read against Decision M". `amended` is treated as `current` for now. No `currency` key = archived, out of scope.

8. **Spirit-alignment overlay (SPIRIT bucket).** SEPARATELY from the literal CONTRADICT triage (steps 3-5), flag an approach that violates the *spirit* of an active decision without contradicting any single clause. Gated hard against noise -- defensive-over-citation applies with FULL force here. Emit a SPIRIT flag ONLY when ALL FOUR hold:
   - (i) **No literal CONTRADICT on the same decision.** A decision appears in at most one of the two lanes; a clause that only describes the ruling's OWN scope (e.g. "no retro-enforcement") is not a standing forward prohibition -- route it to SPIRIT.
   - (ii) **Verbatim-quotable violation.** Quote, VERBATIM, the text the approach works against: `**Intent:**`, a specific Problem/Rationale sentence, OR -- widened for bounded retrieval -- the `**Decision:**` clause (a REQUIRED marker per `docs/contracts/decision-entry.yaml`, the most reliable fallback). Ungrounded means no flag. The terse-historical residual (no quotable marker, no SPIRIT flag) is Decision 160 point 8.
   - (iii) **WARN or NOTE severity only.** Never BLOCK -- that stays reserved for literal contradiction (step 5).
   - (iv) **Capped at 3.** Keep the 3 highest-severity (WARN over NOTE) if more qualify.

8b. **ROADMAP overlay.** Run `bin/venv-python -m scripts.roadmap.scope_projection <Scope file list>` (pure-local, credential-free; contract: `docs/contracts/exit-criteria-ledger.yaml` `roadmap_alignment_projection`). A tier_item matches when a `files_in_scope` entry equals a Scope path or is its directory prefix; K = match count. **CONFLICT**/**CITE** rows MUST name the open criterion id (`T-N.M:cK`) and the plan element it forecloses or duplicates; a row that cannot is **RELATED**. CONFLICT is ALWAYS WARN, never BLOCK. Show at most 5 rows, ordered CONFLICT > CITE > RELATED, live (`in_progress`/`not_started`) items first; append `(showing 5 of K)` when K > 5. K, c and r always report FULL counts. Non-zero exit: `Roadmap items intersected: unavailable (projection failed)` replaces the ROADMAP line, no rows, run excluded from the 40-plan tally.

### Phase 3: Structured Output

9. Return exactly this output. Each section is mandatory even when empty -- render `None` there -- so the planning agent's parsing logic never has to branch.

```
## Decision Scout Report

### Decisions to Cite (CITE)
- **Decision N**: [title] — [one-line reason: which clause governs which part of the approach]

### Contradiction Flags (CONTRADICT)
- **Decision N** [BLOCK | WARN | NOTE]: [title]
  - Contradiction: [specific clause vs specific element of the proposed approach]
  - Suggested resolution: [pivot to X | add explicit deferral note citing Decision N | clarify with human before proceeding]

### Related Decisions (RELATED)
- **Decision N**: [title] — [one-line: in the neighbourhood, mention if discussed]

### Spirit-Alignment Flags (SPIRIT)
- **Decision N** [WARN | NOTE]: [title]
  - Violated intent (verbatim): "[the quote required by gate (ii)]"
  - Divergence: [the specific element of the proposed approach that works against the quoted intent]
  - Suggested resolution: [align to X | add explicit deferral note citing Decision N | clarify with human]

### Roadmap Alignment (ROADMAP)
- **T-N.M** [CONFLICT | CITE | RELATED]: [name] -- [CONFLICT/CITE: criterion id T-N.M:cK + element it forecloses/duplicates; RELATED: one-line note]

[(showing 5 of K) if K > 5]

### Verdict
NO_FLAGS | FLAGS_FOUND | BLOCK

(NO_FLAGS = no CONTRADICT, no SPIRIT flag, no ROADMAP CONFLICT row (CITE-only/RELATED-only ROADMAP rows still count as NO_FLAGS).
FLAGS_FOUND = >=1 CONTRADICT at WARN/NOTE, >=1 SPIRIT flag, or >=1 ROADMAP CONFLICT row (all three lanes are WARN/NOTE-only).
BLOCK = >=1 CONTRADICT at BLOCK severity; pivot before confirming. ROADMAP rows never raise the verdict to BLOCK.)

Decisions triaged: N of M
Roadmap items intersected: K; CONFLICT: c; RELATED: r
(Planning agent: copy both count lines verbatim into the plan's scout context line.)
```

10. Cap total response at ~1,200 words (ROADMAP share: <=150). The planning agent reads this verbatim and surfaces it to the human; bloat dilutes the signal. Revisit: after 40 consecutive plans at `CONFLICT: 0`, ROADMAP demotes to a RELATED-only pointer list -- the intersection and integrity line keep running, only the judgement pass stops (never retire the lane).

---

## Quality Gate (self-check before output)

Verify before returning:
- [ ] You applied the mechanical tag shortlist (step 3) before judgment, and read ONLY shortlisted entries as targeted sections -- never a whole-file load
- [ ] Every CITE and CONTRADICT entry names a decision number that exists in the index
- [ ] Every CONTRADICT entry has both a clause-level citation AND a severity
- [ ] The Verdict line is one of the three exact strings (no variations)
- [ ] "Decisions triaged: N of M" is present and N equals M (the live:true count from Phase 1 step 1)
- [ ] Total length under 1,200 words; ROADMAP section, integrity line and copy instruction present, at most 5 rows shown, none marked BLOCK
- [ ] Every SPIRIT flag carries a verbatim quote per gate (ii), no paraphrase
- [ ] SPIRIT flags number <= 3, and no decision appears in both SPIRIT and CONTRADICT

If any checkbox is false, fix before returning -- the caller cannot self-verify these; a malformed output forces re-dispatch.

---

## Anti-patterns

- **Over-citation (keyword-only or defensive).** Shortlist broadly (step 3), but CITE only when the decision's *clause* governs the approach's *action*; be ruthless -- CITE only when omission would meaningfully harm the plan. Long form: `gate_subagent_retrieval.anti_patterns`.
- **Hedged contradictions.** "This *might* contradict Decision N." -> either it does or it doesn't. If undetermined, mark NOTE and explain what's uncertain. Hedge in the explanation, not the classification.
- **Editing files.** Read-only -- never modify a file, even to fix a typo; file a rec instead.
- **SPIRIT over-citation.** The highest-noise lane -- an unquotable "this feels misaligned" is not a flag. Three well-grounded flags beat ten hedged ones.
