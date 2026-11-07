"""Emergent-north-star detector: rules, bounds, ordering, determinism, loaders, error path and preflight glue."""

from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import pathlib
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.preflight import emergent_north_star as m
from scripts.preflight.emergent_north_star import Entry, detect

TODAY = date(2026, 7, 1)
N = 100


def entry(n: int, text: str | None = None, **kw) -> Entry:
    base = {
        "number": n,
        "text": text if text is not None else f"filler{n}",
        "amends": (),
        "supersedes": (),
        "related": (),
        "kind": None,
        "serves": None,
        "decided": None,
    }
    base.update(kw)
    return Entry(**base)


def chain(start: int, size: int, tokens: str, **kw) -> dict[int, Entry]:
    return {n: entry(n, tokens, amends=(n - 1,) if n > start else (), **kw) for n in range(start, start + size)}


def world(groups: dict[int, Entry], total: int = N) -> list[Entry]:
    out = dict(groups)
    for n in range(1, total + 1):
        out.setdefault(n, entry(n))
    return [out[n] for n in sorted(out)]


def tokens(tag: str, count: int = 3) -> str:
    return " ".join(f"tk{tag}{chr(97 + i)}" for i in range(count))


def test_constants_are_pinned() -> None:
    assert (m.DETECTOR_VERSION, m.DF_MIN, m.DF_MAX_SHARE) == (1, 2, 0.15)
    assert (m.CUT_PERCENTILE, m.CUT_FLOOR, m.CUT_CEIL, m.MIN_CLUSTER) == (0.95, 0.03, 0.08, 4)
    assert (m.HUB_PERCENTILE, m.MIN_CITERS, m.MIN_LATER) == (0.90, 3, 10)
    assert (m.MAX_CLUSTERS, m.MAX_TERMS, m.MAX_RISING, m.MAX_HUBS) == (5, 5, 5, 10)
    assert len(m.STOPWORDS) == 128
    digest = hashlib.sha256("\n".join(sorted(m.STOPWORDS)).encode("utf-8")).hexdigest()
    assert digest == "473388601e0a4b7bbe84a35f43e6c1333918b217a416a68dbc0f08b75dd46a21"  # pragma: allowlist secret


def test_four_member_chain_clusters_and_three_does_not() -> None:
    r = detect(world(chain(1, 4, tokens("a"))), {}, TODAY)
    assert r["cluster_count"] == 1
    c = r["clusters"][0]
    assert c["members"] == [1, 2, 3, 4]
    assert c["mean_similarity"] == 1.0
    assert r["as_of"] == "2026-07-01"
    assert detect(world(chain(1, 3, tokens("a"))), {}, TODAY)["cluster_count"] == 0


def test_edge_below_the_cut_is_dropped() -> None:
    groups = chain(1, 3, tokens("a"))
    groups[4] = entry(4, tokens("b"), amends=(3,))
    r = detect(world(groups), {}, TODAY)
    assert r["cluster_count"] == 0
    assert r["corpus"]["amends_edges"] == 3
    assert r["corpus"]["kept_edges"] == 2


def test_self_and_unknown_amends_are_ignored() -> None:
    r = detect([entry(1, amends=(1, 99)), entry(2)], {}, TODAY)
    assert r["corpus"]["amends_edges"] == 0
    assert r["corpus"]["live"] == 2


def test_cut_clamps_to_floor_and_ceiling() -> None:
    assert detect(world({}), {}, TODAY)["corpus"]["edge_cut"] == 0.03
    groups: dict[int, Entry] = {}
    for g in range(3):
        for k in range(15):
            n = g * 15 + k + 1
            groups[n] = entry(n, tokens(f"g{g}"))
    assert detect(world(groups), {}, TODAY)["corpus"]["edge_cut"] == 0.08


def test_empty_corpus_has_no_percentile() -> None:
    r = detect([], {}, TODAY)
    assert r["corpus"]["live"] == 0
    assert r["corpus"]["edge_cut"] == 0.03
    assert r["clusters"] == []


