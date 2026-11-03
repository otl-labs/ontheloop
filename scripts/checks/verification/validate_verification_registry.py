"""Verification registry schema validation (T3.1, Decision 104; re-grained to
config/agent/verification_registry/entries/<check_id>.yaml shards, Decision 176).

Reads exclusively through scripts.verification_graduation's loader (load_entries/entries_at_ref)
-- never a hand-rolled path join to the pre-migration flat file, which is now a rejected shape
(see the flat-file-resurrection leg below).
"""

from __future__ import annotations

from pathlib import Path

from scripts import verification_graduation
from scripts.checks import _common, registry
from scripts.checks.verification import _implementation_merge as _im


def _added_entries(current_entries: list[dict], root: Path | None = None) -> list[dict]:
    """Entries present now but absent from origin/main, matched by check_id (VF-06 c2).

    Returns [] when origin/main does not resolve (advisory -- the caller's own reachability
    check is what decides whether to skip the whole differential leg, not this helper)."""
    baseline = verification_graduation.entries_at_ref("origin/main", repo_root=root or _common.ROOT)
    if baseline is None:
        return []
    baseline_ids = {e.get("check_id") for e in baseline if isinstance(e, dict)}
    return [e for e in current_entries if isinstance(e, dict) and e.get("check_id") not in baseline_ids]


def _modified_entries(current_entries: list[dict], root: Path | None = None) -> list[dict]:
    """Entries present in BOTH current and origin/main (same check_id) whose parsed record
    mapping differs from the baseline -- closes the silent-modification hole: a record whose
    check_spec is edited in place, without a new check_id, was previously invisible to the
    differential gate (a neutered check could pass unnoticed).

    Compares PARSED MAPPINGS, never bytes or text: the sharding migration itself re-renders
    every record at width=200, so a byte/text-based detector would select all 476 records instead
    of only the ones whose content genuinely changed. Returns [] when origin/main does not
    resolve (mirrors _added_entries)."""
    baseline = verification_graduation.entries_at_ref("origin/main", repo_root=root or _common.ROOT)
    if baseline is None:
        return []
    baseline_by_id = {e.get("check_id"): e for e in baseline if isinstance(e, dict)}
    modified: list[dict] = []
    for entry in current_entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("check_id")
        if cid in baseline_by_id and baseline_by_id[cid] != entry:
            modified.append(entry)
    return modified


def _late_closure_state(
    added: list[dict], root: Path, failed: list[str]
) -> tuple[dict[str, str], set[str]]:
    """({check_id: X^} for committed late-closure shards, {check_id} for working-tree-only ones).

    Only ADDED shards whose plan_slug names a plan with a late_closure_route X and whose check_id is one of that
    plan's graduation_check_ids qualify. The route is evaluated once per plan; git failures read as no route."""
    bases: dict[str, str] = {}
    pending: set[str] = set()
    base = _common.status_diff_base(root) or "origin/main"
    by_plan: dict[str, list[dict]] = {}
    for row in added:
        slug = row.get("plan_slug")
        if isinstance(slug, str) and slug:
            by_plan.setdefault(f"docs/plans/PLAN-{slug}.yaml", []).append(row)
    for plan_rel, rows in by_plan.items():
        committed = _common._plan_declared_at_ref(plan_rel, root, "HEAD") and not _common._plan_declared_at_ref(
            plan_rel, root, base
        )
        working = not committed and bool(_common.resolve_declared_plans([plan_rel], root, base))
        if not (committed or working):
            continue
        try:
            merge = _im.late_closure_route(plan_rel, root, base, "HEAD" if committed else None)
        except _im.LateClosureError as exc:
            failed.append(f"verification-registry late closure: {exc}")
            continue
        if merge is None:
            continue
        ids = _im.graduation_check_ids(_im._plan_at(plan_rel, root, base))
        for row in rows:
            if row.get("check_id") in ids:
                if working:
                    pending.add(row["check_id"])
                else:
                    bases[row["check_id"]] = merge.parent
    return bases, pending


def _run_added_entry_differentials(
    candidates: list[dict],
    failed: list[str],
    root: Path | None = None,
    bases: dict[str, str] | None = None,
    pending: set[str] | None = None,
) -> None:
    """VF-06 c2 (added) + the modified-record close (c2'): for each added-or-modified entry in
    this diff, run the REAL differential admission gate.

    Materializes each entry's check_spec and asserts it FAILS on origin/main (real git worktree
    revert) and PASSES on HEAD/live. Refuses tautological/non-admitted outcomes and any
    materialize/worktree/revert error with a clear failure message (Decision 55 fail-loud) --
    never a silent pass.
    """
    plural = "y" if len(candidates) == 1 else "ies"
    print(f"  Differential admission gate: {len(candidates)} added-or-modified entr{plural} in this diff.")

    root = root or _common.ROOT
    for row in candidates:
        cid = row.get("check_id", "?")
        if cid in (pending or ()):
            print(f"    PENDING: {cid} (late-closure declaration not committed; the committed run decides)")
            continue
        try:
            if cid in (bases or {}):
                outcome = verification_graduation.run_differential(row, repo_root=root, base_ref=bases[cid])
            else:
                outcome = verification_graduation.run_differential(row, repo_root=root)
        except verification_graduation.GraduationError as exc:
            failed.append(f"verification-registry differential: check_id={cid}: error -- {exc}")
            continue
        if outcome.skipped:
            print(f"    skipped (non-fatal): {cid} ({outcome.reason})")
        elif not outcome.admitted:
            failed.append(f"verification-registry differential: check_id={cid}: not admitted -- {outcome.reason}")
        else:
            late = f" [late-closure base {bases[cid]}]" if cid in (bases or {}) else ""
            print(f"    admitted: {cid} ({outcome.reason}){late}")


