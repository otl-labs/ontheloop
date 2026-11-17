"""Loader for config/persona_image.yaml, the one file holding the persona image name, digest and source commit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PIN_PATH = "config/persona_image.yaml"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TOP_KEYS = {"image", "digest", "source_commit", "build", "consumers"}
_BUILD_KEYS = {"dockerfile", "files", "copy"}
_CONSUMER_NAMES = {"local_container", "github_actions_shell", "fargate"}
_STATUSES = {"built", "pending"}
_CONSUMER_KEYS = {"built": {"status", "location"}, "pending": {"status", "owning_item"}}


class PinUnset(Exception):
    """The pin's digest is still null, so no image reference exists yet."""


@dataclass(frozen=True)
class ImagePin:
    image: str
    digest: str | None
    source_commit: str | None
    dockerfile: str
    files: tuple[str, ...]
    copy: tuple[str, ...]
    consumers: tuple[tuple[str, dict[str, str]], ...]

    def inputs(self) -> tuple[str, ...]:
        return self.files + self.copy

    def reference(self) -> str:
        if self.digest is None:
            raise PinUnset(f"{PIN_PATH}: digest is null; the publish channel has not pinned an image yet")
        return f"{self.image}@{self.digest}"


def _str_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{field}: must be a non-empty list of strings")
    return tuple(value)


def _keys(mapping: Any, allowed: set[str], field: str) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        raise ValueError(f"{field}: must be a mapping")
    extra = set(mapping) - allowed
    if extra:
        raise ValueError(f"{field}: unknown key {sorted(extra)[0]}")
    return mapping


def _consumers(raw: Any) -> tuple[tuple[str, dict[str, str]], ...]:
    rows = _keys(raw, _CONSUMER_NAMES, "consumers")
    out: list[tuple[str, dict[str, str]]] = []
    for name, row in rows.items():
        if not isinstance(row, dict) or row.get("status") not in _STATUSES:
            raise ValueError(f"consumers.{name}.status: must be one of {sorted(_STATUSES)}")
        _keys(row, _CONSUMER_KEYS[row["status"]], f"consumers.{name}")
        if set(row) != _CONSUMER_KEYS[row["status"]] or not all(isinstance(v, str) and v for v in row.values()):
            raise ValueError(f"consumers.{name}: needs exactly {sorted(_CONSUMER_KEYS[row['status']])} as strings")
        out.append((name, dict(row)))
    if not out:
        raise ValueError("consumers: must not be empty")
    return tuple(out)


def load_pin(root: Path) -> ImagePin:
    try:
        data = yaml.safe_load((Path(root) / PIN_PATH).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{PIN_PATH}: cannot read or parse: {exc}") from exc
    data = _keys(data, _TOP_KEYS, PIN_PATH)
    missing = _TOP_KEYS - set(data)
    if missing:
        raise ValueError(f"{PIN_PATH}: missing key {sorted(missing)[0]}")
    image, digest, commit = data["image"], data["digest"], data["source_commit"]
    if not isinstance(image, str) or not image or ":" in image or "@" in image:
        raise ValueError("image: must be a bare name with no tag or digest")
    if digest is not None and not (isinstance(digest, str) and _DIGEST.match(digest)):
        raise ValueError("digest: must be null or sha256:<64 hex>")
    if commit is not None and not (isinstance(commit, str) and _COMMIT.match(commit)):
        raise ValueError("source_commit: must be null or 40 hex")
    if (digest is None) != (commit is None):
        raise ValueError("digest and source_commit: must be both null or both set")
    build = _keys(data["build"], _BUILD_KEYS, "build")
    if set(build) != _BUILD_KEYS or not isinstance(build["dockerfile"], str) or not build["dockerfile"]:
        raise ValueError("build.dockerfile: must be a string")
    return ImagePin(
        image=image,
        digest=digest,
        source_commit=commit,
        dockerfile=build["dockerfile"],
        files=_str_list(build["files"], "build.files"),
        copy=_str_list(build["copy"], "build.copy"),
        consumers=_consumers(data["consumers"]),
    )
