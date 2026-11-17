"""Load a persona contract and its rendered prompt for the litellm backend."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BUILT = frozenset({"read_input"})
_BUDGET_KEYS = ("max_turns", "max_wall_clock_seconds", "max_revisions")


class RunRefused(Exception):
    """The run cannot start: an unbuilt capability, a bad hash, an unsupported backend or workspace."""


@dataclass(frozen=True)
class LoadedContract:
    persona: str
    contract_version: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: tuple[str, ...]
    max_turns: int
    max_wall_clock_seconds: int
    max_revisions: int
    failure_ids: tuple[str, ...]
    prompt: str
    prompt_sha256: str
    blob_names: tuple[str, ...]
    verdict_consistency: dict[str, Any] | None = None


def _integer(budget: dict[str, Any], key: str) -> int:
    value = budget.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunRefused(f"contract budget {key} is missing or not an integer")
    return value


def _rule_malformed(rule: Any) -> bool:
    def condition_ok(cond: Any) -> bool:
        if not isinstance(cond, dict) or not isinstance(cond.get("array"), str):
            return False
        if set(cond) == {"array"}:
            return True
        values = cond.get("in")
        return (
            set(cond) == {"array", "field", "in"}
            and isinstance(cond["field"], str)
            and isinstance(values, list)
            and bool(values)
            and all(isinstance(v, str) for v in values)
        )

    def case_ok(case: Any) -> bool:
        if not isinstance(case, dict) or set(case) != {"verdict", "any"} or not isinstance(case["verdict"], str):
            return False
        conditions = case["any"]
        return isinstance(conditions, list) and bool(conditions) and all(condition_ok(c) for c in conditions)

    if not isinstance(rule, dict) or set(rule) != {"cases", "otherwise"} or not isinstance(rule["otherwise"], str):
        return True
    cases = rule["cases"]
    return not (isinstance(cases, list) and bool(cases) and all(case_ok(c) for c in cases))


def load(contract_path: Path) -> LoadedContract:
    try:
        data = yaml.safe_load(Path(contract_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RunRefused(f"contract unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise RunRefused("contract is not a mapping")
    prompt_file = Path(contract_path).parent / "rendered" / f"{Path(contract_path).stem}.txt"
    try:
        prompt_bytes = prompt_file.read_bytes()
    except OSError as exc:
        raise RunRefused(f"rendered prompt unreadable: {exc}") from exc
    digest = hashlib.sha256(prompt_bytes).hexdigest()
    if digest != data.get("rendered_prompt_sha256"):
        raise RunRefused("rendered prompt does not match rendered_prompt_sha256")
    capabilities = tuple(str(item) for item in data.get("capabilities") or [])
    blobs: list[str] = []
    properties = (data.get("input_schema") or {}).get("properties") or {}
    for capability in capabilities:
        base, _, parameter = capability.partition(":")
        if base not in BUILT:
            raise RunRefused(f"capability {capability} is not built by the litellm backend")
        if not parameter or parameter not in properties:
            raise RunRefused(f"capability {capability} names no blob of input_schema")
        blobs.append(parameter)
    budget = data.get("budget") or {}
    values = {key: _integer(budget, key) for key in _BUDGET_KEYS}
    failure_ids = tuple(str(row["id"]) for row in data.get("failure_outputs") or [] if isinstance(row, dict) and "id" in row)
    rule = data.get("verdict_consistency")
    if rule is not None and _rule_malformed(rule):
        raise RunRefused("verdict_consistency is malformed")
    return LoadedContract(
        persona=str(data.get("persona", "")),
        contract_version=int(data.get("contract_version", 0)),
        input_schema=data.get("input_schema") or {},
        output_schema=data.get("output_schema") or {},
        capabilities=capabilities,
        max_turns=values["max_turns"],
        max_wall_clock_seconds=values["max_wall_clock_seconds"],
        max_revisions=values["max_revisions"],
        failure_ids=failure_ids,
        prompt=prompt_bytes.decode("utf-8"),
        prompt_sha256=digest,
        blob_names=tuple(blobs),
        verdict_consistency=rule,
    )
