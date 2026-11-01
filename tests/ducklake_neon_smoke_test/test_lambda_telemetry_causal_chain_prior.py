"""Tests for scripts/ducklake_smoke/lambda_telemetry_causal_chain_prior.py: selection, rebuilt expectations, loss rules."""

from __future__ import annotations

import ast
import functools
import json
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.ducklake_smoke import lambda_telemetry_causal_chain_prior as prior
from src.telemetry.identity import KEY_PLANS, derive_entity_key, derive_event_id

TENANT, PROJECT = "01BX5ZZKBKACTAV9WEVGEMM000", "01BX5ZZKBKACTAV9WEVGEMM001"
T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
SHA40 = "a" * 40
ROOT = Path(__file__).resolve().parents[2]
GOOD = f"PROBE_SHAPE_VERSION = {prior.PROBE_SHAPE_VERSION}\n"


def identity(run_id: str = "5", attempt: int = 1, hours: float = 6) -> prior.RunIdentity:
    return prior.RunIdentity(run_id, attempt, T0 - timedelta(hours=hours))


class Store:
    """The stored rows of one probe session as session_tree_rows pages (the sent rows plus identity and key columns)."""

    def __init__(self, who: prior.RunIdentity) -> None:
        self.who = who
        self.pages: list[dict[str, Any]] = []
        for table, rows in prior.expected_batches(who)[1]:
            for row in rows:
                stored = {**row, "tenant_id": TENANT, "project_id": PROJECT}
                stored["event_id"] = derive_event_id(table, TENANT, PROJECT, row["external_ref"], row["event_timestamp"])
                for column, plan in KEY_PLANS[table].items():
                    ref = row.get(plan.ref_field)
                    stored[column] = (
                        None
                        if ref is None
                        else derive_entity_key(plan.domain_tag, TENANT, PROJECT, ref, row["session_started_at"])
                    )
                self.pages.append({"table_name": table, "row_json": json.dumps(stored, default=lambda v: v.isoformat())})
        self.session_id = prior.session_id_for(TENANT, PROJECT, who)

    def change(self, table: str, **changes: Any) -> None:
        target = next(p for p in self.pages if p["table_name"] == table)
        target["row_json"] = json.dumps({**json.loads(target["row_json"]), **changes})

    def clone(self, table: str, **changes: Any) -> None:
        source = next(p for p in self.pages if p["table_name"] == table)
        self.pages.append({**source, "row_json": json.dumps({**json.loads(source["row_json"]), **changes})})

    def named(self, verb: str, session_id: str, offset: int, max_rows: int) -> list[dict[str, Any]]:
        assert verb == "session_tree_rows"
        if session_id != self.session_id:
            return []
        page = self.pages[offset : offset + max_rows]
        return [{**p, "total_rows": len(self.pages), "roots_in_tree": 1} for p in page]

    def check(self, **kwargs: Any) -> prior.PriorResult:
        return prior.post_persist(
            [prior.PriorRun(self.who, SHA40)], named=self.named, tenant_id=TENANT, project_id=PROJECT, **kwargs
        )


def _const(value: Any, _arg: str) -> Any:
    return value


def good(_sha: str) -> str:
    return f"PROBE_SHAPE_VERSION = {prior.PROBE_SHAPE_VERSION}\n"


def test_selection_takes_the_four_newest_and_up_to_four_aged_runs_excluding_the_current() -> None:
    runs = [prior.PriorRun(identity(str(n), 1, hours=n * 6), SHA40) for n in range(1, 40)]
    chosen = prior.select_prior(runs, "1", T0)
    ids = [int(r.identity.run_id) for r in chosen]
    assert ids[:4] == [2, 3, 4, 5] and 1 not in ids
    aged = chosen[4:]
    assert 1 <= len(aged) <= 4
    assert all(timedelta(days=7) <= T0 - r.identity.started <= timedelta(days=8) for r in aged)


def test_the_session_id_is_recomputed_from_the_identity() -> None:
    who = identity()
    result, batches = prior.expected_batches(who)
    assert {r["entity_ref"] for r in result.sessions} == {who.entity_ref}
    want = derive_entity_key(prior.SESSION_DOMAIN, TENANT, PROJECT, who.entity_ref, result.sessions[0]["session_started_at"])
    assert prior.session_id_for(TENANT, PROJECT, who) == want
    assert prior.expected_batches(who)[1] == batches
    assert prior.session_id_for(TENANT, PROJECT, identity("5", 2)) != want


def test_shape_digest_pin_and_append_only_history() -> None:
    assert prior.shape_digest(prior.RunIdentity(*prior.PIN_IDENTITY_ARGS)) == prior.SHAPE_DIGESTS[prior.PROBE_SHAPE_VERSION]
    assert prior.PROBE_SHAPE_VERSION in prior.SHAPE_DIGESTS
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: E731
    base = git("merge-base", "HEAD", "origin/main").stdout.strip()
    shown = git("show", f"{base}:{prior.MODULE_PATH}") if base else None
    if shown is None or shown.returncode != 0:
        return
    old = next(
        ast.literal_eval(n.value)
        for n in ast.parse(shown.stdout).body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "SHAPE_DIGESTS" for t in n.targets)
    )
    assert all(prior.SHAPE_DIGESTS.get(version) == digest for version, digest in old.items())


