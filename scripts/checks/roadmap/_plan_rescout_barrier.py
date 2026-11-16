"""The re-scout barrier (T4.32 c4 interactive half, rec-4490): a plan's last decision-scout row must match the fingerprint.

Kept out of validate_plan_documents.py like _plan_sidecar_approval.py so it is covered from its own mirror test. It
imports scripts.roadmap.plan_fingerprint (THE single extractor) and never derives a payload, digest or edge itself.
Every path that cannot tell is a failure; a finding is a warning only in push context (the PR check is the gate).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.roadmap import approved_words

FINGERPRINT_MIN_SCHEMA_VERSION = 6
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")
_PLANS_DIR = "docs/plans"
_REMEDY = "(re-run the decision scout on the plan as it is now and record the value its report prints)"
_BLOCK_REMEDY = (
    "(re-run the decision scout after the pivot; a BLOCK the operator accepted is merged only by the operator's admin "
    "bypass in the GitHub UI)"
)


@dataclass
class BarrierResult:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    examined: int = 0
    compared: int = 0
    compared_names: set[str] = field(default_factory=set)


def default_read_base(root: Path, base: str | None) -> Callable[[str], str | None]:
    def read(rel: str) -> str | None:
        if base is None:
            return None
        result = subprocess.run(
            ["git", "show", f"{base}:{rel}"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        return result.stdout if result.returncode == 0 else None

    return read


def _last_scout(doc: Any) -> Any:
    rows = [row for row in doc.gate_rounds if row.gate == "decision-scout"]
    return rows[-1] if rows else None


def _sidecar_rel(plan: Mapping[str, Any]) -> str:
    slug = plan.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError("plan has no usable 'slug' (the sidecar lookup needs it)")
    return approved_words.sidecar_rel(slug)


def _load_plan(text: str) -> Mapping[str, Any]:
    plan = yaml.safe_load(text)
    if not isinstance(plan, Mapping):
        raise ValueError("plan is not a mapping")
    return plan


class _Computer:
    """Computes fingerprints over one lazily loaded tree."""

    def __init__(self, fp: Any, root: Path, read_base: Callable[[str], str | None]) -> None:
        self._fp = fp
        self._root = root
        self._read_base = read_base
        self._tree: Any = None

    def _compute(self, plan: Mapping[str, Any], sidecar_text: str | None) -> str:
        if self._tree is None:
            self._tree = self._fp.load_tree(self._root)
        sidecar = approved_words.parse_sidecar(sidecar_text) if sidecar_text is not None else None
        return str(self._fp.fingerprint(self._fp.projection(plan, self._tree, sidecar)))

    def head(self, plan_text: str) -> str:
        plan = _load_plan(plan_text)
        path = self._root / _sidecar_rel(plan)
        return self._compute(plan, path.read_text(encoding="utf-8") if path.is_file() else None)

    def base(self, plan: Mapping[str, Any]) -> str:
        return self._compute(plan, self._read_base(_sidecar_rel(plan)))


def barrier(
    entries: Sequence[tuple[str, str]],
    docs: Mapping[str, Any],
    root: Path,
    base: str | None,
    push_context: bool,
    read_base: Callable[[str], str | None] | None = None,
    *,
    diff_count: int,
    seam: bool = False,
    event: str | None = None,
) -> BarrierResult:
    result = BarrierResult()
    findings: list[str] = []
    root = Path(root)
    read = read_base if read_base is not None else default_read_base(root, base)
    plans = [(status, path) for status, path in entries if status in ("A", "M", "??") and Path(path).name in docs]
    _gate_findings(findings, base, plans, entries, diff_count, seam, event)
    try:
        from scripts.roadmap import plan_edges, plan_fingerprint  # noqa: PLC0415
    except ImportError as exc:
        findings.append(f"scout-input fingerprint module cannot be imported: {exc}")
        return _route(result, findings, push_context)
    errors = (
        plan_fingerprint.FingerprintError,
        plan_edges.PlanEdgesError,
        approved_words.ApprovedTextError,
        OSError,
        UnicodeDecodeError,
        yaml.YAMLError,
        ValueError,
    )
    computer = _Computer(plan_fingerprint, root, read)
    for status, path in plans:
        name = Path(path).name
        result.examined += 1
        _plan_findings(result, findings, computer, errors, status, name, docs[name], root / path, read)
    for status, path in plans:
        name = Path(path).name
        if status in ("A", "??") and docs[name].schema_version >= FINGERPRINT_MIN_SCHEMA_VERSION:
            if not any(f.startswith(f"{name}: ") for f in findings) and name not in result.compared_names:
                findings.append(f"{name}: added at schema_version {docs[name].schema_version} but not compared")
    return _route(result, findings, push_context)


def _gate_findings(
    findings: list[str],
    base: str | None,
    plans: Sequence[tuple[str, str]],
    entries: Sequence[tuple[str, str]],
    diff_count: int,
    seam: bool,
    event: str | None,
) -> None:
    if base is None and (plans or not seam):
        findings.append("cannot resolve a diff base, so the re-scout barrier cannot tell which plans are new or changed")
    if event == "pull_request" and base is not None and diff_count == 0 and (plans or not seam):
        findings.append(
            "the PR diff lists no file (git diff failed or returned nothing), so the re-scout barrier cannot tell which "
            "plans are new or changed"
        )


def _plan_findings(
    result: BarrierResult,
    findings: list[str],
    computer: _Computer,
    errors: tuple[type[BaseException], ...],
    status: str,
    name: str,
    doc: Any,
    head_path: Path,
    read: Callable[[str], str | None],
) -> None:
    scout = _last_scout(doc)
    if scout is not None and scout.verdict == "BLOCK":
        findings.append(f"{name}: re-scout required: the last decision-scout row is BLOCK {_BLOCK_REMEDY}")
    base_text = read(f"{_PLANS_DIR}/{name}") if status == "M" else None
    base_plan: Mapping[str, Any] | None = None
    try:
        if base_text is not None:
            base_plan = _load_plan(base_text)
            base_version = base_plan.get("schema_version")
            if isinstance(base_version, int) and doc.schema_version < base_version:
                findings.append(f"{name}: schema_version lowered from {base_version} to {doc.schema_version}")
                return
        if doc.schema_version < FINGERPRINT_MIN_SCHEMA_VERSION:
            return
        head_fp = computer.head(head_path.read_text(encoding="utf-8"))
        if base_plan is not None and computer.base(base_plan) == head_fp:
            return
    except errors as exc:
        findings.append(f"{name}: scout-input fingerprint cannot be computed: FINGERPRINT-ERROR: {exc}")
        return
    result.compared += 1
    result.compared_names.add(name)
    _compare(findings, name, scout, head_fp)


def _compare(findings: list[str], name: str, scout: Any, head_fp: str) -> None:
    if scout is None:
        findings.append(f"{name}: re-scout required: no decision-scout row {_REMEDY}")
    elif scout.scout_input_fingerprint is None:
        findings.append(f"{name}: re-scout required: the last decision-scout row records no scout_input_fingerprint {_REMEDY}")
    elif scout.scout_input_fingerprint != head_fp:
        findings.append(
            f"{name}: re-scout required: the last decision-scout row records {scout.scout_input_fingerprint}; this tree "
            f"gives {head_fp} {_REMEDY}"
        )


def _route(result: BarrierResult, findings: list[str], push_context: bool) -> BarrierResult:
    (result.warnings if push_context else result.failures).extend(findings)
    return result
