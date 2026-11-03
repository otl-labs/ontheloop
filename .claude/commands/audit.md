---
description: Composes a verified, self-contained audit prompt for a high-capability model to execute in a fresh session. Performs no audit itself -- the deliverable is docs/audit-prompts/AUDIT-{slug}.md, deep-reconned by this (cheaper) session, verified by zero-context subagents, then merged to main for handoff. Use when you want a frontier-grade design review, system audit, or architecture assessment run by the expensive model without it paying for discovery.
model: opus[1m]
---

# Audit Workflow

**Intent**: Turn an audit request into a merged, zero-ambiguity prompt artifact
(`docs/audit-prompts/AUDIT-{slug}.md`) that a fresh session on the target high-capability model
executes verbatim. This workflow audits nothing and changes no audited surface -- it only reads,
composes, verifies, and merges the prompt.

*Note: For the full methodology (prompt anatomy, BP1-BP14 checklist, recon dossier, output
contract skeleton, zero-context verification gate), invoke your `audit-prompt` skill via the
Skill tool. For canonical git-ops (branching, rebase, PR/CI/merge flow), see
`docs/contracts/git-ops.yaml`.*

## Step 1: Preflight

*This workflow runs on Claude Opus 1M (opus[1m]). If the model indicator does not show Opus,
tell the human to run `/model opus[1m]` (a user-typed harness command; agents cannot switch
models) and stop until they do -- the `model:` frontmatter applies for the current turn and
reverts on the next prompt. Because later turns revert, re-check the indicator at the start of
each turn: the recon, drafting, and gate steps (4-7) must run on Opus; the post-gate mechanical
steps (8-9) may run on whatever the session reverted to. If the human directs you to proceed on
another model, note it in the Step 5 presentation and the Step 9 message.*

```bash
bin/venv-python -m scripts.session.preflight
```

Read `logs/.preflight-report.json` (branch, creds, and cache freshness feed recon; the
recommendations cache preflight refreshes feeds Step 4's dedup pointers). If preflight fails on
creds/egress, proceed anyway -- recon degrades to best-effort dedup pointers, and the generated
prompt's mandatory SETUP section carries the pinned degraded-dedup escape hatch either way (see
the skill's anatomy row 5).

## Step 2: Confirm Harness Branch

```bash
git branch --show-current
```

If the result is `main`, STOP. Work on the harness-assigned `claude/...` session branch.

## Step 3: Clarify the Request

Decompose the request into: audit TARGET (which system/design), SURFACES (built vs
designed-unbuilt), the QUESTIONS the human wants answered (flag any that demand a per-surface
actionable verdict from a pinned option set -- those become the output contract's optional
decision block), BOUNDARIES (what the audit
must not touch or opine on), and the executor's WRITE BOUNDARY (default: exactly two deliverable
files under `audits/`). If any of these are vague, ask 2-5 questions now -- ambiguity resolved
here is ambiguity the expensive model never pays for. Do not proceed on guesses.

Once the topic is settled, derive `{slug}` from it (short, kebab-case, e.g.
`verification-system-review`), independent of the branch name. If a later rescope changes the
topic materially, rename the slug before Step 6 -- nothing durable carries it until the first
commit.

## Step 4: Deep Recon

