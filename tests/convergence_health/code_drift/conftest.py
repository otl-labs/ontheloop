"""Shared fixtures for the code_drift concern-split test package (rec-2709-style decomposition,
PLAN-convergence-health-prod-drift-red). Decision 131 point 2 forbids one tests/** module
importing from another test_* module, so fixtures both halves need live here -- a
`from .conftest import X` is permitted (the final module component is `conftest`, not `test_*`).
"""

from __future__ import annotations

import io
import json
import time
from typing import Any, Optional

import pytest

from scripts.ci.deploy_gate import GateError
from scripts.convergence_health import code_drift


@pytest.fixture(autouse=True)
def _no_github_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every detector test falls back to HEAD (the pre-gate behaviour) unless it supplies a target_resolver."""

    def _unreachable() -> Optional[str]:
        raise GateError("no GitHub API in unit tests")

    monkeypatch.setattr(code_drift, "_default_target_resolver", _unreachable)


class _FakeDeployRecordsS3:
    """Minimal S3 stub for read_deploy_record: same source_git_sha for every function's Key,
    unless a per-function override is supplied. A function absent from both sha_by_function and
    default_sha raises (simulating a missing/never-deployed record -> read_deploy_record's
    NoSuchKey path returns None)."""

    def __init__(self, default_sha: Any = None, sha_by_function: Optional[dict[str, str]] = None) -> None:
        self._default_sha = default_sha
        self._sha_by_function = sha_by_function or {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        function = Key.rsplit("/", 1)[-1].removesuffix(".json")
        sha = self._sha_by_function.get(function, self._default_sha)
        if sha is None:
            raise RuntimeError("NoSuchKey")
        body = json.dumps({"code_sha256": "abc", "source_git_sha": sha}).encode()
        return {"Body": io.BytesIO(body)}


class _RecordingS3:
    """Records every Key it's asked for (populating the caller-supplied `seen_functions` set) and
    returns a canned deploy record. Reconciles the retired monolith's two near-identical NESTED
    `_RecordingS3` classes (ducklake's, unprefixed; prod's, which additionally asserted the
    `deploy-records/prod/` key prefix) into one shared, parameterized fixture -- the original
    pair closed over a per-test-method-local `seen_functions` variable, which a shared module-level
    class cannot do, so both are now constructor arguments instead."""

    def __init__(self, seen_functions: set[str], expected_key_prefix: Optional[str] = None, sha: str = "SHA_OLD") -> None:
        self._seen_functions = seen_functions
        self._expected_key_prefix = expected_key_prefix
        self._sha = sha

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if self._expected_key_prefix is not None:
            assert Key.startswith(self._expected_key_prefix)
        function = Key.rsplit("/", 1)[-1].removesuffix(".json")
        self._seen_functions.add(function)
        body = json.dumps({"code_sha256": "abc", "source_git_sha": self._sha}).encode()
        return {"Body": io.BytesIO(body)}


class GitRunnerStub:
    """Configurable git_runner double for the reachability oracle (Decision 103).

    Both detect_ducklake_code_drift and detect_prod_code_drift now issue THREE distinct kinds of
    call through the same injected runner: (1) `git rev-parse --is-shallow-repository` (the
    shallow-history guard), (2) the ref-less `git log -1 --format=%H -- <pathspecs>` HEAD lookup,
    and (3) `git log -1 --format=%H <sha> -- <pathspecs>` per deploy record's recorded sha. A bare
    `lambda argv: "X"` constant answers all three identically and silently defeats the
    reachability comparison (every record trivially "reaches" HEAD) -- this stub answers each
    kind distinctly and records every call for call-shape assertions.
    """

    def __init__(
        self,
        head_latest: str = "",
        reachable: Optional[dict[str, str]] = None,
        is_shallow: bool = False,
        merge_bases: Optional[dict[tuple[str, str], str]] = None,
        head_age_hours: float = 0.0,
    ) -> None:
        self.head_latest = head_latest
        self.reachable = reachable or {}
        self.is_shallow = is_shallow
        self.merge_bases = merge_bases or {}
        self.head_age_hours = head_age_hours
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:3] == ["git", "rev-parse", "--is-shallow-repository"]:
            return "true" if self.is_shallow else "false"
        if argv[:2] == ["git", "merge-base"]:
            return self.merge_bases.get((argv[2], argv[3]), "")
        if argv[:4] == ["git", "log", "-1", "--format=%ct"]:
            return str(int(time.time() - self.head_age_hours * 3600))
        # Remaining shape: ["git", "log", "-1", "--format=%H", (optional sha), "--", *pathspecs]
        dashdash = argv.index("--")
        ref_tokens = argv[4:dashdash]
        if not ref_tokens:
            return self.head_latest
        return self.reachable.get(ref_tokens[0], "")
