#!/usr/bin/env python3
"""Static screen of a Terraform root, run before any credential exists (Decision 238 cl.3, cl.4).

Run by path with `python3 -I`; the repo root is appended (never inserted at 0) so the stdlib always wins. Pure
functions over {relative path: text} so tests run on synthetic trees, plus two CLI verbs:

  screen --root DIR        prints PASS, or one "REFUSED <rule> <file>:<line>" line per finding; exit 1 on a refusal.
  backstop --plan-json F   after plan, checks Terraform's own parsed configuration; prints only BACKSTOP_OK or
                           "BACKSTOP_REFUSED <rule>" (no address, no expression).

Policy constants below are code-owned. The screen reads only public PR text, so printing locations is safe.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(_ROOT))

from scripts.ci import terraform_hcl_lexer as lexer  # noqa: E402
from scripts.ci.terraform_hcl_lexer import LexRefusal, Token  # noqa: E402


def _package_resolves_under_root() -> bool:
    return Path(lexer.__file__).resolve().is_relative_to(_ROOT)


MAIN_ONLY_FILES = frozenset({"providers.tf", ".terraform.lock.hcl", "backend-sandbox.hcl"})
DOC_FILES = frozenset({"README.md", "CLAUDE.md"})
ALLOWED_DATA_TYPES = frozenset({"aws_iam_policy_document"})
ALLOWED_RESOURCE_TYPES = frozenset(
    {
        "aws_cloudwatch_event_rule",
        "aws_cloudwatch_event_target",
        "aws_cloudwatch_log_group",
        "aws_cloudwatch_metric_alarm",
        "aws_dynamodb_table",
        "aws_iam_openid_connect_provider",
        "aws_iam_role",
        "aws_iam_role_policy",
        "aws_lambda_function",
        "aws_lambda_function_url",
        "aws_lambda_layer_version",
        "aws_lambda_permission",
        "aws_s3_bucket",
        "aws_s3_bucket_lifecycle_configuration",
        "aws_s3_bucket_notification",
        "aws_s3_bucket_policy",
        "aws_s3_bucket_public_access_block",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_s3_bucket_versioning",
        "aws_secretsmanager_secret",
        "aws_secretsmanager_secret_version",
        "aws_sns_topic",
        "aws_sns_topic_subscription",
        "aws_ssm_parameter",
        "neon_database",
        "neon_project",
        "neon_role",
    }
)
ALLOWED_FUNCTIONS = frozenset(
    {"jsonencode", "jsondecode", "yamldecode", "flatten", "try", "can", "length", "regex", "format", "ceil", "file"}
)
VALIDATION_ONLY_FUNCTIONS = frozenset({"regex", "can"})
ALLOWED_FILE_PATHS = frozenset(
    {
        "${path.module}/../../config/feature_flags.yaml",
        "${path.module}/../../config/lambda/ducklake/version.yaml",
    }
)
TOP_LEVEL_BLOCKS = frozenset({"resource", "data", "locals", "variable", "output", "moved", "removed"})
_KEYWORDS = frozenset({"for", "in", "if", "else", "endif", "endfor"})
_SHAPE_OPERATORS = frozenset({"?", "==", "!=", "<", ">", "<=", ">=", "&&", "||", "!"})
_CONDITION_CONTEXTS = (("lifecycle", "precondition"), ("lifecycle", "postcondition"), ("variable", "validation"))
_FILE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
_LITERAL_IDENTS = frozenset({"true", "false", "null"})

Finding = tuple[str, str, int]  # rule, file, line


def _significant(tokens: list[Token]) -> list[Token]:
    return [t for t in tokens if t.kind != "nl"]


def _block_name_at(tokens: list[Token], open_idx: int) -> str | None:
    """Name of the block opened by tokens[open_idx] ('{'), or None for an object constructor."""
    j = open_idx - 1
    header: list[Token] = []
    while j >= 0 and tokens[j].kind != "nl" and not (tokens[j].kind == "punct" and tokens[j].text in "{}"):
        header.append(tokens[j])
        j -= 1
    header.reverse()
    if header and header[0].kind == "ident" and all(t.kind in ("ident", "str") for t in header):
        return header[0].text
    return None


def _walk(tokens: list[Token], stack: tuple[str, ...]) -> Any:
    """Yield (token, block stack) for every token, descending into interpolations with the parent's stack."""
    stack_list = list(stack)
    for idx, tok in enumerate(tokens):
        if tok.kind == "punct" and tok.text == "{":
            name = _block_name_at(tokens, idx)
            stack_list.append(name or "")
        yield tok, tuple(stack_list)
        for sub in tok.subs:
            yield from _walk(sub, tuple(stack_list))
        if tok.kind == "punct" and tok.text == "}" and stack_list:
            stack_list.pop()


def _in_condition(stack: tuple[str, ...]) -> bool:
    return any(stack[-len(c) :] == c for c in _CONDITION_CONTEXTS if len(stack) >= len(c))


