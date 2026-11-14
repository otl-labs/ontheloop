"""Standing check: terraform/bootstrap and terraform/personal stay account-portable (rec-5025, SIT Decision 252).

Predicates (a)-(g): no literal per-account bucket name or state key outside account_names.tf; the two account_names.tf
files agree; the environment variables agree; the sandbox render equals today's names and sit adds "-sit"; the
suffix map matches the validation; each SIT backend file agrees with the sit render; and no secret or parameter name
carries an environment token (the same names in every account, Decision 237 cl.1).
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import _common, registry
from scripts.checks.iam_tf import _account_names as _an

ROOTS = ("bootstrap", "personal")
SIT_BACKENDS = (
    ("terraform/bootstrap/backend-sit.hcl", "bootstrap_state_bucket_name", None),
    ("config/terraform/backend-personal-sit.hcl", "data_lake_bucket_name", "tfstate/personal/sit/terraform.tfstate"),
)
SANDBOX_NAMES = {
    "data_lake_bucket_name": "agent-platform-data-lake",
    "ducklake_dr_bucket_name": "agent-platform-ducklake-catalog-dr",
    "bootstrap_state_bucket_name": "agent-platform-bootstrap-tfstate",
}
_BUCKETS = "agent-platform-data-lake|agent-platform-ducklake-catalog-dr|agent-platform-bootstrap-tfstate"
_LITERAL_RES = (
    re.compile(rf"arn:aws:s3:::(?:{_BUCKETS})"),
    re.compile(rf"s3://(?:{_BUCKETS})"),
    re.compile(rf'\bbucket\s*=\s*"(?:{_BUCKETS})'),
)
_LOCAL_FOR = {
    "agent-platform-data-lake": "local.data_lake_bucket_arn or local.data_lake_bucket_name",
    "agent-platform-ducklake-catalog-dr": "local.ducklake_dr_bucket_arn or local.ducklake_dr_bucket_name",
    "agent-platform-bootstrap-tfstate": "local.bootstrap_state_bucket_arn or local.bootstrap_state_bucket_name",
}
_BLOCK_RE = re.compile(r'^(?:resource|data)\s+"(aws_secretsmanager_secret|aws_ssm_parameter)"\s+"[^"]+"\s*\{', re.MULTILINE)
_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]*)"', re.MULTILINE)
_NAMES_LOCAL_RE = re.compile(r"^\s*\w*(?:parameter_names|secret_names)\s*=\s*([\[{])", re.MULTILINE)
_FRAGMENT_RE = re.compile(r'(?:secret:|parameter/|/secret/)[^"\s,\]\)]*')
_ENV_TOKENS = {"sandbox", "sit"}


def strip_comment(line: str) -> str:
    """Drop a trailing # or // comment, only outside a quoted string. /* */ blocks are never stripped."""
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and in_str:
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and (ch == "#" or line.startswith("//", i)):
            return line[:i]
        i += 1
    return line


def _code(text: str) -> str:
    return "\n".join(strip_comment(ln) for ln in text.splitlines())


def _block_body(text: str, open_idx: int) -> str:
    """Text from the brace at open_idx to its matching close, quotes respected."""
    depth, in_str, i = 0, False, open_idx
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_str:
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in "{[":
            depth += 1
        elif not in_str and ch in "}]":
            depth -= 1
            if depth == 0:
                return text[open_idx : i + 1]
        i += 1
    return text[open_idx:]


def _tf_files(root: Path) -> list[tuple[str, Path]]:
    return [(r, p) for r in ROOTS for p in sorted((root / "terraform" / r).glob("*.tf"))]


def _drop_backend_blocks(code: str) -> str:
    for m in reversed(list(re.finditer(r'backend\s+"[^"]+"\s*\{', code))):
        body = _block_body(code, m.end() - 1)
        code = code[: m.start()] + code[m.end() - 1 + len(body) :]
    return code


def _literal_findings(root: Path) -> list[str]:
    out: list[str] = []
    for r, path in _tf_files(root):
        if path.name == "account_names.tf":
            continue
        code = _drop_backend_blocks(_code(path.read_text(encoding="utf-8")))
        rel = path.relative_to(root)
        for n, line in enumerate(code.splitlines(), 1):
            for rx in _LITERAL_RES:
                m = rx.search(line)
                if m:
                    base = next(b for b in _LOCAL_FOR if b in m.group(0))
                    out.append(f"(a) {rel}:{n}: literal per-account bucket name; use {_LOCAL_FOR[base]}")
            if r == "personal" and "tfstate/personal/sandbox/" in line:
                out.append(f"(a) {rel}:{n}: literal state key; use tfstate/personal/${{var.environment}}/")
    return out


def _env_variable(text: str) -> str:
    m = re.search(r'variable\s+"environment"\s*\{', text)
    return re.sub(r"\s+", " ", _block_body(text, m.end() - 1)) if m else ""


def _shared_lines(text: str) -> dict[str, str]:
    return {k: v for k, v in _an._ASSIGN_RE.findall(text) if k in _an.name_locals(text)} | {
        "name_suffix": str(_an.suffix_map(text))
    }


