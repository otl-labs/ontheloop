#!/usr/bin/env bash
cat >/dev/null 2>&1 || true
printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[hook_canary] repo hooks loaded"}}'
exit 0
