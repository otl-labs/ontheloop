"""Implement-scope diff-vs-plan boundary check (Decision 59's deterministic scope guard,
docs/contracts/implement-scope-boundary.yaml).

Discharges Decision 59 ("scope guard becomes a deterministic diff-vs-plan check"): for every
implement-leg plan resolved via ``_common.resolve_declared_plans`` (content-keyed:
``implementation_declared`` newly true in this diff, mirrors validate_vp_replay), every touched
path in the diff must be a member of the resolved plan(s)' declared ``scope[].file`` union, or a
path derived from a ``docs/contracts/implement-scope-boundary.yaml`` ``sanction_rows`` entry whose
trigger fired for that plan. It also flags a prohibited edit to a resolved plan's own scope-
governing fields (``scope``, ``acceptance_criteria``, ``verification_plan[].command``/``.expected``)
-- the CONTENT invariant's plan-field-edit and VP-step-substitution enforcement arms.

Legs (mirrors validate_vp_replay's structure):
  Plan-only leg: a diff-present ``docs/plans/PLAN-*.yaml`` whose ``implementation_declared`` did
    NOT newly flip true DEFERS -- prints a DEFER line, enforces nothing for that plan.
  Enforcing leg: for every resolved plan, unions its declared scope, derives its sanctioned
    companions from the contract's ``sanction_rows``, and checks every path in ``changed_files``
    against (declared scope) union (derived sanctions). It also diffs the resolved plan's own
    content against its base-ref content for a prohibited field edit.

Seam: ``changed_files`` is derived from ``_common.get_status_aware_diff`` (its untracked-``??``
leg is the only primitive that sees a Created scope row before the commit flow runs; its "D"
rows are the only primitive that surfaces a deleted path at all -- a deleted path outside scope
is a known, named gap the contract records: get_changed_files existence-filters it away, so the
seam choice here decides whether a deletion is even visible). Never globs docs/plans/ -- plans are
derived one hop off the diff via ``_common.plan_paths_from_changed``.

Injection seams (``plan_paths``/``changed_files``/``root``), accepted UNFILTERED (the
validate_plan_scope_closure precedent): when ``plan_paths`` is given, it IS the resolved
implement-leg set directly -- no diff-derivation, no DEFER printing, no vacuous/skipped legs. This
is what lets a plan's own Verification Plan dispatch the enforcing leg against its own working
tree before anything is committed (ambient resolution would see no flip yet, since
``implementation_declared`` is set only at the commit-flow step).

The scope_file_is_prose_gated sanction kind (prose_budgets_ratchet_down) sanctions
config/prose_budgets.yaml only when a prose-gated surface is in declared scope AND the registry's
base-to-working diff is decrease-only (every entry value <= base, every other byte identical); it
is inert when the plan declares the registry itself, and never appends to ``failed`` -- a diff it
declines to sanction falls through to the ordinary out-of-scope STOP.

A malformed contract or plan is a reported finding, never a raise (Decision 55: fail loud via
``failed``, not an uncaught exception). A ``sanction_rows`` entry naming a ``trigger.kind`` this
module does not implement is a loud failure, never a silently-skipped row.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.preflight.prose_context import _resolve_s1_root_load_set

CONTRACT_REL_PATH = "docs/contracts/implement-scope-boundary.yaml"

_STOP_DISPOSITION = (
    "STOP: an undeclared touched path is never resolved by editing the plan's own scope -- the "
    "escape is a human-directed plan amendment landed as its own reviewed act, never a unilateral append."
)


def _load_contract(root: Path) -> dict:
    return yaml.safe_load((root / CONTRACT_REL_PATH).read_text(encoding="utf-8"))


def _plan_content_at_ref(plan_rel: str, root: Path, ref: str) -> dict | None:
    """Best-effort read of `plan_rel`'s parsed content at git ref `ref`. None on any failure
    (unresolvable ref, missing path at that ref, unparseable/non-dict payload, or a non-existent
    ref for a genuinely net-new plan) -- never raised, mirroring _common._plan_declared_at_ref."""
    result = _common.run(["git", "show", f"{ref}:{plan_rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        return None
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _secrets_baseline_keys(baseline_rel: str, root: Path, base: str, failed: list[str]) -> set[str]:
    """Union of `baseline_rel`'s results-map filename keys from the working tree and from `base`.

    Missing at either half contributes nothing silently (no baseline, no churn); unparseable JSON
    at either half appends a finding, never raises (Decision 55). The base-ref half mirrors
    `_plan_content_at_ref`'s git-show-return-code convention: a non-zero `git show` (unresolvable
    ref, or path absent at base) contributes nothing silently.
    """
    keys: set[str] = set()

    working_path = root / baseline_rel
    if working_path.exists():
        try:
            working_data = json.loads(working_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failed.append(
                f"scope-boundary: {baseline_rel} is unparseable in the working tree -- cannot derive its sanctioned scope"
            )
        else:
            if isinstance(working_data, dict):
                keys.update((working_data.get("results") or {}).keys())

    result = _common.run(["git", "show", f"{base}:{baseline_rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode == 0:
        try:
            base_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            failed.append(f"scope-boundary: {baseline_rel} is unparseable at base ref {base!r} -- cannot derive its scope")
        else:
            if isinstance(base_data, dict):
                keys.update((base_data.get("results") or {}).keys())

    return keys


_ENTRY_LINE_RE = re.compile(r"^(\s*[^\s#][^#]*?:\s*)\d+(\s*(?:#.*)?)$")


def _prose_ratchet_sanction(row: dict, plan_scope_files: set[str], root: Path, base: str) -> list[str]:
    """Sanctioned path for the scope_file_is_prose_gated kind: the registry, only when the plan's
    declared scope meets a prose-gated surface and the registry diff is decrease-only. Prints why
    it declined; never appends to `failed` (the out-of-scope STOP reports the path)."""
    registry_rel = cast(str, (row.get("trigger") or {}).get("registry_path"))
    if registry_rel in plan_scope_files:
        return []
    working_path = root / registry_rel
    if not working_path.exists():
        print(f"  NOTE: {registry_rel} absent in the working tree -- prose_budgets_ratchet_down not applied.")
        return []
    working_text = working_path.read_text(encoding="utf-8")
    extract = _marker_guard.make_flat_extractor("raise-approved")
    gated = {k for k in extract(working_text) if "/" in k}
    for member in _resolve_s1_root_load_set(root):
        try:
            gated.add(member.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            print(f"  NOTE: S1 member {member} resolves outside {root} -- skipped.")
    if not plan_scope_files & gated:
        return []
    result = _common.run(["git", "show", f"{base}:{registry_rel}"], capture_output=True, text=True, encoding="utf-8", cwd=root)
    if result.returncode != 0:
        print(f"  NOTE: {registry_rel} absent at base ref {base!r} -- prose_budgets_ratchet_down not applied.")
        return []
    base_text = result.stdout

    def shape(text: str) -> list[str]:
        return [_ENTRY_LINE_RE.sub(r"\g<1>#\g<2>", ln) for ln in text.splitlines()]

    base_vals = extract(base_text)
    working_vals = extract(working_text)
    lowered_only = shape(base_text) == shape(working_text) and all(
        k in base_vals and v.value <= base_vals[k].value for k, v in working_vals.items()
    )
    if not lowered_only:
        print(f"  NOTE: prose_budgets_ratchet_down sanctions only lowered values in {registry_rel}; other text changed")
        return []
    return [cast(str, (row.get("sanctions") or {}).get("path_template", ""))]


def _row_derived_paths(
    kind: str | None,
    row: dict,
    doc,
    plan_rel: str,
    plan_scope_files: set[str],
    root: Path,
    base: str,
    failed: list[str],
    baseline_key_memo: dict[str, set[str]],
) -> list[str] | None:
    """Derived sanctioned paths for one sanction_rows entry against one resolved plan. None means
    `kind` is an unimplemented sanction_kind -- the caller reports a loud failure, never a silent
    skip. `root`/`base`/`failed`/`baseline_key_memo` are consumed only by the
    scope_file_in_secrets_baseline branch; `baseline_key_memo` is keyed on the row's
    `trigger.baseline_path` (a per-row field, not a single unkeyed slot) so a future second
    baseline-backed row does not silently reuse this row's key set, and the union read (plus any
    unparseable-baseline finding) happens at most once per dispatch regardless of how many
    resolved plans or rows consult it.
    """
    sanctions = row.get("sanctions") or {}
    if kind == "graduation_check_id_per_step":
        template = sanctions.get("path_template", "")
        return [
            template.format(graduation_check_id=step.graduation_check_id)
            for step in doc.verification_plan
            if step.graduation == "graduate" and step.graduation_check_id
        ]
    if kind == "resolved_plan_path":
        return [plan_rel]
    if kind == "scope_contains_file":
        target = (row.get("trigger") or {}).get("file")
        if target and target in plan_scope_files:
            return [sanctions.get("path_template", "")]
        return []
    if kind == "scope_file_in_secrets_baseline":
        # Cast, not validate: trigger.baseline_path is guaranteed present by the contract's own
        # row-shape assertion (VP step 1 of PLAN-secrets-baseline-sanction-row), never re-checked
        # here -- a row that omits it is a contract-authoring defect, not a runtime case this
        # function guards against (see the plan's "root / None RAISE" note).
        baseline_path = cast(str, (row.get("trigger") or {}).get("baseline_path"))
        if baseline_path not in baseline_key_memo:
            baseline_key_memo[baseline_path] = _secrets_baseline_keys(baseline_path, root, base, failed)
        if plan_scope_files & baseline_key_memo[baseline_path]:
            return [sanctions.get("path_template", "")]
        return []
    if kind == "scope_file_is_prose_gated":
        return _prose_ratchet_sanction(row, plan_scope_files, root, base)
    return None


def _check_prohibited_field_edits(row: dict, plan_rel: str, doc, root: Path, base: str, failed: list[str]) -> None:
    """`doc` is the already-loaded (working-tree) PlanDocument -- reused via model_dump() rather
    than re-reading and re-parsing the same file a second time."""
    prohibited = row.get("prohibited_field_edits") or []
    if not prohibited:
        return
    base_content = _plan_content_at_ref(plan_rel, root, base)
    if base_content is None:
        return  # net-new plan (absent at base) -- nothing to diff a prohibited edit against
    working_content = doc.model_dump()

    disposition = row.get("disposition_on_violation", _STOP_DISPOSITION)
    for field in prohibited:
        if "[]." in field:
            top, sub = field.split("[].", 1)
            base_steps = {s.get("step"): s.get(sub) for s in (base_content.get(top) or []) if isinstance(s, dict)}
            working_steps = {s.get("step"): s.get(sub) for s in (working_content.get(top) or []) if isinstance(s, dict)}
            for step_id, base_val in base_steps.items():
                if step_id not in working_steps:
                    failed.append(
                        f"scope-boundary {plan_rel}: prohibited plan-field edit -- verification_plan step "
                        f"{step_id} was deleted (had field {sub!r} declared at base). {disposition}"
                    )
                elif working_steps[step_id] != base_val:
                    failed.append(
                        f"scope-boundary {plan_rel}: prohibited plan-field edit -- verification_plan step "
                        f"{step_id} field {sub!r} changed from base. {disposition}"
                    )
        elif field in base_content and base_content.get(field) != working_content.get(field):
            failed.append(
                f"scope-boundary {plan_rel}: prohibited plan-field edit -- top-level field {field!r} changed from base. "
                f"{disposition}"
            )


def _enforce(resolved: list[str], changed: list[str], root: Path, base: str, failed: list[str]) -> None:
    if not resolved:
        print("  PASS: no resolved implement-leg plan(s) -- nothing to enforce.")
        return

    try:
        contract = _load_contract(root)
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"scope-boundary: could not load {CONTRACT_REL_PATH}: {exc}")
        return
    sanction_rows = (contract.get("sanction_rows") or {}) if isinstance(contract, dict) else {}
    bookkeeping_row = sanction_rows.get("implementing_plan_bookkeeping") or {}

    declared_scope: set[str] = set()
    sanctioned: set[str] = set()
    baseline_key_memo: dict[str, set[str]] = {}

    for plan_rel in resolved:
        if not (root / plan_rel).exists():
            print(f"  SKIP: {plan_rel} (not present on disk -- deleted in this diff)")
            continue
        try:
            doc = _common.load_plan(plan_rel, root)
        except Exception as exc:  # noqa: BLE001 -- a malformed plan is a reported finding, never a raise
            failed.append(f"scope-boundary {plan_rel}: could not load plan: {exc}")
            continue

        plan_scope_files = {entry.file for entry in doc.scope}
        declared_scope |= plan_scope_files

        for row_name, row in sanction_rows.items():
            kind = (row.get("trigger") or {}).get("kind") if isinstance(row, dict) else None
            derived = _row_derived_paths(kind, row, doc, plan_rel, plan_scope_files, root, base, failed, baseline_key_memo)
            if derived is None:
                failed.append(
                    f"scope-boundary: sanction row {row_name!r} declares unimplemented trigger kind "
                    f"{kind!r} -- the check cannot enforce it"
                )
                continue
            sanctioned.update(p for p in derived if p)

        _check_prohibited_field_edits(bookkeeping_row, plan_rel, doc, root, base, failed)

    allowed = declared_scope | sanctioned
    unsanctioned = sorted({p for p in changed if p not in allowed})
    for path in unsanctioned:
        disposition = bookkeeping_row.get("disposition_on_violation", _STOP_DISPOSITION)
        failed.append(
            f"scope-boundary: touched path {path!r} is outside declared scope and no sanction row covers it. {disposition}"
        )

    if not unsanctioned:
        print(f"  PASS: {len(changed)} touched path(s) all within declared scope or a sanctioned companion.")


@registry.register("validate_scope_boundary", owner="platform")
def validate_scope_boundary(
    failed: list[str],
    plan_paths: list[str] | None = None,
    changed_files: list[str] | None = None,
    root: Path | None = None,
) -> None:
    """Diff-vs-declared-scope boundary check (docs/contracts/implement-scope-boundary.yaml).

    `plan_paths`/`changed_files`/`root` are test/dogfood injection seams, accepted UNFILTERED
    (the validate_plan_scope_closure precedent). `plan_paths` given (not None) IS the resolved
    implement-leg plan set directly, bypassing diff-derivation entirely -- this is what lets a
    plan's own Verification Plan dispatch the enforcing leg before anything is committed.
    `changed_files` defaults to `_common.get_status_aware_diff(root)`, unpacked to a flat path
    list (status discarded -- presence is all this check needs; see the module docstring on why
    that primitive, not `get_changed_files`, is the chosen seam).

    Composes exactly ONE terminal Decision 170 declaration per dispatch
    (docs/contracts/check-accounting.yaml): no docs/plans/PLAN-*.yaml in the diff at all is an
    empty domain (examined(0)); a diff-present plan set with an unreachable base DEFERs
    everything and declares skipped; otherwise examined(len(resolved)).
    """
    print("\n=== Implement-scope boundary validation (Decision 59) ===")
    root = root if root is not None else _common.ROOT
    changed = changed_files if changed_files is not None else [path for _status, path in _common.get_status_aware_diff(root)]

    if plan_paths is not None:
        resolved = list(plan_paths)
        base = _common.push_context_base(root) or "origin/main"
        _enforce(resolved, changed, root, base, failed)
        registry.examined(len(resolved), unit="declared_plans")
        return

    plan_files = _common.plan_paths_from_changed(changed)
    if not plan_files:
        print("  PASS: no docs/plans/PLAN-*.yaml in the diff -- no-op.")
        registry.examined(0, unit="declared_plans")
        return

    if not _common.origin_main_reachable(root):
        print("  SKIP: origin/main unreachable (advisory locally, authoritative in CI) -- deferring scope enforcement.")
        for plan_rel in plan_files:
            print(f"  DEFER: {plan_rel} -- diff base unreachable.")
        registry.skipped("diff base unreachable")
        return

    base = _common.push_context_base(root) or "origin/main"
    resolved = _common.resolve_declared_plans(changed, root, base)
    resolved_set = set(resolved)
    for plan_rel in plan_files:
        if plan_rel not in resolved_set:
            print(f"  DEFER: {plan_rel} -- implementation_declared not newly true in this diff; scope enforcement deferred.")

    _enforce(resolved, changed, root, base, failed)
    registry.examined(len(resolved), unit="declared_plans")
