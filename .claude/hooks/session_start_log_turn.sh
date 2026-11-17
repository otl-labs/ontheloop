#!/usr/bin/env bash
# SessionStart capture pin hook (PLAN-telemetry-capture-hooks rule HK-2; rule PV-1 of PLAN-telemetry-capture-producer-fixes).
#
# Registered as `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref=<ref>` on every
# SessionStart source (startup, resume, clear, compact, fork). Its exit-0 stderr line makes Claude Code record a
# SessionStart hook attachment whose stderr carries the checkout facts line (rule PV-1) and, for exactly one well-formed
# --project-ref flag, a second 'capture pin project_ref=<ref>' line (rule PW-1, read by rule P-2): Claude Code 2.1.317
# records a label instead of the command in cloud sessions, so the parser reads the stderr, never the command field.
# src/turn_capture/transcript.py's project_ref_pin and src/turn_capture/provenance.py read them from that attachment.
# It reads no stdin, writes no file, never refreshes or locks the git index and never starts a capture pass, so a fork is
# first captured only after its own fork attachment exists. Nothing on stdout (that becomes context). Exits 0 on every path.
#
# Base resolution (rec-4426): in a fresh cloud container refs/remotes/origin/main is days stale and the history is shallow
# while session_start_deepen_history and session_start_sync_main are still fetching, so the tracking ref is never trusted
# when a live tip is wanted. (1) One read-only `ls-remote origin refs/heads/main` yields the live tip S (no local write,
# no prompt). (2) base is merge-base HEAD S, accepted at once when it equals HEAD or S, else only in a non-shallow
# repository. (3) Only when unresolved, the repository was shallow at the hook's start and GITHUB_ACTIONS is not true,
# it retries once a second until one wall-clock deadline (10 s, or --base-wait-seconds=0..60 for tests), then prints
# unknown, never the tracking ref. (4) Only when the live read fails and the repository was not shallow at the start does
# it use the tracking-ref merge-base. Without a timeout binary no network call is made. Every git call here runs under the
# remaining deadline.
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || repo=""
sha_re='^[0-9a-f]{40}([0-9a-f]{24})?$'

bounded() {
    if command -v timeout >/dev/null 2>&1; then timeout 5 "$@"; else "$@"; fi
}

now_us() {
    local t="${EPOCHREALTIME//[!0-9]/}"
    [[ -n "$t" ]] && printf '%s' "$((10#$t))"
}

wait_s=10
for arg in "$@"; do
    if [[ "$arg" == --base-wait-seconds=* && "${arg#--base-wait-seconds=}" =~ ^([0-9]|[1-5][0-9]|60)$ ]]; then wait_s="${arg#--base-wait-seconds=}"; fi
done

live_seen=""
live_base=""
resolve_live_base() {
    local start_us="" deadline_us=0 n left_us secs out tip mb hd shallow_now
    local -a genv=(env GIT_TERMINAL_PROMPT=0)
    if [[ -z "${GIT_SSH_COMMAND:-}" && -z "$(bounded git -C "$repo" config core.sshCommand 2>/dev/null)" ]]; then
        genv+=("GIT_SSH_COMMAND=ssh -o BatchMode=yes")
    fi
    start_us="$(now_us)"
    if [[ -n "$start_us" ]]; then deadline_us=$((start_us + wait_s * 1000000)); fi
    while :; do
        secs=5
        if [[ -n "$start_us" ]]; then
            n="$(now_us)"
            left_us=$((deadline_us - n))
            if ((left_us <= 0)); then return 1; fi
            secs=$(((left_us + 999999) / 1000000))
            if ((secs > 5)); then secs=5; fi
        fi
        if out="$("${genv[@]}" timeout "$secs" git -C "$repo" ls-remote --exit-code origin refs/heads/main 2>/dev/null)"; then
            tip="${out%%[[:space:]]*}"
            if [[ "$tip" =~ $sha_re ]]; then
                live_seen=1
                if mb="$(timeout "$secs" git -C "$repo" merge-base HEAD "$tip" 2>/dev/null)" && [[ "$mb" =~ $sha_re ]]; then
                    hd="$(timeout "$secs" git -C "$repo" rev-parse HEAD 2>/dev/null)"
                    shallow_now="$(timeout "$secs" git -C "$repo" rev-parse --is-shallow-repository 2>/dev/null)"
                    if [[ "$mb" == "$hd" || "$mb" == "$tip" || "$shallow_now" == false ]]; then
                        live_base="$mb"
                        return 0
                    fi
                fi
            fi
        fi
        if [[ -z "$start_us" || "$was_shallow" != true || "${GITHUB_ACTIONS:-}" == true ]]; then return 1; fi
        n="$(now_us)"
        left_us=$((deadline_us - n))
        if ((left_us <= 0)); then return 1; fi
        if ((left_us >= 1000000)); then sleep 1; else sleep "0.$(printf '%06d' "$left_us")"; fi
    done
}

sha_or_unknown() {
    if [[ "$1" =~ $sha_re ]]; then printf '%s' "$1"; else printf 'unknown'; fi
}

checkout=unknown
base=unknown
dirty=unknown
if [[ -n "$repo" ]] && command -v git >/dev/null 2>&1; then
    head_sha="$(bounded git -C "$repo" rev-parse HEAD 2>/dev/null)" && checkout="$(sha_or_unknown "$head_sha")"
    was_shallow="$(bounded git -C "$repo" rev-parse --is-shallow-repository 2>/dev/null)"
    if command -v timeout >/dev/null 2>&1; then resolve_live_base; fi
    if [[ -n "$live_base" ]]; then
        base="$(sha_or_unknown "$live_base")"
    elif [[ -z "$live_seen" && "$was_shallow" != true ]]; then
        base_sha="$(bounded git -C "$repo" merge-base HEAD origin/main 2>/dev/null)" && base="$(sha_or_unknown "$base_sha")"
    fi
    if changes="$(bounded git -C "$repo" --no-optional-locks status --porcelain --untracked-files=no 2>/dev/null)"; then
        if [[ -n "$changes" ]]; then dirty=true; else dirty=false; fi
    fi
fi
ref_re='^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$'
ref_line=""
refs=()
for arg in "$@"; do
    if [[ "$arg" == --project-ref=* ]]; then refs+=("${arg#--project-ref=}"); fi
done
if [[ "${#refs[@]}" -eq 1 && "${refs[0]}" =~ $ref_re ]]; then
    ref_line="[session_start_log_turn] capture pin project_ref=${refs[0]}"
fi
{
    printf '%s\n' "[session_start_log_turn] capture pin checkout=${checkout} base=${base} dirty=${dirty}"
    if [[ -n "$ref_line" ]]; then printf '%s\n' "$ref_line"; fi
} >&2
exit 0
