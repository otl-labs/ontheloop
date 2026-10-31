"""Row loader for plan_lint: reads derived_findings and frozen_companions from the plan-obligations contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_rows(contract_path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], str | None]:
    """Return (rows by id, frozen_companions, error). Never raises; a missing or malformed section yields an error."""
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, [], f"derived_findings: could not read {contract_path}: {exc}"
    section = data.get("derived_findings") if isinstance(data, dict) else None
    rows = section.get("rows") if isinstance(section, dict) else None
    if not isinstance(rows, dict) or not rows:
        return {}, [], f"derived_findings: {contract_path} has no non-empty derived_findings.rows mapping"
    bad = sorted(str(k) for k, v in rows.items() if not isinstance(v, dict) or not isinstance(v.get("gating"), bool))
    if bad:
        return {}, [], f"derived_findings: rows without a boolean gating flag: {', '.join(bad)}"
    frozen = data.get("frozen_companions")
    if frozen is None:
        frozen = []
    if not isinstance(frozen, list) or not all(isinstance(r, dict) for r in frozen):
        return {}, [], f"derived_findings: {contract_path} frozen_companions is not a list of mappings"
    return dict(rows), frozen, None
