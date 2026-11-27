import shutil
from pathlib import Path

import pytest
import yaml

from src.persona_backends.contract import BUILT, RunRefused, load
from tests.persona_backends.fakes import FIXTURE
from tests.persona_contracts.fixture_tree import MALFORMED_RULES

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


@pytest.mark.parametrize("capability", ["read_repo", "run_tests", "edit_repo"])
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
        if row["binding"]["litellm"]["owning_item"] == "T4.32:c1"
    }
    assert set(BUILT) == owned


def test_unreadable_and_non_mapping_contracts_are_refused(tmp_path):
    with pytest.raises(RunRefused, match="unreadable"):
        load(tmp_path / "absent.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [", encoding="utf-8")
    with pytest.raises(RunRefused, match="unreadable"):
        load(bad)
    listing = tmp_path / "list.yaml"
    listing.write_text("- a\n", encoding="utf-8")
    with pytest.raises(RunRefused, match="not a mapping"):
        load(listing)


def test_missing_rendered_prompt_is_refused(tmp_path):
    contract = _copy(tmp_path)
    (contract.parent / "rendered" / "fixture_persona.txt").unlink()
    with pytest.raises(RunRefused, match="rendered prompt unreadable"):
        load(contract)


def test_capability_naming_an_undeclared_blob_is_refused(tmp_path):
    contract = _copy(tmp_path, lambda d: d["capabilities"].append("read_input:ghost"))
    with pytest.raises(RunRefused, match="ghost"):
        load(contract)


def test_present_rule_loads_and_absent_is_none(tmp_path):
    rule = {"cases": [{"verdict": "REVISE", "any": [{"array": "findings"}]}], "otherwise": "PROCEED"}
    assert load(_copy(tmp_path, lambda d: d.__setitem__("verdict_consistency", rule))).verdict_consistency == rule
    assert load(FIXTURE).verdict_consistency is None


@pytest.mark.parametrize("name", sorted(MALFORMED_RULES))
def test_malformed_verdict_rule_is_refused(tmp_path, name):
    with pytest.raises(RunRefused, match="verdict_consistency is malformed"):
        load(_copy(tmp_path, lambda d: d.__setitem__("verdict_consistency", MALFORMED_RULES[name])))


def test_non_mapping_verdict_rule_is_refused(tmp_path):
    with pytest.raises(RunRefused, match="verdict_consistency is malformed"):
        load(_copy(tmp_path, lambda d: d.__setitem__("verdict_consistency", ["REVISE"])))


def test_warehouse_read_loads_verbs(tmp_path):
    def mutate(d):
        d["capabilities"].extend(["warehouse_read:b", "warehouse_read:a", "warehouse_read:b"])

    loaded = load(_copy(tmp_path, mutate))
    assert loaded.warehouse_verbs == ("b", "a") and loaded.blob_names == ("notes",)
    assert load(FIXTURE).warehouse_verbs == ()


@pytest.mark.parametrize("capability", ["warehouse_read", "warehouse_read:", "warehouse_read:read_input"])
def test_warehouse_read_naming_read_input_is_refused(tmp_path, capability):
    with pytest.raises(RunRefused, match="warehouse_read"):
        load(_copy(tmp_path, lambda d: d["capabilities"].append(capability)))


def _tree_contract(tmp_path, mutate=None, caps=("read_tree:head",)):
    def change(data):
        data["capabilities"] += list(caps)
        data["budget"]["read_tree"] = {
            "max_calls": 5,
            "max_read_bytes": 1000,
            "max_search_results": 10,
            "max_total_bytes": 9999,
        }
        data["failure_outputs"].append({"id": "review_incomplete", "meaning": "A read limit was reached."})
        if mutate:
            mutate(data)

    return _copy(tmp_path, change)


def test_read_tree_needs_its_budget_and_failure_output(tmp_path):
    loaded = load(_tree_contract(tmp_path))
    assert loaded.trees == ("head",) and loaded.read_tree_budget["max_calls"] == 5
    assert load(FIXTURE).trees == () and load(FIXTURE).read_tree_budget is None
    mutations = [
        lambda d: d["budget"].pop("read_tree"),
        lambda d: d["budget"].update(read_tree="x"),
        lambda d: d["budget"]["read_tree"].pop("max_total_bytes"),
        lambda d: d["budget"]["read_tree"].update(max_calls=True),
        lambda d: d["budget"]["read_tree"].update(max_calls=0),
        lambda d: d["failure_outputs"].pop(),
    ]
    for mutate in mutations:
        with pytest.raises(RunRefused):
            load(_tree_contract(tmp_path / str(mutations.index(mutate)), mutate))


def test_read_tree_names_only_head_or_main(tmp_path):
    assert load(_tree_contract(tmp_path / "a", caps=("read_tree:head", "read_tree:main"))).trees == ("head", "main")
    for bad in ("read_tree:other", "read_tree:", "read_tree"):
        with pytest.raises(RunRefused, match="names no tree"):
            load(_tree_contract(tmp_path / bad.replace(":", "_"), caps=(bad,)))
