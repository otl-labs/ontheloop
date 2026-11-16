"""Census of STS call sites (rec-4510) and the no-minting rule (Decision 256 cl.1): the agent proxy signs every session call
as the agent-platform-session IAM user and refuses sts:AssumeRole, so no session code calls an STS API that returns
credentials. See scripts/aws_identity.py's docstring."""

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


MINTING_ROOTS = ("scripts", "src", "bin", ".claude/hooks")
_MINTING_PATTERNS = (
    re.compile(r"\.(assume_role\w*|assume_root|get_session_token|get_federation_token)\("),
    re.compile(r"\baws\s+sts\s+(assume-role[\w-]*|assume-root|get-session-token|get-federation-token)\b"),
)


def scan_credential_minting(repo_root: Path) -> set[str]:
    """Repo-relative tracked text files (any suffix) under MINTING_ROOTS with a non-comment credential-minting call."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *MINTING_ROOTS],
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
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        if any(not line.lstrip().startswith("#") and any(p.search(line) for p in _MINTING_PATTERNS) for line in lines):
            found.add(rel)
    return found


def test_no_session_code_mints_credentials() -> None:
    assert scan_credential_minting(Path.cwd()) == set(), "session code must not call an STS API that returns credentials"


def test_minting_scanner_flags_a_synthetic_call(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "bin").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "offender.py").write_text("c.assume_role_with_web_identity(RoleArn=r)\n", encoding="utf-8")
    (tmp_path / "scripts" / "commented.py").write_text("# c.assume_role(RoleArn=r)\n", encoding="utf-8")
    (tmp_path / "scripts" / "policy.py").write_text("assume_role_policy = {}\n", encoding="utf-8")
    (tmp_path / "bin" / "shelly").write_text("aws sts get-session-token\n", encoding="utf-8")
    assert scan_credential_minting(tmp_path) == {"scripts/offender.py", "bin/shelly"}
