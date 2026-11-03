"""Project-configuration registry check (Decision 224): schema and cross-reference rules for
docs/contracts/project-configuration.yaml.

The contract is the sole registry of user-set configuration: one row per setting with its kind,
home per substrate, readers, setter, override and exported variables, local and cloud defaults,
and this repository's current home and migration status. Rules enforced here:

(a) row ids are unique; (b) kind is a key of kinds; (c) every home named in a row is a key of
homes and allowed for the row's kind on that substrate by kind_homes, and a secret or private row
(or kind_homes.secret/private on any substrate) never names a tracked or in-tree home
(platform_file, clone_file); a row naming vendor_store, container_env_file or fargate_injection
cites Decision 206 clause 2 in its basis; (d) applies_when is "always", {port, adapter} declared
in docs/contracts/ports.yaml, or {cloud} in clouds, and an id segment <port> is allowed only on an
applies_when: always row; (e) secret/private rows carry a non-null variable and null defaults;
project/machine rows carry a null variable or one matching ^OTL_[A-Z0-9_]+$; exports match
^[A-Z][A-Z0-9_]*$; (f) every reader is an existing repository path or an owning item that
resolves; (g) this_repository shape, status and current_home/home consistency; (h) enforcement is
"advisory" or a registered check name; (i) every semantics key names a row field or a top-level
key; (j) ambient_trigger exists and names this contract; (k) a row id fits its applies_when.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scripts.checks import _common, registry
from scripts.checks.contracts.validate_ports_contract import _dec_numbers, _resolves, _roadmap_criteria

_CONTRACT_BASENAME = "project-configuration.yaml"
_CHECK_LABEL = "Project configuration contract"
_TRACKED_OR_IN_TREE_HOMES = frozenset({"platform_file", "clone_file"})
_D206_HOMES = frozenset({"vendor_store", "container_env_file", "fargate_injection"})
_OTL_VARIABLE = r"^OTL_[A-Z0-9_]+$"
_EXPORT_VARIABLE = r"^[A-Z][A-Z0-9_]*$"
_PORT_ADAPTER_ID = re.compile(r"^ports\.([a-z0-9_]+)\.([a-z0-9_]+)\.")
_CLOUD_ID = re.compile(r"^clouds\.([a-z0-9_]+)\.")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThisRepository(_Strict):
    current_home: str | None
    note: str | None = None
    status: Literal["open", "resolved", "accepted"]
    owning_item: str

    @model_validator(mode="after")
    def _shape(self) -> "ThisRepository":
        if self.current_home is None and not (self.note and self.note.strip()):
            raise ValueError("current_home null needs a non-empty note")
        if self.status == "accepted" and not re.match(r"^dec-\d+$", self.owning_item):
            raise ValueError("accepted status needs a dec-NNN owning_item")
        return self


class Defaults(_Strict):
    local: Any
    cloud: Any


class Setting(_Strict):
    id: str
    kind: str
    basis: str
    applies_when: dict[str, str] | Literal["always"]
    homes: dict[str, Any]
    variable: str | None
    exports: list[str]
    readers: list[str]
    set_by: Literal["init", "user"]
    defaults: Defaults
    this_repository: ThisRepository | None
    enforcement: str

    @model_validator(mode="after")
    def _shape(self) -> "Setting":
        if not self.basis.strip():
            raise ValueError(f"setting {self.id}: basis is blank")
        return self


class ProjectConfigurationBody(_Strict):
    contract: dict[str, Any]
    amendment_log: list[Any]
    ambient_trigger: str
    kinds: dict[str, str]
    homes: dict[str, str]
    kind_homes: dict[str, dict[str, list[str]]]
    precedence: dict[str, list[str]]
    clouds: list[str]
    not_configuration: dict[str, str]
    semantics: dict[str, str]
    settings: list[Setting]

    @model_validator(mode="after")
    def _unique(self) -> "ProjectConfigurationBody":
        ids = [s.id for s in self.settings]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate setting ids")
        return self


def _homes_for(body: ProjectConfigurationBody, row: Setting, substrate: str) -> list[str]:
    value = row.homes.get(substrate)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _check_homes(body: ProjectConfigurationBody) -> list[str]:
    findings: list[str] = []
    for kind, substrates in body.kind_homes.items():
        for substrate, names in substrates.items():
            for name in names:
                if name not in body.homes:
                    findings.append(f"kind_homes.{kind}.{substrate} names undeclared home {name!r}")
                if kind in ("secret", "private") and name in _TRACKED_OR_IN_TREE_HOMES:
                    findings.append(f"kind_homes.{kind}.{substrate} names tracked/in-tree home {name!r}")
    for row in body.settings:
        if row.kind not in body.kinds:
            findings.append(f"setting {row.id}: kind {row.kind!r} is not a key of kinds")
            continue
        allowed = body.kind_homes.get(row.kind, {})
        for substrate in ("local", "ci", "cloud"):
            for name in _homes_for(body, row, substrate):
                if name not in body.homes:
                    findings.append(f"setting {row.id}: home {name!r} ({substrate}) is not a declared home")
                    continue
                if name not in allowed.get(substrate, []):
                    findings.append(f"setting {row.id}: home {name!r} is not allowed for kind {row.kind!r} on {substrate}")
                if row.kind in ("secret", "private") and name in _TRACKED_OR_IN_TREE_HOMES:
                    findings.append(f"setting {row.id}: secret/private row names tracked/in-tree home {name!r}")
                if name in _D206_HOMES and "Decision 206 clause 2" not in row.basis:
                    findings.append(f"setting {row.id}: home {name!r} needs 'Decision 206 clause 2' cited in basis")
    return findings


def _check_applies_when(body: ProjectConfigurationBody, ports: dict[str, set[str]]) -> list[str]:
    findings: list[str] = []
    for row in body.settings:
        aw = row.applies_when
        port_match = _PORT_ADAPTER_ID.match(row.id)
        cloud_match = _CLOUD_ID.match(row.id)
        if aw == "always":
            if port_match:
                findings.append(f"setting {row.id}: a ports.<port>.<adapter>.* id needs applies_when port/adapter, not always")
            continue
        if set(aw) == {"port", "adapter"}:
            if aw["adapter"] not in ports.get(aw["port"], set()):
                findings.append(f"setting {row.id}: applies_when {aw} does not name a declared port/adapter pair")
            if port_match and (port_match.group(1), port_match.group(2)) != (aw["port"], aw["adapter"]):
                findings.append(f"setting {row.id}: id does not match its applies_when port/adapter")
        elif set(aw) == {"cloud"}:
            if aw["cloud"] not in body.clouds:
                findings.append(f"setting {row.id}: applies_when cloud {aw['cloud']!r} is not declared")
            if cloud_match and cloud_match.group(1) != aw["cloud"]:
                findings.append(f"setting {row.id}: id does not match its applies_when cloud")
        else:
            findings.append(f"setting {row.id}: applies_when {aw} is neither always, {{port, adapter}} nor {{cloud}}")
    return findings


def _check_variables(body: ProjectConfigurationBody) -> list[str]:
    findings: list[str] = []
    for row in body.settings:
        if row.kind in ("secret", "private"):
            if not row.variable:
                findings.append(f"setting {row.id}: secret/private row needs a non-null variable")
            if row.defaults.local is not None or row.defaults.cloud is not None:
                findings.append(f"setting {row.id}: secret/private row needs null defaults")
        else:
            if row.variable is not None and not re.match(_OTL_VARIABLE, row.variable):
                findings.append(f"setting {row.id}: variable {row.variable!r} does not match {_OTL_VARIABLE}")
        for export in row.exports:
            if not re.match(_EXPORT_VARIABLE, export):
                findings.append(f"setting {row.id}: export {export!r} does not match {_EXPORT_VARIABLE}")
    return findings


def _check_readers(body: ProjectConfigurationBody, root: Path, items: dict[str, set[str]], decs: set[int]) -> list[str]:
    findings: list[str] = []
    for row in body.settings:
        for reader in row.readers:
            if (root / reader).exists():
                continue
            if _resolves(reader, items, decs):
                continue
            findings.append(f"setting {row.id}: reader {reader!r} is neither an existing path nor a resolvable owning item")
    return findings


def _check_this_repository(
    body: ProjectConfigurationBody, root: Path, items: dict[str, set[str]], decs: set[int]
) -> list[str]:
    findings: list[str] = []
    local_home_path = {"platform_file": "ontheloop/platform.yaml", "clone_file": ".ontheloop/config.yaml"}
    for row in body.settings:
        tr = row.this_repository
        if tr is None:
            continue
        if not _resolves(tr.owning_item, items, decs):
            findings.append(f"setting {row.id}: this_repository.owning_item {tr.owning_item!r} does not resolve")
        if tr.status == "resolved":
            local_homes = _homes_for(body, row, "local")
            expected = local_home_path.get(local_homes[0]) if local_homes else None
            if row.kind in ("secret", "private"):
                if tr.current_home is not None:
                    findings.append(f"setting {row.id}: resolved secret/private row needs a null current_home")
            elif expected is not None and tr.current_home != expected:
                findings.append(f"setting {row.id}: resolved row's current_home does not match its local home {expected!r}")
            if expected == "ontheloop/platform.yaml" and not (root / expected).exists():
                findings.append(f"setting {row.id}: resolved row names {expected!r}, which does not exist")
    return findings


def _check_enforcement(body: ProjectConfigurationBody) -> list[str]:
    checks = registry.all_checks()
    return [
        f"setting {row.id}: enforcement {row.enforcement!r} is neither 'advisory' nor a registered check"
        for row in body.settings
        if row.enforcement != "advisory" and row.enforcement not in checks
    ]


def _check_semantics(body: ProjectConfigurationBody) -> list[str]:
    top_level = set(ProjectConfigurationBody.model_fields)
    row_fields = set(Setting.model_fields)
    return [f"semantics key {k!r} names no field" for k in body.semantics if k not in top_level and k not in row_fields]


def _check_trigger(body: ProjectConfigurationBody, root: Path) -> list[str]:
    path = root / body.ambient_trigger
    target = f"docs/contracts/{_CONTRACT_BASENAME}"
    if not path.is_file() or target not in path.read_text(encoding="utf-8"):
        return [f"ambient_trigger {body.ambient_trigger!r} does not exist or no longer names {target}"]
    return []


def _ports_map(root: Path) -> dict[str, set[str]]:
    data = yaml.safe_load((root / "docs" / "contracts" / "ports.yaml").read_text(encoding="utf-8"))
    return {p["id"]: {a["id"] for a in p["adapters"]} for p in data["ports"]}


def _rule_findings(body: ProjectConfigurationBody, root: Path) -> list[str]:
    items, decs = _roadmap_criteria(root), _dec_numbers(root)
    return [
        *_check_homes(body),
        *_check_applies_when(body, _ports_map(root)),
        *_check_variables(body),
        *_check_readers(body, root, items, decs),
        *_check_this_repository(body, root, items, decs),
        *_check_enforcement(body),
        *_check_semantics(body),
        *_check_trigger(body, root),
    ]


@registry.register("validate_project_configuration", owner="platform")
def validate_project_configuration(failed: list[str], root: Path | None = None) -> None:
    """Fail if docs/contracts/project-configuration.yaml violates its schema or any cross-reference rule."""
    print("\n=== Project configuration contract (project-configuration.yaml) ===")
    base = root if root is not None else _common.ROOT
    path = base / "docs" / "contracts" / _CONTRACT_BASENAME
    try:
        body = ProjectConfigurationBody.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
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
        print(f"  PASS: {_CONTRACT_BASENAME} satisfies every rule ({len(body.settings)} settings).")
    registry.examined(len(body.settings), unit="settings")
