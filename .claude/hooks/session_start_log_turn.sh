#!/usr/bin/env bash
# SessionStart capture pin hook (PLAN-telemetry-capture-hooks rule HK-2; rule PV-1 of PLAN-telemetry-capture-producer-fixes).
#
# Registered as `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=<ref>` on every
# SessionStart source (startup, resume, clear, compact, fork). Its exit-0 stderr line makes Claude Code record a
# SessionStart hook attachment whose command carries --project-ref=<ref> and whose stderr carries the checkout facts:
# src/turn_capture/transcript.py's project_ref_pin and src/turn_capture/provenance.py read them from that attachment.
# It reads no stdin, writes no file, never refreshes or locks the git index and never starts a capture pass, so a fork is
# first captured only after its own fork attachment exists. Nothing on stdout (that becomes context). Exits 0 on every path.
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || repo=""
sha_re='^[0-9a-f]{40}([0-9a-f]{24})?$'

bounded() {
    if command -v timeout >/dev/null 2>&1; then timeout 5 "$@"; else "$@"; fi
}

sha_or_unknown() {
    if [[ "$1" =~ $sha_re ]]; then printf '%s' "$1"; else printf 'unknown'; fi
}

checkout=unknown
base=unknown
dirty=unknown
if [[ -n "$repo" ]] && command -v git >/dev/null 2>&1; then
    head_sha="$(bounded git -C "$repo" rev-parse HEAD 2>/dev/null)" && checkout="$(sha_or_unknown "$head_sha")"
    base_sha="$(bounded git -C "$repo" merge-base HEAD origin/main 2>/dev/null)" && base="$(sha_or_unknown "$base_sha")"
    if changes="$(bounded git -C "$repo" --no-optional-locks status --porcelain --untracked-files=no 2>/dev/null)"; then
        if [[ -n "$changes" ]]; then dirty=true; else dirty=false; fi
    fi
fi
printf '%s\n' "[session_start_log_turn] capture pin checkout=${checkout} base=${base} dirty=${dirty}" >&2
exit 0
