"""Mirror home for scripts/ops_portal/closure_gate.py (Decision 186, PLAN-escape-closure-obligation).

Exercises the gate body directly: the escape-classified predicate over existing UNION merged
context, the four `<kind>:<ref>` artifact kinds resolving present/absent, the FIX-BOUND leg (a
resolvable artifact whose defining file is untouched by the fix commit is refused), check:'s
TIER-fact requirement (never bare registry membership), the fixture: tests/fixtures/ root
constraint, the waiver grammar as CATEGORY MEMBERSHIP, and the contract-parity assertion that this
module's vocabularies equal docs/contracts/ci-rca-lifecycle.yaml::closure_obligation's.
"""

from __future__ import annotations

import ast
import hashlib
import json

import pytest
import yaml

from scripts.ops_portal._common import ROOT
from scripts.ops_portal.closure_gate import (
    ARTIFACT_KINDS,
    FAILS,
    HOLDS,
    KIND_STRENGTH,
    WAIVER_CATEGORIES,
    AcceptanceVerdictRefused,
    _fix_commit_touches,
    _fixture_kind_fact,
    assert_acceptance_verdict,
    changed_files,
    closure_stamps_applicable,
    is_escape_classified,
    is_valid_waiver,
    parse_context_json,
    resolve_closure_artifact,
)


def _head() -> str:
    from scripts.ops_portal.ci_rca_lifecycle import current_commit_sha

    sha = current_commit_sha()
    assert sha, "git rev-parse HEAD must resolve inside the test environment"
    return sha


class TestIsEscapeClassified:
    def test_escape_class_set(self) -> None:
        assert is_escape_classified({"escape_class": "no-edge"}) is True

    def test_detection_gap_escape_mode_undetermined_counts_as_set(self) -> None:
        assert is_escape_classified({"detection_gap": {"escape_mode": "undetermined"}}) is True

    def test_neither_set_is_false(self) -> None:
        assert is_escape_classified({}) is False
        assert is_escape_classified({"detection_gap": {}}) is False


class TestArtifactResolution:
    def test_named_artifact_absent_on_main_is_refused(self) -> None:
        """The known-bad fixture: a named artifact that does not exist on main never resolves,
        regardless of a well-formed fix sha."""
        assert resolve_closure_artifact("shard:this-shard-id-does-not-exist-on-main", _head()) is False

    def test_check_kind_requires_pre_tier_not_membership(self) -> None:
        """check:<name> resolves against Entry.pre, never bare _ALL_ENTRIES membership -- a
        registered entry with pre=False does not discharge the obligation. Without this the gate
        is vacuous for the 50/92 tier_misplaced escapes, whose catching check is already
        registered before any fix lands."""
        from scripts.checks import registry

        assert "validate_requirements" in registry._ALL_ENTRIES
        assert registry._ALL_ENTRIES["validate_requirements"].pre is False
        assert resolve_closure_artifact("check:validate_requirements", _head()) is False

        # A pre=True entry is a DIFFERENT refusal reason (the fix-bound leg, tested separately) --
        # this asserts the tier fact itself is real, not merely that the whole path is inert.
        assert "validate_workflow_agent_safety" in registry._ALL_ENTRIES
        assert registry._ALL_ENTRIES["validate_workflow_agent_safety"].pre is True

    def test_artifact_not_touched_by_fix_commit_is_refused(self) -> None:
        """A resolvable artifact (kind-fact holds) whose defining file was NOT touched by the
        named fix commit is refused; an unresolvable sha refuses too (fail closed, Decision 55)."""
        # kind-fact holds (the shard file exists), but HEAD never touched it.
        assert resolve_closure_artifact("shard:acceptance-literal-lint-guard", _head()) is False
        # an unresolvable / garbage sha refuses regardless of kind-fact.
        assert resolve_closure_artifact("shard:acceptance-literal-lint-guard", "0" * 40) is False
        # no fix sha at all on the artifact route is refused.
        assert resolve_closure_artifact("shard:acceptance-literal-lint-guard", None) is False
        assert resolve_closure_artifact("shard:acceptance-literal-lint-guard", "") is False

    def test_fixture_kind_requires_tests_fixtures_root(self) -> None:
        """fixture:<path> must name a path under tests/fixtures/; fixture:README.md resolves as
        a path on disk but does not discharge the obligation -- unconstrained, it would DEPRESS
        the Decision 186 reversal-condition (a) waiver rate by counting on the artifact side for
        free."""
        assert (ROOT / "README.md").is_file()
        holds, defining = _fixture_kind_fact("README.md", ROOT)
        assert holds is False
        assert defining == []

        holds, defining = _fixture_kind_fact("tests/fixtures/__init__.py", ROOT)
        assert holds is True
        assert defining == ["tests/fixtures/__init__.py"]

        assert resolve_closure_artifact("fixture:README.md", "0" * 40) is False

    def test_bare_rec_id_never_resolves(self) -> None:
        """A bare 'rec-NNN' token is not `<kind>:<ref>`-shaped and simply fails to parse -- the
        gate never dereferences a rec id, so no rec-to-rec pointer is even expressible."""
        assert resolve_closure_artifact("rec-3132", _head()) is False


