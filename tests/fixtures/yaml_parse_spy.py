"""Shared spy for pure-Python vs. libyaml YAML-loader construction (rec-4365's regression guard).

tests/fixtures is exempt from the cross-test-import guard (Decision 131), so every mirror test
module imports this from here rather than from a sibling test_*.py file.

install(monkeypatch) wraps yaml.reader.Reader.__init__ (shared by every pure-Python loader --
SafeLoader, Loader, FullLoader, BaseLoader -- and never used by the C loaders) and
yaml.CSafeLoader.__init__, and returns a YamlParseLog of one entry per construction: which kind of
loader, how large the parsed stream was, and which scripts/ frames were on the call stack.

A str or bytes stream counts its own length (str counts characters, bytes counts bytes); a file
object counts its fstat size; an unmeasurable stream (e.g. an io.StringIO, whose .fileno() raises)
is recorded as None, never 0 (Decision 55: unavailable is never zero). Recording is wrapped so it
never raises -- an unreadable call stack logs an empty stack -- and the wrapper always delegates to
the real constructor regardless of whether recording itself failed.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"


@dataclass(frozen=True)
class _Entry:
    kind: str  # "pure" | "libyaml"
    size: int | None
    stack: tuple[str, ...]


@dataclass
class YamlParseLog:
    """Construction log the spy appends to; summary properties read it without mutating it."""

    entries: list[_Entry] = field(default_factory=list)

    @property
    def pure_entries(self) -> list[_Entry]:
        return [e for e in self.entries if e.kind == "pure"]

    @property
    def pure_bytes(self) -> int:
        """Total size recorded across every pure-Python construction (None entries count as 0 here;
        callers that must fail outright on an unmeasurable entry check pure_entries directly)."""
        return sum(e.size or 0 for e in self.pure_entries)

    @property
    def libyaml_sizes(self) -> list[int | None]:
        return [e.size for e in self.entries if e.kind == "libyaml"]

    @property
    def libyaml_frames(self) -> set[str]:
        frames: set[str] = set()
        for e in self.entries:
            if e.kind == "libyaml":
                frames.update(e.stack)
        return frames


def _measure(stream: object) -> int | None:
    if isinstance(stream, (str, bytes)):
        return len(stream)
    fileno = getattr(stream, "fileno", None)
    if fileno is not None:
        try:
            return os.fstat(fileno()).st_size
        except (OSError, ValueError, TypeError):
            return None
    return None


def _scripts_frames(scripts_dir: Path) -> tuple[str, ...]:
    """Every scripts/ frame on the current call stack, as "path:function" relative to scripts_dir."""
    try:
        frames: list[str] = []
        for frame_info in inspect.stack():
            try:
                rel = Path(frame_info.filename).resolve().relative_to(scripts_dir)
            except (OSError, ValueError):
                continue
            frames.append(f"{rel.as_posix()}:{frame_info.function}")
        return tuple(frames)
    except Exception:  # noqa: BLE001 -- recording must never raise
        return ()


def install(monkeypatch: pytest.MonkeyPatch) -> YamlParseLog:
    log = YamlParseLog()
    real_reader_init = yaml.reader.Reader.__init__
    real_csafe_init = yaml.CSafeLoader.__init__

    def _pure_init(self: object, stream: object, *args: object, **kwargs: object) -> None:
        try:
            log.entries.append(_Entry("pure", _measure(stream), _scripts_frames(_SCRIPTS_DIR)))
        except Exception:  # noqa: BLE001 -- recording must never raise
            pass
        return real_reader_init(self, stream, *args, **kwargs)

    def _libyaml_init(self: object, stream: object, *args: object, **kwargs: object) -> None:
        try:
            log.entries.append(_Entry("libyaml", _measure(stream), _scripts_frames(_SCRIPTS_DIR)))
        except Exception:  # noqa: BLE001 -- recording must never raise
            pass
        return real_csafe_init(self, stream, *args, **kwargs)

    monkeypatch.setattr(yaml.reader.Reader, "__init__", _pure_init)
    monkeypatch.setattr(yaml.CSafeLoader, "__init__", _libyaml_init)
    return log
