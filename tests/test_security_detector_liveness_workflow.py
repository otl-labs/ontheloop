"""security-detector-liveness.yml is a read-only probe under the CI branch identity (rec-4044, Decision 202).

The workflow runs hourly (and on dispatch) as agent-platform-github-ci-branch -- never the planner or
the reserved convergence-writer session -- with the Decision 172 fail-loud outcome guard, the venv
install, and one probe call whose --bootstrap-window flag rides only on the
SECURITY_DETECTOR_PROVISIONED condition. It never touches terraform, S3 or the convergence record.
The CI branch role grants exactly the probe's four reads.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]


# --- String- and comment-aware HCL helpers (patterns are string literals full of braces, so a
# naive brace count would split mid-string; comments are blanked so a commented header never matches).


def _scan(text: str, blank_strings: bool) -> str:
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#" or (c == "/" and text[i + 1 : i + 2] == "/"):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                step = 2 if text[i] == "\\" else 1
                if blank_strings:
                    for j in range(i, min(i + step, n)):
                        if text[j] != "\n":
                            out[j] = " "
                i += step
            i += 1
        else:
            i += 1
    return "".join(out)


@lru_cache(maxsize=256)
def _mask(text: str) -> str:
    """Equal-length copy with string contents and #/// comments blanked (quotes and newlines kept)."""
    return _scan(text, blank_strings=True)


@lru_cache(maxsize=256)
def _strip_comments(text: str) -> str:
    """Equal-length copy with only comments blanked; string contents intact."""
    return _scan(text, blank_strings=False)


def _block_body(text: str, open_brace_idx: int) -> str:
    """Body between the '{' at open_brace_idx and its match, computed on masked text."""
    masked = _mask(text)
    depth = 0
    for i in range(open_brace_idx, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : i]
    raise ValueError(f"unbalanced braces at {open_brace_idx}")


WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "security-detector-liveness.yml"
OIDC_CI_ROLES = _REPO_ROOT / "terraform" / "personal" / "oidc_ci_roles.tf"
_FORBIDDEN = (
    "terraform",
    "CONVERGENCE_KEY",
    "s3 cp",
    "aws s3",
    "materialise-tfvars",
    "tf-drift-convergence-writer",
    "planner",
)
_GATED_FLAG_RE = re.compile(r"vars\.SECURITY_DETECTOR_PROVISIONED\s*!=\s*'true'\s*&&\s*'--bootstrap-window'")
PROBE_READS = {
    ("cloudwatch:DescribeAlarms", '"*"'),
    ("cloudwatch:DescribeAlarmHistory", '"arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:platform-security-*"'),
    ("sns:ListSubscriptionsByTopic", "aws_sns_topic.alerts.arn"),
    ("sns:GetTopicAttributes", "aws_sns_topic.alerts.arn"),
}


def _steps(doc: dict) -> list[dict]:
    jobs = doc.get("jobs") or {}
    return [s for job in jobs.values() for s in (job.get("steps") or []) if isinstance(s, dict)]


def _trigger_problems(doc: dict) -> list[str]:
    problems: list[str] = []
    trigger = doc.get("on", doc.get(True)) or {}
    crons = [c.get("cron", "") for c in trigger.get("schedule") or []]
    if not crons or not all(re.fullmatch(r"\d{1,2} \* \* \* \*", c) for c in crons):
        problems.append(f"not scheduled hourly: {crons}")
    if "workflow_dispatch" not in trigger:
        problems.append("not dispatchable")
    jobs = doc.get("jobs") or {}
    if len(jobs) != 1:
        problems.append(f"expected one job, found {sorted(jobs)}")
    problems += [
        f"job {n} does not declare actions: read"
        for n, j in jobs.items()
        if (j.get("permissions") or {}).get("actions") != "read"
    ]
    return problems


