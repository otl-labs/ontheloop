"""Planner secret-value-read isolation and Neon key handling (plan neon-key-out-of-pr-plans, rec-4618).

STATIC-TEXT STANDING CHECK (family-matches validate_convergence_writer_isolation.py): parses
terraform/personal's aws_iam_policy_document blocks -- resolving source_policy_documents the same way the
refresh-read coverage checks do -- plus the personal-root workflows and the terraform-init-retry composite,
and asserts the Neon key never reaches a PR-triggered plan:

  (1) every Allow statement in the planner's resolved policy whose actions can return a Secrets Manager value
      carries the reserved-session aws:userid condition, or covers only ALLOWED_UNCONDITIONED resources. A
      NotAction Allow fails closed.
  (2) exactly one aws_iam_role_policy and zero aws_iam_role_policy_attachment resources target the planner role.
  (3) the reserved session name is a role-session-name value in terraform-drift.yml only.
  (4) -refresh=false appears on no personal-root terraform plan, and every personal-root terraform plan or apply
      runs through `neon_api_key.sh run --` (the trusted plan wraps it in trusted_plan_env.sh and quiet_run.sh).
  (5) no aws_secretsmanager_secret_version data source exists under terraform/personal.
  (6) every job that runs `neon_api_key.sh run --` also uses the terraform-init-retry composite, which carries
      the neon_api_key_check.sh delegate step, and the delegate execs `neon_api_key.sh check`.
  (7) exactly one planner Allow grants GetSecretValue on both the Neon key and the catalog DSN, under the
      reserved-session condition (the secret-version type is TRANSITIVE in _read_coverage.py, so
      validate_ci_refresh_read_coverage cannot see this gap).

Credential-free: pure text and YAML parsing, no boto3 or terraform invocation.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

from scripts.checks import _common, registry
from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from scripts.checks.iam_tf import validate_secret_parameter_grants as _spg

# Only Benjamin can add to this list (plan neon-key-out-of-pr-plans). The tfvars secret stays unconditioned
# until plan C.
ALLOWED_UNCONDITIONED = frozenset(
    {
        "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-terraform-personal-tfvars-*",
        # Decision 237 cl.2: the terraform-personal tfvars parameter every plan reads.
        "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars",
    }
)
_NEON_KEY_ARN = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:neon-api-key-*"
_DSN_ARN = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ducklake-neon-catalog-dsn-*"
_VALUE_ACTIONS = ("secretsmanager:getsecretvalue", "secretsmanager:batchgetsecretvalue")
_RESERVED_LOCAL_MARKER = "local.convergence_writer_session_name"
_SESSION_LOCAL_RE = re.compile(r'convergence_writer_session_name\s*=\s*"([^"]+)"')
_PLANNER = "github_ci_planner"

_WORKFLOW_DIR = Path(".github") / "workflows"
_PERSONAL_WORKFLOWS = ("terraform-apply-sandbox.yml", "reconcile.yml", "terraform-drift.yml", "terraform-plan-trusted.yml")
_ACTION_DIR = Path(".github") / "actions" / "terraform-init-retry"
_INIT_RETRY_USES = "./.github/actions/terraform-init-retry"
_RUN_PREFIX_RE = re.compile(r"neon_api_key\.sh\"?\s+run\s+--")
_TF_CMD_RE = re.compile(
    r"^(?:\w+=\$\()?(?:[A-Za-z_]\w*=\S+\s+)*"
    r"(?:bash\s+\S*trusted_plan_env\.sh\"?\s+--\s+)?"
    r"(?:bash\s+\S*neon_api_key\.sh\"?\s+run\s+--\s+)?"
    r"(?:bash\s+\S*quiet_run\.sh\"?\s+\S+\s+--\s+)?"
    r"(?:python3\s+\S*stale_plan_signature\.py\"?\s+--wrap\s+--\s+)?"
    r"terraform\s+(plan|apply)\b"
)
_EFFECT_RE = re.compile(r'\beffect\s*=\s*"([^"]*)"')
_NOT_ACTIONS_RE = re.compile(r"\bnot_actions\s*=")
_NOT_RESOURCES_RE = re.compile(r"\bnot_resources\s*=")
_CONDITION_BLOCK_RE = re.compile(r"condition\s*\{")
_TEST_RE = re.compile(r'\btest\s*=\s*"([^"]*)"')
_VARIABLE_RE = re.compile(r'\bvariable\s*=\s*"([^"]*)"')
_VALUES_RE = re.compile(r"\bvalues\s*=\s*\[(?P<body>.*?)\]", re.DOTALL)
_ROLE_POLICY_RES_RE = re.compile(r'resource\s+"aws_iam_role_policy"\s+"(?P<name>\w+)"\s*\{')
_ATTACHMENT_RES_RE = re.compile(r'resource\s+"aws_iam_role_policy_attachment"\s+"(?P<name>\w+)"\s*\{')
_ROLE_REF_RE = re.compile(r"\brole\s*=\s*aws_iam_role\.(\w+)\b")
_SECRET_VERSION_DATA_RE = re.compile(r'^\s*data\s+"aws_secretsmanager_secret_version"', re.MULTILINE)


def _find_statements(body: str) -> list[dict]:
    statements = []
    for m in _vir._STATEMENT_BLOCK_RE.finditer(body):
        stmt_body = _vir._extract_block(body, m.end() - 1)
        sid_m = _vir._SID_RE.search(stmt_body)
        effect_m = _EFFECT_RE.search(stmt_body)
        actions_m = _vir._ACTIONS_RE.search(stmt_body)
        resources_m = _vir._RESOURCES_RE.search(stmt_body)
        conditions = []
        for cond_m in _CONDITION_BLOCK_RE.finditer(stmt_body):
            cond_body = _vir._extract_block(stmt_body, cond_m.end() - 1)
            test_m = _TEST_RE.search(cond_body)
            var_m = _VARIABLE_RE.search(cond_body)
            values_m = _VALUES_RE.search(cond_body)
            conditions.append(
                {
                    "test": test_m.group(1) if test_m else None,
                    "variable": var_m.group(1) if var_m else None,
                    "values_raw": values_m.group("body") if values_m else "",
                }
            )
        statements.append(
            {
                "sid": sid_m.group(1) if sid_m else None,
                "effect": effect_m.group(1) if effect_m else "Allow",
                "actions": _vir._QUOTED_RE.findall(actions_m.group("body")) if actions_m else [],
                "has_not_actions": bool(_NOT_ACTIONS_RE.search(stmt_body)),
                "has_not_resources": bool(_NOT_RESOURCES_RE.search(stmt_body)),
                "resources_raw": resources_m.group("body") if resources_m else None,
                "resources": _vir._QUOTED_RE.findall(resources_m.group("body")) if resources_m else [],
                "conditions": conditions,
            }
        )
    return statements


def _parse_documents(text: str) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for m in _vir._DATA_BLOCK_RE.finditer(text):
        body = _vir._extract_block(text, m.end() - 1)
        source_m = _vir._SOURCE_DOCS_RE.search(body)
        sources = _vir._SOURCE_DOC_REF_RE.findall(source_m.group("body")) if source_m else []
        docs[m.group("name")] = {"sources": sources, "statements": _find_statements(body)}
    return docs


def _strip_hcl_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//")))


def _can_return_value(stmt: dict) -> bool:
    if stmt["has_not_actions"]:
        return True
    return any(fnmatch.fnmatchcase(target, a.lower()) for a in stmt["actions"] for target in _VALUE_ACTIONS)


def _has_reserved_condition(stmt: dict) -> bool:
    return any(
        c["test"] == "StringLike" and c["variable"] == "aws:userid" and _RESERVED_LOCAL_MARKER in c["values_raw"]
        for c in stmt["conditions"]
    )


def _ssm_unconditioned_reads(allows: list[dict]) -> list[str]:
    """Sids of Allow statements with an SSM value verb reaching /secret/ (or unresolvable) without the drift condition."""
    bad: list[str] = []
    for s in allows:
        if not (s["has_not_actions"] or _spg.covers_value_verb(s["actions"])):
            continue
        listed = None if s["resources_raw"] is None else _spg.string_list(s["resources_raw"])
        if s["has_not_resources"] or listed is None:
            bad.append(f"{s['sid'] or '<no-sid>'} (resource expression is not a list of string literals)")
            continue
        by_path = s["has_not_actions"] or _spg.covers_by_path(s["actions"])
        reaching = [r for r in listed if _spg.reaches_secret(r, by_path)]
        if reaching and not _has_reserved_condition(s) and not set(reaching) <= ALLOWED_UNCONDITIONED:
            bad.append(s["sid"] or "<no-sid>")
    return bad


def _sids(statements: list[dict]) -> str:
    return ", ".join(s["sid"] or "<no-sid>" for s in statements)


def _check_policy(text: str, key: str, failed: list[str]) -> None:
    docs = _parse_documents(text)
    role_policy = _vir._parse_role_policy_map(text)
    doc_name = role_policy.get(_PLANNER)
    if not doc_name:
        failed.append(f"{key} could not resolve {_PLANNER}'s policy document under terraform/personal")
        return
    unresolved: list[str] = []
    statements = _vir._resolve_statements(doc_name, docs, unresolved=unresolved)
    if unresolved:
        failed.append(
            f"{key} {_PLANNER} references unresolvable policy document(s) {sorted(set(unresolved))!r} -- cannot "
            "determine its secret-value reads (Decision 129 clause 4: fail loud, never a silent pass)"
        )
        return
    allows = [s for s in statements if s["effect"] == "Allow"]

    bad = [
        s
        for s in allows
        if _can_return_value(s)
        and not _has_reserved_condition(s)
        and not (s["resources"] and set(s["resources"]) <= ALLOWED_UNCONDITIONED)
    ]
    if bad:
        failed.append(
            f"{key} {_PLANNER} has unconditioned Secrets Manager value read(s) outside the tfvars secret "
            f"(sid(s): {_sids(bad)}) -- PR sessions must not read the Neon key, DSN, PAT or inference keys; "
            "condition them on the reserved drift session (never widen ALLOWED_UNCONDITIONED)"
        )

    ssm_bad = _ssm_unconditioned_reads(allows)
    if ssm_bad:
        failed.append(
            f"{key} {_PLANNER} has unconditioned SSM value read(s) reaching parameter/secret/ outside the tfvars "
            f"parameter (sid(s): {', '.join(ssm_bad)}) -- condition them on the reserved drift session "
            "(Decision 237 cl.2; never widen ALLOWED_UNCONDITIONED)"
        )

    drift_reads = [
        s
        for s in allows
        if _has_reserved_condition(s)
        and _can_return_value(s)
        and {_NEON_KEY_ARN, _DSN_ARN} <= set(s["resources"])
        and any(a.lower() == "secretsmanager:getsecretvalue" for a in s["actions"])
    ]
    if len(drift_reads) != 1:
        failed.append(
            f"{key} expected exactly one {_PLANNER} Allow granting secretsmanager:GetSecretValue on both the "
            f"Neon key and the catalog DSN under the reserved-session aws:userid condition, found {len(drift_reads)} "
            "(without it the hourly drift run fails only after deploy)"
        )

    files = sorted(Path(_common.ROOT / _vir._PERSONAL_DIR_REL).glob("*.tf"))
    policies = attachments = 0
    for f in files:
        ftext = f.read_text(encoding="utf-8")
        for rx, kind in ((_ROLE_POLICY_RES_RE, "policy"), (_ATTACHMENT_RES_RE, "attachment")):
            for m in rx.finditer(ftext):
                body = _vir._extract_block(ftext, m.end() - 1)
                ref = _ROLE_REF_RE.search(body)
                if ref and ref.group(1) == _PLANNER:
                    if kind == "policy":
                        policies += 1
                    else:
                        attachments += 1
    if policies != 1 or attachments != 0:
        failed.append(
            f"{key} the planner role must have exactly one aws_iam_role_policy and no policy attachment "
            f"(found {policies} inline, {attachments} attachment) -- a second grant path would bypass the audit"
        )

    if _SECRET_VERSION_DATA_RE.search(_strip_hcl_comments(text)):
        failed.append(
            f'{key} a data "aws_secretsmanager_secret_version" block exists under terraform/personal -- '
            "the Neon key is an ephemeral input variable, never a data source (state would hold the value)"
        )


def _logical_commands(run: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for raw in run.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return [c for c in out if c]


def _check_command(name: str, job_id: str, cmd: str, m: re.Match[str], key: str, failed: list[str]) -> tuple[bool, bool]:
    """Check one terraform plan/apply command. Returns (wrapped, has_refresh_false)."""
    has_refresh_false = "-refresh=false" in cmd
    wrapped = bool(_RUN_PREFIX_RE.search(cmd))
    where = f"{key} {name}:{job_id}"
    if has_refresh_false:
        failed.append(f"{where} -refresh=false on a personal-root plan: {cmd[:90]}")
    if not wrapped:
        failed.append(f"{where} personal-root terraform {m.group(1)} is not run through neon_api_key.sh run --: {cmd[:90]}")
    return wrapped, has_refresh_false


def _check_job(name: str, job_id: str, job: dict, key: str, failed: list[str]) -> tuple[int, int]:
    steps = job.get("steps") or []
    examined = refresh_false = 0
    wrapped_here = False
    for step in steps:
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for cmd in _logical_commands(run):
            m = _TF_CMD_RE.search(cmd)
            if not m:
                continue
            examined += 1
            wrapped, rf = _check_command(name, job_id, cmd, m, key, failed)
            wrapped_here = wrapped_here or wrapped
            refresh_false += int(rf)
    if wrapped_here and not any(s.get("uses") == _INIT_RETRY_USES for s in steps):
        failed.append(f"{key} {name}:{job_id} runs neon_api_key.sh run -- but does not use terraform-init-retry")
    return examined, refresh_false


def _check_workflows(root: Path, session_name: str, key: str, failed: list[str]) -> int:
    wf_dir = root / _WORKFLOW_DIR
    reserved_users = [
        path.name
        for path in sorted(wf_dir.glob("*.yml"))
        if re.search(rf"role-session-name:\s*{re.escape(session_name)}\b", path.read_text(encoding="utf-8"))
    ]
    if reserved_users != ["terraform-drift.yml"]:
        failed.append(
            f"{key} the reserved session name {session_name!r} must be a role-session-name value in "
            f"terraform-drift.yml only (found {reserved_users}) -- a PR-reachable workflow would hold the key"
        )

    examined = refresh_false = 0
    for name in _PERSONAL_WORKFLOWS:
        path = wf_dir / name
        if not path.is_file():
            failed.append(f"{key} {name} is missing")
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            e, r = _check_job(name, job_id, job, key, failed)
            examined += e
            refresh_false += r
    if refresh_false != 0:
        failed.append(f"{key} -refresh=false must appear on no personal-root plan command, found {refresh_false}")
    return examined


def _check_action(root: Path, key: str, failed: list[str]) -> None:
    action = root / _ACTION_DIR / "action.yml"
    delegate = root / _ACTION_DIR / "neon_api_key_check.sh"
    if not action.is_file() or "neon_api_key_check.sh" not in action.read_text(encoding="utf-8"):
        failed.append(f"{key} terraform-init-retry/action.yml has no neon_api_key_check.sh step")
    if not delegate.is_file() or not re.search(
        r'^exec bash "\$GITHUB_WORKSPACE/scripts/ci/neon_api_key\.sh" check$',
        delegate.read_text(encoding="utf-8"),
        re.MULTILINE,
    ):
        failed.append(f"{key} neon_api_key_check.sh does not exec neon_api_key.sh check")


@registry.register("validate_planner_secret_value_reads", owner="platform")
def validate_planner_secret_value_reads(failed: list[str]) -> None:
    """See module docstring for assertions (1)-(7)."""
    print("\n=== Planner secret-value reads and Neon key handling (rec-4618) ===")
    key = "planner-secret-value-reads:"
    root = _common.ROOT
    personal_dir = root / _vir._PERSONAL_DIR_REL
    text = _vir._read_root_text(personal_dir)
    if not text:
        registry.skipped(f"no *.tf file readable under {personal_dir}")
        failed.append(f"{key} cannot read any *.tf file under {personal_dir}")
        return
    session_m = _SESSION_LOCAL_RE.search(text)
    if not session_m:
        failed.append(f"{key} local convergence_writer_session_name not found under {personal_dir}")
        return

    before = len(failed)
    _check_policy(text, key, failed)
    examined = _check_workflows(root, session_m.group(1), key, failed)
    _check_action(root, key, failed)
    registry.examined(examined, unit="terraform_commands")
    if len(failed) == before:
        print("  PASS: planner holds no unconditioned value read; Neon key reaches trusted terraform runs only.")
    else:
        for finding in failed[before:]:
            print(f"  FAIL: {finding}")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_planner_secret_value_reads(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
