"""Tier A north-star models (Decision 243): the shape of docs/contracts/north-star.yaml.

No first-party imports and no I/O at import: the decision-entry grammar and the scout lane import this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "docs" / "contracts" / "north-star.yaml"
_ID = re.compile(r"^NS\.[1-9][0-9]*$")

Binds = Literal["repository", "release", "both"]
Adopters = Literal["bound", "may_override", "set_scale"]


def _adopters_rule(binds: str, adopters: str | None) -> None:
    if binds in ("release", "both") and adopters is None:
        raise ValueError(f"adopters is required when binds is {binds}")
    if binds == "repository" and adopters is not None:
        raise ValueError("adopters is forbidden when binds is repository")


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("must be non-empty")
    return value


class _Scoped(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["north_star"]
    detail: str
    failure_signal: str
    binds: Binds
    adopters: Adopters | None = None
    carried_by: list[int]

    @field_validator("failure_signal")
    @classmethod
    def _failure_signal(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("carried_by")
    @classmethod
    def _carried_by(cls, value: list[int]) -> list[int]:
        if not value or any(n < 1 for n in value):
            raise ValueError("carried_by must be a non-empty list of positive Decision numbers")
        return value

    @model_validator(mode="after")
    def _binding(self) -> _Scoped:
        _adopters_rule(self.binds, self.adopters)
        return self


class Mission(_Scoped):
    statement: str

    @field_validator("statement", "detail")
    @classmethod
    def _text(cls, value: str) -> str:
        return _non_empty(value)


class NorthStarEntry(_Scoped):
    id: str
    principle: str
    status: Literal["active", "retired"] = "active"

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError(f"id {value!r} does not match ^NS\\.[1-9][0-9]*$")
        return value

    @field_validator("principle", "detail")
    @classmethod
    def _text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("detail")
    @classmethod
    def _why(cls, value: str) -> str:
        if not value.startswith("Why:"):
            raise ValueError('detail must start with "Why:"')
        return value


class PartialOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    groups: list[list[str]]

    @field_validator("rule")
    @classmethod
    def _rule(cls, value: str) -> str:
        return _non_empty(value)


class NorthStarDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: dict[str, Any]
    amendment_log: list[dict[str, Any]]
    semantics: dict[str, str]
    mission: Mission
    principles: list[NorthStarEntry]
    partial_order: PartialOrder

    @model_validator(mode="after")
    def _unique_ids(self) -> NorthStarDocument:
        ids = [p.id for p in self.principles]
        if len(ids) != len(set(ids)):
            raise ValueError("entry ids must be unique")
        return self

    def ids(self) -> set[str]:
        return {p.id for p in self.principles}

    def active_ids(self) -> set[str]:
        return {p.id for p in self.principles if p.status == "active"}


def parse_north_star(text: str, where: str = "<text>") -> NorthStarDocument:
    """Validate north-star YAML text; every failure is a ValueError naming where."""
    try:
        return NorthStarDocument.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"{where}: {exc}") from exc


def load_north_star(path: Path | None = None) -> NorthStarDocument:
    target = DEFAULT_PATH if path is None else Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{target}: {exc}") from exc
    return parse_north_star(text, str(target))
