"""The code_reviewer input provider (docs/contracts/personas/code_reviewer.yaml; PLAN-code-reviewer-add).

Pure: no network and no token. The GitHub-shaped inputs come from one shell-prepared file, prepared/review-context.json,
written by review_context.py's fetch verb; everything else is read from the head snapshot as data (never imported,
executed or installed) or, for rule text and the verdict App id, from base_root, the trusted main checkout. Lives in the
shell package outside the persona image's build.copy, so persona code stays GitHub-agnostic (Decision 205 cl.2).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from scripts.checks.verification import validate_weakening_candidates as weakening
from scripts.personas import materialise, reference_hits, route, snapshot
from scripts.personas.materialise import Unusable
from scripts.session import merge_checks

REVIEW_CONTEXT = Path("prepared") / "review-context.json"
CONTRACT = "docs/contracts/personas/code_reviewer.yaml"
PROJECT_CONTEXT = "docs/PROJECT_CONTEXT.md"
ARCHIVE = "docs/DECISIONS_ARCHIVE.md"
EXEMPTIONS_HEADER = "## Code Review Exemptions"
SCOPE_BOUNDARY = "docs/contracts/implement-scope-boundary.yaml"
DEPLOY_PATHS = "docs/contracts/deploy-paths.yaml"
TESTS_GUIDANCE = "tests/CLAUDE.md"
GUIDANCE_SECTIONS = ("## Mock exhaustion", "## Test-count coupling")
PATH_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_REFERENCED = 200
NO_CANDIDATES = "no weakening candidates"
BODY_WITHHELD = "description withheld: over the size cap"
STATUS_MAP = {"added": "A", "modified": "M", "changed": "M", "removed": "D"}


def _incomplete(detail: str) -> Unusable:
    return Unusable("input_incomplete", detail)


def _load_context() -> dict[str, Any]:
    try:
        doc = json.loads(REVIEW_CONTEXT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _incomplete("the review context is missing or malformed") from exc
    shape = {
        "merge_base_sha": str,
        "truncated": bool,
        "files": list,
        "diff": str,
        "merge_base_texts": dict,
        "check_runs": list,
        "body_author_association": str,
        "comments": list,
        "head_ref": str,
    }
    if not isinstance(doc, dict) or any(not isinstance(doc.get(k), t) for k, t in shape.items()):
        raise _incomplete("the review context is missing or malformed")
    if doc["truncated"]:
        raise _incomplete("the compare listing is truncated")
    if not doc["files"] or not all(isinstance(f, dict) and f.get("filename") and f.get("status") for f in doc["files"]):
        raise _incomplete("the review context lists no usable changed files")
    if not doc["diff"].strip():
        raise _incomplete("the review context carries no diff")
    return doc


def _base_read(base: Path, rel: str) -> str:
    try:
        return (base / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unusable("input_unusable", f"{rel} is unreadable at the base") from exc


def _base_yaml(base: Path, rel: str) -> Any:
    try:
        return yaml.safe_load(_base_read(base, rel))
    except yaml.YAMLError as exc:
        raise Unusable("input_unusable", f"{rel} does not parse at the base") from exc


def _section(text: str, prefix: str) -> str:
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).rstrip("\n")


def _clean(rel: str) -> bool:
    path = PurePosixPath(rel)
    return bool(rel) and not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def _read(root: Path, rel: str) -> tuple[str | None, str]:
    """(text, note): the head file's text, or None with the reason it cannot be read as text."""
    try:
        data = snapshot.read_bytes(root, rel)
    except snapshot.SnapshotRefused as exc:
        return None, f"not a readable regular file: {exc}"
    if data is None:
        return None, "absent at head"
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, "not UTF-8"


def _plan(root: Path, plan_paths: list[str], head_ref: str) -> tuple[str | None, str]:
    if plan_paths:
        text, _ = _read(root, plan_paths[0])
        return plan_paths[0], text or ""
    slug = head_ref[len("agent/") :] if head_ref.startswith("agent/") else ""
    if slug and SLUG.fullmatch(slug):
        rel = f"docs/plans/PLAN-{slug}.yaml"
        text, _ = _read(root, rel)
        if text is not None:
            return rel, text
    return None, ""


