"""Evaluator for the CD.45 work-item pilot fixture (docs/work-item-pilot/telemetry-feedback-loop.yaml).

CD.45 binds while pending: the fixture is a one-off provisional_v0 pilot in the Decision 197
clause-3 grain, sized by schema and item count, never by lines. Six legs share one registered
check; the schema, caps and sunset terms are frozen in _work_item_pilot_model.py.

  L1 schema (extra=forbid models), referential integrity, per-item failure_signal uniqueness.
  L2 item-count cap, canonical-layout equality with render(parsed), the per-item line ceiling.
  L3 single-instance guard (a line-anchored text scan, never yaml.safe_load: Decision 153).
  L4 edge-target and evidence-anchor resolution, failing closed on ids absent from ROADMAP-PLATFORM.
  L5 header-equals-code for the item-count cap and the sunset terms.
  L6 sunset write-freeze with two operator-only exits (CD.45 no longer pending, or a Decision
     section naming both CD.45 and docs/work-item-pilot/).

registry.examined(1, unit="fixtures") is declared once the fixture is located, before any leg, so
every later exit path is declared (Decision 170).
"""

from __future__ import annotations

import re
from datetime import date

import yaml
from pydantic import ValidationError

from scripts.checks import _common, registry
from scripts.checks.roadmap import _work_item_pilot_model as model
from scripts.checks.structural._classify import effective_lines
from scripts.roadmap import roadmap_archive

_ROADMAP_REL_PATH = "docs/ROADMAP-PLATFORM.yaml"
_DECISIONS_REL_PATH = "docs/DECISIONS.md"
_ARCHIVE_REL_PATH = "docs/DECISIONS_ARCHIVE.md"
_FAIL_LABEL = "Work-item pilot fixture"
_SINGLE_INSTANCE_RE = re.compile(r"^(?:work_items|work_item_criteria|work_item_edges):", re.MULTILINE)
_DECISION_HEADER_RE = re.compile(r"^## Decision (\d+):", re.MULTILINE)
_CD_RE = re.compile(r"(?<![\w.])" + re.escape(model.SUNSET_CD) + r"(?!\d)")
_SECTION_BREAK_RE = re.compile(r"^## ", re.MULTILINE)
_CRITERION_REF_RE = re.compile(r"^(?P<item>[^:]+):(?P<crit>c[0-9]+)$")
_REC_RE = re.compile(r"^rec-[0-9]+$")
_MAX_REPORTED = 12


class _Context:
    """Lazily-read shared inputs for one run; each file is read at most once."""

    def __init__(self) -> None:
        self._roadmap_read = False
        self._roadmap: dict | None = None
        self._tracked: list[str] | None = None
        self._decision_sections: list[str] | None = None
        self._decision_numbers: set[int] | None = None

    def roadmap(self) -> dict | None:
        if not self._roadmap_read:
            self._roadmap_read = True
            path = _common.ROOT / _ROADMAP_REL_PATH
            try:
                loaded = roadmap_archive.hydrate_from_disk(yaml.safe_load(path.read_text(encoding="utf-8")), path)
            except (OSError, yaml.YAMLError, roadmap_archive.ArchiveIntegrityError):
                loaded = None
            self._roadmap = loaded if isinstance(loaded, dict) else None
        return self._roadmap

    def tracked(self) -> list[str] | None:
        if self._tracked is None:
            result = _common.run(["git", "ls-files"], capture_output=True, text=True, encoding="utf-8", cwd=_common.ROOT)
            if result.returncode != 0:
                return None
            self._tracked = result.stdout.splitlines()
        return self._tracked

    def decision_sections(self) -> list[str]:
        """Each '## Decision N:' section of docs/DECISIONS.md, header through the next '## ' line."""
        if self._decision_sections is None:
            text = _read(_DECISIONS_REL_PATH)
            starts = [m.start() for m in _DECISION_HEADER_RE.finditer(text)]
            breaks = [m.start() for m in _SECTION_BREAK_RE.finditer(text)]
            sections = []
            for start in starts:
                end = next((b for b in breaks if b > start), len(text))
                sections.append(text[start:end])
            self._decision_sections = sections
        return self._decision_sections

    def decision_numbers(self) -> set[int]:
        if self._decision_numbers is None:
            text = _read(_DECISIONS_REL_PATH) + "\n" + _read(_ARCHIVE_REL_PATH)
            self._decision_numbers = {int(n) for n in _DECISION_HEADER_RE.findall(text)}
        return self._decision_numbers


