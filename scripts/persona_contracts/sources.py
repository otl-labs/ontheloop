"""Read-only lookups the persona checks share: roadmap criteria, NAMED_READS keys and persona backends."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
_NAMED_READS_REL = "src/common/ducklake_named_reads.py"
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def open_criteria(root: Path) -> set[str]:
    """Every "<item>:<criterion>" whose status is open on the roadmap under `root`."""
    data = yaml.load((root / _ROADMAP_REL).read_text(encoding="utf-8"), Loader=_LOADER)
    found: set[str] = set()
    for item in data.get("tier_items", []):
        for crit in item.get("exit_criteria", []) or []:
            if isinstance(crit, dict) and crit.get("status") == "open":
                found.add(f"{item.get('id')}:{crit.get('id')}")
    return found


def named_read_keys(root: Path) -> set[str]:
    """Keys of NAMED_READS whose agent_facing is true, read from the module file under `root`."""
    name = f"_persona_named_reads_{abs(hash(str(root)))}"
    spec = importlib.util.spec_from_file_location(name, root / _NAMED_READS_REL)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_NAMED_READS_REL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return {key for key, nr in module.NAMED_READS.items() if nr.agent_facing}


def resolve_values_ref(root: Path, ref: str) -> list[Any] | None:
    """Resolve "<file>#<dotted.path>.values" style refs: the `values` list of the mapping the path names."""
    rel, _, dotted = ref.partition("#")
    node: Any = yaml.load((root / rel).read_text(encoding="utf-8"), Loader=_LOADER)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    values = node.get("values") if isinstance(node, dict) else None
    return values if isinstance(values, list) else None
