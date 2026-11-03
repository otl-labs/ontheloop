"""CaptureCursor: positions and pins only, never pending rows (Decision 84 I-4).

Binds to parser_version: a mismatch resets everything except the four pins (project_ref, billing_shape,
root_session_ref, session_started_at_ms) and the recorded finalization cuts, so a re-emission cannot re-key the tree
and a bump re-emits every finalization's close row (Decision 207 R7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

CURSOR_FORMAT = 2
_INT_FIELDS = ("cursor_format", "parser_version", "session_started_at_ms")
_STR_FIELDS = ("producer", "root_session_ref", "project_ref", "billing_shape")


class CursorError(ValueError):
    """A malformed cursor, or a pin that the caller's inputs or the source contradict."""


class SourceMutated(CursorError):
    """A sidecar pinned present is missing or changed."""


@dataclass(frozen=True)
class CaptureCursor:
    producer: str
    parser_version: int
    root_session_ref: str
    project_ref: str
    billing_shape: str
    session_started_at_ms: int
    lines_consumed: dict[str, int] = field(default_factory=dict)
    sidecars: dict[str, str | None] = field(default_factory=dict)
    finalized: bool = False
    finalize_cuts: list[dict[str, int]] = field(default_factory=list)
    cursor_format: int = CURSOR_FORMAT

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> CaptureCursor:
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise CursorError(f"cursor is not JSON: {exc}") from exc
        if isinstance(raw, dict) and raw.get("cursor_format") != CURSOR_FORMAT:
            raise CursorError("unsupported cursor_format")
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise CursorError("cursor keys do not match the cursor format")
        _validate(raw)
        return cls(**raw)

    def reset(self, parser_version: int) -> CaptureCursor:
        return CaptureCursor(
            self.producer,
            parser_version,
            self.root_session_ref,
            self.project_ref,
            self.billing_shape,
            self.session_started_at_ms,
            finalize_cuts=[dict(cut) for cut in self.finalize_cuts],
        )

    def check_pins(self, *, root_session_ref: str, project_ref: str, billing_shape: str, session_started_at_ms: int) -> None:
        wanted = (root_session_ref, project_ref, billing_shape, session_started_at_ms)
        held = (self.root_session_ref, self.project_ref, self.billing_shape, self.session_started_at_ms)
        if wanted != held:
            names = ("root_session_ref", "project_ref", "billing_shape", "session_started_at_ms")
            bad = [n for n, w, h in zip(names, wanted, held, strict=True) if w != h]
            raise CursorError(f"pin mismatch: {', '.join(bad)}")

    def check_sidecar(self, name: str, sha256: str | None) -> None:
        pinned = self.sidecars.get(name)
        if pinned is not None and pinned != sha256:
            raise SourceMutated(f"sidecar {name!r} pinned present changed or vanished")


def _validate(raw: dict[str, Any]) -> None:
    for key in _INT_FIELDS:
        if type(raw[key]) is not int or raw[key] < 0:
            raise CursorError(f"{key} must be a non-negative integer")
    for key in _STR_FIELDS:
        if not isinstance(raw[key], str) or not raw[key]:
            raise CursorError(f"{key} must be a non-empty string")
    lines = raw["lines_consumed"]
    if not isinstance(lines, dict) or any(type(v) is not int or v < 0 for v in lines.values()):
        raise CursorError("lines_consumed must map file keys to non-negative integers")
    sidecars = raw["sidecars"]
    if not isinstance(sidecars, dict) or any(v is not None and not isinstance(v, str) for v in sidecars.values()):
        raise CursorError("sidecars must map names to a sha256 or null")
    if type(raw["finalized"]) is not bool:
        raise CursorError("finalized must be a boolean")
    cuts = raw["finalize_cuts"]
    if not isinstance(cuts, list) or any(
        not isinstance(cut, dict) or any(type(v) is not int or v < 0 for v in cut.values()) for cut in cuts
    ):
        raise CursorError("finalize_cuts must be a list of maps from file keys to non-negative integers")
