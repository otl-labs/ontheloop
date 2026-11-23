"""rec-4678: scripts.ci_rca.fallback. Only the writer transport and the reader lookups are patched."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from scripts.ci_rca import fallback
from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.ops_portal.ci_rca_schema import CiRcaContext

ROOT = Path(__file__).resolve().parents[1]
RUN = {
    "run_id": "4242",
    "head_sha": "abcdef0123456789abcdef0123456789abcdef01",  # pragma: allowlist secret
    "workflow_name": "CI",
    "run_url": "https://example.invalid/runs/4242",
    "step_outcomes": "evidence=success rca_agent=failure",
}


def make_bundle(fp: str = "1" * 64, category: str = "test_failure", **over: Any) -> dict:
    data: dict[str, Any] = {
        "schema_version": 3,
        "workflow_name": "CI",
        "failed_check": "pytest",
        "failure_category": category,
        "fingerprint": fp,
        "error_signature": "AssertionError: boom",
        "affected_nodeids": [],
        "earliest_viable_gate": "undetermined",
        "actual_gate_that_caught_it": "CI",
        "escape_mode": None,
        "vacuous_pass": False,
        **over,
    }
    data["sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return data


def put(dirpath: Path, *bundles: dict) -> Path:
    dirpath.mkdir(exist_ok=True)
    for i, b in enumerate(bundles):
        (dirpath / f"{i}-{b['sha256'][:8]}.json").write_text(json.dumps(b), encoding="utf-8")
    return dirpath


def envelope(tmp: Path, subtype: str = "error_max_turns", result: str = "ran out") -> Path:
    p = tmp / "agent.json"
    p.write_text(json.dumps({"type": "result", "subtype": subtype, "result": result}), encoding="utf-8")
    return p


class Env:
    def __init__(self, tmp: Path, bundles: list[dict], agent: Optional[Path] = None) -> None:
        self.tmp = tmp
        self.bundles_dir = put(tmp / "b", *bundles)
        self.agent = agent or envelope(tmp)
        self.out = tmp / "out.txt"
        self.filed: list[dict] = []

    def filer(self, fields: dict, context_v2_json: dict) -> Any:
        self.filed.append({"fields": fields, "ctx": context_v2_json})
        return SimpleNamespace(rec_id=f"rec-90{len(self.filed)}", inserted=True, reason=None)

    def run(self, force_rca: bool = False, open_by_fp: Optional[dict] = None, closed: Optional[dict] = None, **kw: Any):
        open_by_fp, closed = open_by_fp or {}, closed or {}
        return fallback.run_file(
            agent_output=self.agent,
            bundles_dir=self.bundles_dir,
            jobs=self.tmp / "jobs.json",
            run=RUN,
            force_rca=force_rca,
            out=self.out,
            filer=kw.pop("filer", self.filer),
            finder=lambda fp: open_by_fp.get(fp),
            resolver=lambda fp: closed.get(fp),
            pending_dir=self.tmp / "pending",
            citation=".github/workflows/ci-rca.yml:500",
            **kw,
        )

    def out_lines(self) -> list[str]:
        return self.out.read_text(encoding="utf-8").split() if self.out.exists() else []


def test_no_filed_marker_files_undetermined_human_review_rec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rec-4678 acceptance: real file_rec_with_outcome validators and cross-check; only transport and lookups patched."""
    import scripts.ops_data_portal as p

    bundle = make_bundle()
    env = Env(tmp_path, [bundle])
    monkeypatch.setenv("CI_RCA_BUNDLE_EMIT_DIR", str(env.bundles_dir))
    monkeypatch.setenv("S3_LOG_BUCKET", "bucket-x")
    monkeypatch.delenv("CI_RCA_FORCE_RCA", raising=False)
    with (
        patch("scripts.ops_data_portal._ducklake_write", return_value={"key": "rec-9301"}) as write,
        patch("scripts.ops_data_portal.find_open_ci_rca_rec_by_fingerprint", return_value=None),
        patch("scripts.ops_data_portal.closed_head_of_chain", return_value=None),
        patch("scripts.ops_portal.ci_rca_schema._check_bundle_s3_existence", return_value="ok"),
    ):
        marked, code = env.run(filer=p.file_rec_with_outcome)
    assert (marked, code) == (True, 1)
    assert env.out_lines() == ["rec-9301,test_failure"]
    stored = write.call_args.args[1]
    ctx = json.loads(stored["context_v2_json"])
    assert ctx["agent_failure"] == "error_max_turns"
    assert ctx["rca_confidence"] == "undetermined"
    assert ctx["detection_gap"]["earliest_viable_gate"] == "undetermined"
    assert ctx["evidence_bundle_ref"] == {
        "sha256": bundle["sha256"],
        "s3_uri": f"s3://bucket-x/ci-rca-evidence/{bundle['sha256']}.json",
        "upload_status": "ok",
    }
    assert "warn_mode_reject" not in ctx
    assert stored["tags"] == ["ci-rca-fallback"]
    assert stored["source"] == "ci_rca"
    assert "4242" in stored["context"] and RUN["head_sha"] in stored["context"] and "pytest" in stored["context"]
    CiRcaContext.model_validate(ctx)


