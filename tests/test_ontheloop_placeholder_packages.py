"""Standing regression guards for the ontheloop placeholder-package invariants (rec-3941).

Mirrors VP steps 1-13 of PLAN-ontheloop-placeholder-packages as pytest assertions so a later
regression (a manifest drifting off the pinned name/version/licence, a placeholder source
gaining a real dependency, or the publish workflow's dispatch-only/credential-free shape
eroding) is caught by the standing suite, not only by the plan's own one-time verification.
"""

from __future__ import annotations

import re
import tomllib

import yaml

from scripts.checks._common import ROOT

_PACKAGES = ROOT / "packages"
_ROOT_LICENSE = (ROOT / "LICENSE").read_bytes()

_PLACEHOLDER_DIRS = ["ontheloop-py", "ontheloop-js", "ontheloop-js-scoped", "ontheloop-rs"]

_INERT_SOURCES = [
    _PACKAGES / "ontheloop-py" / "src" / "ontheloop" / "__init__.py",
    _PACKAGES / "ontheloop-js" / "index.js",
    _PACKAGES / "ontheloop-js-scoped" / "index.js",
    _PACKAGES / "ontheloop-rs" / "src" / "lib.rs",
]

_INERT_PATTERN = re.compile(r"^\s*(import|from|require|use|extern)\b", re.M)


def test_py_manifest_identity() -> None:
    project = tomllib.loads((_PACKAGES / "ontheloop-py" / "pyproject.toml").read_text())["project"]
    assert project["name"] == "ontheloop"
    assert project["version"] == "0.0.0"
    assert project["license"] == "BUSL-1.1"


def test_js_bare_manifest_identity() -> None:
    manifest = yaml.safe_load((_PACKAGES / "ontheloop-js" / "package.json").read_text())
    assert manifest["name"] == "ontheloop"
    assert manifest["version"] == "0.0.0"
    assert manifest["license"] == "BUSL-1.1"


def test_js_scoped_manifest_identity() -> None:
    manifest = yaml.safe_load((_PACKAGES / "ontheloop-js-scoped" / "package.json").read_text())
    assert manifest["name"] == "@onthelooplabs/ontheloop"
    assert manifest["version"] == "0.0.0"
    assert manifest["license"] == "BUSL-1.1"
    assert manifest["publishConfig"]["access"] == "public"


def test_rs_manifest_identity() -> None:
    package = tomllib.loads((_PACKAGES / "ontheloop-rs" / "Cargo.toml").read_text())["package"]
    assert package["name"] == "ontheloop"
    assert package["version"] == "0.0.0"
    assert package["license"] == "BUSL-1.1"
    assert package.get("description")
    assert package.get("repository")


def test_placeholder_licences_match_root() -> None:
    divergent = [d for d in _PLACEHOLDER_DIRS if (_PACKAGES / d / "LICENSE").read_bytes() != _ROOT_LICENSE]
    assert divergent == []


def test_placeholder_sources_are_inert() -> None:
    violations = [str(p) for p in _INERT_SOURCES if _INERT_PATTERN.search(p.read_text())]
    assert violations == []


def test_publish_workflow_shape_and_trust_triple() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "publish-ontheloop.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    triggers = workflow.get("on") or workflow.get(True)
    assert sorted(triggers) == ["workflow_dispatch"]

    job = workflow["jobs"]["publish"]
    assert job["environment"] == "pypi"
    assert job["permissions"]["id-token"] == "write"
    assert (workflow.get("permissions") or {}).get("id-token") is None

    text = workflow_path.read_text()
    assert text.count("secrets.") == 0
    assert text.count("password") == 0
    assert text.count("upload-artifact") == 0

    trust = yaml.safe_load((ROOT / "docs" / "contracts" / "package-registry-reservation.yaml").read_text())[
        "pypi_trusted_publisher"
    ]
    trusted_workflow = yaml.safe_load((ROOT / ".github" / "workflows" / trust["workflow_filename"]).read_text())
    assert trusted_workflow["name"] == "publish-ontheloop"
    assert trusted_workflow["jobs"]["publish"]["environment"] == trust["environment"]
    assert trust["repository"] == "benjamin-blake/theseus"


def test_no_node_or_rust_toolchain_in_ci() -> None:
    """Mirrors PLAN-ontheloop-placeholder-packages VP step 12 (waived from graduation as
    green-by-construction on the un-implemented tree); this is the standing guard that carries
    its regression-detection value going forward."""
    markers = ("setup-node", "actions-rs/", "dtolnay/rust-toolchain", "setup-rust")
    hits = sorted(
        {
            workflow.name
            for workflow in (ROOT / ".github" / "workflows").glob("*.yml")
            for marker in markers
            if marker in workflow.read_text()
        }
    )
    assert hits == []


def test_publish_workflow_proof_only_is_fail_safe() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "publish-ontheloop.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]  # PyYAML reads the bare `on` key as True
    assert inputs["proof_only"]["type"] == "boolean"
    assert inputs["proof_only"]["default"] is False

    steps = workflow["jobs"]["publish"]["steps"]
    build = next(s for s in steps if s["name"] == "Build distribution")
    publish = next(s for s in steps if s.get("uses", "").startswith("pypa/gh-action-pypi-publish"))
    assert steps.index(build) < steps.index(publish)
    assert build["env"]["PROOF_ONLY"] == "${{ inputs.proof_only }}"
    assert build["run"].count("\n") == 0
    packages_dir = publish["with"]["packages-dir"]
    assert build["run"].endswith(f"python -m scripts.ci.pypi_proof_only_guard ontheloop {packages_dir}")
    assert publish["with"]["skip-existing"] == "${{ inputs.proof_only }}"
    assert publish["with"]["attestations"] == "${{ !inputs.proof_only }}"

    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text()
    assert re.search(r"^/scripts/ci/pypi_proof_only_guard\.py\s+@\S+", codeowners, re.M)