class TestWaiverGrammar:
    def test_unknown_waiver_category_is_refused(self) -> None:
        """Membership, not shape, is the acceptance bar -- a well-shaped but off-vocabulary
        category is refused, or Decision 186 point 8's ratchet is defeatable by typing a new
        category into a string."""
        assert is_valid_waiver("made_up_category_nobody_ratified", "a perfectly good reason string") is False

    def test_duplicate_of_is_not_a_waiver_category(self) -> None:
        """HUMAN RULING, BINDING: duplicate_of is excluded from WAIVER_CATEGORIES entirely -- a
        duplicate asserts an artifact is OWED, never that one is infeasible."""
        assert "duplicate_of" not in WAIVER_CATEGORIES
        assert is_valid_waiver("duplicate_of", "master rec carries the fix") is False

    def test_empty_reason_is_refused(self) -> None:
        assert is_valid_waiver("environment_only", "") is False
        assert is_valid_waiver("environment_only", "   ") is False

    def test_well_formed_waiver_is_valid(self) -> None:
        assert is_valid_waiver("stale_no_recurrence", "last_seen=2026-01-01, created=2025-12-01") is True

    def test_waiver_vocabulary_is_the_ratified_seed_set(self) -> None:
        """WAIVER_CATEGORIES is EXACTLY the four-member ratified seed set -- an exact-set
        assertion, not a superset check, so a fifth category cannot be added without failing this
        test (Decision 186 point 8: adding a category is a numbered-Decision event)."""
        assert WAIVER_CATEGORIES == frozenset(
            {"stale_no_recurrence", "environment_only", "no_premerge_gate_by_design", "risk_accepted"}
        )


class TestStampApplicability:
    def test_escape_classified_implies_stampable(self) -> None:
        """CONTAINMENT (Decision 186 point 8's fourth weakening form): every raw context_v2_json
        cell that parses to an escape-classified ctx is stampable -- so the fix-commit binding a
        closing update_rec threads can never be silently dropped for an escape-classified rec. A
        future widening of closure_stamps_applicable that drops this containment would defeat the
        ratchet by letting an escape-classified rec's own closing write skip closure_fix_sha.
        Checked over both raw-string and already-parsed-dict inputs (the two call-site shapes)
        and both is_escape_classified triggers (escape_class and detection_gap.escape_mode)."""
        escape_classified_raws = [
            json.dumps({"escape_class": "no-edge"}),
            json.dumps({"detection_gap": {"escape_mode": "undetermined"}}),
            {"escape_class": "capped"},
            {"detection_gap": {"escape_mode": "tier_misplaced"}},
        ]
        for raw in escape_classified_raws:
            assert is_escape_classified(parse_context_json(raw)) is True
            assert closure_stamps_applicable(raw) is True

        # The converse does not hold -- stampable never implies escape-classified; this is a
        # one-directional containment, not an equivalence.
        non_escape_raw = json.dumps({"last_seen": "2026-01-01"})
        assert closure_stamps_applicable(non_escape_raw) is True
        assert is_escape_classified(parse_context_json(non_escape_raw)) is False