def test_composed_context_is_strict_valid_and_acceptance_lints() -> None:
    fields, ctx = fallback.compose(
        make_bundle(),
        bundle_name="a.json",
        subtype="no_output",
        run=RUN,
        workflow_file=".github/workflows/ci.yml",
        pending_dir=Path("/nonexistent"),
        citation=".github/workflows/ci-rca.yml:500",
        jobs_no_step=2,
    )
    CiRcaContext.model_validate(ctx)
    assert lint_acceptance_command(fields["acceptance"], require_discrimination=True, require_decidable=True)[0]
    assert len(fields["title"]) <= 80 and "not diagnosed (CI RCA fallback)" in fields["title"]
    assert "2 failing job(s) ran no step" in fields["context"]


@pytest.mark.parametrize(
    "over",
    [
        {"vacuous_pass": True, "failure_category": "vacuous_pass"},
        {"actual_gate_that_caught_it": None, "failure_category": "convergence_refused"},
        {"error_signature": "E" * 5000},
        {"failed_check": "author skipped forgot " * 20},
        {"affected_nodeids": [f"tests/t{i}.py::a b" for i in range(8)]},
        {"escape_mode": "check_ran_vacuously"},
    ],
)
def test_bundle_shapes_compose_schema_valid_without_stamp(over: dict) -> None:
    b = make_bundle(**over)
    fields, ctx = fallback.compose(
        b,
        bundle_name="a.json",
        subtype="x" * 50,
        run=RUN,
        workflow_file=".github/workflows/ci.yml",
        pending_dir=Path("/nonexistent"),
        citation="a.py:1",
        jobs_no_step=0,
    )
    assert ctx["agent_failure"] == "unparseable_output"
    CiRcaContext.model_validate(ctx)
    assert "warn_mode_reject" not in ctx
    assert len(fields["title"]) <= 80 and len(ctx["proximate_cause"]) <= 600
    assert all(40 <= len(e) <= 400 for e in ctx["why_chain"])
    assert fields["acceptance"].count("::") <= 5
    for word in ("author", "did not run", "skipped", "forgot", "missed", "ignored"):
        assert word not in (ctx["detection_gap"]["gap_explanation"] + " ".join(ctx["why_chain"])).lower()


def test_pending_copy_means_upload_failed(tmp_path: Path) -> None:
    b = make_bundle()
    pend = tmp_path / "pend"
    pend.mkdir()
    (pend / f"{b['sha256']}.json").write_text("{}", encoding="utf-8")
    assert fallback._evidence_ref(b["sha256"], pend) == {"sha256": b["sha256"], "s3_uri": "", "upload_status": "upload_failed"}


def test_unobserved_steps_mirror_scope() -> None:
    scope = [{"job_id": 7, "steps": [{"step_index": 2, "log_retrieved": False}, {"step_index": 3, "log_retrieved": True}]}]
    _, ctx = fallback.compose(
        make_bundle(retrieval_evidence={"scope": scope}),
        bundle_name="a",
        subtype="no_output",
        run=RUN,
        workflow_file=".github/workflows/ci.yml",
        pending_dir=Path("/x"),
        citation="a.py:1",
        jobs_no_step=0,
    )
    assert ctx["unobserved_steps"] == [{"job_id": 7, "step_index": 2}]


def test_parser_miss_files_nothing_and_marks_the_open_rec(tmp_path: Path) -> None:
    b = make_bundle()
    env = Env(tmp_path, [b])
    marked, code = env.run(open_by_fp={b["fingerprint"]: "rec-5491"})
    assert (marked, code, env.filed) == (True, 0, [])
    assert env.out_lines() == ["rec-5491,test_failure"]


