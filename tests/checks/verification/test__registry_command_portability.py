"""Mirror of scripts/checks/verification/_registry_command_portability.py."""

import io
import tokenize
from pathlib import Path

import pytest
import yaml

from scripts.checks.verification import _registry_command_portability as p

DEPRECATED = Path(__file__).resolve().parents[3] / "config/agent/verification_registry/entries/deprecated"
LIVE_TOKEN = "LIVE_CHECKOUT:"
RG_TOKEN = "RG:"


def _row(command: object, slot: str = "command_exit_zero") -> dict:
    return {"check_id": "c", "primitive_slot": slot, "check_spec": {"command": command}}


def _sh(body: str) -> dict:
    return _row(["bash", "-c", body])


def _py(body: str) -> dict:
    return _row(["bin/venv-python", "-c", body])


def _prefixes(row: dict) -> set[str]:
    return {m.split(" ", 1)[0] for m in p.findings(row).messages}


RG_CASES = [
    pytest.param(_sh("rg -n 'foo|bar' src/"), id="bash-c-quoted-pipe"),
    pytest.param(_sh("echo a\nrg x"), id="multiline-rg-on-line-2"),
    pytest.param(_row(["bash", "-lc", "rg x"]), id="bash-lc"),
    pytest.param(_row(["timeout", "60", "bash", "-c", "rg x"]), id="timeout-bash-c"),
    pytest.param(_row(["bash", "-c", "LC_ALL=C rg -q foo src/"]), id="var-prefixed"),
    pytest.param(_row(["timeout", "60", "rg", "x"]), id="top-level-timeout"),
    pytest.param(_row(["env", "rg", "x"]), id="top-level-env"),
    pytest.param(_sh('echo "$(rg x)"'), id="subst-in-double-quotes"),
    pytest.param(_sh("echo `rg x`"), id="backtick"),
    pytest.param(_sh("bin/venv-python -c \"import subprocess; subprocess.run(['rg','x'])\""), id="python-in-shell"),
    pytest.param(_py("import subprocess; subprocess.run(['bash','-c','rg x'])"), id="python-list-bash-c"),
    pytest.param(_sh("! rg x"), id="negated"),
    pytest.param(_sh("command rg x"), id="command-prefix"),
    pytest.param(_sh("exec rg x"), id="exec-prefix"),
    pytest.param(_row(["sh", "-c", "rg x"]), id="sh-c"),
    pytest.param(_row(["sh", "-ec", "rg x"]), id="sh-ec"),
    pytest.param(_row(["python", "-c", "x = ['rg','x']"]), id="python-c"),
    pytest.param(_row(["python3", "-c", "x = ['rg','x']"]), id="python3-c"),
    pytest.param(_row(["python3.12", "-c", "x = ['rg','x']"]), id="python3-12-c"),
    pytest.param(_sh("(rg x)"), id="subshell"),
    pytest.param(_sh("rg x &"), id="background"),
    pytest.param(_sh("true; rg x"), id="semicolon"),
    pytest.param(_sh("true && rg x"), id="and"),
    pytest.param(_sh("false || rg x"), id="or"),
    pytest.param(_sh("echo a | rg x"), id="pipe"),
    pytest.param(_sh("env rg x"), id="segment-env"),
    pytest.param(_sh("echo ok # c\nrg x"), id="after-comment-line"),
    pytest.param(_row(["bash", "-c", "rg x"], "command_output_matches"), id="output-matches-slot"),
    pytest.param(_row(["bash", "-c", "rg x"], "metric_under_threshold"), id="metric-slot"),
]