def _assume_problems(steps: list[dict]) -> list[str]:
    assumes = [s for s in steps if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials")]
    with_ = (assumes[0].get("with") or {}) if len(assumes) == 1 else {}
    if not str(with_.get("role-to-assume", "")).endswith("role/agent-platform-github-ci-branch"):
        return ["does not assume exactly agent-platform-github-ci-branch"]
    if "role-session-name" in with_:
        return ["sets a role-session-name"]
    sid = assumes[0].get("id")
    guard = [
        s
        for s in steps
        if re.search(rf"steps\.{sid}\.outcome\s*!=\s*'success'", str(s.get("if", ""))) and "exit 1" in str(s.get("run", ""))
    ]
    if not sid or assumes[0].get("continue-on-error") is not True or not guard:
        return ["assume step lacks the fail-loud outcome guard"]
    return []


def workflow_problems(text: str) -> list[str]:
    doc: dict[Any, Any] = yaml.safe_load(text)
    steps = _steps(doc)
    problems = _trigger_problems(doc) + _assume_problems(steps)
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    if "pip install -r requirements.txt" not in runs:
        problems.append("does not install the venv")
    if not re.search(r"-m scripts\.ci\.security_detector_probe --file-on-transition", runs):
        problems.append("does not run the probe with --file-on-transition")
    body = yaml.safe_dump(doc.get("jobs"), sort_keys=True)
    problems += [f"mentions {word}" for word in _FORBIDDEN if word.lower() in body.lower()]
    flag_lines = [line for line in text.splitlines() if "--bootstrap-window" in line and not line.lstrip().startswith("#")]
    if not flag_lines or not all(_GATED_FLAG_RE.search(line) for line in flag_lines):
        problems.append("--bootstrap-window is not gated on SECURITY_DETECTOR_PROVISIONED")
    return problems


def grant_problems(tf_text: str) -> list[str]:
    m = re.search(r'data\s+"aws_iam_policy_document"\s+"github_ci_branch"\s*\{', _strip_comments(tf_text))
    if m is None:
        return ["github_ci_branch policy document not found"]
    body = _block_body(tf_text, m.end() - 1)
    pairs: set[tuple[str, str]] = set()
    sids: list[str] = []
    for sm in re.finditer(r"\bstatement\s*\{", _strip_comments(body)):
        stmt = _strip_comments(_block_body(body, sm.end() - 1))
        sid = (re.search(r'sid\s*=\s*"(\w+)"', stmt) or [None, ""])[1]
        if not sid.startswith("SecurityDetectorLiveness"):
            continue
        sids.append(sid)
        if (re.search(r'effect\s*=\s*"(\w+)"', stmt) or [None, "Allow"])[1] != "Allow":
            return [f"{sid} is not an Allow"]
        actions = re.findall(r'"([^"]+)"', (re.search(r"\bactions\s*=\s*\[(.*?)\]", stmt, re.S) or [None, ""])[1])
        resources = [r.strip() for r in (re.search(r"\bresources\s*=\s*\[(.*?)\]", stmt, re.S) or [None, ""])[1].split(",")]
        pairs |= {(a, r) for a in actions for r in resources if r}
    problems = [] if "SecurityDetectorLivenessRead" in sids else ["no SecurityDetectorLivenessRead Sid"]
    if pairs != PROBE_READS:
        problems.append(f"probe grant {sorted(pairs)} != the four probe reads")
    return problems


def _wf() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _sub(text: str, old: str, new: str) -> str:
    assert old in text, f"mutation anchor {old!r} not found"
    return text.replace(old, new, 1)


_WF_REDS: dict[str, Callable[[str], str]] = {
    "terraform step": lambda t: t + "      - name: plan\n        run: terraform plan\n",
    "convergence record read": lambda t: (
        t + "      - name: read\n        run: aws s3 cp s3://bucket/convergence/personal/sandbox.json -\n"
    ),
    "missing outcome guard": lambda t: _sub(t, "if: steps.oidc_probe.outcome != 'success'", "if: always()"),
    "planner role": lambda t: _sub(t, "role/agent-platform-github-ci-branch", "role/agent-platform-github-ci-planner"),
    "reserved session name": lambda t: _sub(
        t,
        "aws-region: ${{ env.AWS_REGION }}\n\n      - name: Fail",
        "aws-region: ${{ env.AWS_REGION }}\n          role-session-name: tf-drift-convergence-writer\n\n      - name: Fail",
    ),
    "unconditional bootstrap window": lambda t: _sub(
        t, "--file-on-transition $BOOTSTRAP_FLAG", "--file-on-transition --bootstrap-window"
    ),
    "no actions read": lambda t: _sub(t, "      actions: read ", "      pull-requests: read "),
    "no schedule": lambda t: _sub(t, "  schedule:\n    - cron: '47 * * * *'\n", ""),
}


class TestSecurityDetectorLivenessWorkflow:
    def test_workflow_is_a_read_only_probe(self) -> None:
        assert workflow_problems(_wf()) == []

    def test_ci_branch_grants_exactly_the_probe_reads(self) -> None:
        assert grant_problems(OIDC_CI_ROLES.read_text(encoding="utf-8")) == []

    @pytest.mark.parametrize("label", sorted(_WF_REDS))
    def test_workflow_red_case(self, label: str) -> None:
        assert workflow_problems(_WF_REDS[label](_wf())) != [], f"red case {label!r} was not detected"

    def test_missing_grant_red_case(self) -> None:
        text = OIDC_CI_ROLES.read_text(encoding="utf-8")
        assert grant_problems(_sub(text, 'sid       = "SecurityDetectorLivenessRead"', 'sid       = "Other"')) != []
        assert grant_problems(_sub(text, '"sns:ListSubscriptionsByTopic", "sns:GetTopicAttributes"', '"sns:*"')) != []

    def test_workflow_path_is_the_probe_constant(self) -> None:
        from scripts.ci import security_detector_probe as probe

        assert Path(WORKFLOW).name == probe.WORKFLOW_FILE
