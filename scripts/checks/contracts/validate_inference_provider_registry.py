"""Inference-provider registry parity check (rec-3059 first-wave enforcer build).

Asserts docs/contracts/inference-provider.yaml's provider_defaults.executor_path
(valid_providers/default) agrees with scripts/llm/model_registry.py's
_VALID_PROVIDERS / _DEFAULT_EXECUTOR_PROVIDER, and that retired_providers stay disjoint from the
valid set. When the contract carries a tier_model mapping, a further leg resolves every persona and
tier role through scripts/llm/tier_selection.py (project-configuration.yaml read from the same directory), and a
capability_class leg checks the class vocabulary, the member and class rows and rule (a) (rec-5646).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common, registry

_CONTRACT_NAME = "inference-provider.yaml"


def _check_provider_defaults(failed: list[str], path: Path, provider_defaults: dict) -> None:
    executor_path = provider_defaults.get("executor_path")
    valid_providers = executor_path.get("valid_providers") if isinstance(executor_path, dict) else None
    default_provider = executor_path.get("default") if isinstance(executor_path, dict) else None
    if (
        not isinstance(valid_providers, list)
        or not valid_providers
        or not isinstance(default_provider, str)
        or not default_provider
    ):
        failed.append(
            f"Inference-provider registry: {path} 'provider_defaults.executor_path' is missing a "
            "non-empty valid_providers list or default string"
        )
        return

    from scripts.llm import model_registry  # noqa: PLC0415

    providers_match = set(valid_providers) == set(model_registry._VALID_PROVIDERS)
    default_matches = default_provider == model_registry._DEFAULT_EXECUTOR_PROVIDER
    if not providers_match or not default_matches:
        failed.append(
            f"Inference-provider registry: {_CONTRACT_NAME} declares valid_providers={valid_providers} "
            f"default={default_provider!r} but model_registry.py has "
            f"_VALID_PROVIDERS={sorted(model_registry._VALID_PROVIDERS)} "
            f"_DEFAULT_EXECUTOR_PROVIDER={model_registry._DEFAULT_EXECUTOR_PROVIDER!r}"
        )


def _check_retired_providers(failed: list[str], retired: list) -> None:
    retired_ids: set[str] = set()
    for entry in retired:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            retired_ids.add(entry["id"])

    from scripts.llm import model_registry  # noqa: PLC0415

    overlap = sorted(retired_ids & set(model_registry._VALID_PROVIDERS))
    if overlap:
        failed.append(f"Inference-provider registry: {_CONTRACT_NAME} retired_providers overlap valid providers: {overlap}")


def _check_tier_selection(failed: list[str], target_dir: Path, data: dict) -> int:
    if not isinstance(data.get("tier_model"), dict):
        return 0
    from scripts.llm import tier_selection  # noqa: PLC0415

    try:
        project_configuration = tier_selection._load_one(target_dir / "project-configuration.yaml")
        selections = tier_selection.resolve_all(inference_provider=data, project_configuration=project_configuration)
    except tier_selection.TierSelectionError as exc:
        failed.append(f"Inference-provider registry: {exc}")
        return 0
    return len(selections)


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value) and len(set(value)) == len(value)


def _registry_personas(failed: list[str], target_dir: Path) -> list[str]:
    path = target_dir / "executor-personas.yaml"
    prefix = f"Inference-provider registry: capability_class leg: {path}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"{prefix} could not be read or parsed: {exc}")
        return []
    personas = data.get("personas") if isinstance(data, dict) else None
    if not isinstance(personas, list):
        failed.append(f"{prefix} has no personas list")
        return []
    names: list[str] = []
    for entry in personas:
        if isinstance(entry, dict) and isinstance(entry.get("persona"), str):
            names.append(entry["persona"])
        else:
            failed.append(f"{prefix} has a personas entry that is not a mapping carrying a string persona")
    return names


def _platform_file_findings(failed: list[str], target_dir: Path) -> None:
    path = target_dir.parent.parent / "ontheloop" / "platform.yaml"
    if not path.is_file():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"Inference-provider registry: capability_class leg: {path} could not be read or parsed: {exc}")
        return
    if not isinstance(data, dict):
        failed.append(f"Inference-provider registry: capability_class leg: {path} is not a YAML mapping")
        return
    project = data.get("project")
    if isinstance(project, dict) and "capability_class" in project:
        failed.append(
            f"Inference-provider registry: capability_class leg: {path} sets project.capability_class while the rows' "
            "current_home is null; T4.24 c16 moves the reader first"
        )


def _check_capability_class(failed: list[str], target_dir: Path, data: dict) -> None:
    if not isinstance(data.get("tier_model"), dict):
        return
    from scripts.llm import tier_selection  # noqa: PLC0415

    prefix = "Inference-provider registry: capability_class leg:"
    block = data.get("capability_class")
    if not isinstance(block, dict):
        failed.append(f"{prefix} {_CONTRACT_NAME} has no capability_class mapping")
    else:
        for key in ("values", "claude_code_model_values"):
            if not _string_list(block.get(key)):
                failed.append(f"{prefix} capability_class.{key} is absent, not a list of unique strings")
    personas = _registry_personas(failed, target_dir)
    _platform_file_findings(failed, target_dir)
    config_path = target_dir / "project-configuration.yaml"
    try:
        project_configuration = tier_selection._load_one(config_path)
    except tier_selection.TierSelectionError as exc:
        failed.append(f"{prefix} {config_path.name} unusable: {exc}")
        return
    for finding in tier_selection.capability_class_findings(data, project_configuration, personas):
        failed.append(f"{prefix} {finding}")


@registry.register("validate_inference_provider_registry", owner="platform")
def validate_inference_provider_registry(failed: list[str], *, contracts_dir: Path | None = None) -> None:
    """Fail if provider_defaults or retired_providers drift from scripts/llm/model_registry.py."""
    print("\n=== Inference-provider registry parity ===")
    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"
    path = target_dir / _CONTRACT_NAME

    if not path.is_file():
        failed.append(f"Inference-provider registry: {path} not found")
        registry.skipped(f"{_CONTRACT_NAME} not found")
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        failed.append(f"Inference-provider registry: could not read/parse {path}: {exc}")
        registry.skipped(f"{_CONTRACT_NAME} not parseable")
        return
    if not isinstance(data, dict):
        failed.append(f"Inference-provider registry: {path} is not a YAML mapping")
        registry.skipped(f"{_CONTRACT_NAME} not a YAML mapping")
        return

    error_count_before = len(failed)
    examined_count = 0

    provider_defaults = data.get("provider_defaults")
    if not isinstance(provider_defaults, dict) or not provider_defaults:
        failed.append(f"Inference-provider registry: {path} missing or empty top-level 'provider_defaults'")
    else:
        examined_count += 1
        _check_provider_defaults(failed, path, provider_defaults)

    retired_providers = data.get("retired_providers")
    if not isinstance(retired_providers, list) or not retired_providers:
        failed.append(f"Inference-provider registry: {path} missing or empty top-level 'retired_providers'")
    else:
        examined_count += len(retired_providers)
        _check_retired_providers(failed, retired_providers)

    resolved = _check_tier_selection(failed, target_dir, data)
    _check_capability_class(failed, target_dir, data)
    examined_count += resolved

    registry.examined(examined_count, unit="provider_registry_entries")

    if len(failed) == error_count_before:
        print(f"  PASS: {_CONTRACT_NAME} provider_defaults and retired_providers agree with model_registry.py.")
        if resolved:
            print(f"  PASS: tier selection resolves {resolved} persona-role pairs.")
