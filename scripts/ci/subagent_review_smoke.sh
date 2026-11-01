#!/usr/bin/env bash
# Smoke of the real merge-time plan reviewer (rec-4639): runs review.sh with the real pinned CLI on
# the sentinel fixture and fails unless the reviewer returned a verdict and no SENTINEL- string
# reached the digest or either transcript. Prints only the outcome, the stats and STARVED markers
# and, when starved, the transcript's envelope subtype -- never digest or transcript content.
# Dispatched by a maintainer from subagent-review-smoke.yml; CLAUDE_CODE_OAUTH_TOKEN comes from env.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
FIXTURES="${ROOT}/tests/fixtures/subagent_plan_review"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cp "${FIXTURES}/plan.json" "${WORK}/plan.json"
cp "${FIXTURES}/source.tf.txt" "${WORK}/main.tf"
: > "${WORK}/github_output"

(
  cd "$WORK" || exit 1
  WORKSPACE_ROOT="$ROOT" GITHUB_OUTPUT="${WORK}/github_output" \
    CONTEXT_DESCRIPTION="a smoke test of the plan review gate on a fixture plan" \
    bash --noprofile --norc -e -o pipefail "${ROOT}/.github/actions/subagent-plan-review/review.sh"
) > /dev/null 2> "${WORK}/stderr"

OUTCOME=$(grep '^outcome=' "${WORK}/github_output" | tail -n 1 | cut -d= -f2)
echo "SMOKE outcome=${OUTCOME:-none}"
grep -E '^\[SUBAGENT-REVIEW\] (stats|STARVED)' "${WORK}/stderr"

STATUS=0
case "$OUTCOME" in
  proceed | revise) ;;
  *)
    STATUS=1
    LAST="${WORK}/review_retry.json"
    [ -f "$LAST" ] || LAST="${WORK}/review.json"
    python3 - "$LAST" <<'PYEOF'
import json
import sys

subtype = "unknown"
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    text = ""
decoder = json.JSONDecoder()
pos = text.find("{")
while pos != -1:
    try:
        obj, end = decoder.raw_decode(text, pos)
    except ValueError:
        pos = text.find("{", pos + 1)
        continue
    if isinstance(obj, dict) and obj.get("type") == "result":
        subtype = str(obj.get("subtype", "unknown"))[:40]
    pos = text.find("{", end)
print(f"SMOKE transcript subtype={subtype}")
PYEOF
    ;;
esac

for name in plan_digest.txt review.json review_retry.json; do
  if [ -f "${WORK}/${name}" ] && grep -q 'SENTINEL-' "${WORK}/${name}"; then
    echo "SMOKE sentinel leaked into ${name}"
    STATUS=1
  fi
done
exit "$STATUS"