def test_coverage_uncovered_carried_and_serves_only() -> None:
    groups = {**chain(1, 4, tokens("a")), **chain(11, 4, tokens("b")), **chain(21, 4, tokens("c"), serves=("NS.1",))}
    r = detect(world(groups), {"mission": frozenset({12, 90})}, TODAY)
    by_first = {c["members"][0]: c for c in r["clusters"]}
    assert by_first[1]["uncovered"] is True
    assert by_first[11]["carried"] == {"mission": 1}
    assert by_first[11]["uncovered"] is False
    assert by_first[21]["served"] == {"NS.1": 4}
    assert by_first[21]["carried"] == {}
    assert by_first[21]["uncovered"] is False


def test_all_mechanism_cluster_is_kept_and_kinds_counted() -> None:
    r = detect(world(chain(1, 4, tokens("a"), kind="mechanism")), {}, TODAY)
    assert r["clusters"][0]["kinds"] == {"mechanism": 4}
    r = detect(world(chain(1, 4, tokens("a"))), {}, TODAY)
    assert r["clusters"][0]["kinds"] == {"undeclared": 4}


def test_cluster_order_and_cap_with_total_count() -> None:
    groups = {**chain(1, 5, tokens("a")), **chain(11, 4, tokens("b")), **chain(21, 4, tokens("c"))}
    groups[22] = entry(22, tokens("c", 2) + " tkcz", amends=(21,))
    groups[23] = entry(23, tokens("c", 2) + " tkcz", amends=(22,))
    groups[24] = entry(24, tokens("c", 2), amends=(23,))
    groups.update(chain(31, 4, tokens("d"), serves=("NS.2",)))
    r = detect(world(groups), {}, TODAY)
    firsts = [c["members"][0] for c in r["clusters"]]
    assert firsts == [1, 11, 21, 31]
    assert r["clusters"][2]["mean_similarity"] < r["clusters"][1]["mean_similarity"]
    for i, base in enumerate((41, 51, 61)):
        groups.update(chain(base, 4, tokens(f"e{i}")))
    r = detect(world(groups), {}, TODAY)
    assert len(r["clusters"]) == m.MAX_CLUSTERS
    assert r["cluster_count"] == 7
    assert [c["members"][0] for c in r["clusters"]] == [1, 11, 41, 51, 61]


def test_term_order_and_cap() -> None:
    r = detect(world(chain(1, 4, tokens("t", 7))), {}, TODAY)
    assert r["clusters"][0]["terms"] == [f"tkt{chr(97 + i)}" for i in range(5)]


def test_terms_need_half_of_the_members() -> None:
    groups = chain(1, 4, "tkshared")
    groups[1] = entry(1, "tkshared tkonly")
    r = detect(world(groups), {}, TODAY)
    assert r["clusters"][0]["terms"] == ["tkshared"]


def cited(cites: dict[int, list[int]], overrides: dict[int, dict] | None = None) -> list[Entry]:
    related: dict[int, list[int]] = {}
    for target, citers in cites.items():
        for c in citers:
            related.setdefault(c, []).append(target)
    entries = {n: entry(n, related=tuple(related.get(n, ()))) for n in range(1, 31)}
    for n, kw in (overrides or {}).items():
        entries[n] = entry(n, related=tuple(related.get(n, ())), **kw)
    return [entries[n] for n in sorted(entries)]


CITES = {
    1: [10, 11, 12],
    2: [10, 11, 12],
    3: [10, 11, 12],
    4: [10, 11, 12],
    5: list(range(10, 17)),
    25: [26, 27, 28],
    6: [10, 11],
}


def test_rising_excludes_hub_carried_mechanism_few_citers_and_few_later() -> None:
    r = detect(cited(CITES, {3: {"kind": "mechanism"}}), {"x": frozenset({2})}, TODAY)
    assert [x["decision"] for x in r["rising"]] == [4, 1]
    assert r["hubs"] == [5]
    assert r["hub_count"] == 1
    assert r["corpus"]["hub_cut"] == 3
    assert r["corpus"]["carried"] == 1


