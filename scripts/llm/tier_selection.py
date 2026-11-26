"""Resolve each verdict persona and tier role to one model selection (T4.32 c1, plan 1 of 2).

Pure over two loaded mappings: docs/contracts/inference-provider.yaml and
docs/contracts/project-configuration.yaml. No AWS, no environment reads, no network, no model call.
Refusals follow Decision 173 clause 2(a), 2(b), 2(d) and clause 4, and Decision 206 clause 3.

An end_user resolution reads this repository's own tier rows only until ontheloop/platform.yaml
exists (T4.24 c16, SA-13 in docs/contracts/ports.yaml).

CLI: python -m scripts.llm.tier_selection --check [--root PATH]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_INFERENCE_PROVIDER = "docs/contracts/inference-provider.yaml"
_PROJECT_CONFIGURATION = "docs/contracts/project-configuration.yaml"
_ROLES = ("primary", "warm_fetched_escape_hatch")
_MEMBERS_ROW = "project.capability_class.members"
_CLASSES_ROW = "project.capability_class.classes"
_ENV_ROW_PREFIX = "ports.run_persona.litellm.api_key."  # pragma: allowlist secret


class TierSelectionError(ValueError):
    """A selection the contracts do not permit; the message names the persona, role and rule."""


@dataclass(frozen=True)
class Selection:
    persona: str
    role: str
    provider: str
    model: str
    thinking: str
    effort: str
    billing_shape: str
    cold_start_assertion: str
    env_name: str


def _refuse(persona: str, role: str, rule: str, detail: str) -> TierSelectionError:
    return TierSelectionError(f"tier selection refused for {persona} {role} [{rule}]: {detail}")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tier_row(role: str, inference_provider: dict[str, Any]) -> dict[str, Any] | None:
    tiers = _mapping(inference_provider.get("tier_model")).get("tiers")
    for row in tiers if isinstance(tiers, list) else []:
        if isinstance(row, dict) and row.get("role") == role:
            return row
    return None


def _models_row(tier: dict[str, Any], inference_provider: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ref = tier.get("selection_ref")
    name = ref.rsplit(".", 1)[-1] if isinstance(ref, str) else ""
    rows = _mapping(_mapping(inference_provider.get("model_id_formats")).get("litellm_tier_models"))
    row = rows.get(name)
    return name, row if isinstance(row, dict) else {}


def _prefix(persona: str, role: str, inference_provider: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    tier = _tier_row(role, inference_provider)
    if tier is None or str(tier.get("state", "")).startswith("Deferred"):
        raise _refuse(persona, role, "tier_not_committed", "no committed tier_model.tiers row carries this role")
    name, row = _models_row(tier, inference_provider)
    models = row.get("models")
    default = row.get("default_model")
    if not row or not isinstance(models, list) or not isinstance(default, str) or default not in models:
        raise _refuse(persona, role, "tier_models_row", f"litellm_tier_models.{name} lacks a default_model listed in models")
    prefixes = {m.split("/", 1)[0] for m in models if isinstance(m, str)}
    if len(prefixes) != 1:
        raise _refuse(
            persona, role, "tier_models_row", f"litellm_tier_models.{name} models mix LiteLLM prefixes {sorted(prefixes)}"
        )
    return tier, row, default.split("/", 1)[0]


def _persona_names(inference_provider: dict[str, Any]) -> list[str]:
    block = _mapping(_mapping(inference_provider.get("model_id_formats")).get("litellm_persona_selection"))
    names = block.get("personas")
    return list(names) if isinstance(names, list) else []


def _default_local(project_configuration: dict[str, Any], row_id: str) -> Any:
    rows = project_configuration.get("settings")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("id") == row_id:
            return _mapping(row.get("defaults")).get("local")
    return None


def _class_settings(persona: str, role: str, project_configuration: dict[str, Any]) -> dict[str, Any]:
    members = _default_local(project_configuration, _MEMBERS_ROW)
    classes = _default_local(project_configuration, _CLASSES_ROW)
    if not isinstance(members, dict) or not isinstance(classes, dict):
        raise _refuse(persona, role, "capability_class", f"rows {_MEMBERS_ROW} and {_CLASSES_ROW} must be mappings")
    name = members.get(persona)
    if not isinstance(name, str):
        raise _refuse(persona, role, "capability_class", f"{_MEMBERS_ROW} has no string class for {persona}")
    entry = classes.get(name)
    if not isinstance(entry, dict):
        raise _refuse(persona, role, "capability_class", f"{_CLASSES_ROW} has no entry for class {name!r}")
    settings = entry.get(role)
    if not isinstance(settings, dict):
        raise _refuse(persona, role, "capability_class", f"class {name!r} has no settings mapping for this role")
    return settings


def _row_defaults(project_configuration: dict[str, Any], row_id: str, found: list[str]) -> dict[str, Any] | None:
    settings = project_configuration.get("settings")
    rows = settings if isinstance(settings, list) else []
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == row_id), None)
    if row is None:
        found.append(f"row {row_id} is missing")
        return None
    defaults = _mapping(row.get("defaults"))
    if not isinstance(defaults.get("local"), dict):
        found.append(f"row {row_id} defaults.local is not a mapping")
    if defaults.get("local") != defaults.get("cloud"):
        found.append(f"row {row_id} defaults.local differs from defaults.cloud")
    return defaults


def _role_findings(name: str, role: str, setting: Any, vocabulary: dict[str, Any]) -> list[str]:
    if not isinstance(setting, dict):
        return [f"class {name!r} role {role} is not a mapping"]
    found = [
        f"class {name!r} role {role} holds key {k!r}, not thinking or effort"
        for k in sorted(set(setting) - {"thinking", "effort"})
    ]
    for key in ("thinking", "effort"):
        allowed = vocabulary.get(key)
        if setting.get(key) not in (allowed if isinstance(allowed, list) else []):
            found.append(f"class {name!r} role {role} {key} {setting.get(key)!r} is outside the vocabulary")
    return found


def _class_findings(name: str, entry: Any, values: list[Any], models: list[Any], vocabulary: dict[str, Any]) -> list[str]:
    found = [f"classes row key {name!r} is outside capability_class.values"] if name not in values else []
    if not isinstance(entry, dict):
        return [*found, f"class {name!r} is not a mapping"]
    if entry.get("claude_code_model") not in models:
        found.append(
            f"class {name!r} claude_code_model {entry.get('claude_code_model')!r} is outside claude_code_model_values"
        )
    found += [
        f"class {name!r} has role key {k!r}, not primary or warm_fetched_escape_hatch"
        for k in entry
        if k != "claude_code_model" and k not in _ROLES
    ]
    for role in _ROLES:
        found += _role_findings(name, role, entry.get(role), vocabulary)
    return found


def _member_findings(members: dict[str, Any], classes: dict[str, Any], persona_names: list[Any]) -> list[str]:
    found: list[str] = []
    for member, name in members.items():
        if not isinstance(name, str):
            found.append(f"member {member} has a non-string class {name!r}")
        elif name not in classes:
            found.append(f"member {member} names class {name!r}, which the classes row lacks")
    for persona in persona_names:
        if not isinstance(persona, str):
            found.append(f"personas element {persona!r} is not a string")
        elif persona not in members:
            found.append(f"persona {persona} has no member row")
    return found


def _rule_a_findings(members: dict[str, Any], values: list[Any]) -> list[str]:
    rank = {v: i for i, v in enumerate(values)}  # values run highest first
    critic, scout = members.get("plan_critic"), members.get("decision_scout")
    ranked = isinstance(critic, str) and isinstance(scout, str) and critic in rank and scout in rank
    if ranked and rank[critic] < rank[scout]:
        return []
    return [f"rule (a): plan_critic class {critic!r} does not rank strictly above decision_scout class {scout!r}"]


def capability_class_findings(
    inference_provider: dict[str, Any], project_configuration: dict[str, Any], registry_personas: list[str]
) -> list[str]:
    """One string per breach of the capability_class rows; never raises."""
    found: list[str] = []
    members_defaults = _row_defaults(project_configuration, _MEMBERS_ROW, found) or {}
    classes_defaults = _row_defaults(project_configuration, _CLASSES_ROW, found) or {}
    members = _mapping(members_defaults.get("local"))
    classes = _mapping(classes_defaults.get("local"))
    block = _mapping(inference_provider.get("capability_class"))
    raw_values, raw_models = block.get("values"), block.get("claude_code_model_values")
    values: list[Any] = raw_values if isinstance(raw_values, list) else []
    models: list[Any] = raw_models if isinstance(raw_models, list) else []
    selection = _mapping(_mapping(inference_provider.get("model_id_formats")).get("litellm_persona_selection"))
    vocabulary = _mapping(selection.get("vocabulary"))
    found += _member_findings(members, classes, [*registry_personas, *_persona_names(inference_provider)])
    for name, entry in classes.items():
        found += _class_findings(name, entry, values, models, vocabulary)
    return found + _rule_a_findings(members, values)


def resolve(
    persona: str,
    role: str,
    *,
    inference_provider: dict[str, Any],
    project_configuration: dict[str, Any],
    credential_holder: str = "on_the_loop",
) -> Selection:
    if role not in _ROLES:
        raise _refuse(persona, role, "unknown_role", f"known roles are {list(_ROLES)}")
    model_formats = _mapping(inference_provider.get("model_id_formats"))
    personas = _persona_names(inference_provider)
    if any(not isinstance(n, str) for n in personas):
        raise _refuse(persona, role, "capability_class", "litellm_persona_selection.personas holds a non-string element")
    if persona not in personas:
        raise _refuse(persona, role, "unknown_persona", f"known personas are {personas}")
    holders = _mapping(_mapping(_mapping(inference_provider.get("vocabulary")).get("axes")).get("credential_holder")).get(
        "values"
    )
    if credential_holder not in (holders if isinstance(holders, list) else []):
        raise _refuse(persona, role, "credential_holder_vocabulary", f"{credential_holder!r} is not in the axis values")
    if credential_holder == "end_user" and role != "primary":
        raise _refuse(
            persona,
            role,
            "end_user_primary_only",
            "until ontheloop/platform.yaml exists an end_user resolution reads this repository's own rows, "
            "so only the primary role resolves (T4.24 c16, SA-13)",
        )

    tier, row, prefix = _prefix(persona, role, inference_provider)
    default = row["default_model"]
    retired = {r.get("id") for r in inference_provider.get("retired_providers", []) if isinstance(r, dict)}
    if prefix in retired:
        raise _refuse(persona, role, "2(a) retired_provider", f"{prefix!r} is listed in retired_providers")
    provider = str(row.get("provider", ""))
    vendor = provider.lower().removesuffix("-direct")
    if vendor != prefix:
        raise _refuse(
            persona, role, "provider_prefix_mismatch", f"provider {provider!r} differs from LiteLLM prefix {prefix!r}"
        )
    if credential_holder == "on_the_loop":
        other = next(r for r in _ROLES if r != role)
        if _prefix(persona, other, inference_provider)[2] == prefix:
            raise _refuse(persona, role, "2(b) vendor_independence", f"both roles resolve to the {prefix!r} vendor")
    billing = tier.get("billing_shape")
    if role == "primary" and billing != "metered_marginal":
        raise _refuse(
            persona, role, "2(d) primary_billing_shape", f"primary billing_shape is {billing!r}, not metered_marginal"
        )
    assertion = tier.get("cold_start_assertion")
    if billing == "local":
        raise _refuse(persona, role, "local_billing_shape", "no tier role may carry billing_shape local")
    expected = {"fixed_non_rollover_allowance": "remaining_allowance", "metered_marginal": "credential_liveness"}.get(
        str(billing)
    )
    if assertion != expected:
        raise _refuse(
            persona,
            role,
            "clause4 cold_start_assertion",
            f"billing_shape {billing!r} requires {expected!r}, found {assertion!r}",
        )

    selection_block = _mapping(model_formats.get("litellm_persona_selection"))
    vocabulary = _mapping(selection_block.get("vocabulary"))
    settings = _class_settings(persona, role, project_configuration)
    for key in ("thinking", "effort"):
        allowed = vocabulary.get(key)
        if settings.get(key) not in (allowed if isinstance(allowed, list) else []):
            raise _refuse(
                persona, role, "persona_settings", f"{key} {settings.get(key)!r} is outside the vocabulary {allowed}"
            )

    row_id = _ENV_ROW_PREFIX + role
    matched_rows = [r for r in project_configuration.get("settings", []) if isinstance(r, dict) and r.get("id") == row_id]
    if not matched_rows:
        raise _refuse(persona, role, "api_key_row", f"project-configuration has no row {row_id}")
    declared = matched_rows[0].get("variable")
    if declared != f"{prefix.upper()}_API_KEY":
        raise _refuse(persona, role, "api_key_row", f"{row_id} variable {declared!r} is not {prefix.upper()}_API_KEY")

    return Selection(
        persona=persona,
        role=role,
        provider=provider,
        model=default,
        thinking=settings["thinking"],
        effort=settings["effort"],
        billing_shape=str(billing),
        cold_start_assertion=str(assertion),
        env_name=declared,
    )


def resolve_all(
    *,
    inference_provider: dict[str, Any],
    project_configuration: dict[str, Any],
    credential_holder: str = "on_the_loop",
) -> list[Selection]:
    personas = _persona_names(inference_provider)
    roles = _ROLES[:1] if credential_holder == "end_user" else _ROLES
    return [
        resolve(
            persona,
            role,
            inference_provider=inference_provider,
            project_configuration=project_configuration,
            credential_holder=credential_holder,
        )
        for persona in personas
        for role in roles
    ]


def _load_one(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TierSelectionError(f"cannot read or parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TierSelectionError(f"{path} is not a YAML mapping")
    return data


def load(root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    base = root if root is not None else _ROOT
    return _load_one(base / _INFERENCE_PROVIDER), _load_one(base / _PROJECT_CONFIGURATION)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve verdict persona model selections")
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        provider, config = load(args.root)
        selections = resolve_all(inference_provider=provider, project_configuration=config)
    except TierSelectionError as exc:
        print(exc)
        return 1
    for s in selections:
        print(f"{s.persona} {s.role} {s.model} thinking={s.thinking} effort={s.effort} key={s.env_name}")
    print("TIER-SELECTION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
