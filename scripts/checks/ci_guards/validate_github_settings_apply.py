"""terraform/github CI apply fence (PLAN-terraform-github-gated-apply, Decision 248).

The apply App key, the two bootstrap roles and the apply script are reachable only from main's
terraform-github-apply.yml, behind one operator approval. Each predicate takes parsed or raw input so the red cases run
on synthetic data; the registered check loads the real tree.

  (a) no workflow or composite action except WORKFLOW names either role, the apply App entry or the apply script
  (g) each role's trust is one Allow statement, StringEquals only, on aud, one Environment subject per repo segment
      (GATE_ENV for the apply role, tf-trusted-plan for the preview role) and job_workflow_ref exactly the one main ref
  (h) the apply policy grants exactly the key read and terraform/github's state and lock, with no iam: action; the
      preview policy is one state GetObject; both carry the 10,240 B precondition; the key's resource policy is one Deny
      of every non-metadata action to all but four principals, ordered after AdminOps, whose
      GithubApplyKeyResourcePolicy grants exactly the two resource-policy verbs on the entry's exact-suffix ARN
"""

from __future__ import annotations

import re
from typing import Any

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import _blocks

WORKFLOW = "terraform-github-apply.yml"
GATE_ENV = "tf-gated-apply"
PREVIEW_ENV = "tf-trusted-plan"
APPLY_ROLE = "github-settings-apply"
PREVIEW_ROLE = "github-settings-preview"
# Identifiers say "entry", not "secret": CodeQL py/clear-text-logging-sensitive-data treats a secret-named value as
# sensitive, and these are a Secrets Manager entry name and a Terraform reference, never secret material.
APP_ENTRY = "agent-platform-terraform-github-apply-app"  # pragma: allowlist secret -- an entry name, not a value
APPLY_SCRIPT = "scripts/ci/terraform_github_apply.sh"
MAIN_REF = f"otl-labs/ontheloop/.github/workflows/{WORKFLOW}@refs/heads/main"
SETTINGS_TF = "github_settings_apply.tf"
ADMIN_TF = "platform_admin_policies.tf"

_STATE = "arn:aws:s3:::agent-platform-data-lake/tfstate/github/terraform.tfstate"
_ENTRY_REF = "data.aws_secretsmanager_secret.github_apply_app.arn"  # pragma: allowlist secret
_OIDC = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
_OIDC_HOST = "token.actions.githubusercontent.com"
_ADMIN_KEY_ARN = f"arn:aws:secretsmanager:${{var.aws_region}}:${{var.account_id}}:secret:{APP_ENTRY}-??????"
_ADMIN_SID = "GithubApplyKeyResourcePolicy"
_METADATA_VERBS = {"secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy", "secretsmanager:ListSecretVersionIds"}
_EXCEPTED = {
    f"arn:aws:iam::${{var.account_id}}:{p}"
    for p in (f"role/{APPLY_ROLE}", "role/PlatformAdmin", "user/platform-breakglass", "root")
}
_APPLY_GRANTS = {
    ("AppKeyRead", frozenset({"secretsmanager:GetSecretValue"}), frozenset({_ENTRY_REF})),
    ("TfstateGithubObject", frozenset({"s3:GetObject", "s3:PutObject"}), frozenset({_STATE})),
    ("TfstateGithubLock", frozenset({"s3:GetObject", "s3:PutObject", "s3:DeleteObject"}), frozenset({_STATE + ".tflock"})),
}
_PREVIEW_GRANTS = {("TfstateGithubRead", frozenset({"s3:GetObject"}), frozenset({_STATE}))}
_SUB_RE = re.compile(r'^flatten\(\[for repo in local\.github_repos : \["repo:\$\{repo\}:environment:([^"]+)"\]\]\)$')
_INTERP_RE = re.compile(r"\$\{local\.(\w+)\}")


class Expr(str):  # noqa: SLOT000 -- str subtypes cannot take a non-empty __slots__, and .src is set per instance
    """A non-literal HCL expression: whitespace-normalised text, with the original source in .src."""

    src = ""