def test_rising_share_order_months_and_ties() -> None:
    entries = cited(CITES, {1: {"decided": date(2026, 1, 1)}, 4: {"decided": date(2026, 6, 30)}})
    r = detect(entries, {}, TODAY)
    rows = {x["decision"]: x for x in r["rising"]}
    assert [x["decision"] for x in r["rising"]][:4] == [4, 3, 2, 1]
    assert rows[1]["share"] == round(3 / 29, 3)
    assert rows[1]["months"] == round((TODAY - date(2026, 1, 1)).days / 30.44, 1)
    assert rows[1]["per_month"] == round(3 / rows[1]["months"], 2)
    assert rows[4]["months"] == 1.0
    assert rows[2]["months"] is None
    assert rows[2]["per_month"] is None


def test_rising_is_capped() -> None:
    cites = {n: [20, 21, 22] for n in range(1, 8)}
    cites[8] = list(range(9, 20))
    cites[9] = list(range(10, 19))
    r = detect(cited(cites), {}, TODAY)
    assert len(r["rising"]) == m.MAX_RISING


def test_hubs_are_capped() -> None:
    cites = {n: list(range(n + 1, n + 8)) for n in range(1, 13)}
    r = detect(cited(cites), {}, TODAY)
    assert len(r["hubs"]) <= m.MAX_HUBS
    assert r["hub_count"] >= len(r["hubs"])


def test_serves_counters_distinguish_key_from_value() -> None:
    entries = [entry(1, serves=("NS.1",)), entry(2, serves=()), entry(3), entry(4)]
    corpus = detect(entries, {}, TODAY)["corpus"]
    assert corpus["serves_declared"] == 1
    assert corpus["serves_eligible"] == 2


def test_two_calls_are_byte_identical() -> None:
    entries = world({**chain(1, 5, tokens("a")), **chain(11, 4, tokens("b"))})
    a = json.dumps(detect(entries, {"k": frozenset({3})}, TODAY), sort_keys=True)
    b = json.dumps(detect(list(reversed(entries)), {"k": frozenset({3})}, TODAY), sort_keys=True)
    assert a == b


INDEX_ROWS = [
    {"number": 1, "live": True, "decided_date": "2026-01-15", "amends": [], "supersedes": [], "kind": {"value": "direction"}},
    {
        "number": 2,
        "live": True,
        "decided_date": "2026-02",
        "amends": [1],
        "supersedes": [],
        "serves": ["NS.1"],
        "triage_excerpt": "excerpt text only",
    },
    {"number": 3, "live": True, "decided_date": "", "amends": [], "supersedes": [2]},
    {"number": 4, "live": False, "decided_date": "2026-03-01", "amends": [], "supersedes": []},
    {"number": 5, "live": True, "decided_date": "2026-03-01", "amends": [], "supersedes": [], "serves": []},
]


def decision_md(n: int, title: str, body: str) -> str:
    return f"## Decision {n}: {title} (Decided)\n\n**Status:** Decided\n**Date:** 2026-01-01\n\n{body}\n\n"


def fixtures(tmp_path: Path) -> tuple[Path, list[Path]]:
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"decisions": INDEX_ROWS}), encoding="utf-8")
    md = tmp_path / "DECISIONS.md"
    md.write_text(
        decision_md(
            1, "First title", "**Problem:** the problem words\n\n**Intent:** the intent words\n\n**Related:** Decision 2"
        )
        + decision_md(2, "Second title", "**Decision:** nothing else")
        + decision_md(3, "Third title", "**Problem:** p3\n\n**Related:** Decisions 1/2")
        + decision_md(5, "Fifth title", "**Intent:** i5"),
        encoding="utf-8",
    )
    archive = tmp_path / "ARCHIVE.md"
    archive.write_text(decision_md(4, "Archived title", "**Intent:** archived words"), encoding="utf-8")
    return index, [md, archive]


