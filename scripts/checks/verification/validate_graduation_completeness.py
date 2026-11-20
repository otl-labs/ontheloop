"""Verification graduation completeness gate (T3.21, VF-05 enforcement, re-keyed to content
resolution -- see the amendment on Decision 132/148 in docs/DECISIONS.md).

VF-05 (T3.18) shipped the graduation PRODUCER (the tier-item bookkeeping walk,
docs/contracts/tier-item-lifecycle.yaml#bookkeeping_walk, graduates a plan's own
kernel-expressible VP steps into a new
config/agent/verification_registry/entries/<check_id>.yaml shard) and VF-06 (validate_verification_registry's
real differential admission gate). Neither one is an OBLIGATION: nothing forces a fix PR to
actually add the registry row it owes, so a skip is invisible to CI (a plan-PR incident, PR
#586, shipped 4 orphaned checks -- no new registry entries, so "correctly graduated nothing"
looked identical to "forgot to graduate"). This check closes that gap by enforcing the plan's
OWN declared graduation dispositions across two legs:

  Plan-PR leg: a diff-added or diff-modified docs/plans/PLAN-*.yaml must carry a graduation
    disposition (graduate|waive|not-applicable) on every pre-deploy VP step -- field presence
    only, no kernel-expressibility inference (that classification judgement is the fresh-context
    plan-critique gate's job, at plan time). Enforced only when the plan is net-new in the diff
    (git diff --diff-filter=A) OR it already declares >=1 disposition somewhere -- a merely-
    modified plan that declares zero dispositions anywhere (a correction to a pre-field plan, or
    the lagged .yaml archival sweep) is a pre-field plan and is skipped, not failed.

  Implement leg: resolves plans via `_common.resolve_declared_plans` (content-keyed: an
    edge-triggered `implementation_declared` False->True flip between the diff base and the
    working tree), loads each resolved PLAN-{slug}.yaml, and asserts every VP step declared
    graduate has a matching row in the CURRENT registry (plan_slug == slug AND
    check_id == graduation_check_id): one new in this diff, or one already on the base (an earlier PR
    of the same plan graduated it). A base row listed in known_red.yaml does not count.
    waive/not-applicable steps require no row. A step whose
    graduation proved impossible at implement time is expected to have been flipped to waive
    (with a reason) in the same PR -- that flip satisfies this leg with no row.

Fail-loud (Decision 55) on genuine errors (a schema-import failure). Advisory SKIP (never a
failure): origin/main is unreachable (no fetch, detached clone, etc) -- skips the WHOLE implement
leg (content resolution cannot tell a flip from a pre-existing declaration without a reachable
base). One terminal Decision 170 declaration per dispatch (docs/contracts/check-accounting.yaml):
no plan present in the diff at all is an empty domain (`examined(0)`, never skipped); a
diff-present plan set with an unreachable base declares `skipped`; otherwise
`examined(len(resolved), unit="declared_plans")`.

Both of this check's own diff baselines -- `_default_baseline_registry_entries` and
`_added_plan_paths` -- take their base as an injected parameter, defaulted at this module's call
site from `push_context_base(root) or "origin/main"` -- paired with the SAME `root` the base is
then evaluated against -- so neither silently computes an empty added-row/added-plan set on the
post-merge main run (where a hardcoded `origin/main` literal equals HEAD) NOR reads the real
repository's push-context base while operating on an injected (e.g. fixture) `root` (rec-3166:
the root seam had no paired base seam).

Injection seams (changed_files, root, load_plan, baseline_registry_reader) mirror the
validate_vp_replay / validate_sloc_budget_raises precedents for testability without real git
state, except where a seam would defeat the point of the test (content resolution and the
net-new-plan-path predicate use real `git log` / `git diff` against `root` -- tests set up a
throwaway repo with a `refs/remotes/origin/main` ref, mirroring tests/test_verification_graduation.py).

scripts/verification_checks.py (the six-slot CD.29 kernel) is never touched here -- this check
is diff-scoped plan/registry parsing only; it never re-runs a differential (that stays owned by
validate_verification_registry / VF-06).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from scripts import verification_graduation
from scripts.checks import _common, registry
from scripts.checks.verification import validate_known_red_shrinks

if TYPE_CHECKING:
    from scripts.roadmap.plan_document import PlanDocument

LoadPlanFn = Callable[[str, Path], "PlanDocument"]
BaselineRegistryReaderFn = Callable[[Path], list[dict]]


def _added_plan_paths(root: Path, base: str) -> set[str]:
    """Plan paths added (git diff-filter=A) in this diff vs `base` -- net-new plans.

    `base` is an injected parameter (defaulted at the check's call site from
    `push_context_base() or "origin/main"`) -- never derived here, so a check running against
    an injected `root` reads its baseline from THAT repository rather than the real one.
    """
    result = _common.run(
        ["git", "diff", "--name-only", "--diff-filter=A", base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
    )
    if result.returncode != 0:
        return set()
    return {f for f in result.stdout.strip().splitlines() if _common.PLAN_PATH_RE.match(f)}


def _plan_pr_leg(plan_files: list[str], root: Path, failed: list[str], base: str, load_plan: LoadPlanFn | None = None) -> None:
    """Enforce the plan-PR leg over `plan_files` -- the diff-present PLAN-*.yaml set. The
    caller guarantees `plan_files` is non-empty (the empty-domain case is the outer function's
    own early return, before `base` is even derived); this leg has no empty-set case of its own."""
    load_plan = load_plan or _common.load_plan
    added = _added_plan_paths(root, base)
    for plan_rel in plan_files:
        plan_path = root / plan_rel
        if not plan_path.exists():
            print(f"  SKIP (plan-PR leg): {plan_rel} (not present on disk -- deleted in this diff)")
            continue

        try:
            doc = load_plan(plan_rel, root)
        except ImportError as exc:
            failed.append(
                f"graduation-completeness (plan-PR leg) {plan_rel}: could not import scripts.roadmap.plan_document: {exc}"
            )
            continue
        except Exception as exc:  # noqa: BLE001 -- schema validity is validate_plan_documents' concern
            print(f"  SKIP (plan-PR leg): {plan_rel}: load error ({exc}) -- not double-reported here")
            continue

        pre_deploy_steps = [s for s in doc.verification_plan if s.phase == "pre-deploy"]
        if not pre_deploy_steps:
            print(f"  PASS (plan-PR leg): {plan_rel} -- no pre-deploy step(s), nothing to enforce.")
            continue

        has_any_disposition = any(s.graduation is not None for s in doc.verification_plan)
        is_net_new = plan_rel in added
        if not is_net_new and not has_any_disposition:
            print(f"  SKIP (plan-PR leg): {plan_rel} (merely-modified, zero dispositions -- pre-field plan carve-out)")
            continue

        missing = [s.step for s in pre_deploy_steps if s.graduation is None]
        if missing:
            failed.append(
                f"graduation-completeness (plan-PR leg) {plan_rel}: pre-deploy step(s) {missing} lack a graduation disposition"
            )
        else:
            print(f"  PASS (plan-PR leg): {plan_rel} -- all {len(pre_deploy_steps)} pre-deploy step(s) carry a disposition.")


def _current_registry_entries(root: Path) -> list[dict]:
    """The live registry at `root`, via the loader's sole read path."""
    return verification_graduation.load_entries(repo_root=root)