def _file_rules(name: str, text: str) -> list[Finding]:
    try:
        tokens = lexer.lex(text)
    except LexRefusal as exc:
        return [(exc.rule, name, exc.line)]
    walked = list(_walk(tokens, ()))
    out = _top_level(name, tokens) + _shape_and_function_rules(name, walked) + _validation_function_rules(name, walked)
    for group in [tokens] + [sub for tok in tokens for sub in tok.subs]:
        out += _call_rules(name, _significant(group))
    return out


def _block_type_finding(name: str, sig: list[Token], i: int) -> list[Finding]:
    tok = sig[i]
    labels: list[str] = []
    for nxt in sig[i + 1 :]:
        if nxt.kind != "str":
            break
        labels.append(nxt.text)
    if not labels:
        return []
    if tok.text == "resource" and labels[0] not in ALLOWED_RESOURCE_TYPES:
        return [("resource_type", name, tok.line)]
    if tok.text == "data" and labels[0] not in ALLOWED_DATA_TYPES:
        return [("data_type", name, tok.line)]
    return []


def _top_level_token(name: str, sig: list[Token], i: int) -> list[Finding]:
    tok = sig[i]
    prev = sig[i - 1] if i else None
    if tok.kind == "str" and prev is not None and prev.kind in ("ident", "str"):
        return []
    starts_block = prev is None or (prev.kind == "punct" and prev.text == "}")
    if tok.kind != "ident" or not starts_block:
        return [("unclassified_token", name, tok.line)]
    if tok.text not in TOP_LEVEL_BLOCKS:
        return [("toplevel_block", name, tok.line)]
    return _block_type_finding(name, sig, i)


def _top_level(name: str, tokens: list[Token]) -> list[Finding]:
    out: list[Finding] = []
    depth = 0
    sig = _significant(tokens)
    for i, tok in enumerate(sig):
        prev = sig[i - 1] if i else None
        if tok.kind == "punct" and tok.text in "{[(":
            if depth == 0 and not (tok.text == "{" and prev is not None and prev.kind in ("ident", "str")):
                out.append(("unclassified_token", name, tok.line))
            depth += 1
        elif tok.kind == "punct" and tok.text in "}])":
            depth -= 1
        elif depth == 0:
            out += _top_level_token(name, sig, i)
    return out


def _opens_block(walked: list[tuple[Token, tuple[str, ...]]], idx: int) -> bool:
    prev = walked[idx - 1][0] if idx else None
    nxt = walked[idx + 1][0] if idx + 1 < len(walked) else None
    if nxt is None or (prev is not None and prev.kind == "punct" and prev.text == "."):
        return False
    return nxt.kind in ("ident", "str") or (nxt.kind == "punct" and nxt.text == "{")


def _shape_and_function_rules(name: str, walked: list[tuple[Token, tuple[str, ...]]]) -> list[Finding]:
    out: list[Finding] = []
    for idx, (tok, stack) in enumerate(walked):
        if tok.kind == "punct":
            if tok.text in _SHAPE_OPERATORS and not _in_condition(stack):
                rule = "conditional_operator" if tok.text == "?" else "comparison_or_logical_operator"
                out.append((rule, name, tok.line))
            elif tok.text == "::":
                out.append(("provider_function", name, tok.line))
        elif tok.kind == "ident":
            nxt = walked[idx + 1][0] if idx + 1 < len(walked) else None
            if tok.text == "if":
                out.append(("for_if_clause", name, tok.line))
            elif tok.text in ("dynamic", "provisioner", "connection") and _opens_block(walked, idx):
                rule = "dynamic_block" if tok.text == "dynamic" else "provisioner_block"
                out.append((rule, name, tok.line))
            elif (
                tok.text in ("count", "for_each")
                and nxt is not None
                and nxt.kind == "punct"
                and nxt.text == "="
                and stack in (("resource",), ("data",))
            ):
                out.append(("meta_argument", name, tok.line))
    return out


def _call_rules(name: str, sig: list[Token]) -> list[Finding]:
    out: list[Finding] = []
    for i, tok in enumerate(sig):
        if tok.kind != "ident" or tok.text in _KEYWORDS or i + 1 >= len(sig):
            continue
        if not (sig[i + 1].kind == "punct" and sig[i + 1].text == "("):
            continue
        if tok.text not in ALLOWED_FUNCTIONS:
            out.append(("function_not_allowlisted", name, tok.line))
        elif tok.text == "file" and not _file_call_ok(sig, i):
            out.append(("file_path_not_allowlisted", name, tok.line))
        elif tok.text == "try" and not _try_call_ok(sig, i):
            out.append(("try_form_not_allowed", name, tok.line))
    return out


def _file_call_ok(sig: list[Token], i: int) -> bool:
    return (
        i + 3 < len(sig)
        and sig[i + 2].kind == "str"
        and sig[i + 2].text in ALLOWED_FILE_PATHS
        and sig[i + 3].kind == "punct"
        and sig[i + 3].text == ")"
    )


