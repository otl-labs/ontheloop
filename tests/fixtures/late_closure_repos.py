"""Fixture builder for the late-closure tests: a repo whose main carries a rec-bundling plan, its implementation
merge X (a trailer-bearing commit touching a scope file) and a `closure` branch holding the closure-only change."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
PLAN = fx.plan_rel(SLUG)
BUNDLED = ["rec-1"]
SHARD_ID = "chk-a"
SHARD = f"config/agent/verification_registry/entries/{SHARD_ID}.yaml"
SRC = "src/impl.py"
DOC = "doc.md"
DOC_BASE = "alpha beta gamma delta\n"
DOC_APPROVED = "alpha BETA GAMMA delta\n"


def plan_doc(
    declared: bool | None, bundled: list[str] | None = None, extra: dict | None = None, doc_scope: bool = False
) -> dict:
    doc = {
        "slug": SLUG,
        "bundled_recommendations": BUNDLED if bundled is None else bundled,
        "scope": [{"file": SRC}, {"file": PLAN}] + ([{"file": DOC}] if doc_scope else []),
        "verification_plan": [{"step": 1, "graduation": "graduate", "graduation_check_id": SHARD_ID}],
    }
    if declared is not None:
        doc["implementation_declared"] = declared
    return doc | (extra or {})


def write_plan_doc(repo: Path, doc: dict) -> None:
    fx.write(repo, PLAN, yaml.safe_dump(doc, sort_keys=False))


def shard_row(check_id: str = SHARD_ID, path: str = SRC, slug: str = SLUG) -> dict:
    return {
        "check_id": check_id,
        "primitive_slot": "file_presence",
        "guard_target": path,
        "plan_slug": slug,
        "graduated_at": "2026-11-03",
        "check_spec": {"path": path, "mode": "exists"},
    }


def shard_text(check_id: str = SHARD_ID, path: str = SRC) -> str:
    return yaml.safe_dump(shard_row(check_id, path), sort_keys=False)


def commit_all(repo: Path, message: str) -> str:
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", message)
    return fx.git(repo, "rev-parse", "HEAD")


def build_main(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
    *,
    bundled: list[str] | None = None,
    impl_message: str | None = "feat\n\nResolves: rec-1\n",
    impl_file: str = SRC,
    impl_declared: bool | None = None,
    sidecar: bool = False,
    sidecar_landed: bool = False,
    later_edit: bool = False,
) -> tuple[Path, str | None]:
    """Main = seed, plan commit (undeclared), implementation commit X. Returns (repo, X sha or None).

    sidecar_landed: the plan scopes doc.md, the plan commit writes its base words and the sidecar, and X applies the
    approved words (the realistic shape); later_edit adds a commit editing doc.md after X."""
    fx.clear_ci_env(monkeypatch)
    repo = fx.make_repo(tmp_path)
    write_plan_doc(repo, plan_doc(False, bundled, doc_scope=sidecar_landed))
    if sidecar_landed:
        fx.write(repo, DOC, DOC_BASE)
    fx.write(repo, "config/agent/verification_registry/entries/chk-seed.yaml", shard_text("chk-seed", "README.md"))
    if sidecar or sidecar_landed:
        fx.write_sidecar(repo, SLUG, [{"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA GAMMA"}])
    commit_all(repo, "plan")
    sha = None
    if impl_message is not None:
        fx.write(repo, impl_file, "x = 1\n")
        if sidecar_landed:
            fx.write(repo, DOC, DOC_APPROVED)
        if impl_declared is not None:
            write_plan_doc(repo, plan_doc(impl_declared, bundled, doc_scope=sidecar_landed))
        sha = commit_all(repo, impl_message)
        if later_edit:
            fx.write(repo, DOC, DOC_APPROVED + "later edit\n")
            commit_all(repo, "later edit")
    fx.push_main(repo)
    return repo, sha


def closure_branch(
    repo: Path,
    *,
    shard: str = SHARD_ID,
    extra_files: dict[str, str] | None = None,
    plan_extra: dict | None = None,
    message: str = "closure\n",
    bundled: list[str] | None = None,
    commit: bool = True,
    doc_scope: bool = False,
) -> str | None:
    fx.branch(repo, "closure")
    write_plan_doc(repo, plan_doc(True, bundled, plan_extra, doc_scope))
    fx.write(repo, f"config/agent/verification_registry/entries/{shard}.yaml", shard_text(shard))
    for rel, text in (extra_files or {}).items():
        fx.write(repo, rel, text)
    return commit_all(repo, message) if commit else None
