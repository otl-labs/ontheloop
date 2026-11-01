"""Contract-text guards for PLAN-telemetry-production-flip (T2.36 c2, Decision 230, Decision 236).

The writer contract states the production binding (catalog ducklake_telemetry on the ducklake_telemetry_writer_prod login),
the blob-root write fence in place of the CI-write residual, both operator audit lines and the production invocation
measure; and no live text in .github/workflows/, .github/actions/, scripts/ or docs/contracts/*.yaml names the retired
heavy --lambda-telemetry flag (each contract's amendment_log and previous_versions blocks are history, so they are
skipped). After the cleanup PR the shared reader's contract routes the event verbs to the telemetry reader and keeps no
deadline row, the telemetry reader's contract carries the moved deadline and threads invariants, read-engine and PC-01 name
the telemetry reader, PC-06 names no content server, and no live code keeps the smoke-pin read surface. After the narrow
PR no live ducklake_reader.yaml or read-engine.yaml text says the shared reader reads telemetry-blobs/. After the bookkeeping
PR no live contract (history skipped), src/, scripts/, terraform/ or .github/ text names the closed rec-4292,
and after the content reader build none names the closed rec-4481. Each predicate has red cases on synthetic input.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT = _ROOT / "docs" / "contracts" / "ducklake_telemetry_writer.yaml"
_LOGIN = "ducklake_telemetry_writer_prod"
_CATALOG = re.compile(r"\bducklake_telemetry\b")
_FENCE = "only the telemetry writer roles put to the blob root"
_OPERATOR_LINES = ("Operator writes:", "Integrity repair:")
_BARE_FLAG = re.compile(r"--lambda-telemetry(?![-\w])")
_HISTORY_KEY = re.compile(r"^(\s*)(amendment_log|previous_versions):")
_CONTRACTS = _ROOT / "docs" / "contracts"
_TELEMETRY_READER = "agent-platform-ducklake-telemetry-reader"
_RETIRED_READ_SURFACE = ("require_smoke_pin", "_SMOKE_ONLY", "until slice 2c")
_SHARED_READER_DEADLINE = re.compile(r"deadline_ms|\b504\b|read_deadline_exceeded|TELEMETRY_META_SCHEMA")
_SMOKE_DEFERRAL = re.compile(r"smoke catalog only|until slice 2c")
_SHARED_READER_BLOB = re.compile(r"TelemetryBlobRead|telemetry-blobs/")
_REC_4292 = re.compile(r"\brec-4292\b")
_REC_4481 = re.compile(r"\brec-4481\b(?! plan [123]\b)")


def writer_contract_problems(doc: dict) -> list[str]:
    contract = doc.get("contract") or {}
    fields = {
        "description": contract.get("description") or "",
        "write_path": (contract.get("governance") or {}).get("write_path") or "",
    }
    problems = [
        f"{name} must name ducklake_telemetry and {_LOGIN}"
        for name, text in fields.items()
        if not (_CATALOG.search(text) and _LOGIN in text)
    ]
    invariants = [i for i in doc.get("audit_invariants") or [] if isinstance(i, str)]
    claim = [i for i in invariants if i.startswith("Claim-check rule")]
    if len(claim) != 1:
        problems.append(f"expected one claim-check invariant, found {len(claim)}")
    elif _FENCE not in claim[0] or "rec-4292" in claim[0] or "named residual" in claim[0]:
        problems.append("the claim-check invariant must state the Decision 230 cl.5 fence and name no residual owner")
    problems += [f"no audit invariant starts {p!r}" for p in _OPERATOR_LINES if not any(i.startswith(p) for i in invariants)]
    note = (contract.get("provisional_v0") or {}).get("surfacing_note") or ""
    if "AWS/Lambda Invocations" not in note:
        problems.append("the surfacing note must measure production_invocations as AWS/Lambda Invocations")
    return problems


def live_lines(text: str, *, skip_history: bool) -> Iterable[tuple[int, str]]:
    history_indent: int | None = None
    for number, line in enumerate(text.splitlines(), 1):
        indent = len(line) - len(line.lstrip())
        if history_indent is not None:
            if not line.strip() or indent > history_indent or (indent == history_indent and line.lstrip().startswith("- ")):
                continue
            history_indent = None
        match = _HISTORY_KEY.match(line) if skip_history else None
        if match:
            history_indent = len(match.group(1))
            continue
        yield number, line


def bare_flag_hits(files: dict[str, tuple[str, bool]]) -> list[str]:
    return [
        f"{name}:{number}"
        for name, (text, skip_history) in sorted(files.items())
        for number, line in live_lines(text, skip_history=skip_history)
        if _BARE_FLAG.search(line)
    ]


def _live_files() -> dict[str, tuple[str, bool]]:
    files: dict[str, tuple[str, bool]] = {}
    for tree in (_ROOT / ".github" / "workflows", _ROOT / ".github" / "actions", _ROOT / "scripts"):
        for path in sorted(tree.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[str(path.relative_to(_ROOT))] = (path.read_text(encoding="utf-8", errors="replace"), False)
    for path in sorted((_ROOT / "docs" / "contracts").glob("*.yaml")):
        files[str(path.relative_to(_ROOT))] = (path.read_text(encoding="utf-8"), True)
    return files


def test_writer_contract_states_the_production_binding_fence_and_operator_lines() -> None:
    doc = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    assert writer_contract_problems(doc) == []
    claim_at = next(i for i, s in enumerate(doc["audit_invariants"]) if s.startswith("Claim-check rule"))
    mutations = [
        lambda d: d["contract"].update(
            description=d["contract"]["description"].replace("ducklake_telemetry ", "ducklake_smoke ")
        ),
        lambda d: d["contract"].update(description=d["contract"]["description"].replace(_LOGIN, "ducklake_telemetry_writer")),
        lambda d: d["contract"]["governance"].update(
            write_path=d["contract"]["governance"]["write_path"].replace(_LOGIN, "x")
        ),
        lambda d: d["audit_invariants"].__setitem__(claim_at, d["audit_invariants"][claim_at].replace(_FENCE, "x")),
        lambda d: d["audit_invariants"].__setitem__(
            claim_at, d["audit_invariants"][claim_at] + " A named residual owned by rec-4292."
        ),
        lambda d: d.update(audit_invariants=[i for i in d["audit_invariants"] if not i.startswith("Operator writes:")]),
        lambda d: d.update(audit_invariants=[i for i in d["audit_invariants"] if not i.startswith("Integrity repair:")]),
        lambda d: d["contract"]["provisional_v0"].update(surfacing_note="RequestCount by Verb"),
    ]
    for mutate in mutations:
        bad = copy.deepcopy(doc)
        mutate(bad)
        assert writer_contract_problems(bad), mutate


def test_no_live_text_names_the_retired_heavy_flag() -> None:
    assert bare_flag_hits(_live_files()) == []
    history = "contract:\n  id: x\namendment_log:\n  - date: d\n    summary: >-\n      ran --lambda-telemetry\n"
    flat_history = "previous_versions:\n- summary: --lambda-telemetry\n  note: x\n"
    green = {
        "a.yaml": (history, True),
        "b.yaml": (flat_history, True),
        "c.yml": ("run: x --lambda-telemetry-production\nrun: x --lambda-telemetry-smoke --profile p", False),
    }
    assert bare_flag_hits(green) == []
    red = {
        "after_history.yaml": (history + "notes: run --lambda-telemetry\n", True),
        "workflow.yml": ("run: x --lambda-telemetry --profile p", False),
        "eol.py": ('"""Run --lambda-telemetry"""', False),
        "history_off.yml": (history, False),
    }
    assert bare_flag_hits({**green, **red}) == ["after_history.yaml:7", "eol.py:1", "history_off.yml:6", "workflow.yml:1"]


def retired_surface_hits(files: dict[str, str]) -> list[str]:
    return [
        f"{name}:{number}:{needle}"
        for name, text in sorted(files.items())
        for number, line in enumerate(text.splitlines(), 1)
        for needle in _RETIRED_READ_SURFACE
        if needle in line
    ]


def _code_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for tree in ("src", "scripts", "terraform", ".github"):
        for path in sorted((_ROOT / tree).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and ".terraform" not in path.parts:
                files[str(path.relative_to(_ROOT))] = path.read_text(encoding="utf-8", errors="replace")
    return files


def test_retired_smoke_read_surface_is_absent() -> None:
    assert retired_surface_hits(_code_files()) == []
    assert retired_surface_hits({"a.py": "def require_production_pin():\n    pass\n"}) == []
    red = {"a.py": "x = _SMOKE_ONLY\n", "b.tf": "# smoke reads until slice 2c\n", "c.py": "ev.require_smoke_pin()\n"}
    assert retired_surface_hits(red) == ["a.py:1:_SMOKE_ONLY", "b.tf:1:until slice 2c", "c.py:1:require_smoke_pin"]


def _invariant(doc: dict, prefix: str) -> str:
    found = [i for i in doc.get("audit_invariants") or [] if isinstance(i, str) and i.startswith(prefix)]
    return found[0] if len(found) == 1 else ""


def reader_contracts_problems(shared: dict, shared_text: str, telemetry: dict) -> list[str]:
    problems = []
    for prefix in ("Event-journal verbs", "Verification verbs"):
        if _TELEMETRY_READER not in _invariant(shared, prefix):
            problems.append(f"the shared reader's {prefix!r} invariant must name {_TELEMETRY_READER}")
    if "unregistered verb" not in _invariant(shared, "Event-journal verbs"):
        problems.append("the shared reader must answer an event verb as an unregistered verb")
    hits = [n for n, line in live_lines(shared_text, skip_history=True) if _SHARED_READER_DEADLINE.search(line)]
    if hits:
        problems.append(f"the shared reader's live text keeps a deadline or telemetry-pin row at lines {hits}")
    for prefix in ("Event-journal read deadline", "Telemetry slot threads"):
        if _invariant(shared, prefix):
            problems.append(f"the {prefix!r} invariant must move to the telemetry reader's contract")
    deadline = _invariant(telemetry, "Event read deadline")
    if not all(
        s in deadline for s in ("deadline_ms", "504 read_deadline_exceeded", "one catalog probe", "__ducklake_metadata_")
    ):
        problems.append("the telemetry reader must carry the moved deadline invariant with its one catalog probe")
    threads = _invariant(telemetry, "Threads and spill")
    if not all(s in threads for s in ("threads=1", "withdrawn", "/tmp/telemetry-spill")):
        problems.append("the telemetry reader must carry the moved threads-and-spill invariant at threads=1")
    return problems


def _load(name: str) -> tuple[dict, str]:
    text = (_CONTRACTS / name).read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def test_reader_contract_routes_production_reads_to_the_telemetry_reader() -> None:
    shared, shared_text = _load("ducklake_reader.yaml")
    telemetry, _ = _load("ducklake_telemetry_reader.yaml")
    assert reader_contracts_problems(shared, shared_text, telemetry) == []
    named = shared["verbs"]["named_read"]
    assert 504 not in named["response_codes"] and "read_deadline_exceeded" not in named["typed_errors"]
    at = {
        prefix: next(i for i, s in enumerate(d["audit_invariants"]) if s.startswith(prefix))
        for d, prefix in (
            (shared, "Event-journal verbs"),
            (telemetry, "Event read deadline"),
            (telemetry, "Threads and spill"),
        )
    }

    def swap(doc: dict, prefix: str, old: str, new: str) -> None:
        doc["audit_invariants"][at[prefix]] = doc["audit_invariants"][at[prefix]].replace(old, new)

    mutations = [
        lambda s, t: swap(s, "Event-journal verbs", _TELEMETRY_READER, "the shared reader"),
        lambda s, t: swap(s, "Event-journal verbs", "unregistered verb", "served verb"),
        lambda s, t: s["audit_invariants"].append("Telemetry slot threads and spill (rec-4474): threads=1."),
        lambda s, t: swap(t, "Event read deadline", "one catalog probe", "a probe"),
        lambda s, t: swap(t, "Threads and spill", "threads=1", "threads=8"),
        lambda s, t: t.update(audit_invariants=[i for i in t["audit_invariants"] if not i.startswith("Threads and spill")]),
    ]
    for mutate in mutations:
        bad_shared, bad_telemetry = copy.deepcopy(shared), copy.deepcopy(telemetry)
        mutate(bad_shared, bad_telemetry)
        assert reader_contracts_problems(bad_shared, shared_text, bad_telemetry), mutate
    for line in ("      504: x", "      event envelope {action: named_read, deadline_ms?}", "  - TELEMETRY_META_SCHEMA pin"):
        live = shared_text.replace("amendment_log:\n", line + "\namendment_log:\n", 1)
        assert reader_contracts_problems(shared, live, telemetry), line
    history = shared_text.replace("amendment_log:\n", "amendment_log:\n  - summary: dropped deadline_ms and 504\n", 1)
    assert reader_contracts_problems(shared, history, telemetry) == []


def read_engine_problems(doc: dict, text: str) -> list[str]:
    mechanism = str(((doc.get("read_surface") or {}).get("named_verbs") or {}).get("mechanism") or "")
    problems = [] if _TELEMETRY_READER in mechanism else [f"named_verbs.mechanism must name {_TELEMETRY_READER}"]
    hits = [n for n, line in live_lines(text, skip_history=True) if _SMOKE_DEFERRAL.search(line)]
    return problems + ([f"live smoke-only deferral at lines {hits}"] if hits else [])


def test_read_engine_names_the_telemetry_reader_for_event_verbs() -> None:
    doc, text = _load("read-engine.yaml")
    assert read_engine_problems(doc, text) == []
    assert "smoke catalog only until slice 2c" in text, "the 2026-10-17 amendment entry is history and stays verbatim"
    bad = copy.deepcopy(doc)
    bad["read_surface"]["named_verbs"]["mechanism"] = "served from a second warm connection of the shared reader"
    assert read_engine_problems(bad, text)
    assert read_engine_problems(doc, text + "notes: event verbs read the smoke catalog only\n")


def ports_problems(doc: dict) -> list[str]:
    rows = {f.get("id"): f for f in doc.get("coupling_findings") or []}
    pc01 = " ".join(str((rows.get("PC-01") or {}).get("description") or "").split())
    pc06 = rows.get("PC-06") or {}
    pc06_text = " ".join(str(pc06.get("description") or "").split())
    problems = []
    if "to the telemetry reader's own Function URL only" not in pc01 or "to the shared reader on the smoke catalog" in pc01:
        problems.append("PC-01 must route event reads to the telemetry reader only")
    if "served by no deployed function" not in pc06_text or "rec-4696" not in pc06_text:
        problems.append("PC-06 must say no deployed function serves transcript content, owner rec-4696")
    if pc06.get("status") != "open" or pc06.get("owning_item") != "T4.23:c7":
        problems.append("PC-06 stays open under T4.23:c7")
    return problems


def test_pc01_and_pc06_name_the_telemetry_reader_and_no_content_server() -> None:
    doc, _ = _load("ports.yaml")
    assert ports_problems(doc) == []
    at = {f["id"]: i for i, f in enumerate(doc["coupling_findings"]) if f.get("id") in ("PC-01", "PC-06")}
    mutations = [
        lambda d: d["coupling_findings"][at["PC-01"]].update(description="routes to the shared reader on the smoke catalog"),
        lambda d: d["coupling_findings"][at["PC-06"]].update(description="The reader's transcript_content verb serves it."),
        lambda d: d["coupling_findings"][at["PC-06"]].update(status="accepted"),
    ]
    for mutate in mutations:
        bad = copy.deepcopy(doc)
        mutate(bad)
        assert ports_problems(bad), mutate


def shared_reader_blob_grant_hits(files: dict[str, str]) -> list[str]:
    return [
        f"{name}:{number}"
        for name, text in sorted(files.items())
        for number, line in live_lines(text, skip_history=True)
        if _SHARED_READER_BLOB.search(line)
    ]


def test_no_contract_names_the_shared_reader_blob_grant() -> None:
    files = {name: _load(name)[1] for name in ("ducklake_reader.yaml", "read-engine.yaml")}
    assert shared_reader_blob_grant_hits(files) == []
    history = (
        "contract:\n  id: x\namendment_log:\n  - summary: >-\n      the TelemetryBlobRead grant on telemetry-blobs/ goes\n"
    )
    assert shared_reader_blob_grant_hits({"a.yaml": history}) == []
    restored = files["ducklake_reader.yaml"].replace(
        "amendment_log:\n",
        '  - "The reader role\'s TelemetryBlobRead grant is reached only through transcript_content."\namendment_log:\n',
        1,
    )
    red = {
        "after_history.yaml": history + "notes: the role reads telemetry-blobs/<meta_schema>/*\n",
        "rationale.yaml": "read_surface:\n  rationale: the S3-read-only role also reads telemetry-blobs/\n",
        "ducklake_reader.yaml": restored,
    }
    hits = shared_reader_blob_grant_hits({"a.yaml": history, **red})
    assert [h.split(":")[0] for h in hits] == ["after_history.yaml", "ducklake_reader.yaml", "rationale.yaml"], hits


def closed_rec_hits(pattern: re.Pattern[str], contracts: dict[str, str], code: dict[str, str]) -> list[str]:
    hits = [
        f"{name}:{number}"
        for name, text in sorted(contracts.items())
        for number, line in live_lines(text, skip_history=True)
        if pattern.search(line)
    ]
    return hits + [
        f"{name}:{number}"
        for name, text in sorted(code.items())
        for number, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]


def rec_4292_hits(contracts: dict[str, str], code: dict[str, str]) -> list[str]:
    return closed_rec_hits(_REC_4292, contracts, code)


def test_no_live_text_names_rec_4292() -> None:
    contracts = {str(p.relative_to(_ROOT)): p.read_text(encoding="utf-8") for p in sorted(_CONTRACTS.glob("*.yaml"))}
    assert rec_4292_hits(contracts, _code_files()) == []
    history = "contract:\n  id: x\namendment_log:\n  - summary: >-\n      the CI-write residual, owner rec-4292\n"
    green = {"a.yaml": history, "b.yaml": "notes: rec-42920 is another rec\n"}
    assert rec_4292_hits(green, {"c.py": "# rec-42921\n"}) == []
    red_contracts = {**green, "after_history.yaml": history + "production_column_set:\n  rec: rec-4292\n"}
    red_code = {"d.py": '"""Residual owned by rec-4292."""\n', "e.tf": "amendment_log:\n  - rec-4292\n"}
    assert rec_4292_hits(red_contracts, red_code) == ["after_history.yaml:7", "d.py:1", "e.tf:2"]


def test_no_live_text_names_rec_4481() -> None:
    contracts = {str(p.relative_to(_ROOT)): p.read_text(encoding="utf-8") for p in sorted(_CONTRACTS.glob("*.yaml"))}
    assert closed_rec_hits(_REC_4481, contracts, _code_files()) == []
    history = "contract:\n  id: x\namendment_log:\n  - summary: >-\n      transcript content, owner rec-4481\n"
    green = {"a.yaml": history, "b.yaml": "notes: rec-44810 and rec-4481 plan 1 are other text\n"}
    assert closed_rec_hits(_REC_4481, green, {"c.py": "# rec-44811 and rec-4481 plan 2\n"}) == []
    red_contracts = {**green, "after_history.yaml": history + "invariant: owner rec-4481\n"}
    red_code = {"d.py": '"""Content owned by rec-4481."""\n', "e.tf": "# rec-4481\n"}
    assert closed_rec_hits(_REC_4481, red_contracts, red_code) == ["after_history.yaml:6", "d.py:1", "e.tf:1"]
