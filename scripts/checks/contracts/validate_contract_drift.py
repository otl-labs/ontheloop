# complexity-waiver: decision-43
"""Contract drift gate (CD.25 + T2.56 Class D population gate): reject ritual (Class A/B/C) and
mechanism (Class D) contract YAMLs in docs/contracts/ that violate CD.25 or the Class D
population contract (docs/contracts/contract-population.yaml, this gate's own evaluator target
-- self-hosting).

Pass 1 (structural, per-file): unparseable/malformed YAML is caught as a defect (never silently
swallowed the way load_all_contracts is allowed to). Each depth-1 *.yaml/*.yml file classifies as
ritual (Class A/B/C), Class D, or unshaped -- see scripts/checks/contracts/_population.py for the
shared classification/evaluator-resolution/subject-uniqueness/census primitives this module
orchestrates. `contract-population.yaml`'s own basename appears below (`_CONTRACT_POPULATION`) so
this module's `{check: validate_contract_drift}` evaluator declaration genuinely resolves.

Pass 2 (diff-aware, scoped to files changed vs the git merge-base): amendment-log + status
transition for ritual contracts (unchanged categories 6/7), plus the Class D amendment-log rule
-- resolving each file's base content via the three-way union (path, `-M` rename map,
`contract.id`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.checks import _common, registry
from scripts.checks.contracts import _population, _shared

if TYPE_CHECKING:
    from scripts.contracts_schema import ContractDocument, ContractMeta

# Genuinely used below (target_dir / _CONTRACT_POPULATION) to locate this gate's own self-hosting
# contract -- also the literal that makes this module's {check: validate_contract_drift}
# evaluator declaration resolve (executable-context basename match, not a docstring mention).
_CONTRACT_POPULATION = "contract-population.yaml"

_DOCS_CONTRACTS_PREFIX = "docs/contracts/"


@registry.register("validate_contract_drift", owner="platform")
def validate_contract_drift(
    failed: list[str],
    contracts_dir: Path | None = None,
    router_path: Path | None = None,
) -> None:
    """Gate on contract drift: reject ritual (A/B/C) and Class D contract YAMLs that violate
    CD.25 or the Class D population contract.

    contracts_dir / router_path: overrides for test isolation (default to ROOT/docs/contracts
    and ROOT/docs/contracts/file-router.yaml respectively).
    """
    print("\n=== Contract drift gate (CD.25 + T2.56 population gate) ===")

    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"
    if not target_dir.is_dir():
        failed.append(f"Contract drift (fail-closed): {target_dir} does not exist -- docs/contracts/ is required.")
        registry.skipped(f"{target_dir} does not exist")
        return

    router = router_path if router_path is not None else _common.ROOT / "docs" / "contracts" / "file-router.yaml"

    root_str = str(_common.ROOT)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)

    error_count_before = len(failed)
    census = _population.Census()

    try:
        import yaml as _yaml

        from scripts.contracts import ContractValidationError, load_contract, load_contract_meta, resolve_refs
        from scripts.contracts_enforcement import (
            _load_contract_from_text,
            check_amendment_for_diff,
            check_re_ratification_trigger,
            check_required_inline_fields,
            check_status_transition,
        )

        # ---- baseline resolution (once per run; every reader below is a fresh git call, no
        # module-level memoization -- see _shared.py for why) ----
        baseline_reachable = _common.origin_main_reachable(_common.ROOT)
        merge_base: str | None = None
        baseline_paths: set[str] = set()
        rename_map: dict[str, str] = {}
        id_index: dict[str, str] = {}

        if baseline_reachable:
            mb_result = _common.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=_common.ROOT,
            )
            if mb_result.returncode == 0:
                merge_base = mb_result.stdout.strip()

        if merge_base:
            bp = _shared.merge_base_path_set(_common.ROOT, merge_base)
            if bp is not None:
                baseline_paths = bp
            rm = _shared.rename_map(_common.ROOT, merge_base)
            if rm is not None:
                rename_map = rm
            depth1_yaml_baseline = {
                p
                for p in baseline_paths
                if p.startswith(_DOCS_CONTRACTS_PREFIX)
                and "/" not in p[len(_DOCS_CONTRACTS_PREFIX) :]
                and Path(p).suffix in (".yaml", ".yml")
            }
            envelopes = _shared.baseline_class_d_envelopes(_common.ROOT, merge_base, depth1_yaml_baseline)
            if envelopes is not None:
                id_index = envelopes
        else:
            print(
                "  advisory-SKIP: origin/main unreachable -- extension/depth conformance and Pass 2 "
                "(diff-aware) checks skipped; shape, evaluator resolution, subject uniqueness, and "
                "the census still run."
            )

        baseline_available = baseline_reachable and merge_base is not None

        def _base_reader(rel_path: str) -> str | None:
            # Every call site below is gated behind `baseline_available`, which is True only
            # when merge_base is not None -- no defensive None-guard needed here (AGENTS.md:
            # don't handle scenarios that can't happen).
            result = _common.run(
                ["git", "show", f"{merge_base}:{rel_path}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=_common.ROOT,
            )
            if result.returncode != 0:
                return None
            return result.stdout

        # ---- declared sub-trees (contract-population.yaml declared_subtrees): a closed set, checked every run ----
        subtrees = _population.load_declared_subtrees(target_dir / _CONTRACT_POPULATION)
        failed.extend(_population.subtree_member_failures(target_dir, subtrees))

        # ---- extension/depth conformance over the WHOLE tree (not just *.yaml/*.yml) ----
        if baseline_available:
            baseline_rel = {p[len(_DOCS_CONTRACTS_PREFIX) :] for p in baseline_paths if p.startswith(_DOCS_CONTRACTS_PREFIX)}
            for f in _population.scan_tree(target_dir):
                rel = f.relative_to(target_dir).as_posix()
                if _population.is_governed_extension_depth(rel) or _population.under_declared_subtree(rel, subtrees):
                    continue
                if rel in baseline_rel:
                    continue
                failed.append(
                    f"Contract drift (extension): {rel} is not a depth-1 .yaml/.yml file and is not "
                    "present at the merge-base baseline."
                )

        # ---- Pass 1: structural validation of every depth-1 *.yaml/*.yml ----
        yaml_paths: list[Path] = sorted({*target_dir.glob("*.yaml"), *target_dir.glob("*.yml")})
        ritual_contracts: list[tuple[Path, ContractDocument]] = []
        class_d_metas: dict[str, ContractMeta] = {}  # resolved+admitted D files, by name
        self_hosting_seen = False

        for p in yaml_paths:
            name = p.name
            rel_path = f"{_DOCS_CONTRACTS_PREFIX}{name}"
            if name == _CONTRACT_POPULATION:
                # Genuine runtime use of _CONTRACT_POPULATION (not merely an assignment): this
                # gate's own evaluator declaration, {check: validate_contract_drift}, only
                # resolves because THIS comparison executes -- an unreferenced literal
                # assignment would be exactly the "mention, not reading" loophole this rule
                # exists to close.
                self_hosting_seen = True
            # Every file examined counts toward `scanned` exactly once, unconditionally --
            # every branch below increments EXACTLY ONE of ritual/declared/grandfathered/skipped
            # before its next `continue`, so the bucket identity holds by construction rather
            # than by each branch remembering to keep it in sync.
            census.scanned += 1
            try:
                raw_text = p.read_text(encoding="utf-8")
                data = _yaml.safe_load(raw_text)
            except (OSError, UnicodeDecodeError, _yaml.YAMLError) as exc:
                failed.append(f"Contract drift (cat-1): {name}: {exc}")
                census.skipped += 1
                continue

            if not isinstance(data, dict):
                failed.append(f"Contract drift (cat-1): {name}: not a YAML mapping")
                census.skipped += 1
                continue

            shape = _population.classify_file(data)
            is_new = baseline_available and rel_path not in baseline_paths

            if shape == "unshaped":
                if is_new:
                    failed.append(
                        f"Contract drift (shape): {name}: no ritual (A/B/C) contract block and no valid "
                        "Class D shape (declare `contract.class` as A, B, C, or D)."
                    )
                    census.skipped += 1
                else:
                    census.grandfathered += 1
                continue

            if shape == "ritual":
                census.ritual += 1
                # -- Class A/B/C Pass 1, unchanged from the pre-migration gate --
                try:
                    doc = load_contract(p)
                except ContractValidationError as exc:
                    failed.append(f"Contract drift (structural): {name}: {exc}")
                    continue
                try:
                    resolve_refs(doc, target_dir)
                except ContractValidationError as exc:
                    failed.append(f"Contract drift (ref): {name}: {exc}")
                    continue
                for err in check_required_inline_fields(doc):
                    failed.append(f"Contract drift: {name}: {err}")
                for err in check_re_ratification_trigger(doc):
                    failed.append(f"Contract drift: {name}: {err}")
                ritual_contracts.append((p, doc))
                continue

            # the remaining branch handles shape "class_d"
            smuggled = _population.detect_smuggled_shape(data)
            if smuggled:
                failed.append(f"Contract drift (smuggling): {name}: {smuggled}")
                census.skipped += 1
                continue

            try:
                meta = load_contract_meta(p)
            except ContractValidationError as exc:
                failed.append(f"Contract drift (schema): {name}: {exc}")
                census.skipped += 1
                continue
            # Guaranteed by ContractMeta's Class D validator (contracts_schema.py) -- a Class D
            # envelope cannot pass schema validation without a non-None evaluator.
            assert meta.evaluator is not None

            if is_new and meta.status.value == "active":
                failed.append(
                    f"Contract drift (grandfather): {name}: a NEW Class D file may not declare "
                    "status: active (grandfather-only)."
                )
                census.skipped += 1
                continue

            resolves, detail = _population.resolve_evaluator(name, meta.evaluator, root=_common.ROOT)
            if not resolves:
                failed.append(f"Contract drift (evaluator): {name}: evaluator does not resolve -- {detail}")
                census.skipped += 1
                continue

            grandfather_kind = meta.status.value == "active"
            if grandfather_kind:
                census.grandfathered += 1
            else:
                census.declared += 1
            if meta.status.value == "active":
                census.status_active += 1

            class_d_metas[name] = meta

        # ---- subject uniqueness + topic-equals-subject ----
        class_d_subjects = {name: meta.subject for name, meta in class_d_metas.items() if meta.subject}
        ritual_ids = {p.name: doc.contract.id for p, doc in ritual_contracts}
        for err in _population.check_subject_uniqueness(class_d_subjects, ritual_ids):
            failed.append(f"Contract drift (subject): {err}")

        routes = _load_router_routes(router, _yaml)
        for name, subject in sorted(class_d_subjects.items()):
            topic_err = _population.check_topic_equals_subject(f"{_DOCS_CONTRACTS_PREFIX}{name}", subject, routes)
            if topic_err:
                failed.append(f"Contract drift (subject): {topic_err}")

        # ---- Pass 2: diff-aware checks ----
        if baseline_available:
            diff_result = _common.run(
                ["git", "diff", "--name-only", f"{merge_base}..HEAD", "--", "docs/contracts/"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=_common.ROOT,
            )
            changed_names = {Path(line.strip()).name for line in diff_result.stdout.splitlines() if line.strip()}

            ritual_by_name = {p.name: (p, doc) for p, doc in ritual_contracts}
            for name, (p, head_doc) in ritual_by_name.items():
                if name not in changed_names:
                    continue
                base_text = _base_reader(f"{_DOCS_CONTRACTS_PREFIX}{name}")
                if base_text is None:
                    continue
                try:
                    base_doc = _load_contract_from_text(base_text)
                except ContractValidationError:
                    continue
                for err in check_amendment_for_diff(base_doc, head_doc):
                    failed.append(f"Contract drift: {name}: {err}")
                for err in check_status_transition(base_doc, head_doc):
                    failed.append(f"Contract drift: {name}: {err}")

            for p in yaml_paths:
                name = p.name
                if name not in changed_names:
                    continue
                rel_path = f"{_DOCS_CONTRACTS_PREFIX}{name}"
                try:
                    head_data = _yaml.safe_load(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, _yaml.YAMLError):
                    continue
                if not isinstance(head_data, dict):
                    continue
                head_shape = _population.classify_file(head_data)

                base_text = _population.resolve_base_content(
                    rel_path,
                    head_data,
                    baseline_paths=baseline_paths,
                    rename_map=rename_map,
                    id_index=id_index,
                    base_reader=_base_reader,
                )
                if base_text is None:
                    continue  # genuinely new -- nothing to diff against

                try:
                    base_data = _yaml.safe_load(base_text)
                except _yaml.YAMLError:
                    continue
                if not isinstance(base_data, dict):
                    continue
                base_shape = _population.classify_file(base_data)

                loss = _population.check_conformance_loss(base_shape, head_shape)
                if loss:
                    failed.append(f"Contract drift (conformance-loss): {name}: {loss}")

                if head_shape == "class_d" and base_shape == "class_d":
                    amend_err = _population.check_amendment_log_for_class_d(base_data, head_data)
                    if amend_err:
                        failed.append(f"Contract drift (amendment): {name}: {amend_err}")
        else:
            print("  advisory-SKIP: Pass 2 (diff-aware) checks skipped -- origin/main unreachable.")

        # ---- ratchet (contract-population.yaml's two pins: grandfathered_max,
        # status_active_max) ----
        pins, pin_errors = _population.validate_ratchet_pins(target_dir)
        for err in pin_errors:
            failed.append(f"Contract drift (ratchet): {err}")
        if baseline_available:
            for key in pins:
                for err in _population.check_pin_direction(key, target_dir, pins[key], base_reader=_base_reader):
                    failed.append(f"Contract drift (ratchet): {err}")

        # ---- pin-vs-census equality binding: every pin must EQUAL its live census bucket,
        # never merely be >= it (acceptance criterion 4). Suppressed when any contract was
        # SKIPPED (rec-3101): a skipped contract's bucket never increments, so the equality
        # binding would otherwise emit a SECOND error whose obvious reading is "lower the pin" --
        # the correct fix is always to fix the skipped contract, never to shrink the debt ratchet.
        # ----
        census_values = {
            "grandfathered_max": census.grandfathered,
            "status_active_max": census.status_active,
        }
        if census.skipped == 0:
            for err in _population.check_pins_bound_to_census(pins, census_values):
                failed.append(f"Contract drift (ratchet): {err}")

        # ---- census (every run) ----
        print(f"  Census: {census.render()}")
        if not census.bucket_identity_holds():
            failed.append(f"Contract drift (census): bucket identity failed -- {census.render()}")
        if self_hosting_seen:
            print(f"  Self-hosting: {_CONTRACT_POPULATION} was scanned by this run.")

        registry.examined(len(yaml_paths), unit="contracts")

        new_failures = len(failed) - error_count_before
        if new_failures == 0:
            print(f"  PASS: {len(yaml_paths)} file(s) scanned, {census.ritual + census.declared} governed contract(s).")
        else:
            print(f"  FAIL: {len(yaml_paths)} file(s) scanned -- {new_failures} violation(s).")

    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)


def _load_router_routes(router_path: Path, yaml_module) -> list[dict]:
    """Best-effort read of file-router.yaml's `routes` list -- [] on any failure. Not
    authoritative (validate_placement owns strict router validation); this gate only consults it
    for the topic-equals-subject cross-check."""
    try:
        data = yaml_module.safe_load(router_path.read_text(encoding="utf-8"))
    except (OSError, yaml_module.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    routes = data.get("routes")
    return routes if isinstance(routes, list) else []