def test_partial_dedup_agent_filed_the_other(tmp_path: Path) -> None:
    a, b = make_bundle("a" * 64, "cat_a"), make_bundle("b" * 64, "cat_b")
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"subtype": "success", "result": "FILED: rec-77 cat_b"}), encoding="utf-8")
    env = Env(tmp_path, [a, b], agent=agent)
    marked, code = env.run(open_by_fp={"a" * 64: "rec-5"})
    assert (code, env.filed) == (0, [])
    assert env.out_lines() == ["rec-5,cat_a"]


def test_closed_head_stale_rerun_is_covered_not_marked(tmp_path: Path) -> None:
    b = make_bundle()
    env = Env(tmp_path, [b])
    assert env.run(closed={b["fingerprint"]: "drop"}) == (False, 0)
    assert env.filed == [] and env.out_lines() == []


def test_happy_path_makes_no_lookup_and_no_write(tmp_path: Path) -> None:
    b = make_bundle()
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"result": "FILED: rec-77 test_failure"}), encoding="utf-8")
    env = Env(tmp_path, [b], agent=agent)

    def boom(_fp: str) -> str:
        raise AssertionError("reader called")

    with patch("scripts.ci_rca.dedup.decide", side_effect=boom):
        assert env.run() == (False, 0)
    assert env.filed == []


def test_bundle_absent_files_one_rec(tmp_path: Path) -> None:
    env = Env(tmp_path, [])
    assert env.run() == (True, 1)
    assert len(env.filed) == 1
    ctx = env.filed[0]["ctx"]
    assert "evidence_bundle_ref" not in ctx and ctx["detection_gap"]["actual_gate_that_caught_it"] == "unknown"
    assert env.filed[0]["fields"]["file"].endswith(".yml")


def test_no_bundle_but_a_filed_pair_files_nothing(tmp_path: Path) -> None:
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"result": "FILED: rec-77 x"}), encoding="utf-8")
    env = Env(tmp_path, [], agent=agent)
    assert env.run() == (False, 0)


def test_unparseable_bundle_is_filed_as_absent_and_not_covered_by_pairs(tmp_path: Path) -> None:
    b = make_bundle()
    env = Env(tmp_path, [b])
    (env.bundles_dir / "zz.json").write_text("{not json", encoding="utf-8")
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"result": "FILED: rec-77 test_failure"}), encoding="utf-8")
    env.agent = agent
    assert env.run() == (True, 1)
    assert len(env.filed) == 1 and "evidence_bundle_ref" not in env.filed[0]["ctx"]


def test_short_category_looks_every_bundle_up_and_files_the_uncovered(tmp_path: Path) -> None:
    a, b = make_bundle("a" * 64), make_bundle("b" * 64, error_signature="other")
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"result": "FILED: rec-77 test_failure"}), encoding="utf-8")
    env = Env(tmp_path, [a, b], agent=agent)
    marked, code = env.run(open_by_fp={"b" * 64: "rec-77"})
    assert code == 1 and len(env.filed) == 1
    assert env.filed[0]["ctx"]["proximate_cause"].count("AssertionError") == 1


def test_repeated_marker_counts_once(tmp_path: Path) -> None:
    a, b = make_bundle("a" * 64), make_bundle("b" * 64)
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps({"result": "FILED: rec-77 test_failure\nFILED: rec-77 test_failure"}), encoding="utf-8")
    env = Env(tmp_path, [a, b], agent=agent)
    assert env.run()[1] == 1 and len(env.filed) == 2


def test_force_rca_covers_only_by_a_pairs_own_id(tmp_path: Path) -> None:
    b = make_bundle()
    (tmp_path / "x").mkdir()
    env = Env(tmp_path, [b])
    assert env.run(force_rca=True, open_by_fp={b["fingerprint"]: "rec-4001"})[1] == 1
    assert len(env.filed) == 1
    agent = tmp_path / "agent2.json"
    agent.write_text(json.dumps({"result": "FILED: rec-4001 test_failure"}), encoding="utf-8")
    env2 = Env(tmp_path / "x", [b], agent=agent)
    assert env2.run(force_rca=True, open_by_fp={b["fingerprint"]: "rec-4001"}) == (False, 0)