def _plan_scope(plan_text: str) -> list[str]:
    doc = route.parse_plan(plan_text)
    rows = doc.get("scope") if isinstance(doc, dict) else None
    out: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        name = row.get("file") if isinstance(row, dict) else None
        if isinstance(name, str) and _clean(name) and name not in out:
            out.append(name)
    return out


def _imports(root: Path, rel: str) -> list[str]:
    text, _ = _read(root, rel)
    if text is None or not rel.endswith(".py"):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    except (RecursionError, MemoryError) as exc:
        raise Unusable("input_unusable", f"{rel} cannot be parsed") from exc
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{a.name}" for a in node.names)
    found: list[str] = []
    for name in names:
        stem = name.replace(".", "/")
        for cand in (f"{stem}.py", f"{stem}/__init__.py"):
            if cand not in found and _clean(cand) and _read(root, cand)[0] is not None:
                found.append(cand)
    return found


def _scope_entries(root: Path, files: list[dict[str, Any]], plan_rel: str | None, plan_text: str) -> list[dict[str, str]]:
    entries: dict[str, str] = {}
    changed = [str(f["filename"]) for f in files]
    for rel in changed:
        entries.setdefault(rel, "changed in the change")
    plan_scope = _plan_scope(plan_text) if plan_rel else []
    for rel in plan_scope:
        entries.setdefault(rel, f"scope file of {plan_rel}")
    sources = [rel for rel, f in zip(changed, files, strict=True) if f["status"] != "removed" and rel.endswith(".py")] + [
        rel for rel in plan_scope if rel.endswith(".py")
    ]
    for rel in sources:
        for dep in _imports(root, rel):
            entries.setdefault(dep, f"imported by {rel}")
    return [{"path": p, "why": w} for p, w in entries.items()]


def _file_block(root: Path, rel: str) -> str:
    text, note = _read(root, rel)
    return f"=== {rel} ===\n" + (text if text is not None else f"[{note}]")


