# Executor Prompts

This directory contains **prompt templates** used by `scripts/execute_recommendation.py`.
Templates contain `{placeholder}` variables that are filled at runtime with recommendation
data, file contents, and plan text. They serve as the **user message** in each CLI call.

**Stable rules and constraints** live in `config/agent/executor/instructions/executor-*.instructions.md`.
The executor CLI is retired (Decision 246); auto-loading these as system context was the
historical mechanism, not a live one. Do not duplicate rules here.

## Intended Workflow

The executor runs an autonomous loop: **one recommendation at a time, merged to main before the next starts.**

```
main (clean) → executor picks rec → creates agent/{rec-id} branch
  → plan → critique → implement steps → validate → commit per step
  → push → create PR → wait for CI → squash-merge → return to main → pull
  → repeat with next eligible rec
```

**Key rules:**
- Each rec runs on its own branch (`agent/{rec-id}`) created from main
- After successful merge, return to main and pull before the next rec
- Use `--batch` to process multiple eligible recs in dependency order (sequential, one-at-a-time)
- A stale checkpoint means a previous run was interrupted — use `--restart` to clear it, or `--resume` to pick up where it left off

## CLI Reference

```bash
# Full autonomous run (plan → critique → implement → CI → merge)
python -m scripts.execute_recommendation rec-047

# Plan only (no implementation)
python -m scripts.execute_recommendation rec-047 --dry-run

# Skip critique loop (for testing or when plan is known-good)
python -m scripts.execute_recommendation rec-047 --skip-critique

# Stop after PR creation (no CI wait or merge)
python -m scripts.execute_recommendation rec-047 --no-merge

# Clear stale checkpoint and reset rec status to 'open'
python -m scripts.execute_recommendation rec-047 --restart

# Resume from checkpoint after a manual fix mid-step
python -m scripts.execute_recommendation rec-047 --resume

# Process all eligible recs in dependency order
python -m scripts.execute_recommendation --batch --max-recs 5
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `COPILOT_MODEL_PLANNING` | CLI default | Model for plan/critique/refine |
| `COPILOT_MODEL_EXECUTION` | CLI default | Model for implementation steps |
| `PLAN_MAX_REVISIONS` | 3 | Max critique-refine iterations |
| `CI_WAIT_TIMEOUT_SECS` | 600 | Max seconds to wait for CI |
| `SKIP_CODE_REVIEW` | false | Skip the post-impl code review gate |

## Files

| File | Purpose | Placeholders |
|------|---------|--------------|
| `planning.prompt.md` | Initial plan generation | `{rec_id}`, `{title}`, `{context}`, `{file}`, `{acceptance}`, `{dependencies}`, `{effort}`, `{file_content_section}`, `{test_content_section}` |
| `critique.prompt.md` | Plan review and approval | `{plan_text}` |
| `refine.prompt.md` | Plan refinement after critique | `{plan_text}`, `{critique_text}` |
| `implement-step.prompt.md` | Single step implementation | `{step_text}`, `{rec_id}`, `{step_n}`, `{total_steps}`, `{file_content_section}`, `{test_content_section}`, `{pattern_content_section}` |
| `code-review.prompt.md` | Pre-merge focused code review gate | `{rec_id}`, `{title}`, `{acceptance}`, `{plan_steps}`, `{changed_files}`, `{files_block}` |

## Format

Prompts use Python's `.format()` string substitution. Placeholders are wrapped in `{curly_braces}`.

## Iteration

To improve prompts:
1. Edit the `.prompt.md` file directly
2. Test with `python -m scripts.execute_recommendation rec-test --dry-run`
3. Commit changes with descriptive message

Git diffs will clearly show prompt changes separate from code logic.