def _default_baseline_registry_entries(root: Path, base: str = "origin/main") -> list[dict]:
    """Registry entries at `base`, spanning both the sharded and legacy-flat layouts (the
    loader's entries_at_ref). A `base` that does not resolve yields an empty (legitimate)
    baseline; a `base` that resolves but carries neither layout is a genuine anomaly and fails
    loud (Decision 55) rather than silently returning empty.

    `base` defaults to "origin/main" for direct callers (e.g. tests exercising this reader in
    isolation) but is always passed explicitly by `validate_graduation_completeness`, defaulted
    there from `push_context_base() or "origin/main"` -- never derived here, so a check running
    against an injected `root` reads its baseline from THAT repository.
    """
    baseline = verification_graduation.entries_at_ref(base, repo_root=root)
    return baseline if baseline is not None else []


def _obligation_sources_for_step(doc: object, step_id: int) -> list[str]:
    """Sources whose schema-v4 test obligation is proven by this verification_plan step.

    Obligation-aware diagnostics only -- graduation keeps its single registry and its existing
    (plan_slug, graduation_check_id) linkage. Naming the sources turns "registry row missing"
    into "these declared behaviors are about to lose their durable guard".
    """
    obligations = getattr(doc, "test_obligations", None) or []
    return sorted({o.source for o in obligations if o.verification_step == step_id})


