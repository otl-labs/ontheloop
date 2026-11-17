import shutil
from pathlib import Path

import pytest
import yaml

from src.persona_backends.contract import BUILT, RunRefused, load
from tests.persona_backends.fakes import FIXTURE

INDEX = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "executor-personas.yaml"


def _copy(tmp_path: Path, mutate=None, prompt_suffix: str = "") -> Path:
    root = tmp_path / "c"
    (root / "rendered").mkdir(parents=True)
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    contract = root / "fixture_persona.yaml"
    contract.write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    if prompt_suffix:
        with (root / "rendered" / "fixture_persona.txt").open("a", encoding="utf-8") as handle:
            handle.write(prompt_suffix)
    return contract


def test_fixture_contract_loads():
    loaded = load(FIXTURE)
    assert loaded.persona == "fixture_persona"
    assert loaded.blob_names == ("notes",)
    assert (loaded.max_turns, loaded.max_revisions) == (6, 1)
    assert loaded.prompt.startswith("Quoted content")


def test_prompt_one_byte_off_is_refused(tmp_path):
    with pytest.raises(RunRefused, match="rendered_prompt_sha256"):
        load(_copy(tmp_path, prompt_suffix="x"))


@pytest.mark.parametrize("capability", ["read_repo", "run_tests", "edit_repo", "warehouse_read:x"])
def test_unbuilt_capability_is_refused_by_name(tmp_path, capability):
    contract = _copy(tmp_path, lambda d: d["capabilities"].append(capability))
    with pytest.raises(RunRefused, match=capability):
        load(contract)


def test_missing_budget_value_is_refused(tmp_path):
    with pytest.raises(RunRefused, match="max_turns"):
        load(_copy(tmp_path, lambda d: d["budget"].pop("max_turns")))


def test_capability_gate_matches_index():
    index = yaml.safe_load(INDEX.read_text(encoding="utf-8"))
    owned = {
        row["id"]
        for row in index["capability_vocabulary"]["capabilities"]
        if row["binding"]["litellm"]["kind"] == "backend_tool" and row["binding"]["litellm"]["owning_item"] == "T4.32:c1"
    }
    assert set(BUILT) == owned
