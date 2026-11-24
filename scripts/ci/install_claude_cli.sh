#!/usr/bin/env bash
# The one claude CLI installer for CI (PLAN-claude-cli-seam-contract, rec-5509). Usage: install_claude_cli.sh <repo_root>
#
# The version lives only in <repo_root>/config/claude-cli-version. A missing, unreadable or malformed pin prints one
# stderr line and exits 2 before any npm call, so no site can fall through to an unpinned install. A claude already on
# PATH is kept: its path is printed and it is never invoked. Otherwise Decision 74's pinned sudo install runs and the
# installed `claude --version` must name the pin, else exit 1.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "install_claude_cli.sh: usage: install_claude_cli.sh <repo_root>" >&2
    exit 2
fi

PIN_FILE="$1/config/claude-cli-version"
if [ ! -e "$PIN_FILE" ]; then
    echo "install_claude_cli.sh: pin file missing: $PIN_FILE" >&2
    exit 2
fi
if ! VERSION="$(cat -- "$PIN_FILE" 2>/dev/null)"; then
    echo "install_claude_cli.sh: pin file unreadable: $PIN_FILE" >&2
    exit 2
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "install_claude_cli.sh: pin file malformed (want one bare x.y.z line): $PIN_FILE" >&2
    exit 2
fi

if EXISTING="$(command -v claude)"; then
    echo "$EXISTING"
    exit 0
fi

sudo npm install -g "@anthropic-ai/claude-code@${VERSION}" --omit=dev --omit=optional && sudo npm cache clean --force

INSTALLED="$(claude --version 2>&1 || true)"
if [[ "$INSTALLED" != *"$VERSION"* ]]; then
    echo "install_claude_cli.sh: installed claude --version '${INSTALLED}' does not name the pin ${VERSION}" >&2
    exit 1
fi
echo "$INSTALLED"
