"""Class D evaluator for docs/contracts/full-tier-range.yaml (Decision 168).

Derive-and-assert: the variables, full-tier sibling slugs and full-tier job and step names the contract declares must
equal the live constants in scripts.checks._common, scripts.ci_rca.vacuous_pass, scripts.ci_rca.fingerprint and
scripts.ci.last_green, and each sibling slug must be the slug of a CI-tier workflow in config/ci_rca_taxonomy.yaml.
"""

from __future__ import annotations

import yaml

from scripts.checks import _common, registry
from scripts.ci import last_green
from scripts.ci_rca import fingerprint, vacuous_pass
from scripts.ci_rca.evidence._bundle_core import _slugify_workflow

_CONTRACT_BASENAME = "full-tier-range.yaml"
_TAXONOMY_RELPATH = ("config", "ci_rca_taxonomy.yaml")
_CHECK_LABEL = "Full-tier range contract"
_CI_TIER = "CI"


def _load(path_parts: tuple[str, ...]) -> dict:
    data = yaml.safe_load(_common.ROOT.joinpath(*path_parts).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path_parts[-1]}: top-level is not a mapping")
    return data


@registry.register("validate_full_tier_range", owner="platform")
def validate_full_tier_range(failed: list[str]) -> None:
    """Assert the contract's declared names agree with the live code and the taxonomy."""
    print(f"\n=== Full-tier range contract ({_CONTRACT_BASENAME}) ===")
    try:
        contract = _load(("docs", "contracts", _CONTRACT_BASENAME))
        taxonomy = _load(_TAXONOMY_RELPATH)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: could not load inputs: {type(exc).__name__}: {exc}")
        failed.append(_CHECK_LABEL)
        return

    errors: list[str] = []
    variables = contract.get("variables")
    declared_vars = sorted(variables) if isinstance(variables, dict) else []
    live_vars = sorted([_common.FULL_TIER_PREV_ENV, _common.FULL_TIER_LAST_GREEN_ENV, vacuous_pass.RANGE_PREV_ENV])
    if declared_vars != live_vars:
        errors.append(f"variables drift: contract declares {declared_vars!r}, live is {live_vars!r}")

    ci_rca = contract.get("ci_rca")
    siblings = ci_rca.get("full_tier_siblings") if isinstance(ci_rca, dict) else None
    declared_slugs = siblings.get("slugs") if isinstance(siblings, dict) else None
    live_slugs = list(fingerprint.FULL_TIER_SIBLING_SLUGS)
    if declared_slugs != live_slugs:
        errors.append(f"sibling slugs drift: contract declares {declared_slugs!r}, fingerprint.py has {live_slugs!r}")

    workflows = taxonomy.get("workflows")
    ci_slugs = {
        _slugify_workflow(str(n))
        for n, row in (workflows or {}).items()
        if isinstance(row, dict) and row.get("tier") == _CI_TIER
    }
    for slug in live_slugs:
        if slug not in ci_slugs:
            errors.append(f"sibling slug {slug!r} is not the slug of a {_CI_TIER}-tier workflow in the taxonomy")

    terms = contract.get("terms")
    full_tier = terms.get("full_tier") if isinstance(terms, dict) else None
    full_tier = full_tier if isinstance(full_tier, dict) else {}
    if full_tier.get("job") != last_green.FULL_TIER_JOB:
        errors.append(f"job drift: contract declares {full_tier.get('job')!r}, live is {last_green.FULL_TIER_JOB!r}")
    if full_tier.get("step") != last_green.FULL_TIER_STEP:
        errors.append(f"step drift: contract declares {full_tier.get('step')!r}, live is {last_green.FULL_TIER_STEP!r}")

    if errors:
        print("Contract/code drift:")
        for e in errors:
            print(f"  - {e}")
        failed.append(_CHECK_LABEL)
        return

    print("Contract matches live code (variables, sibling slugs, job and step names).")
    registry.examined(len(live_vars) + len(live_slugs) + 2, unit="contract_fields")
