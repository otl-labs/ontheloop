"""rec-4477's acceptance: deploy-paths.yaml names the persona image channel and CODEOWNERS owns its files."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_deploy_paths_names_the_persona_image_channel() -> None:
    data = yaml.safe_load((ROOT / "docs/contracts/deploy-paths.yaml").read_text(encoding="utf-8"))
    row = data["publish_persona_image"]
    assert ".github/workflows/persona-image-publish.yml" in row["trigger"]
    assert "scripts/ci/deploy_gate.py" in row["trigger"] and "Decision 249" in row["trigger"]
    assert "config/persona_image.yaml" in row["blocks_ref"]
    assert row["actor_role"] == "agent"
    workflow = (ROOT / ".github/workflows/persona-image-publish.yml").read_text(encoding="utf-8")
    assert "scripts.ci.deploy_gate resolve --channel persona_image" in workflow


def test_codeowners_names_the_persona_image_channel() -> None:
    owned = {
        line.split()[0]: line.split()[1:]
        for line in (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for path in (
        "/.github/workflows/persona-image-publish.yml",
        "/scripts/ci/persona_image_publish.py",
        "/scripts/ci/registry_record.py",
    ):
        assert owned.get(path) == ["@benjamin-blake"], path


def test_ports_records_the_persona_image_row() -> None:
    data = yaml.safe_load((ROOT / "docs/contracts/ports.yaml").read_text(encoding="utf-8"))
    rows = data["structural_assumptions"]
    last = rows[-1]
    assert "config/persona_image.yaml" in last["assumption"]
    numbers = [int(r["id"].removeprefix("SA-")) for r in rows]
    assert numbers[-1] == max(numbers[:-1]) + 1 and last["id"] == f"SA-{numbers[-1]:02d}"
    assert last["id"] in data["amendment_log"][0]["summary"]