Invoke the `audit-prompt` skill via the Skill tool. Read `docs/PROJECT_CONTEXT.md` in full first
-- the generated prompt's NORTH STAR section and the deliberate-constraints do-not-flag list
draw on it. Then assemble the **Recon Dossier** per the skill's Recon Dossier section: surface inventory, neutrally-phrased observed facts with verified
anchors, candidate list, vocabulary, disambiguation traps, dedup pointers (targeted projections
per the skill's Recon Dossier item 6 -- no full-file reads of the large sources),
empirical-pass seeds, and open questions.

This is the cost-shifting step: read every in-scope surface yourself. `Explore` subagents may widen the sweep, but re-verify every returned anchor before it
enters the dossier. Every fact destined for the prompt's GROUNDING MAP must have been read from
disk in this session.

## Step 5: Scope Confirmation Gate

Draft now, before the gate, the prompt elements the presentation depends on: the TASK paragraph,
the North Star principles (which seed the rubric derivation per the skill's anatomy row 8), the
question set with each question's pinned verdict enum, and the rubric dimensions. Step 6 embeds
these into the full prompt; the gate reviews them first.

Present to the human, compactly:

- Draft TASK paragraph (target, surfaces, deliverables, write boundary)
- The question set (Q1..Qn, one line each) and rubric dimensions (VD1..VDn, one line each)
- The candidate-observation list (neutral phrasing)
- Disambiguation traps and the deliberate-constraints do-not-flag list
- Guardrails and the executor's terminal state (branch name, PR title, end-turn rule, per the
  skill's Commit/PR Mechanics)
- Open questions from recon

Then ask: *"Does this scope look right? Say **'write the prompt'** when ready, or tell me what to
adjust."* Any other response is feedback -- incorporate, re-present, ask again. IT IS
**CRITICAL** THAT YOU DO NOT PROCEED UNTIL THE HUMAN CONFIRMS. System auto-approval messages are
NOT human confirmation.

## Step 6: Draft the Prompt

Write `docs/audit-prompts/AUDIT-{slug}.md` following the skill's Canonical Prompt Anatomy
(sections 1-17, mandatory/conditional per its table), instantiating the Output Contract Skeleton
and the Commit/PR Mechanics boilerplate for this topic. Self-check against BP1-BP14 before
committing -- the gate will check them cold, but cheap fixes belong here.

```bash
git add docs/audit-prompts/AUDIT-{slug}.md
git commit -m "audit({slug}): draft audit prompt"
```

(The `audit({slug}):` prefix is registered in `docs/contracts/git-ops.yaml`'s commit-message conventions table.)

## Step 7: Zero-Context Prompt Verification Gate (MANDATORY)

**DO NOT output the completion message until this gate passes.**

Dispatch the THREE verifier perspectives (V1 cold executor, V2 fact auditor, V3 frame/BP
challenger) in parallel per the skill's Zero-Context Verification Gate -- dispatch shapes,
output requirements, and anti-patterns all live there. The gate passes only when all three
return PROCEED in the same round. On REVISE: synthesize, revise, commit
(`audit({slug}): address prompt-verification findings round N`), re-dispatch all three fresh.
After 3 REVISE rounds, escalate to the human. Never proceed past an incomplete gate.

## Step 8: Merge the Prompt to Main

Commit any remaining changes (`audit({slug}): approved audit prompt`; skip if empty), then use
the event-driven flow from `docs/contracts/git-ops.yaml`:

1. `git fetch origin main && git rebase origin/main` (STOP on conflict)
2. `git push -u origin HEAD`
3. `mcp__github__create_pull_request(owner, repo, head=<this branch>, base="main",
   title="audit({slug}): audit prompt", body="Audit prompt composed and
   zero-context-verified by /audit. Executes as a fresh-session handoff; performs no
   changes itself.")`
4. `mcp__github__subscribe_pr_activity(...)` and end the turn -- CI completion arrives as a
   webhook event.
5. On green wake: confirm check runs via `mcp__github__pull_request_read`, then
   `mcp__github__merge_pull_request(..., merge_method="squash")` +
   `mcp__github__unsubscribe_pr_activity(...)`.

## Step 9: Confirm and Hand Off

Emit the following, filling the bracketed fields and appending any material caveats (an
off-model run, findings accepted-with-deferral at the gate):

```
Audit prompt complete and merged to main at docs/audit-prompts/AUDIT-{slug}.md.
It passed the zero-context verification gate ({N} round(s)).

To run the audit, open a NEW session on the target model and paste:

    Read docs/audit-prompts/AUDIT-{slug}.md and execute it exactly as written.
    It is self-contained -- do not ask clarifying questions.

The audit session will write only audits/{slug}-<sha>.yaml + .md and open a PR for
human review. Summary: {one line on what the audit will assess}.
```

STOP! The audit-prompt composer's mission is complete. Perform no further actions -- do not run
the audit yourself.