def _consistency_findings(root: Path) -> list[str]:
    out: list[str] = []
    names = {r: (root / "terraform" / r / "account_names.tf") for r in ROOTS}
    vars_ = {r: (root / "terraform" / r / "variables.tf") for r in ROOTS}
    if not all(p.is_file() for p in names.values()):
        return [f"(b) missing account_names.tf: {[str(p.relative_to(root)) for p in names.values() if not p.is_file()]}"]
    texts = {r: p.read_text(encoding="utf-8") for r, p in names.items()}
    boot, pers = _shared_lines(texts["bootstrap"]), _shared_lines(texts["personal"])
    for key in sorted(set(boot) & set(pers)):
        if boot[key] != pers[key]:
            out.append(f"(b) local {key} differs between terraform/bootstrap and terraform/personal account_names.tf")
    envs = {r: _env_variable(vars_[r].read_text(encoding="utf-8")) for r in ROOTS}
    if not envs["bootstrap"] or envs["bootstrap"] != envs["personal"]:
        out.append("(c) variable environment missing or different between the two roots (default and validation must match)")
    elif 'default = "sandbox"' not in envs["bootstrap"]:
        out.append('(c) variable environment must default to "sandbox"')
    admitted = _an.admitted_environments(vars_["bootstrap"].read_text(encoding="utf-8"))
    if sorted(_an.suffix_map(texts["bootstrap"])) != sorted(admitted):
        out.append(f"(e) name_suffix map environments differ from the validation's {admitted}")
    sandbox = _an.name_locals(texts["bootstrap"], "sandbox")
    sit = _an.name_locals(texts["bootstrap"], "sit")
    for key, want in SANDBOX_NAMES.items():
        if sandbox.get(key) != want:
            out.append(f"(d) sandbox render of {key} is {sandbox.get(key)!r}, today's name is {want!r}")
        if sit.get(key) != want + "-sit":
            out.append(f"(d) sit render of {key} is {sit.get(key)!r}, expected {want + '-sit'!r}")
    out += _backend_findings(root, sit)
    return out


def _backend_findings(root: Path, sit: dict[str, str]) -> list[str]:
    out: list[str] = []
    for rel, bucket_local, key in SIT_BACKENDS:
        path = root / rel
        if not path.is_file():
            out.append(f"(f) {rel} is missing")
            continue
        code = _code(path.read_text(encoding="utf-8"))
        attrs = dict(_an._MAP_ENTRY_RE.findall(code))
        if attrs.get("bucket") != sit.get(bucket_local):
            out.append(
                f"(f) {rel} bucket is {attrs.get('bucket')!r}, the sit render of {bucket_local} is {sit.get(bucket_local)!r}"
            )
        if key is not None and attrs.get("key") != key:
            out.append(f"(f) {rel} key is {attrs.get('key')!r}, expected {key!r}")
        if re.search(r"^\s*region\b", code, re.MULTILINE):
            out.append(f"(f) {rel} sets region; Decision 202 cl.4 keeps it out of committed files")
    return out


def _has_env_token(value: str) -> bool:
    return any(tok in _ENV_TOKENS for tok in re.split(r"[-_/:.\s]+", value.replace("${", "-").replace("}", "-")))


def _env_token_findings(root: Path) -> list[str]:
    out: list[str] = []
    for _r, path in _tf_files(root):
        code = _code(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root)

        def flag(value: str, where: str, pos: int) -> None:
            if _has_env_token(value):
                line = code.count("\n", 0, pos) + 1
                out.append(f"(g) {rel}:{line}: {where} carries an environment token; names must match in every account")

        for m in _BLOCK_RE.finditer(code):
            nm = _NAME_RE.search(_block_body(code, m.end() - 1))
            if nm:
                flag(nm.group(1), f"{m.group(1)} name", m.start())
        for m in _NAMES_LOCAL_RE.finditer(code):
            for lit in re.finditer(r'"([^"]*)"', _block_body(code, m.end(1) - 1)):
                flag(lit.group(1), "name-list local", m.start())
        for frag in _FRAGMENT_RE.finditer(code):
            flag(frag.group(0), "IAM grant fragment", frag.start())
    return out


def account_portable_findings(root: Path) -> list[str]:
    """Every violation of predicates (a)-(g) under root, as printable lines."""
    return _literal_findings(root) + _consistency_findings(root) + _env_token_findings(root)


@registry.register("validate_account_portable_names", owner="platform")
def validate_account_portable_names(failed: list[str]) -> None:
    """Fail when a terraform root stops being account-portable."""
    print("\n=== Account-portable names (rec-5025) ===")
    registry.examined(len(_tf_files(_common.ROOT)), unit="tf_files")
    findings = account_portable_findings(_common.ROOT)
    if findings:
        for f in findings:
            print(f"  - {f}")
        failed.append("Account-portable names")
    else:
        print("No literal per-account names; both roots agree; SIT backends and shared names portable.")