def _read(rel: str) -> str:
    try:
        return (_common.ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _names_path(section: str, path: str) -> bool:
    """True when the section names `path` as a whole path: no word, dot, slash or dash glues onto either end
    (a file path also refuses a following `.word`, so `second.yaml.old` is not `second.yaml`)."""
    tail = "" if path.endswith("/") else r"(?![\w/-])(?!\.\w)"
    return re.search(r"(?<![\w./-])" + re.escape(path) + tail, section) is not None


def _names_cd(section: str) -> bool:
    """True when the section names CD.45 as a whole id, so CD.450 to CD.459 never open an exit."""
    return _CD_RE.search(section) is not None


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _roadmap_ids(doc: dict, key: str) -> dict[str, dict]:
    return {str(row["id"]): row for row in doc.get(key) or [] if isinstance(row, dict) and "id" in row}


def _structured_criterion_ids(item: dict) -> set[str]:
    return {str(c["id"]) for c in item.get("exit_criteria") or [] if isinstance(c, dict) and "id" in c}


def _leg1_schema(text: str) -> tuple[list[str], model.Fixture | None]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"fixture is not valid YAML: {exc}"], None
    try:
        fixture = model.Fixture.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        more = [f"... and {len(errors) - _MAX_REPORTED} more"] if len(errors) > _MAX_REPORTED else []
        return [f"schema: {e}" for e in errors[:_MAX_REPORTED]] + more, None
    return _referential_integrity(fixture), fixture


def _referential_integrity(fixture: model.Fixture) -> list[str]:
    errors: list[str] = []
    item_ids = [i.id for i in fixture.work_items]
    for dup in sorted({i for i in item_ids if item_ids.count(i) > 1}):
        errors.append(f"duplicate work item id '{dup}'")
    known = set(item_ids)
    criteria_by_item: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for crit in fixture.work_item_criteria:
        pair = (crit.work_item, crit.criterion_id)
        if pair in seen_pairs:
            errors.append(f"duplicate criterion {crit.work_item}:{crit.criterion_id}")
        seen_pairs.add(pair)
        if crit.work_item not in known:
            errors.append(f"criterion {crit.criterion_id} names absent work item '{crit.work_item}'")
        criteria_by_item.setdefault(crit.work_item, []).append(crit.criterion_id)
    edges_from: dict[str, int] = {}
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in fixture.work_item_edges:
        if edge.from_ not in known:
            errors.append(f"edge from absent work item '{edge.from_}'")
        if edge.from_ == edge.to:
            errors.append(f"self-edge on '{edge.from_}'")
        key = (edge.from_, edge.to, edge.kind)
        if key in seen_edges:
            errors.append(f"duplicate edge {edge.from_} -{edge.kind}-> {edge.to}")
        seen_edges.add(key)
        edges_from[edge.from_] = edges_from.get(edge.from_, 0) + 1
    for item_id, ids in criteria_by_item.items():
        if len(ids) > model.MAX_CRITERIA_PER_ITEM:
            errors.append(
                f"'{item_id}' carries {len(ids)} criteria, above MAX_CRITERIA_PER_ITEM={model.MAX_CRITERIA_PER_ITEM}"
            )
    for item_id, count in edges_from.items():
        if count > model.MAX_EDGES_FROM_ITEM:
            errors.append(f"'{item_id}' carries {count} outgoing edges, above MAX_EDGES_FROM_ITEM={model.MAX_EDGES_FROM_ITEM}")
    signals: dict[tuple[str, str], str] = {}
    for item in fixture.work_items:
        reg = item.extension.consideration_register
        for vid in reg.verification:
            if vid not in criteria_by_item.get(item.id, []):
                errors.append(f"'{item.id}' verification names absent criterion '{vid}'")
        sig = (_norm(reg.failure_signal.signal), _norm(reg.failure_signal.metric))
        if sig in signals:
            errors.append(f"'{item.id}' shares its (failure_signal.signal, failure_signal.metric) with '{signals[sig]}'")
        signals.setdefault(sig, item.id)
    return errors