def _schema_errors(entries: list, canonical_slots: frozenset[str]) -> list[str]:
    """Per-entry schema checks: required fields, known primitive_slot, unique check_id."""
    required_fields = {"check_id", "primitive_slot", "guard_target", "plan_slug", "graduated_at"}
    seen_ids: set[str] = set()
    errors: list[str] = []
    for entry in entries:
        cid_hint = entry.get("check_id", "?") if isinstance(entry, dict) else "?"
        if not isinstance(entry, dict):
            errors.append(f"  {cid_hint}: not a mapping")
            continue
        missing = required_fields - entry.keys()
        if missing:
            errors.append(f"  {cid_hint}: missing fields: {sorted(missing)}")
        slot = entry.get("primitive_slot")
        if slot is not None and slot not in canonical_slots:
            errors.append(f"  {cid_hint}: unknown primitive_slot {slot!r} (not in CD.29 vocabulary)")
        cid = entry.get("check_id")
        if cid is not None:
            if cid in seen_ids:
                errors.append(f"  duplicate check_id: {cid!r}")
            seen_ids.add(cid)
    return errors


def _placement_errors(shard_files: list, entries_by_path: dict) -> list[str]:
    """Placement leg: a shard's filename must equal its own check_id (Decision 55 -- structural,
    not documented convention). Each message is failure-detail-shaped (naming the file and the
    check_id it disagrees with), not only a generic label -- appended to `failed` directly."""
    errors: list[str] = []
    for path in shard_files:
        data = entries_by_path.get(path)
        cid = data.get("check_id") if isinstance(data, dict) else None
        expected_name = f"{cid}.yaml" if cid else None
        if expected_name != path.name:
            errors.append(f"verification-registry: {path.name}: filename does not equal check_id (check_id={cid!r})")
    return errors


@registry.register("validate_verification_registry", owner="platform")
def validate_verification_registry(failed: list[str], root: Path | None = None) -> None:
    """Validate config/agent/verification_registry/entries/*.yaml against the CD.29 contract
    (--pre, T3.1).

    Checks:
    1. entries/ directory exists (and the retired flat registry.yaml has NOT resurrected).
    2. Every shard's filename equals its own check_id (placement leg).
    3. Every shard is valid YAML, a mapping, with required fields and a known primitive_slot.
    4. check_id values are unique across the corpus.
    5. VF-06 c2/c2': entries ADDED or MODIFIED in this diff pass the REAL differential admission
       gate (git-worktree revert against origin/main) -- diff-gated no-op when neither happened;
       advisory-skips the whole leg when origin/main is unreachable (Decision 55 -- never an
       empty-baseline misread of every live record as newly added).
    """
    print("\n=== Verification registry (T3.1) ===")
    root = root if root is not None else _common.ROOT
    entries_dir = root / verification_graduation.REGISTRY_ENTRIES_REL

    flat_path = root / verification_graduation.REGISTRY_DIR_REL / verification_graduation.LEGACY_FLAT_BASENAME
    if flat_path.exists():
        failed.append(
            f"verification-registry: {flat_path.relative_to(root)} has resurrected -- "
            "records belong one-per-file under entries/<check_id>.yaml, never a flat monolith"
        )

    if not entries_dir.is_dir():
        failed.append("verification-registry: config/agent/verification_registry/entries/ not found")
        registry.skipped("entries directory missing")
        return

    import yaml  # noqa: PLC0415

    shard_files = sorted(p for p in entries_dir.glob("*.yaml"))
    entries_by_path: dict = {}
    for path in shard_files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failed.append(f"verification-registry: {path.name}: YAML parse error: {exc}")
            continue
        entries_by_path[path] = data

    placement_errors = _placement_errors(shard_files, entries_by_path)
    for e in placement_errors:
        print(f"  {e}")
        failed.append(e)

    entries = list(entries_by_path.values())

    # Lazy-import CANONICAL_SLOTS to avoid a module-level import of scripts.verification_checks.
    root_str = str(root)
    import sys as _sys  # noqa: PLC0415

    injected = root_str not in _sys.path
    if injected:
        _sys.path.insert(0, root_str)
    try:
        from scripts.verification_checks import CANONICAL_SLOTS  # noqa: PLC0415
    finally:
        if injected and root_str in _sys.path:
            _sys.path.remove(root_str)

    errors = _schema_errors(entries, CANONICAL_SLOTS)

    if errors:
        for e in errors:
            print(e)
        failed.append("Verification registry")
        registry.examined(len(entries), unit="graduated_checks")
        return

    print(f"  OK: {len(entries)} graduated checks, all valid.")

    if not _common.origin_main_reachable(root):
        print("  SKIP (differential gate): origin/main unreachable (advisory locally, authoritative in CI).")
        registry.skipped("origin/main unreachable")
        return

    added = _added_entries(entries, root)
    modified = _modified_entries(entries, root)
    candidates = added + modified
    if candidates:
        bases, pending = _late_closure_state(added, root, failed)
        _run_added_entry_differentials(candidates, failed, root, bases, pending)

    registry.examined(len(entries), unit="graduated_checks")
