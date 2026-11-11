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
_API_KEY_ROW_PREFIX = "ports.run_persona.litellm.api_key."  # pragma: allowlist secret


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
    api_key_variable: str


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
    personas = _mapping(_mapping(model_formats.get("litellm_persona_selection")).get("personas"))
    if persona not in personas:
        raise _refuse(persona, role, "unknown_persona", f"known personas are {list(personas)}")
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
    settings = _mapping(personas[persona]).get(role)
    if not isinstance(settings, dict):
        raise _refuse(persona, role, "persona_settings", "the persona has no settings for this role")
    for key in ("thinking", "effort"):
        allowed = vocabulary.get(key)
        if settings.get(key) not in (allowed if isinstance(allowed, list) else []):
            raise _refuse(
                persona, role, "persona_settings", f"{key} {settings.get(key)!r} is outside the vocabulary {allowed}"
            )

    row_id = _API_KEY_ROW_PREFIX + role
    key_rows = [r for r in project_configuration.get("settings", []) if isinstance(r, dict) and r.get("id") == row_id]
    if not key_rows:
        raise _refuse(persona, role, "api_key_row", f"project-configuration has no row {row_id}")
    variable = key_rows[0].get("variable")
    if variable != f"{prefix.upper()}_API_KEY":
        raise _refuse(persona, role, "api_key_row", f"{row_id} variable {variable!r} is not {prefix.upper()}_API_KEY")

    return Selection(
        persona=persona,
        role=role,
        provider=provider,
        model=default,
        thinking=settings["thinking"],
        effort=settings["effort"],
        billing_shape=str(billing),
        cold_start_assertion=str(assertion),
        api_key_variable=variable,
    )


def resolve_all(
    *,
    inference_provider: dict[str, Any],
    project_configuration: dict[str, Any],
    credential_holder: str = "on_the_loop",
) -> list[Selection]:
    personas = _mapping(
        _mapping(_mapping(inference_provider.get("model_id_formats")).get("litellm_persona_selection")).get("personas")
    )
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
        print(f"{s.persona} {s.role} {s.model} thinking={s.thinking} effort={s.effort} key={s.api_key_variable}")
    print("TIER-SELECTION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
