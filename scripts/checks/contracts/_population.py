"""Class D (mechanism contract) population gate innards (T2.56 / contracts-first-class-migration).

Owns the declared-shape validation, evaluator resolution, Class-A/B shape (smuggling) detection,
extension/depth conformance, subject-uniqueness, and the census. scripts/checks/contracts/
validate_contract_drift.py retains orchestration and Pass 1/Pass 2 sequencing -- this module is a
pure, injectable primitives library with no registry.register() entry of its own.

UNCONDITIONAL decomposition (Decision 128): the gate module was already at its budget before this
plan's thirteen feature groups, so the split is the default response, not a contingency.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any, Callable

from scripts.checks import registry
from scripts.checks.contracts._ratchet import (  # noqa: F401 -- re-export after the ratchet split (Decision 128)
    check_pin_direction,
    check_pins_bound_to_census,
    check_ratchet_direction,
    ratchet_spec,
    status_active_max_spec,
    validate_ratchet_pin,
    validate_ratchet_pins,
)
from scripts.contracts_schema import EvaluatorSpec

# ---------------------------------------------------------------------------
# Shape classification (ritual / Class D / unshaped) + Class-A/B smuggling
# ---------------------------------------------------------------------------

_GOVERNED_EXTENSIONS: tuple[str, ...] = (".yaml", ".yml")

_CLASS_A_LITERAL_KEYS = frozenset({"fields"})
_CLASS_B_LITERAL_KEYS = frozenset({"verbs"})

# Class A's inline-field descriptor keys and Class B's verb descriptor keys (INTENT Parts
# 3C-3D). A nested mapping whose leaf values carry >=2 of one class's descriptor keys is
# "shaped" for that class regardless of what its own top-level key is named -- the round-3
# draft's regression was replacing the literal-name rejection with a Class-A-only shape rule,
# which opened exactly this Class B seam (a renamed `operations:` block was never checked).
_CLASS_A_DESCRIPTOR_KEYS = frozenset({"type", "nullable", "description", "semantics", "populated_by", "dq_intent"})
_CLASS_B_DESCRIPTOR_KEYS = frozenset({"payload_schema_ref", "response_codes", "typed_errors"})
_SHAPE_DESCRIPTOR_MIN_HITS = 2


def classify_file(data: Any) -> str:
    """One of "ritual" (contract.class in A/B/C), "class_d" (contract.class == D), or
    "unshaped" (parses, but declares neither -- e.g. a free-form doc, or a malformed class)."""
    if not isinstance(data, dict):
        return "unshaped"
    contract = data.get("contract")
    if not isinstance(contract, dict) or "class" not in contract:
        return "unshaped"
    cls = contract.get("class")
    if cls in ("A", "B", "C"):
        return "ritual"
    if cls == "D":
        return "class_d"
    return "unshaped"


def detect_smuggled_shape(body: dict[str, Any]) -> str | None:
    """Return a description of a detected Class A/B smuggled shape in a Class D `body`, or None.

    Two independent detectors, BOTH retained (constraint: "Retain the literal fields:/verbs:
    rejection alongside the shape rule"): (1) a literal top-level `fields:`/`verbs:` key
    (Decision-precedent name rejection), (2) any top-level key whose value is a mapping of
    mappings where every leaf mapping carries >=2 of one class's descriptor keys (renamed-key
    evasion, e.g. `columns:` standing in for `fields:`, `operations:` standing in for `verbs:`).
    """
    for key in body:
        if key == "contract":
            continue
        if key in _CLASS_A_LITERAL_KEYS:
            return f"literal `{key}:` key is a Class A field-block name"
        if key in _CLASS_B_LITERAL_KEYS:
            return f"literal `{key}:` key is a Class B verb-block name"

    for key, value in body.items():
        if key == "contract" or not isinstance(value, dict) or not value:
            continue
        leaf_mappings = [v for v in value.values() if isinstance(v, dict)]
        if not leaf_mappings:
            continue
        a_hits = sum(1 for leaf in leaf_mappings if len(_CLASS_A_DESCRIPTOR_KEYS & leaf.keys()) >= _SHAPE_DESCRIPTOR_MIN_HITS)
        b_hits = sum(1 for leaf in leaf_mappings if len(_CLASS_B_DESCRIPTOR_KEYS & leaf.keys()) >= _SHAPE_DESCRIPTOR_MIN_HITS)
        if a_hits == len(leaf_mappings):
            return f"`{key}:` is field-shaped (every entry carries >={_SHAPE_DESCRIPTOR_MIN_HITS} Class A descriptor keys)"
        if b_hits == len(leaf_mappings):
            return f"`{key}:` is verb-shaped (every entry carries >={_SHAPE_DESCRIPTOR_MIN_HITS} Class B descriptor keys)"
    return None


def is_governed_extension_depth(rel: str) -> bool:
    """True iff `rel` (posix path relative to the contracts dir) is depth-1 with a governed
    extension. Case-sensitive by construction (mirrors `Path.glob("*.yaml")`'s own case
    sensitivity) -- a `.YAML` file is NOT governed."""
    return "/" not in rel and Path(rel).suffix in _GOVERNED_EXTENSIONS


def load_declared_subtrees(population_path: Path) -> list[dict[str, Any]]:
    """The declared_subtrees rows of contract-population.yaml (an empty list when the key is absent)."""
    import yaml

    try:
        data = yaml.safe_load(population_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    rows = data.get("declared_subtrees") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def under_declared_subtree(rel: str, rows: list[dict[str, Any]]) -> bool:
    """True iff `rel` (posix, relative to the contracts dir) lies under a declared sub-tree's path."""
    return any(rel.startswith(str(r.get("path", "\0"))) for r in rows)