LIVE_CASES = [
    pytest.param(_row(["git", "branch", "--show-current"]), id="top-level-branch"),
    pytest.param(_row(["timeout", "60", "git", "merge-base", "origin/main", "HEAD"]), id="top-level-merge-base"),
    pytest.param(_sh("git branch --show-current"), id="shell-branch"),
    pytest.param(_sh('b=$(git rev-parse --abbrev-ref HEAD); test "$b"'), id="rev-parse-in-subst"),
    pytest.param(_sh("git symbolic-ref HEAD"), id="symbolic-ref"),
    pytest.param(_sh("git merge-base origin/main HEAD"), id="merge-base"),
    pytest.param(_sh("test -n $GITHUB_EVENT_NAME"), id="dollar-github"),
    pytest.param(_sh("test -n ${GITHUB_REF}"), id="brace-github"),
    pytest.param(_sh('test -n "$GITHUB_SHA"'), id="dollar-github-double-quoted"),
    pytest.param(_py("import os; os.getenv('GITHUB_REF')"), id="getenv"),
    pytest.param(_py("import os; os.environ['GITHUB_SHA']"), id="environ-subscript"),
    pytest.param(_py("import os; os.environ.get('GITHUB_REF')"), id="environ-get"),
    pytest.param(_py("push_context_base(Path('.'))"), id="push-context-base"),
    pytest.param(_py("import os; assert os.environ['GITHUB_EVENT_NAME'] == 'push'"), id="environ-compare"),
    pytest.param(_row(["git", "--no-pager", "-c", "k=v", "branch", "--show-current"]), id="git-global-options"),
    pytest.param(_row(["git", "--git-dir=x", "--work-tree=y", "branch", "--show-current"]), id="git-dir-prefix"),
    pytest.param(_row(["git", "-C", "d", "branch", "--show-current"]), id="declared-over-refusal"),
    pytest.param(
        _py(
            "import sys,os,subprocess; os.environ.pop('GITHUB_EVENT_NAME', None); from scripts.checks import _common; "
            "b=_common.push_context_base(); br=subprocess.run(['git','branch','--show-current']).stdout"
        ),
        id="measured-push-context-guard",
    ),
]

CLEAN_CASES = [
    pytest.param(_sh("grep -q 'GITHUB_HEAD_REF' f"), id="grep-names-github"),
    pytest.param(_sh("grep -c 'push_context_base' f"), id="grep-names-push-context"),
    pytest.param(_sh("echo '$GITHUB_REF'"), id="single-quoted-expansion"),
    pytest.param(_sh("echo '$(rg x)'"), id="single-quoted-subst"),
    pytest.param(_py("print('rg')"), id="print-rg"),
    pytest.param(_py("x = 'push_context_base(' + \"os.getenv('GITHUB_REF')\""), id="string-names-token"),
    pytest.param(_py("import os; os.environ.pop('GITHUB_EVENT_NAME', None)"), id="environ-pop"),
    pytest.param(_py("import os; os.environ['GITHUB_EVENT_NAME'] = ''"), id="environ-assign"),
    pytest.param(_py("import os\ndel os.environ['GITHUB_EVENT_NAME']"), id="environ-del"),
    pytest.param(_row(["pytest", "x"], "test_selector"), id="test-selector-slot"),
    pytest.param(_row(["rg", "x"], "grep_count"), id="grep-count-slot"),
    pytest.param(_row(["bash", "script.sh", "-c", "rg"]), id="c-after-script"),
    pytest.param(_row(["bin/venv-python", "-m", "pytest", "-c", "pytest.ini"]), id="c-after-module"),
    pytest.param(_sh("echo x # x | rg y"), id="comment-with-rg"),
    pytest.param(_sh("cmd 2>&1 | grep -q ok"), id="fd-redirect-2-1"),
    pytest.param(_sh("cmd >&2 | grep -q ok"), id="fd-redirect-gt-amp"),
    pytest.param(_sh("cmd &>/dev/null | grep -q ok"), id="amp-gt-redirect"),
    pytest.param(_sh("printf 'a\\nrg x'"), id="quoted-escape-newline"),
    pytest.param(_row(["bash", "-c", "printf 'a\nrg x'"]), id="quoted-newline-never-splits"),
    pytest.param(_py("# x | rg y\nprint(1)"), id="python-comment"),
]