class _Parser:
    """Literal subset of HCL: objects, blocks, lists, strings, numbers and bools; anything else is an Expr."""

    def __init__(self, text: str) -> None:
        self.s, self.i = text, 0

    def _skip(self, newlines: bool = True) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r" or (newlines and c in "\n,"):
                self.i += 1
            elif c == "#" or self.s.startswith("//", self.i):
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                return

    def _string(self) -> str:
        start, depth, self.i = self.i, 0, self.i + 1
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if self.s.startswith("${", self.i):
                depth += 1
                self.i += 1
            elif c == "}" and depth:
                depth -= 1
            elif c == '"' and not depth:
                self.i += 1
                return self.s[start + 1 : self.i - 1]
            self.i += 1
        raise ValueError("unterminated string")

    def _raw(self) -> Expr:
        start, depth = self.i, 0
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == '"':
                self._string()
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if not depth:
                    break
                depth -= 1
            elif not depth and (c in ",\n" or c == "#"):
                break
            self.i += 1
        text = re.sub(r"\s+", " ", self.s[start : self.i]).strip()
        expr = Expr(re.sub(r"([(\[]) ", r"\1", re.sub(r",? ([)\]])", r"\1", text)))
        expr.src = self.s[start : self.i].strip()
        return expr

    def value(self) -> Any:
        self._skip(newlines=False)
        c = self.s[self.i] if self.i < len(self.s) else ""
        if c == "{":
            return self.body("}")
        if c == "[":
            self.i += 1
            items: list[Any] = []
            while True:
                self._skip()
                if self.s[self.i] == "]":
                    self.i += 1
                    return items
                items.append(self.value())
        if c == '"':
            text = self._string()
            self._skip(newlines=False)
            return text if self.i >= len(self.s) or self.s[self.i] in ",\n]}#" else Expr(f'"{text}"' + self._raw())
        raw = self._raw()
        return {"true": True, "false": False}.get(raw, int(raw) if raw.isdigit() else raw)

    def body(self, close: str) -> dict[str, Any]:
        self.i += 1
        out: dict[str, Any] = {}
        while True:
            self._skip()
            if self.i >= len(self.s) or self.s[self.i] == close:
                self.i += 1
                return out
            if self.s[self.i] == '"':
                key = self._string()
            else:
                m = re.compile(r"[\w.-]+").match(self.s, self.i)
                if m is None:
                    raise ValueError(f"unexpected {self.s[self.i]!r} at {self.i}")
                key, self.i = m.group(0), m.end()
            self._skip(newlines=False)
            if self.s[self.i] in "=:":
                self.i += 1
                out[key] = self.value()
            else:
                while self.s[self.i] == '"':
                    self._string()
                    self._skip(newlines=False)
                out.setdefault(key, []).append(self.body("}"))


def parse(text: str) -> Any:
    return _Parser(text).value()


def _jsonencode(value: Any, local: dict[str, Any]) -> Any:
    if isinstance(value, Expr) and value.startswith("local."):
        value = local.get(value[6:])
    if isinstance(value, Expr) and value.startswith("jsonencode(") and value.endswith(")"):
        return parse(value.src[len("jsonencode(") : -1])
    return None


def _locals(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "//")))
    for m in re.finditer(r"^\s*locals\s*\{", stripped, re.M):
        p = _Parser(stripped)
        p.i = m.end() - 1
        out.update(p.body("}"))
    return out


def _resource(text: str, rtype: str, name: str) -> dict[str, Any] | None:
    block = _blocks(text, rtype).get(name)
    return parse(block) if block else None


def _resolve(value: Any, local: dict[str, Any]) -> Any:
    if isinstance(value, Expr) and value.startswith("local.") and value[6:] in local:
        return _resolve(local[value[6:]], local)
    if isinstance(value, str) and not isinstance(value, Expr):
        return _INTERP_RE.sub(lambda m: str(local.get(m.group(1), m.group(0))), value)
    return value


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def check_names(files: dict[str, str]) -> list[str]:
    names = (APPLY_ROLE, PREVIEW_ROLE, APP_ENTRY, APPLY_SCRIPT)
    return [
        f"a: {path} names {n}"
        for path, text in sorted(files.items())
        if path.rsplit("/", 1)[-1] != WORKFLOW or not path.startswith(".github/workflows/")
        for n in names
        if n in text
    ]


