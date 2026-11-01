"""Census of STS call sites (rec-4510): the agent proxy re-signs calls to the project region's STS host as the IAM user,
so an identity-dependent or role-chaining STS call must not target that host. See scripts/aws_identity.py's docstring."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOTS = ("scripts", "src", "bin", ".claude/hooks", ".github/actions")
SCANNED_SUFFIXES = (".py", ".sh", ".yml", ".yaml")

_STS_PATTERNS = (
    re.compile(r"""client\(\s*["']sts["']"""),
    re.compile(r"""["']sts["']"""),
    re.compile(r"""\baws\s+sts\b"""),
)

IDENTITY_READING = "identity-reading via the helper"
EXIT_CODE_ONLY = "exit-code-only liveness"
ACCOUNT_ID_ONLY = "account-id-only"

ALLOWED: dict[str, str] = {
    "scripts/aws_identity.py": IDENTITY_READING,
    "scripts/checks/_scaffolding.py": EXIT_CODE_ONLY,
    "scripts/preflight/aws_infra.py": EXIT_CODE_ONLY,
    "scripts/sync/ops.py": EXIT_CODE_ONLY,
    "scripts/ci/iam_simulate.py": ACCOUNT_ID_ONLY,
    "scripts/ci/security_detector_probe.py": ACCOUNT_ID_ONLY,
}

_RULE_POINTER = "see the rule in scripts/aws_identity.py's docstring"


def scan_sts_call_sites(repo_root: Path) -> set[str]:
    """Return repo-relative paths under ROOTS whose non-comment lines use STS (new, uncommitted files included)."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *ROOTS],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.splitlines()
    found: set[str] = set()
    for rel in listing:
        path = repo_root / rel
        if path.suffix not in SCANNED_SUFFIXES or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if any(pattern.search(line) for pattern in _STS_PATTERNS):
                found.add(rel)
                break
    return found


def test_sts_usage_matches_classed_allow_list() -> None:
    found = scan_sts_call_sites(Path.cwd())
    new = sorted(found - set(ALLOWED))
    stale = sorted(set(ALLOWED) - found)
    assert not new, f"STS usage in files outside the classed allow-list: {new}; {_RULE_POINTER}"
    assert not stale, f"allow-listed files no longer using STS (remove the entry): {stale}"


def test_allow_list_entries_name_a_known_class() -> None:
    assert set(ALLOWED.values()) <= {IDENTITY_READING, EXIT_CODE_ONLY, ACCOUNT_ID_ONLY}


def test_scanner_names_an_extra_sts_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "scripts" / "commented.py").write_text('# boto3.client("sts")\n', encoding="utf-8")
    (tmp_path / "scripts" / "offender.py").write_text('import boto3\nc = boto3.client("sts")\n', encoding="utf-8")
    assert scan_sts_call_sites(tmp_path) == {"scripts/offender.py"}
