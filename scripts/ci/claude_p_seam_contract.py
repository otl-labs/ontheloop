#!/usr/bin/env python3
"""Contract probe for the seam CI RCA rests on: claude CLI -> scripts/ci/claude_p_retry.sh -> FILED parser (rec-5509).

Default mode pipes one fixed prompt through the real wrapper with the first `claude` on PATH, from an empty temp
directory (so no repo CLAUDE.md can steer the reply), and checks each contract CI RCA depends on, printing one FAIL
line per broken contract or one PASS line. tests/ci/test_claude_p_seam_contract.py runs it in --pre against golden
envelopes replayed by a stub CLI.

--capture <dir> is the pin-bump live check: it requires `claude --version` to name the pin in
config/claude-cli-version, runs the same probe against that real CLI and, only on PASS, writes scrubbed goldens
(success.stdout.json, success.stderr.txt, version.txt). A failing CLI gets no goldens. Run it only in an
operator-owned Claude Code session (Decision 206 cl.3); the probe never reads or forwards a credential. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.ci_rca.filing import extract_filed_recs

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "config" / "claude-cli-version"
WRAPPER = ROOT / "scripts" / "ci" / "claude_p_retry.sh"
PRECOMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
GOLDEN_ROOT = ROOT / "tests" / "fixtures" / "claude_cli_envelopes"
SEAM_FILES = ("config/claude-cli-version", "scripts/ci/claude_p_retry.sh", "scripts/ci/install_claude_cli.sh")
GOLDEN_FILES = ("success.stdout.json", "success.stderr.txt", "version.txt")
PROMPT = "Reply with exactly this line and nothing else: FILED: rec-0 seam_contract"
EXPECTED = [("rec-0", "seam_contract")]
CLAUDE_ARGS = ("--output-format", "json", "--model", "haiku", "--max-turns", "1")
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_ID_KEYS = frozenset({"session_id", "uuid"})
_PRIVATE_KEY = re.compile(r"account|email|org|user", re.IGNORECASE)
_NEVER_COMMIT_HOOK = re.compile(r"id:\s*block-sensitive-identifiers\b.*?\n\s*entry:\s*'((?:[^']|'')*)'", re.DOTALL)


def run_probe(out: Path) -> tuple[list[str], str]:
    """Run the prompt through the real wrapper into ``out``; return (FAIL lines, the CLI's working directory)."""
    with tempfile.TemporaryDirectory(prefix="seam-cwd-") as cwd:
        proc = subprocess.run(
            ["bash", str(WRAPPER), str(out), "--", *CLAUDE_ARGS],
            input=PROMPT,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    return check_contract(out, proc.returncode), cwd


def check_contract(out: Path, wrapper_exit: int) -> list[str]:
    failures: list[str] = []
    if wrapper_exit != 0:
        failures.append(f"FAIL wrapper exit: claude_p_retry.sh exited {wrapper_exit}, want 0")
    raw = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    try:
        envelope = json.loads(raw)
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict):
        failures.append("FAIL envelope: the output file is not exactly one JSON object (stdout-only contract, #2311 shape)")
    elif envelope.get("is_error") is not False or not isinstance(envelope.get("result"), str):
        failures.append("FAIL envelope: is_error is not false or result is not a string")
    if not Path(f"{out}.stderr").is_file():
        failures.append("FAIL stderr split: the wrapper wrote no <out>.stderr file")
    found = extract_filed_recs(out)
    if found != EXPECTED:
        cause = "envelope ok, marker missing (model reply)" if isinstance(envelope, dict) else "envelope broken"
        failures.append(f"FAIL marker: {cause}; parser returned {found!r}, want {EXPECTED!r}")
    return failures


def _scrub_text(text: str, prefixes: list[tuple[str, str]]) -> str:
    for prefix, token in prefixes:
        text = text.replace(prefix, token)
    return text


def _scrub_value(value: Any, prefixes: list[tuple[str, str]], *, private: bool = False) -> Any:
    if isinstance(value, dict):
        return {key: _scrub_item(key, item, prefixes, private) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item, prefixes, private=private) for item in value]
    if private:
        return "<redacted>"
    return _scrub_text(value, prefixes) if isinstance(value, str) else value


def _scrub_item(key: str, item: Any, prefixes: list[tuple[str, str]], private: bool) -> Any:
    if key in _ID_KEYS and isinstance(item, str):
        return ZERO_UUID
    return _scrub_value(item, prefixes, private=private or bool(_PRIVATE_KEY.search(key)))


def scrub_capture(stdout: str, stderr: str, workdirs: list[str], home: str) -> tuple[str, str]:
    """Scrub a captured run for a public repo: zero ids, path anchors, redacted account-like fields."""
    pairs = [(path, "<WORKDIR>") for path in workdirs] + [(home, "<HOME>")]
    prefixes = sorted((pair for pair in pairs if len(Path(pair[0]).parts) > 1), key=lambda pair: len(pair[0]), reverse=True)
    envelope = _scrub_value(json.loads(stdout), prefixes)
    tail = stdout[len(stdout.rstrip()) :]
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + tail, _scrub_text(stderr, prefixes)


def never_commit_pattern() -> re.Pattern[str] | None:
    match = _NEVER_COMMIT_HOOK.search(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    return re.compile(match.group(1).replace("''", "'")) if match else None


def capture(target: Path, out: Path) -> int:
    pin = PIN_FILE.read_text(encoding="utf-8").strip()
    try:
        version = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, encoding="utf-8", check=False
        ).stdout.strip()
    except FileNotFoundError:
        version = ""
    if pin not in version:
        print(f"FAIL capture: claude --version is {version!r}, not the pin {pin}; put the CLI at the pin first on PATH")
        return 2
    failures, cwd = run_probe(out)
    if failures:
        print("\n".join(failures))
        return 1
    stdout, stderr = scrub_capture(
        out.read_text(encoding="utf-8"),
        Path(f"{out}.stderr").read_text(encoding="utf-8"),
        [cwd, os.path.realpath(cwd)],
        os.environ.get("HOME", ""),
    )
    pattern = never_commit_pattern()
    if pattern is None or any(pattern.search(text) for text in (stdout, stderr, version)):
        print("FAIL capture: the scrubbed capture matches a never-commit shape (or the hook is unreadable); wrote nothing")
        return 2
    target.mkdir(parents=True, exist_ok=True)
    for name, text in zip(GOLDEN_FILES, (stdout, stderr, version + "\n"), strict=True):
        (target / name).write_text(text, encoding="utf-8")
    print(f"PASS capture: wrote {', '.join(GOLDEN_FILES)} to {target} (claude {version})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, help="write scrubbed goldens here, only on PASS against the pinned CLI")
    parser.add_argument("--out", type=Path, help="wrapper output file (default: a temp path)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="seam-out-") as scratch:
        out = args.out or Path(scratch) / "claude-seam.json"
        if args.capture is not None:
            return capture(args.capture, out)
        failures, _cwd = run_probe(out)
        if failures:
            print("\n".join(failures))
            return 1
        print(f"PASS claude seam contract: one JSON envelope, stderr split, FILED marker parsed {EXPECTED[0]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