def _try_call_ok(sig: list[Token], i: int) -> bool:
    """try(yamldecode(file(<STR>))<.ident | [STR]>, <literal>)"""
    texts = [(t.kind, t.text) for t in sig[i : i + 16]]
    head = [("ident", "try"), ("punct", "("), ("ident", "yamldecode"), ("punct", "("), ("ident", "file"), ("punct", "(")]
    if texts[:6] != head or len(texts) < 9 or texts[6][0] != "str" or texts[7:9] != [("punct", ")"), ("punct", ")")]:
        return False
    rest = texts[9:]
    if rest[:2] and rest[0] == ("punct", ".") and rest[1][0] == "ident":
        rest = rest[2:]
    elif len(rest) >= 3 and rest[0] == ("punct", "[") and rest[1][0] == "str" and rest[2] == ("punct", "]"):
        rest = rest[3:]
    else:
        return False
    if len(rest) < 3 or rest[0] != ("punct", ",") or rest[2] != ("punct", ")"):
        return False
    lit = rest[1]
    return lit[0] in ("str", "number") or (lit[0] == "ident" and lit[1] in _LITERAL_IDENTS)


def _validation_function_rules(name: str, walked: list[tuple[Token, tuple[str, ...]]]) -> list[Finding]:
    return [
        ("validation_only_function", name, tok.line)
        for tok, stack in walked
        if tok.kind == "ident" and tok.text in VALIDATION_ONLY_FUNCTIONS and not _in_validation(stack)
    ]


def _in_validation(stack: tuple[str, ...]) -> bool:
    return stack[-2:] == ("variable", "validation")


def tree_rules(files: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for name in sorted(files):
        if name in MAIN_ONLY_FILES or name in DOC_FILES:
            continue
        if name.endswith(".tf.json"):
            out.append(("tf_json", name, 0))
        elif name == "override.tf" or name.endswith("_override.tf"):
            out.append(("override_file", name, 0))
        elif name == "terraform.tfvars" or name.endswith((".auto.tfvars", ".auto.tfvars.json")):
            out.append(("tfvars_file", name, 0))
        elif not name.endswith(".tf"):
            out.append(("file_not_tf", name, 0))
    return out


def screen_tree(files: dict[str, str]) -> list[Finding]:
    """Rules over a {relative file name: text} map of one root. MAIN_ONLY_FILES come from main and are not lexed."""
    out = tree_rules(files)
    for name in sorted(files):
        if name.endswith(".tf") and name not in MAIN_ONLY_FILES:
            out += _file_rules(name, files[name])
    return sorted(set(out), key=lambda f: (f[1], f[2], f[0]))


def read_root(root: Path) -> tuple[dict[str, str], list[Finding]]:
    files: dict[str, str] = {}
    findings: list[Finding] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if entry.name != ".terraform":
                findings.append(("subdirectory", entry.name, 0))
            continue
        if not _FILE_NAME_RE.fullmatch(entry.name):
            findings.append(("file_name", "invalid-name", 0))
            continue
        try:
            files[entry.name] = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(("non_ascii_or_control", entry.name, 0))
    return files, findings


def screen_root(root: Path) -> list[Finding]:
    files, findings = read_root(root)
    return sorted(set(findings + screen_tree(files)), key=lambda f: (f[1], f[2], f[0]))


def _resource_refusal(res: dict[str, Any]) -> str | None:
    if res.get("provisioners") or res.get("count_expression") or res.get("for_each_expression"):
        return "provisioner_or_meta_argument"
    is_data = res.get("mode") == "data"
    if res.get("type") not in (ALLOWED_DATA_TYPES if is_data else ALLOWED_RESOURCE_TYPES):
        return "data_type" if is_data else "resource_type"
    return None


def backstop(plan: Any) -> str | None:
    """Rule id of the first refusal found in Terraform's own parsed plan JSON, or None."""
    if not isinstance(plan, dict) or not isinstance(plan.get("configuration"), dict):
        return "plan_unreadable"
    cfg = plan["configuration"]
    providers = cfg.get("provider_config") or {}
    if set(providers) != {"aws", "neon"} or any("alias" in (v or {}) for v in providers.values()):
        return "provider_config"
    root = cfg.get("root_module") or {}
    if root.get("module_calls"):
        return "module_calls"
    for res in root.get("resources") or []:
        refusal = _resource_refusal(res)
        if refusal:
            return refusal
    if any((rc.get("change") or {}).get("importing") for rc in plan.get("resource_changes") or []):
        return "importing"
    if any(((chk or {}).get("address") or {}).get("kind") == "check" for chk in plan.get("checks") or []):
        return "check_block"
    return None


def main(argv: list[str] | None = None) -> int:
    if not _package_resolves_under_root():
        print("SCREEN_ERROR scripts package did not resolve under the repo root")
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "screen" and args[1] == "--root":
        findings = screen_root(Path(args[2]))
        if not findings:
            print("PASS")
            return 0
        for rule, name, line in findings:
            print(f"REFUSED {rule} {name}:{line}")
        return 1
    if len(args) == 3 and args[0] == "backstop" and args[1] == "--plan-json":
        try:
            plan = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            plan = None
        refused = backstop(plan)
        print("BACKSTOP_OK" if refused is None else f"BACKSTOP_REFUSED {refused}")
        return 0 if refused is None else 1
    print("usage: terraform_hcl_screen.py screen --root DIR | backstop --plan-json FILE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