def test_reader_error_files_nothing_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    env = Env(tmp_path, [make_bundle()])
    with patch("scripts.ci_rca.dedup.decide", side_effect=RuntimeError("reader down")):
        assert env.run() == (False, 1)
    assert env.filed == [] and "::error::" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("reason", "inserted", "written"),
    [("ci_rca_open_duplicate", False, True), ("ci_rca_stale_rerun", False, False)],
)
def test_not_inserted_outcomes(tmp_path: Path, reason: str, inserted: bool, written: bool) -> None:
    env = Env(tmp_path, [make_bundle()])
    code = env.run(filer=lambda f, context_v2_json: SimpleNamespace(rec_id="rec-3", inserted=inserted, reason=reason))
    assert code == (written, 1) and bool(env.out_lines()) is written


def test_write_failure_exits_1_and_marks_nothing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    env = Env(tmp_path, [make_bundle()])

    def fail(fields: dict, context_v2_json: dict) -> None:
        raise ValueError("writer down")

    assert env.run(filer=fail) == (False, 1)
    assert "filing failed" in capsys.readouterr().err


def test_terminal_subtype(tmp_path: Path) -> None:
    assert fallback.terminal_subtype(tmp_path / "missing") == "no_output"
    p = tmp_path / "e"
    p.write_text("", encoding="utf-8")
    assert fallback.terminal_subtype(p) == "no_output"
    p.write_text("not json", encoding="utf-8")
    assert fallback.terminal_subtype(p) == "unparseable_output"
    p.write_text(json.dumps({"subtype": "Bad Subtype"}), encoding="utf-8")
    assert fallback.terminal_subtype(p) == "unparseable_output"
    p.write_text("[1]", encoding="utf-8")
    assert fallback.terminal_subtype(p) == "unparseable_output"
    assert fallback.terminal_subtype(envelope(tmp_path)) == "error_max_turns"


def test_jobs_without_steps_and_helpers(tmp_path: Path) -> None:
    j = tmp_path / "j.json"
    assert fallback._jobs_without_steps(j) == 0
    j.write_text(json.dumps({"jobs": [{"conclusion": "failure", "steps": []}, {"conclusion": "failure", "steps": [1]}, 3]}))
    assert fallback._jobs_without_steps(j) == 1
    j.write_text("{}")
    assert fallback._jobs_without_steps(j) == 0
    assert fallback._clamp("a  b" * 100, 10).endswith("...")
    assert fallback._workflow_file("CI").startswith(".github/workflows/")
    assert fallback._workflow_file("No Such Workflow") == ".github/workflows/ci-rca.yml"
    assert fallback._citation(ROOT).startswith(".github/workflows/ci-rca.yml:")
    assert fallback._citation(tmp_path) == "scripts/ci_rca/fallback.py:1"
    assert "pytest" in fallback._acceptance({"affected_nodeids": ["tests/a.py::t"]}, "x.yml")
    assert "gh run list --workflow ci.yml" in fallback._acceptance(None, ".github/workflows/ci.yml")


def test_pairs_dedupes_and_tolerates_absent_out(tmp_path: Path) -> None:
    agent = tmp_path / "a.json"
    agent.write_text(json.dumps({"result": "FILED: rec-1 c1\nFILED: rec-2"}), encoding="utf-8")
    assert fallback.run_pairs(agent, tmp_path / "none") == ["rec-1,c1", "rec-2,"]
    out = tmp_path / "o"
    out.write_text("rec-1,c1\nrec-9,c9\n", encoding="utf-8")
    assert fallback.run_pairs(agent, out) == ["rec-1,c1", "rec-2,", "rec-9,c9"]