def test_a_clean_prior_session_is_checked_and_a_missing_one_is_lost() -> None:
    store = Store(identity())
    clean = store.check(git_show=good)
    assert (clean.mode, clean.checked, clean.lost, clean.skipped) == ("checked", 1, 0, 0)
    store.pages = []
    gone = store.check(git_show=good)
    assert (gone.checked, gone.lost) == (1, 1) and gone.lost_classes["missing_in_store"] >= 1


@pytest.mark.parametrize(
    ("cls", "change"),
    [
        ("unexpected_in_store", lambda s: s.clone("telemetry_observations", external_ref="zzz")),
        ("column_mismatch", lambda s: s.change("telemetry_transcripts", content_bytes=1)),
        ("key_mismatch", lambda s: s.change("telemetry_sessions", session_id="0" * 26)),
        ("duplicate_ref_rows", lambda s: s.clone("telemetry_observations", event_id="0" * 26)),
        ("race_duplicate_rows", lambda s: s.clone("telemetry_observations")),
    ],
)
def test_each_compare_class_is_a_loss(cls: str, change: Any) -> None:
    store = Store(identity())
    change(store)
    result = store.check(git_show=good)
    assert result.lost == 1 and result.lost_classes[cls] >= 1


def test_other_generations_shapes_and_unreadable_commits_are_skipped_never_lost() -> None:
    store = Store(identity())
    for page in list(store.pages):
        table = page["table_name"]
        store.change(table, parser_version=99)
    other = store.check(git_show=good)
    assert (other.checked, other.lost, other.skipped) == (0, 0, 1)
    fresh = Store(identity())
    for sha, source in ((SHA40, "PROBE_SHAPE_VERSION = 99\n"), ("not-a-sha", good(SHA40)), (SHA40, None)):
        done = prior.post_persist(
            [prior.PriorRun(fresh.who, sha)],
            named=fresh.named,
            tenant_id=TENANT,
            project_id=PROJECT,
            git_show=functools.partial(_const, source),
        )
        assert (done.checked, done.lost, done.skipped) == (0, 0, 1)


def test_shape_version_is_read_as_a_literal_never_executed() -> None:
    assert prior.shape_version_at(SHA40, lambda _s: "raise SystemExit(1)\nPROBE_SHAPE_VERSION = 3\n") == 3
    assert prior.shape_version_at(SHA40, lambda s: "PROBE_SHAPE_VERSION = int('3')\n") is None
    assert prior.shape_version_at(SHA40, lambda s: "def (:") is None
    assert prior.shape_version_at("g" * 40, lambda s: GOOD) is None and prior.shape_version_at("", lambda s: GOOD) is None


def test_actions_failures_raise_actions_unavailable() -> None:
    def broken(_url: str) -> Any:
        raise OSError("down")

    for call in (lambda: prior.this_run(broken, "1"), lambda: prior.successful_runs(broken, T0)):
        with pytest.raises(prior.ActionsUnavailable):
            call()
    for empty in (None, {}, {"workflow_runs": "x"}):
        with pytest.raises(prior.ActionsUnavailable):
            prior.successful_runs(functools.partial(_const, empty), T0)
    with pytest.raises(prior.ActionsUnavailable):
        prior.this_run(lambda _u: {"id": 1}, "1")


def test_a_reader_that_does_not_answer_is_unavailable_never_lost() -> None:
    def silent(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("504")

    runs = [prior.PriorRun(identity(), SHA40)] * 3
    done = prior.post_persist(runs, named=silent, tenant_id=TENANT, project_id=PROJECT, git_show=good)
    assert (done.mode, done.lost, done.checked, done.skipped) == ("unavailable", 0, 0, 3)


def test_the_wall_clock_budget_counts_the_rest_skipped() -> None:
    store = Store(identity())
    ticks: Iterator[float] = iter([0.0, 0.0, prior.POST_PERSIST_BUDGET_S + 1, 10**6])
    runs = [prior.PriorRun(store.who, SHA40)] * 3
    done = prior.post_persist(
        runs, named=store.named, tenant_id=TENANT, project_id=PROJECT, git_show=good, clock=lambda: next(ticks)
    )
    assert (done.checked, done.skipped) == (1, 2)


def test_the_transcript_is_a_pure_function_of_shape_and_identity() -> None:
    a, b = identity("5", 1), identity("5", 2)
    assert prior.build_files(a) == prior.build_files(identity("5", 1))
    assert prior.build_files(a) != prior.build_files(b) and a.entity_ref != b.entity_ref
    assert a.entity_ref == f"causal-chain-probe:v{prior.PROBE_SHAPE_VERSION}:5.1"
    first = min(r["event_timestamp"] for _, rows in prior.expected_batches(a)[1] for r in rows)
    assert datetime.fromisoformat(str(first).replace("Z", "+00:00")) == a.started.astimezone(timezone.utc)


def test_a_loss_found_before_an_unavailable_read_stays_counted() -> None:
    gone, ok = Store(identity("5")), Store(identity("6"))
    gone.pages = []

    def named(verb: str, session_id: str, offset: int, max_rows: int) -> list[dict[str, Any]]:
        if session_id == ok.session_id:
            raise RuntimeError("504")
        return gone.named(verb, session_id, offset, max_rows)

    runs = [prior.PriorRun(gone.who, SHA40), prior.PriorRun(ok.who, SHA40)]
    done = prior.post_persist(runs, named=named, tenant_id=TENANT, project_id=PROJECT, git_show=good)
    assert (done.mode, done.lost, done.checked, done.skipped) == ("unavailable", 1, 1, 1)