class TestFindings:
    @pytest.mark.parametrize("row", RG_CASES)
    def test_rg_flagged(self, row: dict) -> None:
        found = p.findings(row)
        assert found.messages
        assert all(m.startswith(RG_TOKEN) for m in found.messages)

    @pytest.mark.parametrize("row", LIVE_CASES)
    def test_live_checkout_flagged(self, row: dict) -> None:
        found = p.findings(row)
        assert found.messages
        assert LIVE_TOKEN in {m.split(" ", 1)[0] for m in found.messages}

    @pytest.mark.parametrize("row", CLEAN_CASES)
    def test_clean(self, row: dict) -> None:
        found = p.findings(row)
        assert found.messages == []
        assert found.unparseable == 0

    def test_all_string_rg_python_list_is_declared_over_refusal(self) -> None:
        assert _prefixes(_py("data = ['rg', 'x']")) == {RG_TOKEN}

    def test_remedies_named(self) -> None:
        assert "POSIX grep" in p.findings(_sh("rg x")).messages[0]
        assert "a pytest test that builds its own repository" in p.findings(_sh("git branch --show-current")).messages[0]

    def test_unbalanced_quote_counts_unparseable_without_finding(self) -> None:
        found = p.findings(_sh("echo 'abc"))
        assert found.messages == []
        assert found.unparseable == 1

    def test_unbalanced_segment_quote_counts_unparseable(self) -> None:
        found = p.findings(_sh('echo "abc'))
        assert (found.messages, found.unparseable) == ([], 1)

    @pytest.mark.parametrize("body", ["x = (1,", "if True:\n        a = 1\n    b = 2\n"], ids=["token-error", "indentation"])
    def test_untokenizable_python_body_counts_unparseable(self, body: str) -> None:
        found = p.findings(_py(body))
        assert (found.messages, found.unparseable) == ([], 1)

    @pytest.mark.parametrize(
        "row",
        [
            {"primitive_slot": "command_exit_zero"},
            {"primitive_slot": "command_exit_zero", "check_spec": {"command": "rg x"}},
            {"primitive_slot": "command_exit_zero", "check_spec": {"command": ["bash", 1]}},
            {"primitive_slot": "command_exit_zero", "check_spec": {}},
        ],
        ids=["no-spec", "string-command", "non-string-element", "no-command-key"],
    )
    def test_malformed_rows_count_one_unparseable(self, row: dict) -> None:
        found = p.findings(row)
        assert (found.messages, found.unparseable) == ([], 1)

    def test_non_dict_row_never_raises(self) -> None:
        assert p.findings("x") == p.Findings()  # type: ignore[arg-type]

    def test_too_deep_nesting_counts_unparseable(self) -> None:
        body = "rg x"
        for _ in range(12):
            body = "bash -c '" + body.replace("'", "'\\''") + "'"
        found = p.findings(_sh(body))
        assert found.unparseable >= 1

    def test_python_body_too_deep_counts_unparseable(self) -> None:
        assert p.findings(_row(["python", "-c", "x"]))  # sanity
        ctx = p._Ctx()
        p._python_body("x", ctx, p._MAX_DEPTH + 1)
        assert ctx.unparseable == 1

    def test_shell_and_python_option_operands_are_skipped(self) -> None:
        assert _prefixes(_row(["bash", "-o", "pipefail", "-c", "rg x"])) == {RG_TOKEN}
        assert _prefixes(_row(["python", "-W", "ignore", "-c", "x = ['rg']"])) == {RG_TOKEN}

    def test_bodyless_shell_and_python_heads_are_clean(self) -> None:
        assert p.findings(_row(["bash"])).messages == []
        assert p.findings(_row(["bash", "-c"])).messages == []
        assert p.findings(_row(["python", "-c"])).messages == []
        assert p.findings(_row(["git"])).messages == []
        assert p.findings(_row(["timeout"])).messages == []
        assert p.findings(_row([])).messages == []

    def test_live_corpus_shapes_verbatim(self) -> None:
        rg_shape = yaml.safe_load((DEPRECATED / "data-pipeline-excludes-control-module.yaml").read_text(encoding="utf-8"))
        live_shape = yaml.safe_load((DEPRECATED / "push-context-session-branch-guard.yaml").read_text(encoding="utf-8"))
        assert {m.split(" ", 1)[0] for m in p.findings(rg_shape).messages} == {RG_TOKEN}
        assert {m.split(" ", 1)[0] for m in p.findings(live_shape).messages} == {LIVE_TOKEN}

    def test_ungoverned_counter_creation_retired_shape(self) -> None:
        row = _sh("test -z \"$(rg -l 'ensure_entity_counters_table\\(' src/ scripts/ --glob '!*control_tables*' || true)\"")
        assert _prefixes(row) == {RG_TOKEN}