def test_cli_stdout_is_only_the_marked_line(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    b = make_bundle()
    env = Env(tmp_path, [b])
    with patch("scripts.ci_rca.dedup._default_finder", return_value="rec-5491"):
        code = fallback.main(
            [
                "file", "--agent-output", str(env.agent), "--bundles-dir", str(env.bundles_dir), "--jobs", str(tmp_path / "j"),
                "--run-id", "4x2", "--head-sha", "abc", "--workflow-name", "CI", "--force-rca", "false",
                "--step-outcomes", "a=b", "--out", str(env.out),
            ]
        )  # fmt: skip
    assert code == 0 and capsys.readouterr().out == "marked=true\n"
    assert fallback.main(["pairs", str(env.agent), str(env.out)]) == 0
    assert capsys.readouterr().out.split() == ["rec-5491,test_failure"]


def test_schema_field_roundtrips_and_rejects_malformed() -> None:
    _, ctx = fallback.compose(
        make_bundle(), bundle_name="a", subtype="no_output", run=RUN, workflow_file="x.yml", pending_dir=Path("/x"),
        citation="a.py:1", jobs_no_step=0,
    )  # fmt: skip
    assert CiRcaContext.model_validate(ctx).model_dump()["agent_failure"] == "no_output"
    with pytest.raises(ValidationError):
        CiRcaContext.model_validate({**ctx, "agent_failure": "Bad Value"})


def _steps() -> dict[str, dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows/ci-rca.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["rca"]["steps"]
    return {s["id"]: s for s in steps if "id" in s} | {"_order": [s.get("id") for s in steps]}


def test_workflow_wiring() -> None:
    steps = _steps()
    order = steps["_order"]
    assert order.index("rca_agent") < order.index("fallback") < order.index("mark_rca")
    fb = steps["fallback"]
    assert fb["timeout-minutes"] == 4
    for needle in ("!cancelled()", "hashFiles('scripts/ci_rca/fallback.py')", "rejected_deployment.outputs.skip != 'true'",
                   "designed_refusal.outputs.skip != 'true'", "dedup.outputs.already_filed != 'true'",
                   "fp_dedup.outputs.deduped != 'true'", "persona_gate.outcome == 'success'"):  # fmt: skip
        assert needle in fb["if"], needle
    assert fb["env"]["CI_RCA_BUNDLE_EMIT_DIR"] == "/tmp/ci-rca-bundles" and "CI_RCA_FORCE_RCA" in fb["env"]
    assert fb["env"]["S3_LOG_BUCKET"] == "agent-platform-data-lake" and "GH_TOKEN" not in fb["env"]
    assert fb["run"].strip().splitlines()[-1].endswith('>> "$GITHUB_OUTPUT"') and "|" not in fb["run"]
    mark = steps["mark_rca"]
    assert "steps.fallback.outputs.marked == 'true'" in mark["if"] and "!cancelled()" in mark["if"]
    assert "scripts.ci_rca.fallback pairs" in mark["run"] and "scripts.ci_rca.filing --all" in mark["run"]
    assert "\n  " not in "".join(ln for ln in mark["run"].splitlines() if "fallback pairs" in ln)


def test_contract_names_the_module_and_projection() -> None:
    c = yaml.safe_load((ROOT / "docs/contracts/ci-rca-lifecycle.yaml").read_text(encoding="utf-8"))
    assert (ROOT / c["agent_failure_fallback"]["module"]).is_file()
    assert c["projection_fields"]["agent_failure"]["authored_by"] == "scripts/ci_rca/fallback.py"


def test_missing_bundles_dir_and_non_object_bundle(tmp_path: Path) -> None:
    assert fallback._load_bundles(tmp_path / "nope") == []
    (tmp_path / "a.json").write_text("[1]", encoding="utf-8")
    assert fallback._load_bundles(tmp_path) == [(None, "a.json")]


def test_workflow_file_falls_back_when_taxonomy_enumeration_raises() -> None:
    with patch("scripts.ci_rca.taxonomy.enumerate_workflow_name_paths", side_effect=RuntimeError("x")):
        assert fallback._workflow_file("CI") == ".github/workflows/ci-rca.yml"


def test_unresolved_bucket_means_upload_failed(tmp_path: Path) -> None:
    with patch("scripts.ci_rca.evidence._resolve_bucket", return_value=""):
        assert fallback._evidence_ref("a" * 64, tmp_path)["upload_status"] == "upload_failed"


def test_empty_scope_still_echoes_an_empty_list() -> None:
    _, ctx = fallback.compose(
        make_bundle(retrieval_evidence={"scope": []}),
        bundle_name="a",
        subtype="no_output",
        run=RUN,
        workflow_file="x.yml",
        pending_dir=Path("/x"),
        citation="a.py:1",
        jobs_no_step=0,
    )
    assert ctx["unobserved_steps"] == []


def test_default_filer_is_the_portal_writer(tmp_path: Path) -> None:
    env = Env(tmp_path, [make_bundle()])
    seen: list[dict] = []

    def fake(fields: dict, context_v2_json: dict) -> Any:
        seen.append(fields)
        return SimpleNamespace(rec_id="rec-5", inserted=True, reason=None)

    with patch("scripts.ops_data_portal.file_rec_with_outcome", fake):
        assert fallback.run_file(
            agent_output=env.agent,
            bundles_dir=env.bundles_dir,
            jobs=tmp_path / "j",
            run=RUN,
            force_rca=False,
            out=env.out,
            finder=lambda fp: None,
            resolver=lambda fp: None,
        ) == (True, 1)
    assert len(seen) == 1