def _body_after_comments(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = 0
    while start < len(lines) and lines[start].lstrip().startswith("#"):
        start += 1
    return "".join(lines[start:])


def _leg2_cap_and_layout(text: str, fixture: model.Fixture) -> list[str]:
    errors: list[str] = []
    count = len(fixture.work_items)
    if count > model.ITEM_COUNT_CAP:
        errors.append(f"{count} work items exceed ITEM_COUNT_CAP={model.ITEM_COUNT_CAP}; the cap is frozen in code")
    expected = model.render(fixture)
    body = _body_after_comments(text)
    if body != expected:
        diff_line = next(
            (n for n, (a, b) in enumerate(zip(body.splitlines(), expected.splitlines()), 1) if a != b),
            min(len(body.splitlines()), len(expected.splitlines())) + 1,
        )
        errors.append(
            "fixture body is not the canonical layout (render(parsed)); use the canonical flow-row layout "
            f"- first difference at body line {diff_line}"
        )
    ceiling = model.HEADER_LINE_CEILING + model.PER_ITEM_LINE_CEILING * count
    lines = effective_lines(text)
    if lines > ceiling:
        errors.append(
            f"fixture measures {lines} effective lines, above {ceiling} "
            f"(HEADER_LINE_CEILING {model.HEADER_LINE_CEILING} + "
            f"PER_ITEM_LINE_CEILING {model.PER_ITEM_LINE_CEILING} x {count} items); "
            "use the canonical flow-row layout - more lines need a ratified Decision naming the path (Decisions 165/166)"
        )
    return errors


def _leg3_single_instance(ctx: _Context) -> list[str]:
    tracked = ctx.tracked()
    if tracked is None:
        return ["git ls-files failed; the single-instance scan cannot run (fail closed)"]
    offenders: list[tuple[str, str]] = []
    for rel in tracked:
        if rel == model.FIXTURE_PATH:
            continue
        if rel.startswith(model.PILOT_DIR + "/"):
            offenders.append((rel, f"second file under {model.PILOT_DIR}/"))
        elif rel.endswith((".yaml", ".yml")) and _SINGLE_INSTANCE_RE.search(_read(rel)):
            offenders.append((rel, "column-0 work_items / work_item_criteria / work_item_edges key"))
    errors = []
    for rel, reason in offenders:
        if any(_names_path(sec, rel) and _names_cd(sec) for sec in ctx.decision_sections()):
            continue
        errors.append(
            f"second pilot-class fixture: {rel} ({reason}); CD.45 allows exactly one "
            f"(a docs/DECISIONS.md Decision section naming both {rel} and {model.SUNSET_CD} is the rehome exit)"
        )
    return errors


def _check_resolves(name: str) -> bool:
    return name in registry._ALL_ENTRIES


def _file_line_resolves(ctx: _Context, ref: str) -> str | None:
    path, _, line = ref.rpartition(":")
    if not path or not line.isdigit() or int(line) < 1:
        return "file_line ref must be 'path:line' with a positive line number"
    tracked = ctx.tracked()
    if tracked is None or path not in tracked:
        return f"file_line path '{path}' is not a tracked file"
    if len(_read(path).splitlines()) < int(line):
        return f"file_line '{ref}' is past the end of '{path}'"
    return None


def _roadmap_ref_resolves(ctx: _Context, ref: str) -> str | None:
    doc = ctx.roadmap()
    if doc is None:
        return f"{_ROADMAP_REL_PATH} unreadable (fail closed)"
    tiers, cds = _roadmap_ids(doc, "tier_items"), _roadmap_ids(doc, "candidate_decisions")
    if ref in tiers or ref in cds:
        return None
    match = _CRITERION_REF_RE.match(ref)
    if match and match["item"] in tiers:
        if match["crit"] in _structured_criterion_ids(tiers[match["item"]]):
            return None
        return (
            f"'{ref}': tier item {match['item']} lists no structured criterion '{match['crit']}' (a bare-string "
            "criterion is positional and unstable - T4.23:c13)"
        )
    return (
        f"'{ref}' is not a ROADMAP-PLATFORM tier_items id, candidate_decisions id or Tx:cN criterion "
        "(it may have been archived)"
    )


def _evidence_error(ctx: _Context, kind: str, ref: str) -> str | None:
    if kind == "file_line":
        return _file_line_resolves(ctx, ref)
    if kind == "check":
        return None if _check_resolves(ref) else f"'{ref}' is not a registered check (the registry's manifest Entry index)"
    if kind == "decision":
        ok = ref.isdigit() and int(ref) in ctx.decision_numbers()
        return None if ok else f"decision '{ref}' has no '## Decision {ref}:' header in DECISIONS.md or DECISIONS_ARCHIVE.md"
    if kind == "roadmap":
        return _roadmap_ref_resolves(ctx, ref)
    return None if _REC_RE.match(ref) else f"rec ref '{ref}' must match rec-NNNN"


def _leg4_resolution(ctx: _Context, fixture: model.Fixture) -> list[str]:
    errors: list[str] = []
    pilot_ids = {i.id for i in fixture.work_items}
    if fixture.work_item_edges:
        doc = ctx.roadmap()
        tiers = _roadmap_ids(doc, "tier_items") if doc else {}
        cds = _roadmap_ids(doc, "candidate_decisions") if doc else {}
        if doc is None:
            errors.append(f"{_ROADMAP_REL_PATH} unreadable; edge targets cannot resolve (fail closed)")
        for edge in fixture.work_item_edges:
            to = edge.to
            if to in pilot_ids or to in tiers or to in cds:
                continue
            if _CRITERION_REF_RE.match(to):
                errors.append(f"edge target '{to}' is a criterion id; positional and unstable (T4.23:c13), edges are id-only")
            elif _REC_RE.match(to):
                errors.append(f"edge target '{to}' is a rec id; CI has no rec cache (Decision 181), edges are id-only")
            elif doc is not None:
                errors.append(
                    f"edge target '{to}' is not a pilot item, tier_items id or candidate_decisions id "
                    "(fail closed; it may have been archived out of ROADMAP-PLATFORM)"
                )
    for item in fixture.work_items:
        for ev in item.extension.consideration_register.evidence:
            problem = _evidence_error(ctx, ev.kind, ev.ref)
            if problem:
                errors.append(f"'{item.id}' evidence {ev.kind}: {problem}")
    for crit in fixture.work_item_criteria:
        method = crit.method
        if method.kind == "execution" and method.check is not None and not _check_resolves(method.check):
            errors.append(
                f"criterion {crit.work_item}:{crit.criterion_id} method check '{method.check}' is not a registered check"
            )
    return errors


def _leg5_header(fixture: model.Fixture) -> list[str]:
    head = fixture.fixture
    errors = []
    if head.item_count_cap != model.ITEM_COUNT_CAP:
        errors.append(f"header item_count_cap {head.item_count_cap} differs from code ITEM_COUNT_CAP={model.ITEM_COUNT_CAP}")
    crit, review = head.sunset.first_of
    if crit.criterion_met != model.SUNSET_CRITERION:
        errors.append(f"header sunset criterion_met '{crit.criterion_met}' differs from code '{model.SUNSET_CRITERION}'")
    if review.review_on != model.SUNSET_REVIEW_DATE.isoformat():
        errors.append(
            f"header sunset review_on '{review.review_on}' differs from code '{model.SUNSET_REVIEW_DATE.isoformat()}'"
        )
    return errors


def _leg6_sunset(ctx: _Context, today: date | None) -> tuple[list[str], str | None]:
    doc = ctx.roadmap()
    tier_id = model.SUNSET_CRITERION.split(":")[0]
    crit_id = model.SUNSET_CRITERION.split(":")[1]
    if doc is None:
        return [f"{_ROADMAP_REL_PATH} unreadable; the sunset cannot be evaluated (fail closed)"], None
    tier = _roadmap_ids(doc, "tier_items").get(tier_id)
    cd = _roadmap_ids(doc, "candidate_decisions").get(model.SUNSET_CD)
    if tier is None or cd is None:
        missing = [n for n, row in ((tier_id, tier), (model.SUNSET_CD, cd)) if row is None]
        return [f"{', '.join(missing)} absent from {_ROADMAP_REL_PATH}; fail closed (the id may have been archived)"], None
    crit_row = next((c for c in tier.get("exit_criteria") or [] if isinstance(c, dict) and c.get("id") == crit_id), None)
    if crit_row is None:
        return [f"{model.SUNSET_CRITERION} absent from {_ROADMAP_REL_PATH}; fail closed (the id may have been archived)"], None
    now = today or date.today()
    if crit_row.get("status") != "met" and now < model.SUNSET_REVIEW_DATE:
        return [], None
    changed = _common.get_changed_files(_common.ROOT)
    touched = [f for f in changed if f == model.FIXTURE_PATH or f.startswith(model.PILOT_DIR + "/")]
    if not touched:
        note = (
            f"SUNSET REACHED: {model.SUNSET_CRITERION} met or review {model.SUNSET_REVIEW_DATE.isoformat()} "
            "passed; fixture frozen and untouched - operator review due"
        )
        return [], note
    ratified_exit = cd.get("state") != "pending"
    decision_exit = any(_names_cd(sec) and _names_path(sec, model.PILOT_DIR + "/") for sec in ctx.decision_sections())
    if ratified_exit or decision_exit:
        return [], f"SUNSET REACHED: freeze released by an operator-only exit; touched {', '.join(touched)}"
    message = (
        f"sunset reached ({model.SUNSET_CRITERION} met or review date passed): the fixture is write-frozen "
        f"but this diff touches {', '.join(touched)}; the only exits are {model.SUNSET_CD} leaving 'pending' "
        f"or a docs/DECISIONS.md Decision section naming both {model.SUNSET_CD} and {model.PILOT_DIR}/"
    )
    return [message], None


@registry.register("validate_work_item_pilot", owner="platform")
def validate_work_item_pilot(failed: list[str], *, today: date | None = None) -> None:
    """Run legs L1-L6 on the CD.45 pilot fixture. `today` is injectable so the sunset date leg is
    test-pinned on both sides; no other registered check reads the wall clock for a verdict."""
    path = _common.ROOT / model.FIXTURE_PATH
    if not path.is_file():
        print(f"  FAIL: {model.FIXTURE_PATH} is absent; CD.45 pilot fixture must exist (never deleted, CD.45).")
        failed.append(_FAIL_LABEL)
        return
    registry.examined(1, unit="fixtures")
    text = path.read_text(encoding="utf-8")
    ctx = _Context()
    legs: list[tuple[str, list[str]]] = []

    errors, fixture = _leg1_schema(text)
    legs.append(("L1 schema and integrity", errors))
    legs.append(("L3 single instance", _leg3_single_instance(ctx)))
    if fixture is not None:
        legs.append(("L2 cap and layout", _leg2_cap_and_layout(text, fixture)))
        legs.append(("L4 resolution", _leg4_resolution(ctx, fixture)))
        legs.append(("L5 header equals code", _leg5_header(fixture)))
    sunset_errors, sunset_note = _leg6_sunset(ctx, today)
    legs.append(("L6 sunset", sunset_errors))

    for name, errs in sorted(legs):
        if errs:
            print(f"  FAIL: {name}")
            for err in errs:
                print(f"    - {err}")
        else:
            print(f"  PASS: {name}")
    if sunset_note:
        print(f"  {sunset_note}")
    if any(errs for _, errs in legs):
        failed.append(_FAIL_LABEL)
