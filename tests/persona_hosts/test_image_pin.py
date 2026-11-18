"""Mirror test for src/persona_hosts/image_pin.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.persona_hosts import PinUnset, load_pin

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64
COMMIT = "b" * 40


def _base() -> dict:
    return {
        "image": "registry.example/persona",
        "digest": None,
        "source_commit": None,
        "build": {"dockerfile": "d/Dockerfile", "files": ["d/Dockerfile"], "copy": ["x.py"]},
        "consumers": {
            "local_container": {"status": "built", "location": "src/persona_hosts/local_container/"},
            "fargate": {"status": "pending", "owning_item": "T4.15:c12"},
        },
    }


def _write(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "persona_image.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


def test_live_pin_loads_with_digest_and_commit_together() -> None:
    pin = load_pin(ROOT)
    assert (pin.digest is None) == (pin.source_commit is None)
    assert pin.dockerfile in pin.files
    assert pin.inputs() == pin.files + pin.copy


def test_reference_raises_while_digest_null(tmp_path: Path) -> None:
    with pytest.raises(PinUnset):
        load_pin(_write(tmp_path, _base())).reference()


def test_reference_returns_name_at_digest(tmp_path: Path) -> None:
    data = _base() | {"digest": DIGEST, "source_commit": COMMIT}
    assert load_pin(_write(tmp_path, data)).reference() == f"registry.example/persona@{DIGEST}"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(digest=DIGEST),
        lambda d: d.update(digest="latest", source_commit=COMMIT),
        lambda d: d.update(digest=DIGEST, source_commit="short"),
        lambda d: d.update(image="registry.example/persona:tag"),
        lambda d: d["consumers"]["fargate"].update(status="planned"),
        lambda d: d["consumers"].update(unknown={"status": "pending", "owning_item": "x"}),
        lambda d: d.update(extra=1),
        lambda d: d["build"].update(copy=[]),
        lambda d: d["consumers"]["local_container"].pop("location"),
    ],
)
def test_schema_breach_raises(tmp_path: Path, mutate) -> None:
    data = _base()
    mutate(data)
    with pytest.raises(ValueError):
        load_pin(_write(tmp_path, data))


def test_missing_pin_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        load_pin(tmp_path)


def test_non_mapping_pin_raises(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "persona_image.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_pin(tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("digest"),
        lambda d: d.update(consumers={}),
        lambda d: d["build"].update(dockerfile=""),
        lambda d: d.update(build="not-a-mapping"),
    ],
)
def test_more_schema_breaches_raise(tmp_path: Path, mutate) -> None:
    data = _base()
    mutate(data)
    with pytest.raises(ValueError):
        load_pin(_write(tmp_path, data))