def check_trust(files: dict[str, str]) -> list[str]:
    text = files.get(SETTINGS_TF, "")
    local = _locals(text)
    out = []
    if local.get("github_settings_workflow_refs") != [MAIN_REF]:
        out.append(f"g: local.github_settings_workflow_refs must be exactly [{MAIN_REF}]")
    for res, role, env in (
        ("github_settings_apply", APPLY_ROLE, GATE_ENV),
        ("github_settings_preview", PREVIEW_ROLE, PREVIEW_ENV),
    ):
        block = _resource(text, "aws_iam_role", res)
        if block is None:
            out.append(f"g: aws_iam_role.{res} is missing")
            continue
        if block.get("name") != role or block.get("permissions_boundary") != "aws_iam_policy.github_ci_apply_boundary.arn":
            out.append(f"g: aws_iam_role.{res} must be named {role} and carry the CI apply boundary")
        doc = _jsonencode(block.get("assume_role_policy"), local) or {}
        stmts = _as_list(doc.get("Statement", []))
        s = stmts[0] if len(stmts) == 1 and isinstance(stmts[0], dict) else {}
        cond = s.get("Condition", {})
        eq = cond.get("StringEquals", {}) if isinstance(cond, dict) else {}
        sub = _SUB_RE.match(str(eq.get(f"{_OIDC_HOST}:sub", "")))
        ok = (
            set(s) == {"Effect", "Principal", "Action", "Condition"}
            and s.get("Effect") == "Allow"
            and s.get("Principal") == {"Federated": _OIDC}
            and s.get("Action") == "sts:AssumeRoleWithWebIdentity"
            and list(cond) == ["StringEquals"]
            and set(eq) == {f"{_OIDC_HOST}:aud", f"{_OIDC_HOST}:sub", f"{_OIDC_HOST}:job_workflow_ref"}
            and eq.get(f"{_OIDC_HOST}:aud") == "sts.amazonaws.com"
            and sub is not None
            and _resolve(sub.group(1), local) == env
            and eq.get(f"{_OIDC_HOST}:job_workflow_ref") == "local.github_settings_workflow_refs"
        )
        if not ok:
            out.append(f"g: {role} trust must be one StringEquals statement on aud, environment:{env} and the main ref")
    return out


def _grants(doc: Any, local: dict[str, Any]) -> tuple[set[tuple[str, frozenset[str], frozenset[str]]], list[str]]:
    grants, bad = set(), []
    for s in _as_list((doc or {}).get("Statement", [])):
        if not isinstance(s, dict) or set(s) != {"Sid", "Effect", "Action", "Resource"} or s.get("Effect") != "Allow":
            bad.append(str(s.get("Sid") if isinstance(s, dict) else s))
            continue
        actions = frozenset(str(a) for a in _as_list(s["Action"]))
        if any(a.lower().startswith("iam:") or a == "*" for a in actions):
            bad.append(f"{s['Sid']} (iam)")
        grants.add((str(s["Sid"]), actions, frozenset(str(_resolve(r, local)) for r in _as_list(s["Resource"]))))
    return grants, bad


def _precondition(block: dict[str, Any], local_name: str) -> bool:
    want = f"length(jsonencode(jsondecode(local.{local_name}))) <= 10240"
    return any(
        str(pre.get("condition")) == want for life in block.get("lifecycle", []) for pre in life.get("precondition", [])
    )


def check_policies(files: dict[str, str]) -> list[str]:
    text = files.get(SETTINGS_TF, "")
    local = _locals(text)
    out = []
    for res, want in (("github_settings_apply", _APPLY_GRANTS), ("github_settings_preview", _PREVIEW_GRANTS)):
        block = _resource(text, "aws_iam_role_policy", res) or {}
        ref = str(block.get("policy", ""))
        grants, bad = _grants(_jsonencode(block.get("policy"), local), local)
        if block.get("role") != f"aws_iam_role.{res}.id" or bad or grants != want:
            out.append(f"h: aws_iam_role_policy.{res} must grant exactly {sorted(g[0] for g in want)} (bad: {bad})")
        if not ref.startswith("local.") or not _precondition(block, ref[6:]):
            out.append(f"h: aws_iam_role_policy.{res} lacks its 10,240 B precondition")
    entry = re.search(r'data\s+"aws_secretsmanager_secret"\s+"github_apply_app"\s*\{\s*name\s*=\s*"([^"]+)"\s*\}', text)
    if not entry or entry.group(1) != APP_ENTRY:
        out.append(f"h: data.aws_secretsmanager_secret.github_apply_app must name {APP_ENTRY}")
    out += _key_policy(text, local)
    out += _admin_grant(files.get(ADMIN_TF, ""))
    return out


