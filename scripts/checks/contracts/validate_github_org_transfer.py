"""Evaluator for docs/contracts/github-org-transfer.yaml (rec-3902; Decisions 172, 218).

Invariant (1): the OIDC trust lists (local.github_repos in both Terraform roots) equal, exactly, the
trusted_segments of the phase the contract governs by. Invariant (2), every static breakage_map row, is
extended row by row: this check evaluates the rows in ROW_EVALUATORS, names the rest with their owning
rec in PENDING_STATIC_ROWS (none today: rec-3903's and rec-4355's rows are evaluated), and fails on any
static row it does neither for.

PUBLIC EXTENSION INTERFACE (agreed with the literal-sweep and terraform/github plans): load_contract,
ROW_EVALUATORS, PENDING_STATIC_ROWS. Row evaluators only append to `failed`; the parent declares
registry.examined()/skipped() (Decision 170, last writer wins).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import TRUSTED_WORKFLOWS
from scripts.checks.contracts import _retired_identity

_CONTRACT = "docs/contracts/github-org-transfer.yaml"
_TRUST_ROOTS = ("terraform/personal/oidc.tf", "terraform/bootstrap/github_ci_apply.tf")
_LIST_RE = re.compile(r"github_repos\s*=\s*\[(?P<body>[^\]]*)\]", re.S)
_ENTRY_RE = re.compile(r"^([A-Za-z0-9_.-]+)@(\d+)/([A-Za-z0-9_.-]+)@(\d+)$")
_LABEL = "Org transfer contract"
_TRUSTED_PLAN_ROLE = "terraform/personal/oidc_trusted_plan_role.tf"
_WORKFLOW_REFS_RE = re.compile(r"^\s*trusted_plan_workflow_refs\s*=\s*\[(?P<body>[^\]]*)\]", re.S | re.M)


def load_contract(repo_root: Path) -> dict | None:
    """Parsed contract, or None after registry.skipped() when it is missing or unparseable."""
    try:
        import yaml

        data = yaml.safe_load((repo_root / _CONTRACT).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 -- never raise at check time
        registry.skipped(f"{_CONTRACT} unreadable: {type(exc).__name__}")
        return None
    if not isinstance(data, dict):
        registry.skipped(f"{_CONTRACT} is not a mapping")
        return None
    return data


def _read_list(repo_root: Path, rel: str, failed: list[str]) -> list[str] | None:
    try:
        text = (repo_root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        failed.append(f"{_LABEL}: {rel} unreadable: {exc}")
        return None
    match = _LIST_RE.search(text)
    if match is None:
        failed.append(f"{_LABEL}: {rel} declares no github_repos list")
        return None
    return re.findall(r'"([^"]+)"', match.group("body"))


def _governing_phase(identity: dict[str, Any], failed: list[str]) -> str | None:
    values = identity.get("phase_values") or []
    phase, staged = identity.get("phase"), identity.get("staged_phase")
    if phase not in values:
        failed.append(f"{_LABEL}: identity.phase {phase!r} is not in identity.phase_values")
        return None
    if staged is None:
        return str(phase)
    if staged not in values or values.index(staged) != values.index(phase) + 1:
        failed.append(f"{_LABEL}: identity.staged_phase {staged!r} must be the phase value immediately after {phase!r}")
        return None
    return str(staged)


def _evaluate_oidc_trust_segment(contract: dict, repo_root: Path, failed: list[str]) -> None:
    identity = contract.get("identity") or {}
    governing = _governing_phase(identity, failed)
    lists = {rel: _read_list(repo_root, rel, failed) for rel in _TRUST_ROOTS}
    if governing is None or any(entries is None for entries in lists.values()):
        return
    first, second = (lists[rel] or [] for rel in _TRUST_ROOTS)
    if first != second:
        failed.append(f"{_LABEL}: local.github_repos differ across roots ({first} vs {second})")
        return
    phase_rows = [p for p in contract.get("phases") or [] if p.get("exit_sets_phase") == governing]
    if not phase_rows:
        failed.append(f"{_LABEL}: no phase has exit_sets_phase {governing!r}")
        return
    names = {seg: (identity[seg]["owner"], identity[seg]["repository"]) for seg in ("current", "target")}
    expected = {names[seg] for seg in phase_rows[0].get("trusted_segments") or []}
    seen: list[tuple[str, str]] = []
    repo_ids: set[str] = set()
    for entry in first:
        match = _ENTRY_RE.match(entry)
        if match is None:
            failed.append(f"{_LABEL}: entry {entry!r} is not OWNER@OWNER-ID/REPO@REPO-ID")
            continue
        pair = (match.group(1), match.group(3))
        repo_ids.add(match.group(4))
        if pair not in names.values():
            failed.append(f"{_LABEL}: entry {entry!r} names neither identity.current nor identity.target")
        if pair in seen:
            failed.append(f"{_LABEL}: segment {entry!r} repeats")
        seen.append(pair)
    if len(repo_ids) > 1:
        failed.append(f"{_LABEL}: entries carry more than one repository id")
    if set(seen) != expected:
        failed.append(f"{_LABEL}: trust segments {sorted(set(seen))} != {sorted(expected)} for phase {governing!r}")


def _evaluate_trusted_plan_workflow_ref(contract: dict, repo_root: Path, failed: list[str]) -> None:
    """job_workflow_ref is name-based, unlike the id-based sub segments: GitHub mints it under the identity of
    the governing phase -- current before staging, both while pre_staged, target from transferred on."""
    identity = contract.get("identity") or {}
    governing = _governing_phase(identity, failed)
    try:
        text = (repo_root / _TRUSTED_PLAN_ROLE).read_text(encoding="utf-8")
    except OSError as exc:
        failed.append(f"{_LABEL}: {_TRUSTED_PLAN_ROLE} unreadable: {exc}")
        return
    match = _WORKFLOW_REFS_RE.search(re.sub(r"#[^\n]*", "", text))
    if match is None:
        failed.append(f"{_LABEL}: {_TRUSTED_PLAN_ROLE} declares no trusted_plan_workflow_refs list")
        return
    if governing is None:
        return
    index = (identity.get("phase_values") or []).index(governing)
    segments = ["current"] if index == 0 else ["current", "target"] if index == 1 else ["target"]
    expected = sorted(
        f"{identity[seg]['owner']}/{identity[seg]['repository']}/.github/workflows/{name}@refs/heads/main"
        for seg in segments
        for name in TRUSTED_WORKFLOWS
    )
    values = re.findall(r'"([^"]*)"', match.group("body"))
    if any("*" in v or "?" in v for v in values):
        failed.append(f"{_LABEL}: trusted_plan_workflow_refs carries a wildcard (exact match only, Decision 172 cl.3)")
    if sorted(values) != expected:
        failed.append(f"{_LABEL}: trusted_plan_workflow_refs {sorted(values)} != {expected} for phase {governing!r}")


_TFG_DIR = "terraform/github"
_TFG_FILES = ("variables.tf", "main.tf", "imports.tf")
_HEADER_RE = re.compile(r'^[ \t]*(variable|provider|import)\b[ \t]*(?:"[^"\n]*")?[ \t]*\{', re.M)
_ATTR_RE = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*=", re.M)
_STRING_RE = re.compile(r'"([^"\\]*)"')
_INTERPOLATION_RE = re.compile(r"\$\{[^}]*\}")


def _mask_noise(text: str) -> str:
    """Equal-length copy of `text` with comment bodies and string contents blanked (offsets and quotes preserved)."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "#" or (char == "/" and i + 1 < n and text[i + 1] == "/"):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif char == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            for j in range(i, min(i + 2, n)):
                out[j] = " "
            i += 2
        elif char == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    out[i] = " "
                    i += 1
                if i < n:
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
            i += 1
        else:
            i += 1
    return "".join(out)


