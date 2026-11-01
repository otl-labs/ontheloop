"""rec-4335 acceptance: the PR-time terraform/github plan and its IAM prerequisite.

TestPlannerStateGrant (PLAN-terraform-github-plan-state-grant): the github_ci_planner role reads exactly
one object under tfstate/github/ and nothing else there. The trusted-plan role (PLAN-trusted-plan-identity,
rec-4636) may hold the same exact grant and nothing else naming that prefix. Pure functions of HCL text: no
network, terraform or AWS.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TF_DIR = REPO_ROOT / "terraform" / "personal"

PLANNER_FILE = "oidc_pipeline_roles.tf"
PLANNER_DOC = "github_ci_planner"
TRUSTED_FILE = "oidc_trusted_plan_role.tf"
TRUSTED_DOC = "github_ci_trusted_plan"
GRANT_SID = "TfstateGithubRead"
GRANT_RESOURCE = "${aws_s3_bucket.data_lake.arn}/tfstate/github/terraform.tfstate"
TARGET_ACTION = "s3:GetObject"
TARGET_ARN = "arn:aws:s3:::agent-platform-data-lake/tfstate/github/terraform.tfstate"
LITERAL = "tfstate/github"
_EXACT_GRANT = (GRANT_SID, "Allow", False, [TARGET_ACTION], [GRANT_RESOURCE])

_DOC_RE = r'data\s+"aws_iam_policy_document"\s+"{name}"\s*\{{'
_ANY_DOC_RE = re.compile(r'data\s+"aws_iam_policy_document"\s+"(\w+)"\s*\{')
_STATEMENT_RE = re.compile(r"\bstatement\s*\{")
_SOURCE_RE = re.compile(r"data\.aws_iam_policy_document\.(\w+)\.json")
_BUCKET_REF_RE = re.compile(r"\$\{aws_s3_bucket\.data_lake\.arn\}|(?<![\w.])aws_s3_bucket\.data_lake\.arn\b")
_BUCKET_ARN = "arn:aws:s3:::agent-platform-data-lake"


def strip_comments(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif c == "#" or text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _balanced(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError("unbalanced braces")


def _doc_block(text: str, name: str) -> str | None:
    m = re.search(_DOC_RE.format(name=re.escape(name)), text)
    return None if m is None else text[m.start() : _balanced(text, m.end() - 1) + 1]


def _items(body: str, key: str) -> list[str]:
    m = re.search(rf"\b{key}\s*=\s*\[(.*?)\]", body, re.DOTALL)
    if m is None:
        return []
    raw = [part.strip() for part in m.group(1).split(",") if part.strip()]
    return [part[1:-1] if part.startswith('"') and part.endswith('"') else part for part in raw]


def _parse_statement(body: str) -> dict:
    sid = re.search(r'\bsid\s*=\s*"([^"]*)"', body)
    effect = re.search(r'\beffect\s*=\s*"([^"]*)"', body)
    return {
        "sid": sid.group(1) if sid else None,
        "effect": effect.group(1) if effect else "Allow",
        "actions": _items(body, "actions"),
        "resources": _items(body, "resources"),
        "condition": re.search(r"\bcondition\s*\{", body) is not None,
    }


def statements_of(block: str) -> list[dict]:
    found = []
    for m in _STATEMENT_RE.finditer(block):
        found.append(_parse_statement(block[m.start() : _balanced(block, m.end() - 1) + 1]))
    return found


def doc_statements(texts: dict[str, str], name: str) -> list[dict]:
    """Statements a document composes: its own plus every source_policy_documents document, recursively."""
    for text in texts.values():
        block = _doc_block(text, name)
        if block is None:
            continue
        found = statements_of(block)
        for src in _SOURCE_RE.findall(block):
            found += doc_statements(texts, src)
        return found
    return []


def _expand(resource: str) -> str:
    return _BUCKET_REF_RE.sub(_BUCKET_ARN, resource)


def _grant_violations(texts: dict[str, str]) -> list[str]:
    own = statements_of(_doc_block(texts.get(PLANNER_FILE, ""), PLANNER_DOC) or "")
    own_hits = [s for s in own if any(LITERAL in r for r in s["resources"])]
    if len(own_hits) != 1:
        return [f"planner document names {LITERAL} in {len(own_hits)} statements, want exactly 1"]
    g = own_hits[0]
    bad = []
    if g["sid"] != GRANT_SID or g["effect"] != "Allow" or g["condition"]:
        bad.append(f"grant sid/effect/condition wrong: {g}")
    if g["actions"] != [TARGET_ACTION] or g["resources"] != [GRANT_RESOURCE]:
        bad.append(f"grant actions/resources not exact: {g['actions']} {g['resources']}")
    return bad


def _other_document_violations(texts: dict[str, str]) -> list[str]:
    bad = []
    for name in {n for t in texts.values() for n in _ANY_DOC_RE.findall(t)} - {PLANNER_DOC}:
        for s in doc_statements(texts, name):
            exact = (s["sid"], s["effect"], s["condition"], s["actions"], s["resources"]) == _EXACT_GRANT
            if name == TRUSTED_DOC and exact:
                continue
            if any(LITERAL in r for r in s["resources"]) and s["effect"] == "Allow":
                bad.append(f"document {name} statement {s['sid']} names {LITERAL}")
    return bad


def _refresh_read_violations(texts: dict[str, str]) -> list[str]:
    bad = []
    for s in doc_statements(texts, "ci_full_refresh_read"):
        if s["sid"] == "TfstateRead" and any("tfstate/personal" not in r for r in s["resources"]):
            bad.append(f"TfstateRead leaves tfstate/personal: {s['resources']}")
    return bad


def _glob_coverage_violations(texts: dict[str, str]) -> list[str]:
    bad = []
    for s in doc_statements(texts, PLANNER_DOC):
        if s["effect"] != "Allow" or s["sid"] == GRANT_SID:
            continue
        if not any(fnmatch.fnmatchcase(TARGET_ACTION.lower(), a.lower()) for a in s["actions"]):
            continue
        bad += [
            f"statement {s['sid']} resource {r} covers the github state object"
            for r in s["resources"]
            if fnmatch.fnmatchcase(TARGET_ARN, _expand(r))
        ]
    return bad


def planner_violations(texts: dict[str, str]) -> list[str]:
    """Texts: {filename: comment-stripped HCL} for terraform/personal. Empty list == grant is exactly as planned."""
    checks = (_grant_violations, _other_document_violations, _refresh_read_violations, _glob_coverage_violations)
    return [v for check in checks for v in check(texts)]


def allow_statements_naming_literal(texts: dict[str, str]) -> list[tuple[str, str | None]]:
    """(filename, sid) of every Allow statement-like block (HCL statement or jsonencode object) naming LITERAL."""
    hits: list[tuple[str, str | None]] = []
    for fname, text in texts.items():
        for m in re.finditer(re.escape(LITERAL), text):
            enclosing = _innermost_statement(text, m.start())
            if enclosing is None:
                continue
            effect = re.search(r'\b[eE]ffect\s*=\s*"([^"]*)"', enclosing)
            if effect is not None and effect.group(1) != "Allow":
                continue
            sid = re.search(r'\b[sS]id\s*=\s*"([^"]*)"', enclosing)
            hits.append((fname, sid.group(1) if sid else None))
    return hits


def _innermost_statement(text: str, pos: int) -> str | None:
    """Innermost brace block around pos that carries a resources/Resource key (an IAM statement)."""
    starts = [i for i in range(pos) if text[i] == "{"]
    for start in reversed(starts):
        end = _balanced(text, start)
        if end >= pos and re.search(r"\b[rR]esources?\b\s*=", text[start : end + 1]):
            return text[start : end + 1]
    return None


def real_texts() -> dict[str, str]:
    return {p.name: strip_comments(p.read_text(encoding="utf-8")) for p in sorted(TF_DIR.glob("*.tf"))}


GRANT_HCL = """
  statement {
    sid       = "TfstateGithubRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/github/terraform.tfstate"]
  }