def _member_file_failures(contracts_dir: Path, rel: str, extensions: tuple[str, ...], label: str) -> list[str]:
    import yaml

    if Path(rel).suffix not in extensions:
        return [f"{label}: member {rel} has an extension outside {list(extensions)}"]
    if Path(rel).suffix not in _GOVERNED_EXTENSIONS:
        return []
    try:
        body = yaml.safe_load((contracts_dir / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if isinstance(body, dict) and "contract" in body:
        return [f"{label}: member {rel} carries a top-level contract block"]
    return []


def _subtree_failures(contracts_dir: Path, row: dict[str, Any]) -> list[str]:
    import yaml

    sub, index_name, label = str(row["path"]), str(row["index"]), f"Contract drift (subtree {row['path']})"
    try:
        idx = yaml.safe_load((contracts_dir / index_name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        idx = None
    contract = idx.get("contract") if isinstance(idx, dict) else None
    if not isinstance(contract, dict) or contract.get("class") != "D":
        return [f"{label}: index {index_name} is not a depth-1 Class D contract"]
    out: list[str] = []
    if (contract.get("evaluator") or {}).get("check") != row.get("evaluator"):
        out.append(f"{label}: index {index_name} evaluator check is not {row.get('evaluator')}")
    members: set[str] = set()
    for entry in idx.get(str(row.get("members_key")), []) or []:
        for field in row.get("member_fields", []):
            if isinstance(entry, dict) and isinstance(entry.get(field), str):
                members.add(entry[field].removeprefix("docs/contracts/"))
    present = {f.relative_to(contracts_dir).as_posix() for f in scan_tree(contracts_dir / sub)}
    extensions = tuple(row.get("extensions", []))
    out += [f"{label}: {rel} is not listed as a member by {index_name}" for rel in sorted(present - members)]
    out += [f"{label}: listed member {rel} has no file" for rel in sorted(members - present)]
    for rel in sorted(present & members):
        out += _member_file_failures(contracts_dir, rel, extensions, label)
    return out


def subtree_member_failures(contracts_dir: Path, rows: list[dict[str, Any]]) -> list[str]:
    """Failure strings for each declared sub-tree: the index lists exactly the files present."""
    return [failure for row in rows for failure in _subtree_failures(contracts_dir, row)]


def scan_tree(contracts_dir: Path) -> list[Path]:
    """Every file under `contracts_dir`, recursively, any extension/depth."""
    return sorted(p for p in contracts_dir.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# Evaluator resolution -- evidence of READING, not mention
# ---------------------------------------------------------------------------


def _module_source_and_tree(module_path: Path) -> tuple[str, ast.Module] | None:
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    return source, tree


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def module_contains_literal(module_path: Path, literal: str) -> bool:
    """True iff `literal` appears as an exact string-constant match, OR as the final `/`-delimited
    segment of a path-shaped string-constant, in `module_path`'s AST, in EXECUTABLE context --
    module/class/function docstrings are excluded (comments are never part of the AST to begin
    with, so they need no separate exclusion).

    The path-shaped acceptance (migration-step-3-grandfathering) covers a full-path literal like
    `"docs/contracts/decision-entry.yaml"` -- stronger evidence of reading than a bare basename --
    per Decision 168's own Significance clause naming the contract as the SoT for the evaluator
    grammar (docs/contracts/contract-population.yaml evaluator_kinds.check). MATCHER TRAP GUARD: a
    path-shaped literal containing ANY whitespace is REJECTED even when its final segment equals
    `literal` -- a long prose annotation string that happens to END with a contract's basename
    (e.g. scripts/checks/iam_tf/_write_companions.py's sentence ending in
    "...iam-simulate-fixture.yaml") must never certify as a reading.
    """
    parsed = _module_source_and_tree(module_path)
    if parsed is None:
        return False
    _source, tree = parsed
    docstring_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            value = node.value
            if value == literal:
                return True
            if "/" in value and not any(ch.isspace() for ch in value) and value.rsplit("/", 1)[-1] == literal:
                return True
    return False


def one_hop_scripts_imports(module_path: Path, root: Path) -> list[Path]:
    """Module-level `import`/`from ... import ...` statements in `module_path` that resolve to
    another file under scripts/ -- one hop only (INTENT Invariant-5-style single-hop bound,
    reused here for evaluator resolution)."""
    parsed = _module_source_and_tree(module_path)
    if parsed is None:
        return []
    _source, tree = parsed
    targets: list[Path] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and node.module.startswith("scripts"):
            base_parts = node.module.split(".")
            for alias in node.names:
                candidate = root.joinpath(*base_parts, alias.name).with_suffix(".py")
                if candidate.is_file() and candidate not in targets:
                    targets.append(candidate)
                    continue
                mod_path = root.joinpath(*base_parts).with_suffix(".py")
                if mod_path.is_file() and mod_path not in targets:
                    targets.append(mod_path)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("scripts"):
                    candidate = root.joinpath(*alias.name.split(".")).with_suffix(".py")
                    if candidate.is_file() and candidate not in targets:
                        targets.append(candidate)
    return targets


def _find_check_module(name: str, root: Path) -> Path | None:
    """Locate the scripts/checks/**/*.py module whose `@registry.register(...)` call registers
    `name`, via static AST inspection -- never an import, which would cycle back through
    scripts/validate.py (validate.py re-exports validate_contract_drift, which imports this
    module)."""
    checks_dir = root / "scripts" / "checks"
    if not checks_dir.is_dir():
        return None
    for py_path in sorted(checks_dir.rglob("*.py")):
        parsed = _module_source_and_tree(py_path)
        if parsed is None:
            continue
        _source, tree = parsed
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and _call_registers_name(deco, name):
                        return py_path
    return None


def _call_registers_name(call: ast.Call, name: str) -> bool:
    func = call.func
    is_register = (isinstance(func, ast.Attribute) and func.attr == "register") or (
        isinstance(func, ast.Name) and func.id == "register"
    )
    if not is_register:
        return False
    if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == name:
        return True
    return any(kw.arg == "name" and isinstance(kw.value, ast.Constant) and kw.value.value == name for kw in call.keywords)


def _default_sequenced_check_names() -> set[str]:
    return {step.name for step in [*registry.pre_sequence(), *registry.full_sequence()] if step.kind == "check"}


def _agent_surface_resolves(value: str, contract_basename: str, root: Path) -> bool:
    """An agent_surface evaluator resolves iff its named repo-relative path exists and its text
    contains the contract's basename -- the non-Python analogue of the check AST scan (skills
    and slash commands are markdown, not AST-scannable Python)."""
    target = root / value
    if not target.is_file():
        return False
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return contract_basename in text


def resolve_evaluator(
    contract_basename: str,
    evaluator: EvaluatorSpec,
    *,
    root: Path,
    registered_checks: dict[str, Any] | None = None,
    sequenced_names: set[str] | None = None,
    check_module_resolver: Callable[[str], Path | None] | None = None,
) -> tuple[bool, str]:
    """True + detail iff `evaluator` genuinely READS `contract_basename` -- resolution by
    evidence, never by mention. `EvaluatorSpec._exactly_one_kind` guarantees exactly one of
    `check`/`agent_surface` is set, so this function's two branches are exhaustive."""
    if evaluator.check is not None:
        name = evaluator.check
        registered = registered_checks if registered_checks is not None else registry.all_checks()
        if name not in registered:
            return False, f"{name!r} is not a registered check (registry.all_checks())"
        sequenced = sequenced_names if sequenced_names is not None else _default_sequenced_check_names()
        if name not in sequenced:
            return False, f"{name!r} is registered but not sequenced in pre_sequence()/full_sequence()"
        resolver = check_module_resolver or (lambda n: _find_check_module(n, root))
        module_path = resolver(name)
        if module_path is None or not module_path.is_file():
            return False, f"could not resolve a source module for check {name!r}"
        if module_contains_literal(module_path, contract_basename):
            return True, f"{module_path} contains {contract_basename!r} in executable context"
        for hop in one_hop_scripts_imports(module_path, root):
            if module_contains_literal(hop, contract_basename):
                return True, f"one-hop import {hop} contains {contract_basename!r} in executable context"
        return False, f"neither {module_path} nor its one-hop scripts/ imports contain {contract_basename!r}"

    # EvaluatorSpec._exactly_one_kind guarantees agent_surface is set whenever check is None --
    # the check branch above already returned, so this leg is unconditional, not a fallback.
    value = evaluator.agent_surface
    assert value is not None, "EvaluatorSpec guarantees exactly one of check/agent_surface is set"
    if _agent_surface_resolves(value, contract_basename, root):
        return True, f"agent_surface {value!r} contains {contract_basename!r}"
    return False, f"agent_surface {value!r} does not contain {contract_basename!r}"


# ---------------------------------------------------------------------------
# Subject uniqueness + topic-equals-subject
# ---------------------------------------------------------------------------


def check_subject_uniqueness(
    class_d_subjects: dict[str, str],
    ritual_ids: dict[str, str],
) -> list[str]:
    """`class_d_subjects`: {rel_path: subject} for every valid Class D file this run.
    `ritual_ids`: {rel_path: contract.id} for every ritual (A/B/C) contract this run.

    FAILS on: a Class D subject colliding with a ritual contract id, or two Class D files
    sharing a subject. A ritual contract merely declaring its own id is never itself a failure
    -- ritual ids only seed the namespace here, they are never cross-checked against each other
    by this function (that is a pre-existing, separately-owned invariant).
    """
    errors: list[str] = []
    owners: dict[str, tuple[str, str]] = {}
    for rel_path, contract_id in sorted(ritual_ids.items()):
        owners.setdefault(contract_id, ("ritual", rel_path))

    for rel_path, subject in sorted(class_d_subjects.items()):
        existing = owners.get(subject)
        if existing is None:
            owners[subject] = ("class_d", rel_path)
            continue
        kind, owner_path = existing
        if owner_path == rel_path:
            continue
        if kind == "ritual":
            errors.append(f"{rel_path}: subject {subject!r} collides with the ritual contract id declared in {owner_path}")
        else:
            errors.append(f"{rel_path}: subject {subject!r} duplicates the subject declared by {owner_path}")
    return errors


def check_topic_equals_subject(rel_path: str, subject: str, routes: list[dict[str, Any]]) -> str | None:
    """FAIL iff `rel_path` is named in some file-router route's `targets` and that route's
    `topic` differs from `subject`. A file with no matching route is not this check's concern
    (router-registration completeness is validate_placement's job, not this gate's)."""
    for route in routes:
        targets = route.get("targets")
        topic = route.get("topic")
        if not isinstance(targets, list) or not isinstance(topic, str):
            continue
        if rel_path in targets and topic != subject:
            return f"{rel_path}: subject {subject!r} differs from its file-router topic {topic!r}"
    return None


# ---------------------------------------------------------------------------
# Base resolution union (path, -M rename, contract.id) + Pass 2 diff rules
# ---------------------------------------------------------------------------


def resolve_base_content(
    rel_path: str,
    head_data: dict[str, Any] | None,
    *,
    baseline_paths: set[str],
    rename_map: dict[str, str],
    id_index: dict[str, str],
    base_reader: Callable[[str], str | None],
) -> str | None:
    """Base content for `rel_path`, via the union: path presence, then the `-M` rename map,
    then `contract.id` across baseline Class D envelopes. Returns None only if all three miss
    (a genuinely new file)."""
    if rel_path in baseline_paths:
        text = base_reader(rel_path)
        if text is not None:
            return text
    old_path = rename_map.get(rel_path)
    if old_path is not None:
        text = base_reader(old_path)
        if text is not None:
            return text
    if head_data:
        contract = head_data.get("contract")
        contract_id = contract.get("id") if isinstance(contract, dict) else None
        if isinstance(contract_id, str) and contract_id in id_index:
            return id_index[contract_id]
    return None


def check_amendment_log_for_class_d(base_data: dict[str, Any], head_data: dict[str, Any]) -> str | None:
    """FAIL iff the canonical dump (every top-level key except `amendment_log`) differs between
    base and head with no new `amendment_log[]` entry added. A comment-only or formatting-only
    edit never changes the parsed dict, so it always passes."""

    def _canonical(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k != "amendment_log"}

    if _canonical(base_data) == _canonical(head_data):
        return None
    base_log = base_data.get("amendment_log") or []
    head_log = head_data.get("amendment_log") or []
    new_entries = [e for e in head_log if e not in base_log]
    if not new_entries:
        return "parsed content changed but no new amendment_log[] entry was added"
    return None


def check_conformance_loss(base_shape: str, head_shape: str) -> str | None:
    """FAIL iff the base carried a valid shape (ritual or class_d) and the head does not --
    closes the delete-the-contract-block fallback by identity, not by the ratchet pin's value."""
    if base_shape in ("ritual", "class_d") and head_shape not in ("ritual", "class_d"):
        return f"base shape was {base_shape!r}, head shape is {head_shape!r} (conformance loss)"
    return None


# ---------------------------------------------------------------------------
# Ratchet (grandfathered_max / status_active_max pins) -- moved to _ratchet.py (Decision 128
# decompose-by-default); re-imported at module top so existing callers reaching through this
# module's namespace keep working.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Census:
    scanned: int = 0
    ritual: int = 0
    declared: int = 0
    grandfathered: int = 0
    status_active: int = 0
    skipped: int = 0

    def render(self) -> str:
        return (
            f"scanned={self.scanned} ritual={self.ritual} declared={self.declared} "
            f"grandfathered={self.grandfathered} status_active={self.status_active} skipped={self.skipped}"
        )

    def bucket_identity_holds(self) -> bool:
        """A forgot-a-bucket guard only -- true by construction over a mutually exclusive
        partition, never claimed as evidence against a vacuous pass (see the module docstring
        of validate_contract_drift.py for the full disclaimer)."""
        return self.scanned == self.ritual + self.declared + self.grandfathered + self.skipped