def _implement_pr_leg(
    root: Path,
    resolved: list[str],
    failed: list[str],
    load_plan: LoadPlanFn | None = None,
    baseline_registry_reader: BaselineRegistryReaderFn | None = None,
) -> None:
    """Assert every graduate-disposition step of a resolved plan has a matching row in the current
    registry (new in this diff or already on the base; a known-red base row does not count). `resolved` is
    the content-keyed resolution from `_common.resolve_declared_plans`
    -- every path in it already exists on disk.
    """
    load_plan = load_plan or _common.load_plan
    baseline_registry_reader = baseline_registry_reader or _default_baseline_registry_entries

    if not resolved:
        print("  PASS (implement-PR leg): no plan(s) with a newly-true implementation_declared in this diff -- no-op.")
        return

    current_rows = [e for e in _current_registry_entries(root) if isinstance(e, dict)]
    baseline_ids = {e.get("check_id") for e in baseline_registry_reader(root) if isinstance(e, dict)}
    known_red_path = root / validate_known_red_shrinks.KNOWN_RED_REL
    known_red_ids = (
        set(validate_known_red_shrinks.ids_from_text(known_red_path.read_text(encoding="utf-8")))
        if known_red_path.is_file()
        else set()
    )

    for plan_rel in resolved:
        try:
            doc = load_plan(plan_rel, root)
        except ImportError as exc:
            failed.append(
                f"graduation-completeness (implement-PR leg) {plan_rel}: could not import scripts.roadmap.plan_document: {exc}"
            )
            continue
        except Exception as exc:  # noqa: BLE001 -- schema validity is validate_plan_documents' concern
            print(f"  SKIP (implement-PR leg): {plan_rel}: load error ({exc}) -- not double-reported here")
            continue

        graduate_steps = [s for s in doc.verification_plan if s.graduation == "graduate"]
        if not graduate_steps:
            print(f"  PASS (implement-PR leg): {plan_rel} -- no graduate-disposition step(s).")
            continue

        for step in graduate_steps:
            cid = step.graduation_check_id
            match = next((r for r in current_rows if r.get("check_id") == cid and r.get("plan_slug") == doc.slug), None)
            if match is not None and cid in baseline_ids and cid in known_red_ids:
                failed.append(
                    f"graduation-completeness (implement-PR leg) {plan_rel}: step {step.step} declared graduate "
                    f"(check_id={cid!r}) but its row is on known_red.yaml; repair it before declaring"
                )
            elif match is None:
                sources = _obligation_sources_for_step(doc, step.step)
                obligations_note = f" [test obligation(s) losing their guard: {', '.join(sources)}]" if sources else ""
                other = next((r.get("plan_slug") for r in current_rows if r.get("check_id") == cid), None)
                other_note = f" -- a row with this check_id belongs to plan_slug={other!r}" if other is not None else ""
                failed.append(
                    f"graduation-completeness (implement-PR leg) {plan_rel}: step {step.step} declared graduate "
                    f"(check_id={cid!r}) but no matching new-in-diff registry row found, and none already on main "
                    f"(plan_slug={doc.slug!r}){other_note} -- "
                    "add the registry row, or flip this step to waive with a reason if it proved un-graduatable"
                    f"{obligations_note}"
                )
            else:
                label = "already on main" if cid in baseline_ids else "new in this diff"
                print(f"  PASS (implement-PR leg): {plan_rel}:{step.step} -- registry row {cid!r} present ({label}).")


@registry.register("validate_graduation_completeness", owner="platform")
def validate_graduation_completeness(
    failed: list[str],
    changed_files: list[str] | None = None,
    root: Path | None = None,
    load_plan: LoadPlanFn | None = None,
    baseline_registry_reader: BaselineRegistryReaderFn | None = None,
) -> None:
    """Enforce the plan-declared VF-05 graduation obligation (T3.21) across both legs.

    changed_files / root / load_plan / baseline_registry_reader are test/dogfood injection
    seams -- default to _common.get_changed_files(), _common.ROOT, _common.load_plan, and the
    loader's `verification_graduation.entries_at_ref(base, ...)` reader respectively.

    Composes exactly ONE terminal Decision 170 declaration (see module docstring). The
    empty-domain early return (no docs/plans/PLAN-*.yaml in the diff) fires BEFORE any base
    derivation, so a nonexistent injected `root` is never used as a subprocess cwd when
    there is nothing to enforce.
    """
    print("\n=== Verification graduation completeness (T3.21, VF-05 enforcement) ===")
    root = root if root is not None else _common.ROOT
    changed = changed_files if changed_files is not None else _common.get_changed_files(root)

    plan_files = _common.plan_paths_from_changed(changed)
    if not plan_files:
        print("  PASS (plan-PR leg): no docs/plans/PLAN-*.yaml in the diff -- no-op.")
        print("  PASS (implement-PR leg): no docs/plans/PLAN-*.yaml in the diff -- no-op.")
        registry.examined(0, unit="declared_plans")
        return

    base = _common.push_context_base(root) or "origin/main"
    _plan_pr_leg(plan_files, root, failed, base, load_plan=load_plan)

    if not _common.origin_main_reachable(root):
        print("  SKIP (implement-PR leg): origin/main unreachable (advisory locally, authoritative in CI).")
        registry.skipped("diff base unreachable")
        return

    resolved = _common.resolve_declared_plans(changed, root, base)
    bound_baseline_reader = baseline_registry_reader or (lambda r: _default_baseline_registry_entries(r, base))
    _implement_pr_leg(root, resolved, failed, load_plan=load_plan, baseline_registry_reader=bound_baseline_reader)
    registry.examined(len(resolved), unit="declared_plans")
