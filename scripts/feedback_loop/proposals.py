"""Ready-to-file rec drafts for the friction detector's findings. Nothing here files: the operator does."""

from __future__ import annotations

import ast
import functools
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.feedback_loop.friction import Finding

ROOT = Path(__file__).resolve().parents[2]
RULES_FILE = "src/common/ducklake_event_read_verbs.py"
_BASE_FIELDS: dict[str, Any] = {"source": "feedback_loop", "status": "open", "priority": "Low", "effort": "S"}
SOURCE: str = _BASE_FIELDS["source"]
_EVIDENCE_NOT_PROOF = (
    "The acceptance probe is evidence the friction stopped, not proof a fix worked: a proof close stays with "
    "back-validation (Decision 221)."
)


@functools.cache
def _manifest_modules() -> dict[str, str]:
    modules: dict[str, str] = {}
    for manifest in sorted((ROOT / "scripts" / "checks").glob("*/_manifest.py")):
        for node in ast.walk(ast.parse(manifest.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Entry":
                kw = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
                name, module = kw.get("name"), kw.get("module")
                if isinstance(name, str) and isinstance(module, str):
                    modules[name] = module
    return modules


def source_file(signature: str | None) -> str:
    """The repository file behind a rule-named signature; a miss falls back to the rules file."""
    kind, _, name = (signature or "").partition(":")
    if kind == "hook":
        for suffix in (".py", ".sh"):
            path = f".claude/hooks/{name}{suffix}"
            if (ROOT / path).is_file():
                return path
    elif kind == "gate":
        module = _manifest_modules().get(name)
        if module:
            path = module.replace(".", "/") + ".py"
            if (ROOT / path).is_file():
                return path
    elif kind == "precommit":
        return ".pre-commit-config.yaml"
    return RULES_FILE


def _command(finding: Finding, version: int) -> str:
    return (
        "AWS_PROFILE=agent_platform bin/venv-python -m scripts.feedback_loop friction --assert-quiet "
        f"{finding.detector_id} {shlex.quote(finding.subject_key)} --classifier-version {version}"
    )


def draft(finding: Finding, window: tuple[str, str], versions: dict[str, int]) -> dict[str, Any]:
    """The file_rec fields and context_v2_json for one finding; nothing a rule does not name is spelled in them."""
    # friction imports this module at runtime (SOURCE), so the reverse import waits until the first draft
    from scripts.feedback_loop.friction import COVERAGE, is_published  # noqa: PLC0415

    version, first, last = versions["classifier_version"], window[0], window[1]
    group = finding.group or {}
    if finding.detector_id == COVERAGE:
        pct = round((finding.share or 0.0) * 100)
        title = f"Friction coverage: {pct}% of process-event failures unnamed at classifier version {version}"
        file = RULES_FILE
        counts = f"unmapped share {pct}% of process-event failures"
        label_line = "No rule names the failures behind the unmapped share; the review sheet lists them."
        facts = sessions = 0
    else:
        signature = str(group["signature"])
        published = is_published(signature)
        if published:
            title = f"Recurring friction: {signature}"
        else:
            title = f"Repeated tool failures, tool {finding.subject_key.rsplit(':', 1)[-1]}"
        file = source_file(signature) if published else RULES_FILE
        facts, sessions = group["fact_count"], group["session_count"]
        counts = f"{facts} facts in {sessions} sessions across {group['tree_count']} trees"
        label_line = f"Label {group['label']}, class {group['class']}, rule {group['rule_id']}."
    context = (
        f"The daily friction detector (detector_version {versions['detector_version']}) found {counts} between {first} "
        f"and {last} at classifier_version {version}. {label_line} List examples locally with "
        "bin/venv-python -m scripts.feedback_loop friction --sheet PATH. " + _EVIDENCE_NOT_PROOF
    )
    fields = {
        **_BASE_FIELDS,
        "title": title,
        "file": file,
        "context": context,
        "acceptance": _command(finding, version),
    }
    context_v2 = {
        "detector_id": finding.detector_id,
        "subject_key": finding.subject_key,
        "classifier_version": version,
        "detector_version": versions["detector_version"],
        "window_from": first,
        "window_to": last,
        "fact_count": facts,
        "session_count": sessions,
        "probe_role": "evidence_not_proof",
    }
    return {"fields": fields, "context_v2_json": context_v2}