class TestAcceptanceVerdict:
    """Decision 201: assert_acceptance_verdict refuses every non-holds or mis-keyed record, and
    passes an absent one -- the predicate half of the single update_rec enforcement site."""

    _REC_ID = "rec-1234"
    _ACCEPTANCE = "grep -q foo bar.py"
    _SHA256 = hashlib.sha256(_ACCEPTANCE.encode("utf-8")).hexdigest()
    _CLOSING_SHA = "deadbeef"

    def _holds_record(self, **overrides) -> dict:
        record = {
            "rec_id": self._REC_ID,
            "sha": self._CLOSING_SHA,
            "acceptance_sha256": self._SHA256,
            "verdict": HOLDS,
            "source": "static",
            "arm": "pass",
        }
        record.update(overrides)
        return record

    def test_absent_record_returns_none(self) -> None:
        # No-op proven by absence of AcceptanceVerdictRefused, not a return-value comparison
        # (assert_acceptance_verdict's return type is None; mypy flags `is None` on it).
        assert_acceptance_verdict("open", "closed", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, None)

    def test_holds_record_correctly_keyed_passes(self) -> None:
        assert_acceptance_verdict("open", "closed", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, self._holds_record())

    def test_non_holds_verdict_refused(self) -> None:
        with pytest.raises(AcceptanceVerdictRefused):
            assert_acceptance_verdict(
                "open", "closed", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, self._holds_record(verdict=FAILS)
            )

    def test_mismatched_rec_id_refused(self) -> None:
        with pytest.raises(AcceptanceVerdictRefused):
            assert_acceptance_verdict(
                "open", "closed", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, self._holds_record(rec_id="rec-9999")
            )

    def test_mismatched_acceptance_sha256_refused(self) -> None:
        with pytest.raises(AcceptanceVerdictRefused):
            assert_acceptance_verdict(
                "open",
                "closed",
                self._REC_ID,
                self._ACCEPTANCE,
                self._CLOSING_SHA,
                self._holds_record(acceptance_sha256="stale"),
            )

    def test_mismatched_sha_refused_even_though_record_carries_a_sha(self) -> None:
        """Load-bearing: the record's OWN sha field disagreeing with the independently-supplied
        closing_sha is refused -- proving closing_sha is a caller-supplied gate input, never read
        back out of the record itself (a self-comparison would make this arm vacuously true)."""
        with pytest.raises(AcceptanceVerdictRefused):
            assert_acceptance_verdict(
                "open",
                "closed",
                self._REC_ID,
                self._ACCEPTANCE,
                self._CLOSING_SHA,
                self._holds_record(sha="some-other-sha"),
            )

    def test_status_preserving_write_with_absent_record_is_a_noop(self) -> None:
        assert_acceptance_verdict("open", "open", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, None)

    def test_status_preserving_write_with_invalid_record_is_a_noop(self) -> None:
        """Mirrors assert_closure_obligation's own gating (Decision 186 point 3): a write that is
        not a first-hop transition into a bound status never reaches the record check at all, so
        even a present, badly-keyed record does not raise here."""
        assert_acceptance_verdict(
            "closed", "closed", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, self._holds_record(verdict=FAILS)
        )

    def test_second_hop_into_bound_status_with_invalid_record_is_a_noop(self) -> None:
        """The obligation was already discharged at the first hop into a bound status; a later
        declined -> superseded hop is ungated here exactly as it is in assert_closure_obligation."""
        assert_acceptance_verdict(
            "declined", "superseded", self._REC_ID, self._ACCEPTANCE, self._CLOSING_SHA, self._holds_record(verdict=FAILS)
        )