def _blocks(text: str, kind: str) -> list[tuple[str, str, dict[str, str]]]:
    """(kind, label, depth-1 attributes) of each top-level `kind` block, scanned over the masked text."""
    masked = _mask_noise(text)
    found: list[tuple[str, str, dict[str, str]]] = []
    for header in _HEADER_RE.finditer(masked):
        if header.group(1) != kind:
            continue
        quoted = text[header.start() : header.end() - 1].split('"')
        label = quoted[1] if len(quoted) > 2 else ""
        depth, pos, attrs = 1, header.end(), {}
        while pos < len(masked) and depth:
            line_end = masked.find("\n", pos)
            line_end = len(masked) if line_end < 0 else line_end
            attr = _ATTR_RE.match(masked, pos, line_end) if depth == 1 else None
            if attr is not None:
                attrs[attr.group(1)] = text[attr.end() : line_end].strip()[: len(masked[attr.end() : line_end].strip())]
            depth += masked.count("{", pos, line_end) - masked.count("}", pos, line_end)
            pos = line_end + 1
        found.append((kind, label, attrs))
    return found


def _read_tfg(repo_root: Path, name: str, failed: list[str]) -> str | None:
    rel = f"{_TFG_DIR}/{name}"
    try:
        return (repo_root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        failed.append(f"{_LABEL}: terraform_github_root: {rel} unreadable: {exc}")
        return None


def _evaluate_terraform_github_root(contract: dict, repo_root: Path, failed: list[str]) -> None:
    """terraform/github: owner and name defaults follow the phase, provider owner and import ids are variables."""
    row = "terraform_github_root"
    identity = contract.get("identity") or {}
    scratch: list[str] = []
    governing = _governing_phase(identity, scratch)
    values = identity.get("phase_values") or []
    if governing is None:
        failed.append(f"{_LABEL}: {row} not evaluated: no governing phase")
        return
    if "proven" not in values:
        failed.append(f"{_LABEL}: {row}: identity.phase_values has no 'proven' value")
        return
    seg = identity["current"] if values.index(governing) < values.index("proven") else identity["target"]
    texts = {name: _read_tfg(repo_root, name, failed) for name in _TFG_FILES}
    if texts["variables.tf"] is not None:
        variables = {label: attrs for _, label, attrs in _blocks(texts["variables.tf"], "variable")}
        for var_name, want in (("github_owner", seg["owner"]), ("repository_name", seg["repository"])):
            default = (variables.get(var_name) or {}).get("default")
            match = _STRING_RE.fullmatch(default) if default is not None else None
            if match is None or match.group(1) != want:
                failed.append(
                    f"{_LABEL}: {row}: {_TFG_DIR}/variables.tf variable {var_name} default must be the quoted string"
                    f" {want!r} for phase {governing!r}, found {default!r}"
                )
        for label, attrs in variables.items():
            if attrs.get("sensitive") == "true" and attrs.get("default", "null") != "null":
                failed.append(f"{_LABEL}: {row}: {_TFG_DIR}/variables.tf sensitive variable {label} has a non-null default")
    if texts["main.tf"] is not None:
        providers = _blocks(texts["main.tf"], "provider")
        provider = next((attrs for _, label, attrs in providers if label == "github"), None)
        if provider is None:
            failed.append(f'{_LABEL}: {row}: {_TFG_DIR}/main.tf declares no provider "github"')
        elif provider.get("owner") != "var.github_owner":
            failed.append(f"{_LABEL}: {row}: {_TFG_DIR}/main.tf provider owner must be var.github_owner")
    if texts["imports.tf"] is not None:
        imports = _blocks(texts["imports.tf"], "import")
        if not imports:
            failed.append(f"{_LABEL}: {row}: {_TFG_DIR}/imports.tf has no import block")
        retired = {identity[s][k] for s in ("current", "target") for k in ("owner", "repository")}
        for _, _, attrs in imports:
            raw = attrs.get("id", "")
            if "var.repository_name" not in raw:
                failed.append(f"{_LABEL}: {row}: {_TFG_DIR}/imports.tf import id {raw!r} lacks var.repository_name")
            literal = _INTERPOLATION_RE.sub("", raw)
            if any(name in literal for name in retired):
                failed.append(f"{_LABEL}: {row}: {_TFG_DIR}/imports.tf import id {raw!r} embeds an owner or repository name")


ROW_EVALUATORS: dict[str, Callable[[dict, Path, list[str]], None]] = {
    "oidc_trust_segment": _evaluate_oidc_trust_segment,
    "trusted_plan_workflow_ref": _evaluate_trusted_plan_workflow_ref,
    "terraform_github_root": _evaluate_terraform_github_root,
    **_retired_identity.ROW_EVALUATORS,
}

PENDING_STATIC_ROWS: dict[str, str] = {}


@registry.register("validate_github_org_transfer", owner="platform")
def validate_github_org_transfer(failed: list[str]) -> None:
    """Gate the OIDC trust lists on the org-transfer contract's phase and account for every static row."""
    print("\n=== Org transfer contract evaluator (rec-3902) ===")
    root = _common.ROOT
    contract = load_contract(root)
    if contract is None:
        failed.append(f"{_LABEL}: {_CONTRACT} is missing or unparseable")
        return
    rows = {r["id"]: r for r in contract.get("breakage_map") or [] if isinstance(r, dict) and "id" in r}
    static = {rid for rid, r in rows.items() if r.get("detection") == "static"}
    for rid in sorted(set(ROW_EVALUATORS) | set(PENDING_STATIC_ROWS)):
        if rid not in rows:
            failed.append(f"{_LABEL}: named row {rid!r} is not in the contract's breakage_map")
    for rid in sorted(set(ROW_EVALUATORS) & set(PENDING_STATIC_ROWS)):
        failed.append(f"{_LABEL}: row {rid!r} is both evaluated and pending")
    for rid in sorted(static - set(ROW_EVALUATORS) - set(PENDING_STATIC_ROWS)):
        failed.append(f"{_LABEL}: static row {rid!r} is neither evaluated nor named as pending")
    for rid, rec in sorted(PENDING_STATIC_ROWS.items()):
        if rid in rows and rows[rid].get("rec") != rec:
            failed.append(f"{_LABEL}: pending row {rid!r} maps to {rec} but the contract names {rows[rid].get('rec')}")
    for rid, evaluator in ROW_EVALUATORS.items():
        if rid in rows:
            evaluator(contract, root, failed)
    registry.examined(len(static), unit="static_breakage_rows")