def _key_policy(text: str, local: dict[str, Any]) -> list[str]:
    block = _resource(text, "aws_secretsmanager_secret_policy", "github_settings_apply_key") or {}
    stmts = _as_list((_jsonencode(block.get("policy"), local) or {}).get("Statement", []))
    s = stmts[0] if len(stmts) == 1 and isinstance(stmts[0], dict) else {}
    cond = s.get("Condition", {})
    ok = (
        block.get("secret_arn") == _ENTRY_REF
        and block.get("block_public_policy") is True
        and "aws_iam_role_policy.platform_admin_ops" in _as_list(block.get("depends_on"))
        and set(s) - {"Sid"} == {"Effect", "Principal", "NotAction", "Resource", "Condition"}
        and s.get("Effect") == "Deny"
        and s.get("Principal") in ("*", {"AWS": "*"})
        and set(_as_list(s.get("NotAction"))) == _METADATA_VERBS
        and s.get("Resource") == "*"
        and isinstance(cond, dict)
        and list(cond) == ["ArnNotLike"]
        and list(cond["ArnNotLike"]) == ["aws:PrincipalArn"]
        and set(_as_list(cond["ArnNotLike"]["aws:PrincipalArn"])) == _EXCEPTED
    )
    return (
        [] if ok else ["h: the apply App key policy must be one Deny of all but metadata verbs to all but the four principals"]
    )


def _admin_grant(text: str) -> list[str]:
    doc = _jsonencode(Expr("local.platform_admin_ops_policy_json"), _locals(text)) or {}
    hits = [s for s in _as_list(doc.get("Statement", [])) if isinstance(s, dict) and s.get("Sid") == _ADMIN_SID]
    want = {
        "Sid": _ADMIN_SID,
        "Effect": "Allow",
        "Action": ["secretsmanager:PutResourcePolicy", "secretsmanager:DeleteResourcePolicy"],
        "Resource": _ADMIN_KEY_ARN,
    }
    if (
        len(hits) != 1
        or set(hits[0]) != set(want)
        or any(sorted(_as_list(hits[0][k])) != sorted(_as_list(v)) for k, v in want.items())
    ):
        return [f"h: AdminOps {_ADMIN_SID} must grant exactly the two resource-policy verbs on the entry's exact-suffix ARN"]
    return []


def run_all(names: dict[str, str], bootstrap: dict[str, str]) -> list[list[str]]:
    return [check_names(names), check_trust(bootstrap), check_policies(bootstrap)]


def _load_tree() -> tuple[dict[str, str], dict[str, str]]:
    root = _common.ROOT
    names = {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for pattern in (".github/workflows/*.y*ml", ".github/actions/**/action.y*ml")
        for p in sorted(root.glob(pattern))
    }
    bootstrap = {n: (root / "terraform" / "bootstrap" / n).read_text(encoding="utf-8") for n in (SETTINGS_TF, ADMIN_TF)}
    return names, bootstrap


@registry.register("validate_github_settings_apply", owner="platform")
def validate_github_settings_apply(failed: list[str]) -> None:
    """Assert the terraform/github CI apply fence. See module docstring."""
    print("\n=== terraform/github CI apply fence ===")
    try:
        names, bootstrap = _load_tree()
        groups = run_all(names, bootstrap)
    except (OSError, UnicodeDecodeError, ValueError, AttributeError, IndexError, TypeError) as exc:
        print(f"  FAIL: could not load or parse the inputs: {exc}")
        failed.append("github-settings-apply: inputs unreadable, so no predicate ran")
        registry.examined(0, unit="invariants")
        return
    violations = [x for group in groups for x in group]
    for violation in violations:
        print(f"  FAIL: {violation}")
        failed.append(f"github-settings-apply: {violation}")
    registry.examined(len(groups), unit="invariants")
    if not violations:
        print("  PASS: predicates (a), (g), (h)")
