#!/usr/bin/env python3
"""GHCR channel-record reader for the persona image (Decision 100: a thin reader of the registry's own API).

The record of the persona_image deploy channel is the package's `main` tag: its config's
org.opencontainers.image.revision label names the source commit the image was built from. Stdlib only, no import
from scripts.ci.deploy_gate (that module runs as __main__ and imports this one lazily). Every failure other than an
explicit absent-package answer raises RecordError, so a caller fails closed and never falls back to main HEAD.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

REGISTRY = "ghcr.io"
REVISION_LABEL = "org.opencontainers.image.revision"
TIMEOUT = 30
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
ACCEPT = ", ".join((OCI_MANIFEST, DOCKER_MANIFEST, OCI_INDEX, DOCKER_LIST))
_ABSENT_CODES = {"NAME_UNKNOWN", "MANIFEST_UNKNOWN"}
_SINGLE_TYPES = {OCI_MANIFEST, DOCKER_MANIFEST}

Opener = Callable[..., Any]


class RecordError(RuntimeError):
    """The registry record could not be read or is not the expected shape; callers fail closed."""


class RegistryAnswer(RecordError):
    """An HTTP error answer from the registry, with its first error code (empty when it carried none)."""

    def __init__(self, status: int, code: str, url: str) -> None:
        super().__init__(f"registry answered {status} {code or 'without an error code'} for {url}")
        self.status = status
        self.code = code


def repository(image: str) -> str:
    prefix = f"{REGISTRY}/"
    if not image.startswith(prefix) or ":" in image or "@" in image:
        raise RecordError(f"image must be a bare {prefix}<owner>/<name>: {image!r}")
    return image[len(prefix) :]


def _error_code(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read().decode("utf-8"))["errors"][0]["code"])
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ""


def _get(url: str, headers: dict[str, str], opener: Opener) -> bytes:
    request = urllib.request.Request(url)  # noqa: S310
    for name, value in headers.items():
        request.add_unredirected_header(name, value)
    try:
        with opener(request, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise RegistryAnswer(exc.code, _error_code(exc), url) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RecordError(f"registry read failed for {url}: {exc}") from exc


def _json(url: str, headers: dict[str, str], opener: Opener) -> dict[str, Any]:
    raw = _get(url, headers, opener)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise RecordError(f"registry body for {url} is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise RecordError(f"registry body for {url} is not an object")
    return doc


def pull_token(image: str, token: Optional[str], opener: Opener = urllib.request.urlopen) -> str:
    scope = urllib.parse.quote(f"repository:{repository(image)}:pull", safe=":/")
    url = f"https://{REGISTRY}/token?service={REGISTRY}&scope={scope}"
    headers: dict[str, str] = {}
    if token:
        user = os.environ.get("GITHUB_ACTOR") or "token"
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode("ascii")
    bearer = _json(url, headers, opener).get("token")
    if not isinstance(bearer, str) or not bearer:
        raise RecordError("registry token response carries no token")
    return bearer


def single_manifest(doc: dict[str, Any]) -> dict[str, Any]:
    """The manifest when it is one image manifest; an index or manifest list is refused (build.py builds one manifest)."""
    media = doc.get("mediaType")
    if "manifests" in doc or media in (OCI_INDEX, DOCKER_LIST) or (media is not None and media not in _SINGLE_TYPES):
        raise RecordError(f"expected a single image manifest, got {media or 'an index'}")
    config = doc.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("digest"), str):
        raise RecordError("manifest carries no config digest")
    return doc


def fetch_manifest(image: str, reference: str, bearer: str, opener: Opener = urllib.request.urlopen) -> dict[str, Any]:
    url = f"https://{REGISTRY}/v2/{repository(image)}/manifests/{reference}"
    return single_manifest(_json(url, {"Authorization": f"Bearer {bearer}", "Accept": ACCEPT}, opener))


def fetch_config(image: str, digest: str, bearer: str, opener: Opener = urllib.request.urlopen) -> dict[str, Any]:
    url = f"https://{REGISTRY}/v2/{repository(image)}/blobs/{digest}"
    return _json(url, {"Authorization": f"Bearer {bearer}"}, opener)


def revision_label(config: dict[str, Any]) -> Optional[str]:
    labels = (config.get("config") or {}).get("Labels") or {}
    value = labels.get(REVISION_LABEL) if isinstance(labels, dict) else None
    return value if isinstance(value, str) and value else None


def read_record(
    image: str,
    *,
    token: Optional[str],
    first_publish: bool = False,
    opener: Opener = urllib.request.urlopen,
) -> Optional[str]:
    """The main tag's revision label; None only for an explicit absent answer (or DENIED under first_publish)."""
    try:
        bearer = pull_token(image, token, opener)
        manifest = fetch_manifest(image, "main", bearer, opener)
        config = fetch_config(image, manifest["config"]["digest"], bearer, opener)
    except RegistryAnswer as answer:
        if answer.code in _ABSENT_CODES or (first_publish and answer.code == "DENIED"):
            return None
        raise
    return revision_label(config)