def test_load_entries_text_related_dates_and_fallbacks(tmp_path: Path) -> None:
    index, paths = fixtures(tmp_path)
    by = {e.number: e for e in m.load_entries(index, paths)}
    assert sorted(by) == [1, 2, 3, 5]
    assert by[1].text == "First title the intent words the problem words" or "the problem words" in by[1].text
    assert "the intent words" in by[1].text
    assert by[1].related == (2,)
    assert by[1].kind == "direction"
    assert by[1].serves is None
    assert by[2].text == "Second title excerpt text only"
    assert by[2].serves == ("NS.1",)
    assert by[2].amends == (1,)
    assert by[2].decided == date(2026, 2, 1)
    assert by[3].decided == date(2026, 2, 1)
    assert by[3].supersedes == (2,)
    assert by[3].related == (1, 2)
    assert by[5].serves == ()
    assert by[5].decided == date(2026, 3, 1)
    assert "archived words" not in " ".join(e.text for e in by.values())


def test_load_entries_undated_prefix_is_null(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"decisions": [{"number": 1, "live": True, "decided_date": None}]}), encoding="utf-8")
    md = tmp_path / "D.md"
    md.write_text(decision_md(1, "Only", "**Intent:** words"), encoding="utf-8")
    (entry_,) = m.load_entries(index, [md])
    assert entry_.decided is None
    assert entry_.text == "Only words"


def test_preflight_bucket_happy_path(tmp_path: Path) -> None:
    index, paths = fixtures(tmp_path)
    ns = tmp_path / "ns.yaml"
    ns.write_text("mission: {}\n", encoding="utf-8")
    with (
        patch.object(m, "load_north_star", return_value=object()),
        patch.object(m, "north_star_carriers", return_value={"mission": [1]}),
    ):
        bucket = m.preflight_bucket(index, paths, ns, TODAY)
    assert bucket["as_of"] == "2026-07-01"
    assert bucket["corpus"]["carried"] == 1


def test_preflight_bucket_defaults_today_to_utc(tmp_path: Path) -> None:
    index, paths = fixtures(tmp_path)
    with patch.object(m, "load_north_star", return_value=object()), patch.object(m, "north_star_carriers", return_value={}):
        bucket = m.preflight_bucket(index, paths)
    assert "error" not in bucket
    assert bucket["as_of"] == date.fromisoformat(bucket["as_of"]).isoformat()


def test_preflight_bucket_errors_instead_of_raising(tmp_path: Path) -> None:
    index, paths = fixtures(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid", encoding="utf-8")
    assert m.preflight_bucket(index, paths, bad, TODAY)["error"]
    missing = m.preflight_bucket(tmp_path / "missing.json", paths, None, TODAY)
    assert missing["detector_version"] == 1
    assert missing["error"].startswith("FileNotFoundError")


def test_main_writes_the_bucket_into_the_report(tmp_path: Path) -> None:
    pytest.importorskip("boto3", reason="env: boto3 is not installed")
    from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: PLC0415

    report_path = tmp_path / ".preflight-report.json"
    canned = {"detector_version": 1, "canned": True}
    with (
        patch("scripts.preflight.env_git.check_venv", return_value=True),
        patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
        patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
        patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
        patch(
            "scripts.preflight.session_history.read_session_history",
            return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
        ),
        patch("session_preflight._sync_ops_pull", return_value={}),
        patch(
            "scripts.preflight.context_docs.read_context_files",
            return_value={
                "roadmap_phase": "Phase 2",
                "open_decisions_count": 0,
                "strategic_review_due": False,
                "recommendations_count": 0,
            },
        ),
        patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
        patch("scripts.preflight.emergent_north_star.preflight_bucket", return_value=canned),
        patch("session_preflight.PREFLIGHT_REPORT", report_path),
        patch("builtins.print"),
    ):
        _preflight.main()

    assert json.loads(report_path.read_text(encoding="utf-8"))["emergent_north_star"] == canned


def test_reload_performs_no_import_time_io() -> None:
    def boom(*_a, **_k):
        raise AssertionError("import-time I/O")

    with (
        patch.object(builtins, "open", boom),
        patch.object(pathlib.Path, "read_text", boom),
        patch.object(subprocess, "run", boom),
    ):
        importlib.reload(m)
