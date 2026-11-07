"""Data-modeling standard backstop guard (PLAN-scd2-modeling-defaults).

Diff-aware: returns early (pass) unless docs/contracts/storage-substrate.yaml or
docs/contracts/data-modeling-standard.yaml is in the changed-files set vs origin/main. Unlike
validate_contract_drift's unconditional structural pass (every ritual contract, every run), this
check's data-modeling-standard.yaml shape validation runs on the PR tier only when that file is
itself in the diff -- sound, because git guarantees the file cannot change without appearing in
the diff.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import _common, registry

_REQUIRED_STANDARD_SECTIONS = ("rules", "write_modes", "indexes", "arrival_expectations")


def _load_yaml(path: Path) -> tuple[object | None, str | None]:
    """Returns (data, None) on success or (None, error-message) on any read/parse failure."""
    if not path.is_file():
        return None, f"{path} not found"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"could not read {path}: {exc}"
    except yaml.YAMLError as exc:
        return None, f"could not parse {path}: {exc}"


def _check_storage_substrate(failed: list[str], path: Path) -> int:
    """Returns the number of table entries judged (0 when the file never reached the judgment)."""
    data, err = _load_yaml(path)
    if err is not None:
        failed.append(f"Data-model standard: {err}")
        return 0
    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        failed.append(f"Data-model standard: {path} missing a 'tables' mapping")
        return 0

    offenders = sorted(
        name
        for name, entry in data["tables"].items()
        if isinstance(entry, dict) and entry.get("merge_key") and (not entry.get("grain") or not entry.get("write_mode"))
    )
    if offenders:
        failed.append(
            "Data-model standard: merge_key-bearing table(s) in storage-substrate.yaml missing "
            f"grain and/or write_mode: {offenders}"
        )
    else:
        print("  PASS: storage-substrate.yaml -- every merge_key-bearing table declares grain + write_mode.")
    return len(data["tables"])


def _check_standard_contract(failed: list[str], path: Path) -> int:
    """Returns the number of required sections judged (0 when the file never reached the judgment)."""
    data, err = _load_yaml(path)
    if err is not None:
        failed.append(f"Data-model standard: {err}")
        return 0
    if not isinstance(data, dict):
        failed.append(f"Data-model standard: {path} is not a YAML mapping")
        return 0
    missing = [section for section in _REQUIRED_STANDARD_SECTIONS if section not in data]
    if missing:
        failed.append(f"Data-model standard: {path} missing required section(s): {missing}")
    else:
        print("  PASS: data-modeling-standard.yaml -- all required sections present.")
    return len(_REQUIRED_STANDARD_SECTIONS)


@registry.register("validate_data_model_standard", owner="platform")
def validate_data_model_standard(
    failed: list[str],
    *,
    contracts_dir: Path | None = None,
    changed_files: list[str] | None = None,
) -> None:
    """Diff-aware backstop for the SCD2/append_only data-modeling standard.

    contracts_dir / changed_files: overrides for test isolation (default to ROOT/docs/contracts
    and _common.get_changed_files()).

    Declares skipped when neither trigger file changed (the diff-aware precondition is unmet), else
    examined over the entries actually judged (unit "entries"): every storage-substrate.yaml table
    entry plus every required data-modeling-standard.yaml section, for each changed file that
    reached its judgment.
    """
    print("\n=== Data-modeling standard gate ===")

    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"
    changed = changed_files if changed_files is not None else _common.get_changed_files()
    changed_names = {Path(f).name for f in changed}

    substrate_changed = "storage-substrate.yaml" in changed_names
    standard_changed = "data-modeling-standard.yaml" in changed_names

    if not substrate_changed and not standard_changed:
        print("  SKIP: neither storage-substrate.yaml nor data-modeling-standard.yaml changed.")
        registry.skipped("neither storage-substrate.yaml nor data-modeling-standard.yaml changed")
        return

    judged = 0
    if substrate_changed:
        judged += _check_storage_substrate(failed, target_dir / "storage-substrate.yaml")
    if standard_changed:
        judged += _check_standard_contract(failed, target_dir / "data-modeling-standard.yaml")
    registry.examined(judged, unit="entries")
