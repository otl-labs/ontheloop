"""North-star check (Decision 243): shape, carriers, order and projections of docs/contracts/north-star.yaml.

Legs: CONTRACT (the Class D header), CARRIED (every carried_by number is a Decision header), ORDER (partial-order ids are
known, active and listed once), IDS (NS.1-NS.5 exist and no base id is removed), ROADMAP (the roadmap north_star key is
the pointer), BOUNDARY (boundary_patterns covers the file) and README (the generated block equals the render).
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import _common, registry
from scripts.contract_rules import parse_yaml
from scripts.decisions_md import decision_header_numbers
from scripts.north_star_models import NorthStarDocument, parse_north_star

_CONTRACT = "north-star.yaml"
_LABEL = "North star"
_REL = "docs/contracts/north-star.yaml"
_CORE_IDS = tuple(f"NS.{n}" for n in range(1, 6))
_POINTER = {"source": _REL}
_BEGIN = (
    "<!-- north-star:begin generated from docs/contracts/north-star.yaml by validate_north_star;"
    " edit the contract, never this block -->"
)
_END = "<!-- north-star:end -->"
_INTRO = "The mission, and each entry's why, failure signal and binding scope, live in `docs/contracts/north-star.yaml`."


def render_readme_block(doc: NorthStarDocument) -> str:
    """The README headline block, begin marker to end marker inclusive."""
    bullets = [f"- **{p.id}** - {p.principle}" for p in doc.principles if p.status == "active"]
    return "\n".join([_BEGIN, _INTRO, "", *bullets, _END])


def _contract_leg(doc: NorthStarDocument, headers: set[int]) -> list[str]:
    contract = doc.contract
    out: list[str] = []
    if contract.get("subject") != "north-star":
        out.append("CONTRACT: contract.subject is not north-star")
    if (contract.get("evaluator") or {}).get("check") != "validate_north_star":
        out.append("CONTRACT: contract.evaluator.check is not validate_north_star")
    found = re.search(r"Decision (\d+)", str(contract.get("ratified_via", "")))
    if not found or int(found.group(1)) not in headers:
        out.append(f"CONTRACT: ratified_via {contract.get('ratified_via')!r} names no existing Decision header")
    return out


def _carried_leg(doc: NorthStarDocument, headers: set[int]) -> list[str]:
    carriers = [("mission", doc.mission.carried_by), *((p.id, p.carried_by) for p in doc.principles)]
    return [
        f"CARRIED: {name} carried_by {n} is not a Decision header" for name, nums in carriers for n in nums if n not in headers
    ]


def _order_leg(doc: NorthStarDocument) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ident in (i for group in doc.partial_order.groups for i in group):
        if ident not in doc.ids():
            out.append(f"ORDER: {ident} is not a known entry")
        elif ident not in doc.active_ids():
            out.append(f"ORDER: {ident} is retired")
        if ident in seen:
            out.append(f"ORDER: {ident} appears more than once")
        seen.add(ident)
    return out


def _base_ids(root: Path, base: str) -> set[str] | None:
    result = _common.run(
        ["git", "show", f"{base}:{_REL}"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root
    )
    if result.returncode != 0:
        return None
    try:
        return parse_north_star(result.stdout, f"{base}:{_REL}").ids()
    except ValueError:
        return None


def _ids_leg(doc: NorthStarDocument, root: Path) -> list[str]:
    out = [f"IDS: core entry {i} is missing" for i in _CORE_IDS if i not in doc.ids()]
    base = _common.status_diff_base(root)
    if base is None:
        print("  SKIP: IDS leg has no diff base")
        return out
    base_ids = _base_ids(root, base)
    if base_ids is not None:
        out += [
            f"IDS: {i} is on the base and removed at head (retire by status, never delete)"
            for i in sorted(base_ids - doc.ids())
        ]
    return out


def _roadmap_leg(root: Path) -> list[str]:
    try:
        data = parse_yaml((root / "docs" / "ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8"))
        value = data.get("north_star")
    except (OSError, AttributeError, ValueError) as exc:
        return [f"ROADMAP: docs/ROADMAP-PLATFORM.yaml is unreadable: {exc}"]
    if value != _POINTER:
        return [f"ROADMAP: north_star is {value!r}, not exactly {_POINTER!r}"]
    return []


def _boundary_leg(root: Path) -> list[str]:
    try:
        caps = parse_yaml((root / "config" / "agent" / "executor" / "capabilities.yaml").read_text(encoding="utf-8"))
        patterns = caps.get("boundary_patterns") or []
    except (OSError, AttributeError, ValueError) as exc:
        return [f"BOUNDARY: capabilities.yaml is unreadable: {exc}"]
    if any(isinstance(p, str) and p in _REL for p in patterns):
        return []
    return [f"BOUNDARY: no boundary_patterns entry covers {_REL}"]


def _readme_leg(doc: NorthStarDocument, root: Path) -> list[str]:
    try:
        lines = (root / "README.md").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"README: README.md is unreadable: {exc}"]
    begins = [i for i, line in enumerate(lines) if line.startswith("<!-- north-star:begin")]
    ends = [i for i, line in enumerate(lines) if line == _END]
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        return [f"README: expected one begin marker then one end marker, found {len(begins)} begin and {len(ends)} end"]
    if "\n".join(lines[begins[0] : ends[0] + 1]) != render_readme_block(doc):
        return ["README: the North Star block differs from the contract's render"]
    return []


@registry.register("validate_north_star", owner="platform")
def validate_north_star(failed: list[str], root: Path | None = None) -> None:
    """Fail if the north-star contract is malformed, mis-carried, mis-ordered, or its projections drift."""
    print("\n=== North star (north-star.yaml) ===")
    base_dir = root if root is not None else _common.ROOT
    path = base_dir / "docs" / "contracts" / _CONTRACT
    try:
        doc = parse_north_star(path.read_text(encoding="utf-8"), str(path))
    except (OSError, ValueError) as exc:
        failed.append(_LABEL)
        print(f"  FAIL: {path}: {exc}")
        registry.skipped(f"{_CONTRACT} not loadable")
        return
    headers = decision_header_numbers(paths=[base_dir / "docs" / "DECISIONS.md", base_dir / "docs" / "DECISIONS_ARCHIVE.md"])
    findings = [
        *_contract_leg(doc, headers),
        *_carried_leg(doc, headers),
        *_order_leg(doc),
        *_ids_leg(doc, base_dir),
        *_roadmap_leg(base_dir),
        *_boundary_leg(base_dir),
        *_readme_leg(doc, base_dir),
    ]
    if findings:
        failed.append(_LABEL)
        for finding in findings:
            print(f"  FAIL: {finding}")
    else:
        print(f"  PASS: {_CONTRACT} satisfies every rule and both projections equal it.")
    registry.examined(
        len(doc.principles) + 1 + len(doc.mission.carried_by) + sum(len(p.carried_by) for p in doc.principles),
        unit="north_star_entries_and_references",
    )