def _scope_files(root: Path, entries: list[dict[str, str]], files: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if _clean(entry["path"]) and entry["path"] not in seen:
            seen.add(entry["path"])
            blocks.append(_file_block(root, entry["path"]))
    referenced: list[str] = []
    for item in files:
        if item["status"] == "removed":
            continue
        text, _ = _read(root, str(item["filename"])) if _clean(str(item["filename"])) else (None, "")
        for token in PATH_TOKEN.findall(text or ""):
            if token not in seen and token not in referenced and _clean(token) and _read(root, token)[0] is not None:
                referenced.append(token)
    for rel in referenced[:MAX_REFERENCED]:
        seen.add(rel)
        blocks.append(_file_block(root, rel))
    configs = [Path("pyproject.toml")] + sorted(Path(p.name) for p in root.glob("requirements*.txt"))
    configs += sorted(Path(".github/workflows") / p.name for p in (root / ".github" / "workflows").glob("*"))
    for cfg in configs:
        rel = cfg.as_posix()
        if rel not in seen and _read(root, rel)[0] is not None:
            seen.add(rel)
            blocks.append(_file_block(root, rel))
    return "\n\n".join(blocks) or "[no files in scope]"


def _change_files(files: list[dict[str, Any]]) -> str:
    lines = []
    for item in files:
        name = str(item["filename"])
        if item["status"] == "renamed" and item.get("previous_filename"):
            lines.append(f"renamed {item['previous_filename']} -> {name}")
        else:
            lines.append(f"{item['status']} {name}")
    return "\n".join(lines)


def _changed_pairs(files: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in files:
        status, name = str(item["status"]), str(item["filename"])
        if status == "renamed":
            if item.get("previous_filename"):
                pairs.append(("D", str(item["previous_filename"])))
            pairs.append(("A", name))
        elif status == "copied":
            pairs.append(("A", name))
        elif status in STATUS_MAP:
            pairs.append((STATUS_MAP[status], name))
    return pairs


def _weakening(root: Path, ctx: dict[str, Any]) -> str:
    texts = {k: v for k, v in ctx["merge_base_texts"].items() if isinstance(v, str)}

    def read_head(rel: str) -> str | None:
        return _read(root, rel)[0] if _clean(rel) else None

    try:
        found = weakening.find_candidates(_changed_pairs(ctx["files"]), texts.get, read_head)
    except (RecursionError, MemoryError) as exc:
        raise Unusable("input_unusable", "a head file cannot be analysed for weakening") from exc
    lines: list[str] = []
    for c in found:
        lines.append(f"CANDIDATE [{c.category}] {c.path}::{c.node} -- {c.detail}")
        lines.append(f'  - {{candidate: "[{c.category}] {c.path}::{c.node}", disposition: <label>, cite: "<reference>"}}')
    return "\n".join(lines) or NO_CANDIDATES


def _ci_results(base: Path, runs: list[Any]) -> str:
    app = merge_checks.verdict_app_id(_base_read(base, route.CONFIG))
    notes: list[str] = []
    if not isinstance(app, int) or isinstance(app, bool):
        notes.append(f"verdict_app_id unavailable: {app if isinstance(app, str) else 'no app id is configured'}")
    kept = [
        r
        for r in runs
        if isinstance(r, dict)
        and not str(r.get("name") or "").startswith("persona-verdict/")
        and not (isinstance(app, int) and not isinstance(app, bool) and r.get("app_id") == app)
    ]
    state = "absent" if not kept else "incomplete" if any(r.get("status") != "completed" for r in kept) else "completed"
    doc: dict[str, Any] = {
        "state": state,
        "checks": [{k: r.get(k) for k in ("name", "status", "conclusion", "summary", "app_slug")} for r in kept],
    }
    if notes:
        doc["notes"] = notes
    return json.dumps(doc, sort_keys=True)


def _evidence(resolution: dict[str, Any], ctx: dict[str, Any]) -> str:
    if resolution.get("body_oversized"):
        body = BODY_WITHHELD
    else:
        body = str(resolution.get("body") or "")
    doc = {
        "description": {"author_association": ctx["body_author_association"], "text": body},
        "comments": [
            {"author_association": str(c.get("author_association") or ""), "text": str(c.get("body") or "")}
            for c in ctx["comments"]
            if isinstance(c, dict)
        ],
    }
    return json.dumps(doc, ensure_ascii=False)


def _guidance(base: Path) -> str:
    doc = _base_yaml(base, DEPLOY_PATHS)
    block = doc.get("admin_out_of_band") if isinstance(doc, dict) else None
    if not block:
        raise Unusable("input_unusable", "deploy-paths.yaml has no admin_out_of_band at the base")
    guidance = _base_read(base, TESTS_GUIDANCE)
    parts = [yaml.safe_dump({"admin_out_of_band": block}, sort_keys=False)]
    parts += [s for s in (_section(guidance, p) for p in GUIDANCE_SECTIONS) if s]
    return "\n".join(parts)


def _dispositions(base: Path) -> str:
    doc = _base_yaml(base, SCOPE_BOUNDARY)
    value = (
        (((doc or {}).get("invariants") or {}).get("content") or {}).get("review_dispositions")
        if isinstance(doc, dict)
        else None
    )
    if not value:
        raise Unusable("input_unusable", "implement-scope-boundary.yaml has no review_dispositions at the base")
    return yaml.safe_dump(value, sort_keys=False)


def build(root: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    base = ctx.get("base_root") or materialise.execute.ROOT
    resolution = ctx["resolution"]
    review = _load_context()
    files = review["files"]
    plan_rel, plan_text = _plan(root, list(ctx.get("plan_paths") or []), review["head_ref"])
    entries = _scope_entries(root, files, plan_rel, plan_text)
    context_doc = _base_read(base, PROJECT_CONTEXT)
    if not context_doc.strip():
        raise Unusable("input_unusable", f"{PROJECT_CONTEXT} is empty at the base")
    scope_files = _scope_files(root, entries, files)
    return {
        "plan": plan_text,
        "change_files": _change_files(files),
        "diff": review["diff"],
        "review_scope": json.dumps({"files": entries, "plan": plan_rel}, sort_keys=True),
        "review_scope_files": scope_files,
        "reference_hits": reference_hits.reference_hits(root, files, review["merge_base_texts"], scope_files, plan_rel),
        "project_context": context_doc,
        "review_exemptions": _section(_base_read(base, ARCHIVE), EXEMPTIONS_HEADER),
        "review_dispositions": _dispositions(base),
        "weakening_candidates": _weakening(root, review),
        "ci_results": _ci_results(base, review["check_runs"]),
        "change_evidence": _evidence(resolution, review),
        "review_guidance": _guidance(base),
    }


materialise.PROVIDERS["code_reviewer"] = build