class TestAdmissionReport:
    def test_zero_candidates(self) -> None:
        failures, line = p.admission_report([])
        assert failures == []
        assert line.startswith("Portability (rec-4889): 0 added-or-modified command-slot row(s) examined, 0 refused, ")
        assert line.endswith("0 unparseable segment(s) failed open.")

    def test_clean_candidate(self) -> None:
        failures, line = p.admission_report([_sh("true")])
        assert failures == []
        assert "1 added-or-modified command-slot row(s) examined, 0 refused" in line

    def test_refused_candidate_names_check_id_and_rule(self) -> None:
        row = _sh("rg x") | {"check_id": "bad-one"}
        failures, line = p.admission_report([row, _row(["x"], "file_presence"), "junk"])  # type: ignore[list-item]
        assert len(failures) == 1
        assert failures[0].startswith("verification-registry portability: check_id=bad-one: RG:")
        assert "1 added-or-modified command-slot row(s) examined, 1 refused" in line

    def test_unparseable_count_printed(self) -> None:
        failures, line = p.admission_report([_sh("echo 'abc"), _row("rg x")])
        assert failures == []
        assert "2 added-or-modified command-slot row(s) examined, 0 refused, 2 unparseable segment(s) failed open." in line


class TestEdges:
    @pytest.mark.parametrize(
        "row",
        [
            _row(["bash", "-x", "-c", "rg x"]),
            _row(["python", "-B", "-c", "x = ['rg']"]),
            _sh('echo "$(echo \\"a\\"; rg x)"'),
            _sh("echo $(echo $(rg x))"),
            _sh('echo "a\\"b"; rg x'),
            _sh('echo "`rg x`"'),
            _sh("echo \\$HOME; rg x"),
            _py("x = ['rg', 'a', ]"),
        ],
        ids=[
            "bash-x-c",
            "python-B-c",
            "escaped-quote-in-subst",
            "nested-subst",
            "dq-escape",
            "backtick-in-dq",
            "escape",
            "trailing-comma",
        ],
    )
    def test_more_rg_shapes(self, row: dict) -> None:
        assert _prefixes(row) == {RG_TOKEN}

    @pytest.mark.parametrize(
        "body",
        ["echo $(rg x", "echo `rg x", 'echo "$(rg x"', 'echo "`rg x"', "echo \\"],
        ids=["open-subst", "open-backtick", "open-subst-in-dq", "open-backtick-in-dq", "trailing-backslash"],
    )
    def test_open_constructs_count_unparseable(self, body: str) -> None:
        found = p.findings(_sh(body))
        assert found.unparseable == 1
        assert found.messages == []

    @pytest.mark.parametrize(
        "body",
        [
            "import os; os.getenv(name)",
            'import os; os.getenv("\\x")',
            "import os; os.getenv(b'GITHUB_REF')",
            "x = [1, 2]",
            "x = ['rg' 'a']",
            "x = []",
            "x = ['a',\n)",
        ],
        ids=[
            "non-literal-key",
            "bad-escape",
            "bytes-key",
            "non-string-list",
            "adjacent-strings",
            "empty-list",
            "mismatched-bracket",
        ],
    )
    def test_non_matching_python_shapes_are_clean(self, body: str) -> None:
        assert p.findings(_py(body)).messages == []

    def test_list_cut_off_after_a_comma_is_not_a_list(self) -> None:
        toks = [
            t
            for t in tokenize.generate_tokens(io.StringIO("x = ['a', )").readline)
            if t.type not in {tokenize.NEWLINE, tokenize.ENDMARKER, tokenize.NL}
        ][:-1]
        assert toks[-1].string == ","
        assert p._string_lists(toks, 2) is None

    def test_python_without_c_and_nested_quoted_escape(self) -> None:
        assert p.findings(_row(["python", "-B"])).messages == []
        assert _prefixes(_sh('echo $(echo "a\\"b"; rg x)')) == {RG_TOKEN}
