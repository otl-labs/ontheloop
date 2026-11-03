"""Ports contract check (Decisions 211 and 212): schema and cross-reference rules for docs/contracts/ports.yaml.

The contract is the sole port registry: ports with required verbs, adapters and tiers, a credential-free
oracle or a recorded oracle gap, a conformance-suite pointer, cross-port coupling findings and the
repository-structure assumptions otl init must not make. Rules enforced here:

(a) coupling findings name only declared ports; (b) every owning_item resolves (tier item, optionally
<item>:<criterion>; rec-NNNN by format; dec-NNN against the Decision corpus); (c) implemented adapter
locations and present suite paths exist; (d) finding and assumption locations are repo paths or dec ids;
(e) semantics keys name a real field; (f) the ambient trigger file names this contract; (g) oracle
consistency; (h) run_persona adapters and inference-provider.yaml persona_backend values agree both ways.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scripts.checks import _common, registry

_CONTRACT_BASENAME = "ports.yaml"
_CHECK_LABEL = "Ports contract"
_SNAKE = r"^[a-z][a-z0-9_]*$"
_DOC_KEYS = frozenset({"adapter_tiers", "adapter_status"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Adapter(_Strict):
    id: str
    tier: Literal["supported", "community", "experimental"]
    status: Literal["implemented", "pending"]
    location: str | None = None
    owning_item: str | None = None

    @model_validator(mode="after")
    def _companions(self) -> "Adapter":
        if not re.match(_SNAKE, self.id):
            raise ValueError(f"adapter id {self.id!r} is not snake_case")
        if self.status == "implemented" and (not self.location or self.owning_item):
            raise ValueError(f"implemented adapter {self.id!r} needs location and no owning_item")
        if self.status == "pending" and (not self.owning_item or self.location):
            raise ValueError(f"pending adapter {self.id!r} needs owning_item and no location")
        return self


class Suite(_Strict):
    status: Literal["present", "pending"]
    path: str | None = None
    owning_item: str | None = None

    @model_validator(mode="after")
    def _companions(self) -> "Suite":
        if self.status == "present" and not self.path:
            raise ValueError("present suite needs a path")
        if self.status == "pending" and not self.owning_item:
            raise ValueError("pending suite needs an owning_item")
        return self


class Oracle(_Strict):
    kind: Literal["adapter", "harness"]
    ref: str
    status: Literal["present", "pending"]

    @model_validator(mode="after")
    def _ref(self) -> "Oracle":
        if not self.ref.strip():
            raise ValueError("oracle ref is blank")
        return self


class Port(_Strict):
    id: str
    required_verbs: list[str]
    verbs_status: Literal["settled", "provisional"]
    adapters: list[Adapter]
    oracle: Oracle | None
    oracle_gap: str | None = None
    conformance_suite: Suite

    @model_validator(mode="after")
    def _shape(self) -> "Port":
        if not re.match(_SNAKE, self.id):
            raise ValueError(f"port id {self.id!r} is not snake_case")
        if not self.required_verbs or len(set(self.required_verbs)) != len(self.required_verbs):
            raise ValueError(f"port {self.id!r} required_verbs must be non-empty and unique")
        if any(not re.match(_SNAKE, v) for v in self.required_verbs):
            raise ValueError(f"port {self.id!r} has a non-snake_case verb")
        ids = [a.id for a in self.adapters]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError(f"port {self.id!r} adapters must be non-empty with unique ids")
        has_gap = bool(self.oracle_gap and self.oracle_gap.strip())
        if self.oracle is None and not has_gap:
            raise ValueError(f"port {self.id!r} has neither an oracle nor an oracle_gap")
        if self.oracle is not None and self.oracle_gap is not None:
            raise ValueError(f"port {self.id!r} carries an oracle_gap beside an oracle")
        return self


class CouplingFinding(_Strict):
    id: str
    ports: list[str]
    location: str
    description: str
    status: Literal["open", "accepted", "resolved"]
    owning_item: str

    @model_validator(mode="after")
    def _shape(self) -> "CouplingFinding":
        if not re.match(r"^PC-\d{2,}$", self.id):
            raise ValueError(f"coupling finding id {self.id!r} must match PC-NN")
        if len(set(self.ports)) < 2:
            raise ValueError(f"coupling finding {self.id} must name at least two distinct ports")
        if not self.description.strip():
            raise ValueError(f"coupling finding {self.id} has a blank description")
        return self


class StructuralAssumption(_Strict):
    id: str
    assumption: str
    location: str
    status: Literal["open", "accepted", "resolved"]
    owning_item: str

    @model_validator(mode="after")
    def _shape(self) -> "StructuralAssumption":
        if not re.match(r"^SA-\d{2,}$", self.id):
            raise ValueError(f"structural assumption id {self.id!r} must match SA-NN")
        if not self.assumption.strip():
            raise ValueError(f"structural assumption {self.id} has a blank assumption")
        return self


class PortsBody(_Strict):
    contract: dict[str, Any]
    amendment_log: list[Any]
    semantics: dict[str, str]
    ambient_trigger: str
    ports: list[Port]
    coupling_findings: list[CouplingFinding]
    structural_assumptions: list[StructuralAssumption]

    @model_validator(mode="after")
    def _unique(self) -> "PortsBody":
        if not self.ports:
            raise ValueError("ports must be non-empty")
        for label, rows in (
            ("port", self.ports),
            ("coupling finding", self.coupling_findings),
            ("structural assumption", self.structural_assumptions),
        ):
            ids = [r.id for r in rows]
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate {label} ids")
        return self


def _dec_numbers(root: Path) -> set[int]:
    from scripts.decisions_md import decision_header_numbers  # noqa: PLC0415

    return decision_header_numbers([root / "docs" / "DECISIONS.md", root / "docs" / "DECISIONS_ARCHIVE.md"])


def _roadmap_criteria(root: Path) -> dict[str, set[str]]:
    from scripts.platform_roadmap_state import load  # noqa: PLC0415

    doc = load(root / "docs" / "ROADMAP-PLATFORM.yaml")
    return {item.id: {c.id for c in item.exit_criteria} for item in doc.tier_items}


def _resolves(ref: str, items: dict[str, set[str]], decs: set[int]) -> bool:
    if re.match(r"^rec-\d+$", ref):
        return True
    dec = re.match(r"^dec-(\d+)$", ref)
    if dec:
        return int(dec.group(1)) in decs
    item, _, crit = ref.partition(":")
    return item in items and (not crit or crit in items[item])


def _owning_refs(body: PortsBody) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for port in body.ports:
        refs += [(f"port {port.id} adapter {a.id}", a.owning_item) for a in port.adapters if a.owning_item]
        if port.conformance_suite.owning_item:
            refs.append((f"port {port.id} suite", port.conformance_suite.owning_item))
        if port.oracle is not None and port.oracle.kind == "harness":
            refs.append((f"port {port.id} harness oracle", port.oracle.ref))
    refs += [(f"coupling finding {f.id}", f.owning_item) for f in body.coupling_findings]
    refs += [(f"structural assumption {a.id}", a.owning_item) for a in body.structural_assumptions]
    return refs


def _check_refs(body: PortsBody, root: Path) -> list[str]:
    items, decs = _roadmap_criteria(root), _dec_numbers(root)
    findings = [
        f"{who}: owning_item {ref!r} does not resolve" for who, ref in _owning_refs(body) if not _resolves(ref, items, decs)
    ]
    for kind, rows in (("coupling finding", body.coupling_findings), ("structural assumption", body.structural_assumptions)):
        for row in rows:
            loc = row.location
            if not (root / loc).exists() and not (re.match(r"^dec-\d+$", loc) and _resolves(loc, items, decs)):
                findings.append(f"{kind} {row.id}: location {loc!r} is neither an existing path nor a resolvable dec id")
    return findings


def _check_ports(body: PortsBody, root: Path) -> list[str]:
    declared = {p.id for p in body.ports}
    findings = [
        f"coupling finding {f.id}: port {p!r} is not a declared port"
        for f in body.coupling_findings
        for p in f.ports
        if p not in declared
    ]
    for port in body.ports:
        by_id = {a.id: a for a in port.adapters}
        for a in port.adapters:
            if a.location and not (root / a.location).exists():
                findings.append(f"port {port.id} adapter {a.id}: location {a.location!r} does not exist")
        suite = port.conformance_suite
        if suite.path and not (root / suite.path).exists():
            findings.append(f"port {port.id}: suite path {suite.path!r} does not exist")
        oracle = port.oracle
        if oracle is not None and oracle.kind == "adapter":
            adapter = by_id.get(oracle.ref)
            if adapter is None:
                findings.append(f"port {port.id}: oracle ref {oracle.ref!r} is not among the port's adapters")
            elif (oracle.status == "present") != (adapter.status == "implemented"):
                findings.append(f"port {port.id}: oracle status {oracle.status!r} misstates adapter {adapter.id!r}")
    return findings


def _check_semantics(body: PortsBody) -> list[str]:
    fields = set(PortsBody.model_fields)
    for model in (Port, Adapter, Suite, Oracle, CouplingFinding, StructuralAssumption):
        fields |= set(model.model_fields)
    return [f"semantics key {k!r} names no field" for k in body.semantics if k not in _DOC_KEYS and k not in fields]


def _check_trigger(body: PortsBody, root: Path) -> list[str]:
    path = root / body.ambient_trigger
    if not path.is_file() or "docs/contracts/ports.yaml" not in path.read_text(encoding="utf-8"):
        return [f"ambient_trigger {body.ambient_trigger!r} does not exist or no longer names docs/contracts/ports.yaml"]
    return []


def _check_personas(body: PortsBody, root: Path) -> list[str]:
    port = next((p for p in body.ports if p.id == "run_persona"), None)
    if port is None:
        return []
    data = yaml.safe_load((root / "docs" / "contracts" / "inference-provider.yaml").read_text(encoding="utf-8"))
    axis = data["vocabulary"]["axes"]["persona_backend"]
    values, reserved = set(axis["values"]), set(axis.get("reserved") or [])
    adapters = {a.id for a in port.adapters}
    findings = [f"run_persona adapter {a!r} is not a persona_backend value" for a in sorted(adapters - values - reserved)]
    findings += [f"persona_backend value {v!r} has no run_persona adapter" for v in sorted(values - adapters)]
    return findings


def _rule_findings(body: PortsBody, root: Path) -> list[str]:
    return [
        *_check_ports(body, root),
        *_check_refs(body, root),
        *_check_semantics(body),
        *_check_trigger(body, root),
        *_check_personas(body, root),
    ]


@registry.register("validate_ports_contract", owner="platform")
def validate_ports_contract(failed: list[str], root: Path | None = None) -> None:
    """Fail if docs/contracts/ports.yaml violates its schema or any cross-reference rule."""
    print("\n=== Ports contract (ports.yaml) ===")
    base = root if root is not None else _common.ROOT
    path = base / "docs" / "contracts" / _CONTRACT_BASENAME
    try:
        body = PortsBody.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        findings = _rule_findings(body, base)
    except (OSError, yaml.YAMLError, ValidationError, KeyError, TypeError, ValueError) as exc:
        failed.append(_CHECK_LABEL)
        print(f"  FAIL: {path}: {exc}")
        registry.skipped(f"{_CONTRACT_BASENAME} not loadable")
        return
    if findings:
        failed.append(_CHECK_LABEL)
        for finding in findings:
            print(f"  FAIL: {finding}")
    else:
        open_rows = sum(1 for f in body.coupling_findings if f.status == "open")
        print(
            f"  PASS: {_CONTRACT_BASENAME} satisfies every rule ({len(body.ports)} ports, {open_rows} open coupling findings)."
        )
    registry.examined(len(body.ports), unit="ports")
