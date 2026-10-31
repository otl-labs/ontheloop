#!/usr/bin/env bash
# SessionStart hook: complete a shallow CC-web clone to a full clone (GITOPS-03).
#
# THE WRONG-ANSWER MECHANISM
# CC-web containers clone the repository shallow (~50 commits; measured this
# session). A shallow clone's oldest commit is a GRAFT: git treats it as
# having no parent, so it looks like the graft commit created every file it
# can see, even files that were really last touched long before the shallow
# boundary. Worked example, ground-truthed against the GitHub API on
# 2026-08-18: docs/SESSION_LOG.md was truly last touched by b89c9a4 on
# 2026-07-01 (PR #373); the shallow clone answered a3393db, 2026-08-11, with
# a fabricated +666/-0 diffstat -- wrong commit, wrong date by six weeks,
# invented diffstat, and no error raised anywhere. `git log -- <path>` and
# `git blame` both lie identically for any path unchanged since before the
# graft. This hook does not fix callers that trust these answers -- see
# DEGRADED MODE below and the follow-on recommendation this plan files for
# scripts/convergence_health/record.py's silent return-0.
#
# CONSUMERS AND THE DEPTH EACH NEEDS
#   - scripts/convergence_health/record.py:101 count_unapplied_tf_commits runs
#     `git log <record_sha>..HEAD -- terraform/personal/` and returns a
#     silent 0 on error -- indistinguishable from a true 0 at assess.py:95.
#   - scripts/roadmap/plan_audit.py:126 _verify_rec_in_git greps
#     `git log origin/main --grep=rec-NNNN` and false-negatives any rec
#     merged before the shallow boundary.
#   - ad-hoc agent `git log` / `git blame` needs full history to answer
#     correctly at all; there is no bounded depth that is safe for this use.
#
# WHY FULL --unshallow, NOT A BOUNDED --deepen=N
# Measured on this container: `git fetch --unshallow --quiet origin main`
# took 2.9s, grew .git from 13MB to 21MB, pulled all 902 commits, and left
# .git/shallow absent. A bounded deepen only relocates the wrong-answer
# boundary for essentially no time saved, and the deepest consumer above
# (ad-hoc git log/blame) needs full history since recs and commits span the
# whole repo. Re-measure before adopting a bound if the repo grows an order
# of magnitude. The narrow `origin main` refspec (not the bare form) avoids
# creating ~40 extra remote-tracking refs.
#
# PARALLEL SESSIONSTART HOOKS -- WHY NO LOCK-CONTENTION HANDLING IS NEEDED
# Claude Code runs every hook in a SessionStart matcher block IN PARALLEL,
# not in sequence, so placement in .claude/settings.json next to
# session_start_sync_main.sh is a readability grouping only -- it does not
# sequence the two hooks. With this hook registered the block holds seven
# hooks, four of which touch repo-scoped git: this one, plus these three --
# session_start_sync_main.sh (plain `git fetch origin main`
# + `git branch -f`), session_start_github_readiness.sh (via
# scripts/session/github_readiness.py: `git branch --show-current`,
# `git fetch --dry-run origin main`, `git push --dry-run`), and
# session_start_precommit.sh (`pre-commit install`). Measured on git 2.43.0:
# none of the three acquires .git/shallow.lock -- a plain fetch and a
# --dry-run fetch both return rc=0 with that lock held, and neither rewrites
# .git/shallow. --unshallow is therefore the sole shallow.lock acquirer in
# the block, so a single attempt is safe with no retry/lock machinery.
#
# DEGRADED MODE
# A failed, timed-out, or unreachable-remote fetch leaves every wrong answer
# above live for the rest of the session -- this hook is advisory and never
# retries mid-session. `.git/shallow`'s presence is the machine-detectable
# signal a consumer can check to tell "0 because none" from "0 because I
# cannot see the history" (scripts/convergence_health/record.py does not do
# this yet; that gap is filed as a follow-on recommendation, not fixed here,
# per Decision 55 -- fixing the input does not fix an error-swallowing
# consumer, and this hook must not mask that). The next session's hook
# invocation retries at essentially no cost (2.9s measured).
#
# CI IS UNAFFECTED
# SessionStart hooks do not run in GitHub Actions. ci.yml's main-validate and
# main-canary.yml already check out fetch-depth 0 (full history -- a dated
# correction on Decision 159 clause 1, D177 post-lock dialect); ci-rca.yml
# also checks out fetch-depth 0 (Decision 142). No workflow checkout is
# touched by this change.
#
# Advisory, fail-open, single-attempt: mirrors session_start_sync_main.sh --
# set -uo pipefail, exit 0 on every branch, loud "WARNING: ... (non-fatal)"
# per failure mode rather than a silent bare exit.

set -uo pipefail

log() { printf '[session_start_deepen_history] %s\n' "$*"; }

# Single-fetch wall-clock bound. Covers the one --unshallow attempt below (no
# retry loop -- see the parallel-hooks note above). Measured fetch is 2.9s,
# so the 120s default is ~40x headroom against the harness's 600s SessionStart
# ceiling.
DEEPEN_HISTORY_TIMEOUT_S="${DEEPEN_HISTORY_TIMEOUT_S:-120}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 0

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "not a git repository; skipping"
    exit 0
fi

# A bare --unshallow on an already-complete repo is a fatal git error
# (rc=128, "does not make sense"), so this guard must hold before the fetch
# below runs. Any failure to determine shallowness (empty/non-"true" output)
# also falls into this no-op branch -- refusing to guess is the safe default
# for a command with a fatal failure mode on the wrong input.
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" != "true" ]; then
    log "clone already complete; no-op"
    exit 0
fi

before_count="$(git rev-list --count HEAD 2>/dev/null || echo '?')"

fetch_err="$(timeout "$DEEPEN_HISTORY_TIMEOUT_S" git fetch --unshallow --quiet origin main 2>&1)"
fetch_rc=$?
if [ "$fetch_rc" -ne 0 ]; then
    log "WARNING: git fetch --unshallow origin main failed or timed out after ${DEEPEN_HISTORY_TIMEOUT_S}s (non-fatal, rc=${fetch_rc}): ${fetch_err}"
    log "WARNING: git log/git blame answers for any path unchanged since before the shallow boundary may be silently wrong (wrong commit, wrong date, fabricated diffstat) until a future session retries"
    exit 0
fi

after_count="$(git rev-list --count HEAD 2>/dev/null || echo '?')"

if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    log "WARNING: fetch reported success but repository still reports shallow (non-fatal) -- history may remain incomplete this session"
    exit 0
fi

log "clone completed: ${before_count} -> ${after_count} commits"
exit 0