"""


def _planner_file(extra: str = "", grant: str = GRANT_HCL, source: str = "ci_full_refresh_read") -> str:
    return (
        'data "aws_iam_policy_document" "github_ci_planner" {\n'
        f"  source_policy_documents = [data.aws_iam_policy_document.{source}.json]\n"
        f"{grant}{extra}}}\n"
    )


def _refresh_file(extra: str = "") -> str:
    return (
        'data "aws_iam_policy_document" "ci_full_refresh_read" {\n'
        "  statement {\n"
        '    sid       = "TfstateRead"\n'
        '    effect    = "Allow"\n'
        '    actions   = ["s3:GetObject"]\n'
        '    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/personal/*"]\n'
        "  }\n"
        "  statement {\n"
        '    sid       = "SSMDescribeParameters"\n'
        '    actions   = ["ssm:DescribeParameters"]\n'
        '    resources = ["*"]\n'
        "  }\n"
        f"{extra}}}\n"
        'data "aws_iam_policy_document" "ci_ssm_refresh_read" {\n'
        "}\n"
    )


def _pr_file(extra: str = "") -> str:
    return f'data "aws_iam_policy_document" "github_ci_pr" {{\n{extra}}}\n'


def synthetic(**kw: str) -> dict[str, str]:
    return {
        PLANNER_FILE: strip_comments(_planner_file(kw.get("planner_extra", ""), kw.get("grant", GRANT_HCL))),
        "oidc.tf": strip_comments(_refresh_file(kw.get("refresh_extra", ""))),
        "oidc_ci_roles.tf": strip_comments(_pr_file(kw.get("pr_extra", ""))),
    }


def _stmt(sid: str, actions: str, resources: str, effect: str = "Allow") -> str:
    return (
        f'  statement {{\n    sid = "{sid}"\n    effect = "{effect}"\n'
        f"    actions = [{actions}]\n    resources = [{resources}]\n  }}\n"
    )


class TestPlannerStateGrant:
    def test_real_hcl_grant_is_exactly_as_planned(self) -> None:
        assert planner_violations(real_texts()) == []

    def test_real_hcl_no_other_allow_statement_names_the_state_prefix(self) -> None:
        assert allow_statements_naming_literal(real_texts()) == [(PLANNER_FILE, GRANT_SID), (TRUSTED_FILE, GRANT_SID)]

    def test_trusted_plan_document_may_hold_only_the_exact_grant(self) -> None:
        doc = f'data "aws_iam_policy_document" "{TRUSTED_DOC}" {{\n{GRANT_HCL}}}\n'
        texts = synthetic()
        texts[TRUSTED_FILE] = strip_comments(doc)
        assert planner_violations(texts) == []
        for broad in (
            doc.replace("terraform.tfstate", "*"),
            doc.replace('["s3:GetObject"]', '["s3:GetObject", "s3:PutObject"]'),
        ):
            texts[TRUSTED_FILE] = strip_comments(broad)
            assert planner_violations(texts), broad
        texts[TRUSTED_FILE] = strip_comments(doc.replace(f'"{TRUSTED_DOC}"', '"other_doc"'))
        assert planner_violations(texts)

    def test_real_hcl_refresh_read_still_scoped_to_tfstate_personal(self) -> None:
        stmts = {s["sid"]: s for s in doc_statements(real_texts(), "ci_full_refresh_read")}
        assert stmts["TfstateRead"]["resources"] == ["${aws_s3_bucket.data_lake.arn}/tfstate/personal/*"]

    def test_synthetic_green_baseline_passes(self) -> None:
        texts = synthetic()
        assert planner_violations(texts) == []
        assert allow_statements_naming_literal(texts) == [(PLANNER_FILE, GRANT_SID)]

    def test_red_wildcard_resource(self) -> None:
        grant = GRANT_HCL.replace("terraform.tfstate", "*")
        assert planner_violations(synthetic(grant=grant))

    def test_red_added_put_object(self) -> None:
        grant = GRANT_HCL.replace('["s3:GetObject"]', '["s3:GetObject", "s3:PutObject"]')
        assert planner_violations(synthetic(grant=grant))

    def test_red_grant_placed_in_refresh_read(self) -> None:
        texts = synthetic(grant="", refresh_extra=GRANT_HCL)
        assert planner_violations(texts)

    def test_red_grant_placed_in_pr_role_policy(self) -> None:
        texts = synthetic(grant="", pr_extra=GRANT_HCL)
        assert planner_violations(texts)
        assert (("oidc_ci_roles.tf", GRANT_SID)) in allow_statements_naming_literal(texts)

    def test_red_broader_resource_on_another_get_object_statement(self) -> None:
        for broad in ('"${aws_s3_bucket.data_lake.arn}/tfstate/*"', '"${aws_s3_bucket.data_lake.arn}/*"', '"*"'):
            extra = _stmt("Broad", '"s3:GetObject"', broad)
            assert planner_violations(synthetic(planner_extra=extra)), broad

    def test_red_action_glob_on_another_statement(self) -> None:
        for action in ('"s3:Get*"', '"s3:*"', '"*"'):
            extra = _stmt("Glob", action, '"${aws_s3_bucket.data_lake.arn}/*"')
            assert planner_violations(synthetic(planner_extra=extra)), action

    def test_resource_star_statements_without_get_object_are_excluded_by_action(self) -> None:
        extra = _stmt("CloudWatchLogsRead", '"logs:Describe*", "logs:Get*"', '"*"')
        assert planner_violations(synthetic(planner_extra=extra)) == []

    def test_deny_statement_on_state_prefix_is_out_of_scope(self) -> None:
        deny = _stmt("DenyGithubState", '"s3:*"', '"${aws_s3_bucket.data_lake.arn}/tfstate/github/*"', effect="Deny")
        texts = synthetic()
        texts["platform_dev_policies.tf"] = strip_comments(f'data "aws_iam_policy_document" "dev" {{\n{deny}}}\n')
        assert planner_violations(texts) == []
        assert allow_statements_naming_literal(texts) == [(PLANNER_FILE, GRANT_SID)]

    def test_jsonencode_allow_in_another_file_is_caught(self) -> None:
        texts = synthetic()
        texts["other.tf"] = strip_comments(
            'resource "aws_iam_policy" "x" {\n  policy = jsonencode({\n    Statement = [{\n      Effect = "Allow"\n'
            '      Action = ["s3:GetObject"]\n      Resource = ["arn:aws:s3:::b/tfstate/github/terraform.tfstate"]\n'
            "    }]\n  })\n}\n"
        )
        assert ("other.tf", None) in allow_statements_naming_literal(texts)

    def test_comment_mentions_are_ignored(self) -> None:
        stripped = strip_comments('# tfstate/github\nx = 1 // tfstate/github\n/* tfstate/github */\ny = "a#b"\n')
        assert LITERAL not in stripped
        assert 'y = "a#b"' in stripped


# ---- PLAN-terraform-github-pr-plan: reporter and workflow wiring ----

import ast  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402
import yaml  # noqa: E402

from scripts.ci import terraform_github_plan_report as rpt  # noqa: E402

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terraform-github-plan.yml"
REPORTER = REPO_ROOT / "scripts" / "ci" / "terraform_github_plan_report.py"
TAXONOMY = REPO_ROOT / "docs" / "contracts" / "environment-taxonomy.yaml"
SENTINEL = "SENTINEL-VALUE-9f3a"


def _rc(addr: str, actions: list[str], before=None, after=None, unknown=None, rtype="github_repository", **extra) -> dict:
    change = {"actions": actions, "before": before, "after": after, "after_unknown": unknown or {}}
    change.update(extra.pop("change_extra", {}))
    return {"address": addr, "mode": extra.pop("mode", "managed"), "type": rtype, "change": change}


def _ruleset(before_actors, after_actors, extra_after=None) -> dict:
    before = {"name": "main-protection", "bypass_actors": before_actors}
    after = {"name": "main-protection", "bypass_actors": after_actors, **(extra_after or {})}
    return _rc("github_repository_ruleset.main", ["update"], before, after, rtype="github_repository_ruleset")


def _vis(bypass: str = "hidden", san: str = "hidden") -> dict[str, str]:
    return {"security_and_analysis": san, "ruleset_bypass_actors": bypass}


def _plan(*rcs: dict, **top) -> dict:
    return {"resource_changes": list(rcs), **top}


def _text(plan: dict, vis: dict[str, str]) -> str:
    return rpt.render(rpt.classify(plan, vis), "abc123", "https://example.invalid/run", 2)


class TestPlanReport:
    def test_no_op_plan_is_no_changes(self) -> None:
        rep = rpt.classify(_plan(_rc("a.b", ["no-op"], {"x": 1}, {"x": 1})), _vis())
        assert rep.verdict == "NO_CHANGES"
        assert (rep.imports, rep.add, rep.change, rep.destroy, rep.replace, rep.drift) == (0, 0, 0, 0, 0, [])
        assert not rep.destructive

    def test_hidden_bypass_only_update_is_gated_but_counted_raw(self) -> None:
        rep = rpt.classify(_plan(_ruleset([], [{"actor_id": 5}])), _vis("hidden"))
        assert rep.rows[0].gated and rep.change == 1 and rep.gated == 1
        assert rep.verdict == "VISIBILITY_GATED_ONLY"
        assert "(visibility-gated)" in rpt.render(rep, "s", "u", 2)

    @pytest.mark.parametrize("state", ["visible", "unknown"])
    def test_not_gated_when_probe_not_hidden(self, state: str) -> None:
        rep = rpt.classify(_plan(_ruleset([], [{"actor_id": 5}])), _vis(state))
        assert not rep.rows[0].gated and rep.verdict == "CHANGES"

    def test_bypass_plus_other_key_is_not_gated(self) -> None:
        rep = rpt.classify(_plan(_ruleset([], [{"actor_id": 5}], {"enforcement": "active"})), _vis("hidden"))
        assert not rep.rows[0].gated and rep.rows[0].changed == ["bypass_actors", "enforcement"]

    def test_non_empty_before_is_not_gated(self) -> None:
        rep = rpt.classify(_plan(_ruleset([{"actor_id": 1}], [{"actor_id": 5}])), _vis("hidden"))
        assert not rep.rows[0].gated

    def test_visible_probe_with_empty_state_is_a_conflict(self) -> None:
        rep = rpt.classify(_plan(_ruleset([], [{"actor_id": 5}])), _vis("visible"))
        text = rpt.render(rep, "s", "u", 2)
        assert rep.conflict and not rep.rows[0].gated
        assert "VISIBILITY CONFLICT" in text and "conflict=1" in text.splitlines()[-1]

    def test_gated_row_plus_other_update_is_changes(self) -> None:
        plan = _plan(_ruleset([], [{"actor_id": 5}]), _rc("github_repository.r", ["update"], {"a": 1}, {"a": 2}))
        rep = rpt.classify(plan, _vis("hidden"))
        assert rep.verdict == "CHANGES" and rep.gated == 1 and rep.change == 2

    def test_import_only_row(self) -> None:
        rep = rpt.classify(_plan(_rc("a.b", ["no-op"], {"x": 1}, {"x": 1}, change_extra={"importing": {"id": "q"}})), _vis())
        assert (rep.imports, rep.change) == (1, 0)

    def test_replace_and_delete_are_destructive(self) -> None:
        rep = rpt.classify(_plan(_rc("a.r", ["delete", "create"], {"x": 1}, {"x": 2})), _vis())
        assert (rep.replace, rep.add, rep.destroy) == (1, 1, 1) and rep.destructive
        assert rpt.classify(_plan(_rc("a.d", ["delete"], {"x": 1}, None)), _vis()).destructive
        assert "DESTROY OR REPLACE" in _text(_plan(_rc("a.d", ["delete"], {"x": 1}, None)), _vis())

    def test_after_unknown_nested_false_adds_nothing_and_nested_true_does(self) -> None:
        quiet = _rc("a.b", ["update"], {"x": 1}, {"x": 1}, {"y": [{"z": False}], "w": {"v": False}})
        loud = _rc("a.b", ["update"], {"x": 1}, {"x": 1}, {"y": [{"z": False}, {"z": True}]})
        assert rpt.classify(_plan(quiet), _vis()).rows[0].changed == []
        assert rpt.classify(_plan(loud), _vis()).rows[0].changed == ["y"]

    def test_drift_is_separate_and_listed_by_address(self) -> None:
        plan = _plan(resource_drift=[{"address": "github_repository.drifted", "change": {"before": {"k": SENTINEL}}}])
        rep = rpt.classify(plan, _vis())
        assert rep.drift == ["github_repository.drifted"] and rep.change == 0 and rep.verdict == "NO_CHANGES"
        assert "github_repository.drifted" in rpt.render(rep, "s", "u", 0)

    def test_data_mode_ignored(self) -> None:
        assert rpt.classify(_plan(_rc("data.x.y", ["update"], {"a": 1}, {"a": 2}, mode="data")), _vis()).rows == []

    def test_no_value_ever_rendered(self) -> None:
        rc = _rc("github_repository.r", ["update"], {"a": SENTINEL}, {"a": SENTINEL + "2"}, {"b": True})
        plan = _plan(rc, variables={"v": {"value": SENTINEL}}, prior_state={"v": SENTINEL}, configuration={"v": SENTINEL})
        plan["planned_values"] = {"v": SENTINEL}
        plan["output_changes"] = {"o": {"after": SENTINEL}}
        assert SENTINEL not in _text(plan, _vis())

    def test_load_visibility(self, tmp_path) -> None:
        f = tmp_path / "v.json"
        f.write_text(json.dumps({"security_and_analysis": "false", "ruleset_bypass_actors": "true"}))
        assert rpt.load_visibility(f) == {"security_and_analysis": "hidden", "ruleset_bypass_actors": "visible"}
        f.write_text(json.dumps({"security_and_analysis": "maybe"}))
        assert set(rpt.load_visibility(f).values()) == {"unknown"}
        assert set(rpt.load_visibility(tmp_path / "missing.json").values()) == {"unknown"}

    @pytest.mark.parametrize("content", [None, "not json", "[]", '{"x": 1}'])
    def test_main_fails_closed(self, tmp_path, capsys, content) -> None:
        p = tmp_path / "plan.json"
        if content is not None:
            p.write_text(content)
        args = [
            "--plan-json",
            str(p),
            "--visibility",
            str(tmp_path / "v"),
            "--plan-exit-code",
            "2",
            "--head-sha",
            "s",
            "--run-url",
            "u",
        ]
        assert rpt.main(args) == 2
        assert capsys.readouterr().out == ""

    def test_main_github_output_and_machine_line(self, tmp_path, capsys) -> None:
        p, out = tmp_path / "plan.json", tmp_path / "out"
        p.write_text(json.dumps(_plan(_rc("a.d", ["delete"], {"x": 1}, None))))
        args = [
            "--plan-json",
            str(p),
            "--visibility",
            str(tmp_path / "v"),
            "--plan-exit-code",
            "2",
            "--head-sha",
            "s",
            "--run-url",
            "u",
        ]
        assert rpt.main([*args, "--github-output", str(out)]) == 0
        assert out.read_text().splitlines() == ["verdict=CHANGES", "destructive=true", "conflict=false"]
        assert capsys.readouterr().out.splitlines()[-1].startswith("TF_GITHUB_PLAN_REPORT verdict=")

    def test_stdlib_only(self) -> None:
        allowed = set(sys.stdlib_module_names)
        for node in ast.walk(ast.parse(REPORTER.read_text(encoding="utf-8"))):
            mods = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for m in mods:
                assert m.split(".")[0] in allowed, m


def _steps(wf: dict) -> list[dict]:
    return wf["jobs"]["plan"]["steps"]


def _step(wf: dict, uses_prefix: str) -> dict:
    return next(s for s in _steps(wf) if s.get("uses", "").startswith(uses_prefix))


class TestWorkflowAndTaxonomy:
    @pytest.fixture(scope="class")
    def wf(self) -> dict:
        return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def test_triggers_exact(self, wf) -> None:
        assert set(wf[True]) == {"pull_request"}
        assert wf[True]["pull_request"] == {
            "paths": [
                "terraform/github/**",
                ".github/workflows/terraform-github-plan.yml",
                "scripts/ci/terraform_github_plan_report.py",
            ]
        }

    def test_guard_and_permissions(self, wf) -> None:
        cond = wf["jobs"]["plan"]["if"]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in cond
        assert "github.actor != 'dependabot[bot]'" in cond
        assert wf["permissions"] == {"contents": "read"}
        assert wf["jobs"]["plan"]["permissions"] == {"id-token": "write", "contents": "read", "pull-requests": "write"}

    def test_aws_credentials_step(self, wf) -> None:
        step = _step(wf, "aws-actions/configure-aws-credentials")
        role = step["with"]["role-to-assume"]
        assert "secrets.AWS_ACCOUNT_ID" in role and role.endswith("role/agent-platform-github-ci-planner")
        session = step["with"]["role-session-name"]
        assert session == "terraform-github-plan-pr"
        oidc = (TF_DIR / "oidc.tf").read_text(encoding="utf-8")
        reserved = re.search(r'convergence_writer_session_name\s*=\s*"([^"]+)"', oidc)
        assert reserved and session != reserved.group(1)
        assert "continue-on-error" not in step

    def test_app_token_step(self, wf) -> None:
        step = _step(wf, "actions/create-github-app-token")
        assert set(step["with"]) == {
            "client-id",
            "private-key",
            "permission-metadata",
            "permission-administration",
            "permission-actions",
        }
        assert all(v == "read" for k, v in step["with"].items() if k.startswith("permission-"))

    def test_plan_and_probe_flags(self, wf) -> None:
        plan = next(s for s in _steps(wf) if s.get("id") == "plan")["run"]
        assert "-lock=false" in plan and "-detailed-exitcode" in plan and "> /dev/null" in plan
        probe = next(s for s in _steps(wf) if "Probe" in s.get("name", ""))["run"]
        assert "!= null" in probe and 'has("bypass_actors")' in probe

    def test_nothing_mutating_or_persisted(self, wf) -> None:
        forbidden = [
            "terraform apply",
            "terraform import",
            "state rm",
            "state mv",
            "state push",
            "force-unlock",
            "aws s3",
            "GITHUB_ENV",
        ]
        for s in _steps(wf):
            for token in forbidden:
                assert token not in s.get("run", ""), (token, s.get("name"))
            assert not s.get("uses", "").startswith("actions/upload-artifact")
        last = _steps(wf)[-1]
        assert last["if"] == "always()" and "plan.bin" in last["run"] and "plan.json" in last["run"]

    def test_reviewer_ids_never_in_step_env(self, wf) -> None:
        for s in _steps(wf):
            for key in s.get("env") or {}:
                assert "USER_IDS" not in key.upper() and "REVIEWER_IDS" not in key.upper()

    def test_not_a_required_check(self) -> None:
        assert "terraform-github-plan" not in (REPO_ROOT / "terraform" / "github" / "repo.tf").read_text(encoding="utf-8")

    def test_taxonomy_row(self) -> None:
        row = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))["axis_a"]["github"]
        assert "terraform/github" in row["code_vs_infra"]
        gating = row["apply_gating"]
        assert "terraform-github-plan.yml" in gating and "NEVER auto-apply" in gating and not gating.startswith("admin-only")

    def test_admin_root_gate_does_not_derive_github(self) -> None:
        from scripts.checks.verification import validate_admin_root_premerge_gate as gate

        result = gate._derive_admin_only_roots(TAXONOMY)
        roots, err = result
        assert not err
        flat = " ".join(map(str, roots))
        assert "terraform/bootstrap" in flat and "terraform/github" not in flat

    def test_claude_md_pointer(self) -> None:
        text = (REPO_ROOT / "terraform" / "github" / "CLAUDE.md").read_text(encoding="utf-8")
        assert ".github/workflows/terraform-github-plan.yml" in text