class TestContractParity:
    def test_closure_vocabularies_match_contract(self) -> None:
        """The kind, kind-strength and category vocabularies in closure_gate.py equal those
        declared in docs/contracts/ci-rca-lifecycle.yaml::closure_obligation -- contract-code
        drift fails rather than silently diverging."""
        contract = yaml.safe_load((ROOT / "docs" / "contracts" / "ci-rca-lifecycle.yaml").read_text(encoding="utf-8"))
        obligation = contract["closure_obligation"]

        assert set(obligation["artifact_kinds"]) == ARTIFACT_KINDS
        assert list(obligation["kind_strength"]) == list(KIND_STRENGTH)
        assert set(obligation["waiver_categories"]) == WAIVER_CATEGORIES
        assert "duplicate_of" not in obligation["waiver_categories"]

    def test_stamp_precondition_call_sites_match_importers(self) -> None:
        """The contract's closure_obligation.stamp_precondition.call_sites list equals, in BOTH
        directions, the set of scripts/**/*.py files that IMPORT closure_stamps_applicable --
        resolved via ast.parse (an ImportFrom naming the symbol, or an Import of
        scripts.ops_portal.closure_gate paired with an attribute reference), NEVER a textual
        mention -- which would count this very test and every docstring as a false member."""
        contract = yaml.safe_load((ROOT / "docs" / "contracts" / "ci-rca-lifecycle.yaml").read_text(encoding="utf-8"))
        declared = set(contract["closure_obligation"]["stamp_precondition"]["call_sites"])

        importers: set[str] = set()
        for path in sorted((ROOT / "scripts").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:
                continue
            imports_symbol = False
            imports_module = False
            uses_module_attr = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module == "scripts.ops_portal.closure_gate" and any(
                        alias.name == "closure_stamps_applicable" for alias in node.names
                    ):
                        imports_symbol = True
                elif isinstance(node, ast.Import):
                    if any(alias.name == "scripts.ops_portal.closure_gate" for alias in node.names):
                        imports_module = True
                elif isinstance(node, ast.Attribute) and node.attr == "closure_stamps_applicable":
                    uses_module_attr = True
            if imports_symbol or (imports_module and uses_module_attr):
                importers.add(rel)

        assert importers == declared, (
            f"declared-but-not-importing: {sorted(declared - importers)}; "
            f"importing-but-undeclared: {sorted(importers - declared)}"
        )


def _throwaway_commit(tmp_path) -> tuple:  # noqa: ANN001
    """A tiny, self-contained real git repo with one known commit touching a.txt and b.txt.

    Deliberately NEVER uses this checkout's own HEAD: a `pull_request`-triggered CI run checks
    out a synthetic two-parent merge commit, and `git diff-tree` lists NOTHING for a true merge
    commit by default (the exact gotcha rec-3775's merge-leg gate itself guards against) -- an
    ambient-HEAD-based test would pass locally (a normal single-parent commit) and fail in CI.
    Mirrors the throwaway-repo pattern in tests/verification_graduation/conftest.py.
    """
    import subprocess

    repo = tmp_path / "throwaway"
    repo.mkdir()

    def _git(args: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result

    _git(["init", "-q"])
    _git(["config", "user.email", "test@example.com"])
    _git(["config", "user.name", "Test"])
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"])
    _git(["commit", "-q", "-m", "initial"])
    (repo / "a.txt").write_text("hello again\n", encoding="utf-8")
    (repo / "b.txt").write_text("new file\n", encoding="utf-8")
    _git(["add", "-A"])
    _git(["commit", "-q", "-m", "second"])
    sha = _git(["rev-parse", "HEAD"]).stdout.strip()
    return repo, sha


class TestChangedFiles:
    """rec-3775's DRY extraction: changed_files(sha, root) is the sole diff-tree call site, and
    _fix_commit_touches keeps its fail-closed behaviour through the delegation (behaviour-
    preserving, not a new capability)."""

    def test_returns_the_commits_changed_paths(self, tmp_path) -> None:  # noqa: ANN001
        repo, sha = _throwaway_commit(tmp_path)
        touched = changed_files(sha, repo)
        assert touched == {"a.txt", "b.txt"}

    def test_none_sha_returns_none(self) -> None:
        assert changed_files(None, ROOT) is None
        assert changed_files("", ROOT) is None

    def test_unresolvable_sha_returns_none(self) -> None:
        assert changed_files("0" * 40, ROOT) is None

    def test_fix_commit_touches_still_fail_closed_on_unresolvable_sha(self) -> None:
        assert _fix_commit_touches(["some/file.py"], "0" * 40, ROOT) is False
        assert _fix_commit_touches(["some/file.py"], None, ROOT) is False

    def test_fix_commit_touches_still_intersects_real_changed_set(self, tmp_path) -> None:  # noqa: ANN001
        repo, sha = _throwaway_commit(tmp_path)
        assert _fix_commit_touches(["a.txt"], sha, repo) is True
        assert _fix_commit_touches(["this/path/was/never/touched.py"], sha, repo) is False


def _commit_paths(tmp_path, paths: list[str]) -> tuple:  # noqa: ANN001
    """A throwaway repo whose last commit adds exactly `paths` (the fix commit under test)."""
    import subprocess

    repo = tmp_path / "track_repo"
    repo.mkdir()

    def _git(args: list[str]) -> str:
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result.stdout.strip()

    _git(["init", "-q"])
    _git(["config", "user.email", "test@example.com"])
    _git(["config", "user.name", "Test"])
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(["add", "-A"])
    _git(["commit", "-q", "-m", "initial"])
    for rel in paths:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    _git(["add", "-A"])
    _git(["commit", "-q", "-m", "fix"])
    return repo, _git(["rev-parse", "HEAD"])


class TestConfirmedRepairTrack:
    """Decision 226 cl.1: the confirmed track is read from the fix commit's diff, never from the agent."""

    def test_tests_only_commit_is_test_defect(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_repair_track

        repo, sha = _commit_paths(tmp_path, ["tests/test_a.py", "tests/sub/test_b.py"])
        assert confirmed_repair_track(sha, repo) == "test_defect"

    def test_bookkeeping_paths_are_ignored(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_repair_track

        repo, sha = _commit_paths(
            tmp_path, ["tests/test_a.py", "docs/plans/PLAN-x.yaml", "config/agent/verification_registry/entries/x.yaml"]
        )
        assert confirmed_repair_track(sha, repo) == "test_defect"

    def test_source_plus_tests_is_product_regression(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_repair_track

        repo, sha = _commit_paths(tmp_path, ["scripts/foo.py", "tests/test_a.py"])
        assert confirmed_repair_track(sha, repo) == "product_regression"

    def test_plans_only_commit_is_unset(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_repair_track

        repo, sha = _commit_paths(tmp_path, ["docs/plans/PLAN-x.yaml"])
        assert confirmed_repair_track(sha, repo) is None

    def test_unresolvable_sha_is_unset(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_repair_track

        repo, _ = _commit_paths(tmp_path, ["tests/test_a.py"])
        assert confirmed_repair_track("0" * 40, repo) is None
        assert confirmed_repair_track(None, repo) is None

    def test_constants_match_contract(self) -> None:
        from scripts.ops_portal import closure_gate

        policy = yaml.safe_load((ROOT / "docs/contracts/red-main-recovery.yaml").read_text(encoding="utf-8"))[
            "recovery_policy"
        ]
        assert closure_gate._TEST_PATH_PREFIX == policy["test_path_prefix"]
        assert list(closure_gate._BOOKKEEPING_PREFIXES) == policy["bookkeeping_prefixes"]

    def test_stamp_only_for_ci_rca(self, tmp_path) -> None:  # noqa: ANN001
        from scripts.ops_portal.closure_gate import confirmed_track_stamp

        repo, sha = _commit_paths(tmp_path, ["tests/test_a.py"])
        assert confirmed_track_stamp("ci_rca", sha, repo) == {"repair_track_confirmed": "test_defect"}
        assert confirmed_track_stamp("executor", sha, repo) == {}
        assert confirmed_track_stamp("ci_rca", "0" * 40, repo) == {}

    def test_stamp_fixed_by_sha_writes_confirmed_track_in_same_call(self) -> None:
        from unittest.mock import patch

        from scripts.ops_portal.ci_rca_lifecycle import stamp_fixed_by_sha

        existing = {"id": "rec-1", "context_v2_json": json.dumps({"fingerprint": "a" * 64})}
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.ops_portal.ci_rca_lifecycle.confirmed_repair_track", return_value="test_defect"),
        ):
            stamp_fixed_by_sha("rec-1", "abc1234")
        written = json.loads(mock_update.call_args[0][1]["context_v2_json"])
        assert written["fixed_by_sha"] == "abc1234"
        assert written["repair_track_confirmed"] == "test_defect"

    def test_stamp_fixed_by_sha_leaves_track_unset_when_unresolvable(self) -> None:
        from unittest.mock import patch

        from scripts.ops_portal.ci_rca_lifecycle import stamp_fixed_by_sha

        existing = {"id": "rec-1", "context_v2_json": json.dumps({"fingerprint": "a" * 64})}
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.ops_portal.ci_rca_lifecycle.confirmed_repair_track", return_value=None),
        ):
            stamp_fixed_by_sha("rec-1", "abc1234")
        assert "repair_track_confirmed" not in json.loads(mock_update.call_args[0][1]["context_v2_json"])
