#!/usr/bin/env bash
# Locates the ontheloop checkout for the plugin gate (docs/plans/PLAN-plugin-hooks-locate-checkout.yaml).
# No argument: print the checkout's physical path and exit 0, or print nothing and exit 1. Nothing on stderr.
# --notice SessionStart|UserPromptSubmit: print the trace for an unusable layout; exit 0 on every path.
# Run as `bash FILE` (mode bits vary in the synced copy). Never reads stdin, PWD or recurses below direct children.

MANIFEST_NAME='"name": "ontheloop"'
RESULT=""
COUNT=0

is_candidate() {
    local manifest="$1/.claude/.claude-plugin/plugin.json"
    [ -f "$manifest" ] && grep -qsF -- "$MANIFEST_NAME" "$manifest" 2>/dev/null
}

physical() {
    (cd -P -- "$1" 2>/dev/null && pwd -P) 2>/dev/null
}

locate() {
    local base="${CLAUDE_PROJECT_DIR:-}" d p seen q dup git_dirs=0 pick=""
    local -a found=()
    RESULT=""
    COUNT=0
    case "$base" in
    /*) ;;
    *) return 1 ;;
    esac
    if is_candidate "$base"; then
        p="$(physical "$base")" || p=""
        if [ -n "$p" ]; then
            RESULT="$p"
            return 0
        fi
    fi
    shopt -s nullglob
    for d in "${base%/}"/*/; do
        d="${d%/}"
        is_candidate "$d" || continue
        p="$(physical "$d")" || continue
        [ -n "$p" ] || continue
        dup=0
        for seen in "${found[@]}"; do
            if [ "$seen" = "$p" ]; then
                dup=1
                break
            fi
        done
        [ "$dup" = 1 ] || found+=("$p")
    done
    shopt -u nullglob
    COUNT="${#found[@]}"
    if [ "$COUNT" = 1 ]; then
        RESULT="${found[0]}"
        return 0
    fi
    [ "$COUNT" -gt 1 ] || return 1
    for q in "${found[@]}"; do
        if [ -d "$q/.git" ]; then
            git_dirs=$((git_dirs + 1))
            pick="$q"
        fi
    done
    if [ "$git_dirs" = 1 ]; then
        RESULT="$pick"
        return 0
    fi
    return 1
}

notice() {
    locate && return 0
    case "$1" in
    SessionStart)
        printf '%s\n' "[plugin_hooks] no ontheloop checkout (candidates=$COUNT)" >&2
        ;;
    UserPromptSubmit)
        if [ "$COUNT" -gt 1 ]; then
            printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[plugin_hooks] repo hooks not loaded: '"$COUNT"' ontheloop checkouts under the session start directory, so the ontheloop plugin ran no hook (no guards, no capture). Follow the AGENTS.md hook canary rule."}}'
        fi
        ;;
    esac
    return 0
}

if [ "${1:-}" = "--notice" ]; then
    notice "${2:-}"
    exit 0
fi

if locate 2>/dev/null; then
    printf '%s\n' "$RESULT"
    exit 0
fi
exit 1
