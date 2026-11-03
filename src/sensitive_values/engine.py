"""Profile masking with scrub.py's exact semantics: one re.sub per class, counts only, never matched text."""

from __future__ import annotations

import hashlib
import re
from typing import Callable, NamedTuple

from src.sensitive_values.classes import CLASSES, AcceptSpec, DetectorClass
from src.sensitive_values.profiles import MARKER_FORMAT, PROFILES


class MaskResult(NamedTuple):
    text: str
    counts: dict[str, int]


def _long_or_digit(min_len: int = 32) -> Callable[[str], bool]:
    return lambda token: len(token) >= min_len or any(ch.isdigit() for ch in token)


ACCEPT: dict[str, Callable[..., Callable[[str], bool]]] = {"long_or_digit": _long_or_digit}


def _predicate(spec: AcceptSpec) -> Callable[[str], bool]:
    return ACCEPT[spec.name](**dict(spec.params))


def _profile(profile: str) -> tuple[str, ...]:
    try:
        return PROFILES[profile]
    except KeyError:
        raise KeyError(profile) from None


def mask(text: str, profile: str) -> MaskResult:
    ids = _profile(profile)
    counts = dict.fromkeys(ids, 0)
    out = text
    for class_id in ids:
        row = CLASSES[class_id]
        out = row.regex.sub(_replacer(row, counts), out)
    return MaskResult(out, {k: v for k, v in counts.items() if v})


def _replacer(row: DetectorClass, counts: dict[str, int]) -> Callable[[re.Match[str]], str]:
    marker = MARKER_FORMAT.format(cls=row.id)
    accepted = _predicate(row.accept) if row.accept is not None else None
    keep = row.kind == "keep_prefix"

    def repl(match: re.Match[str]) -> str:
        if accepted is not None and not accepted(match.group(2)):
            return match.group(0)
        counts[row.id] += 1
        return match.group(1) + marker if keep else marker

    return repl


def fingerprint(profile: str) -> str:
    digest = hashlib.sha256()
    for class_id in _profile(profile):
        row = CLASSES[class_id]
        accept = None if row.accept is None else [row.accept.name, sorted(row.accept.params)]
        digest.update(repr([row.id, row.pattern, row.flags, row.kind, accept]).encode("utf-8"))
        digest.update(b"\n")
    return f"{profile}@{digest.hexdigest()[:16]}"
