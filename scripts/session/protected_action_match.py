"""Pure classification for the protected-action gate -- no I/O (rec-4518, docs/contracts/protected-actions.yaml).

Tamper detection, the fast path and classification of a Bash command's simple commands (from
scripts.session.protected_action_shell) against the contract mapping the hook loaded from origin/main. The contract is
the source of every list here except COARSE and the verb tables, which the hook must know before it reads the contract
(a test pins COARSE against the contract). Deny-only: nothing here decides to allow a command by name.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scripts.session import protected_action_shell as sh

COARSE = (
    "terraform",
    "secretsmanager",
    "ssm",
    "get_secret_value",
    "WithDecryption",
    "materialise_tfvars",
    "sync-terraform-assets",
    "neon_api_key",
    "agent_platform_admin",
)
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_SECRET_OPS = frozenset({"get-secret-value", "batch-get-secret-value"})
_SSM_OPS = frozenset({"get-parameter", "get-parameters", "get-parameters-by-path", "get-parameter-history"})
_NAME_FLAGS = ("--secret-id", "--name", "--names", "--secret-id-list")
_UNNAMEABLE_FLAGS = ("--filters", "--cli-input-json", "--cli-input-yaml")
_LOADED_PREFIXES = ("file://", "fileb://", "http://", "https://")
_DECRYPT_RE = re.compile(r"WithDecryption['\"]?\s*[=:]\s*['\"]?True")
_PROFILE_RE = re.compile(r"profile(?:_name)?['\"]?\s*[=:]\s*['\"]?([A-Za-z0-9_.@-]+)", re.IGNORECASE)
_NON_LITERAL = re.compile(r"[$`*?{}\[\]~]")


@dataclass(frozen=True)
class Hit:
    cls: str
    target: str
    approval: str
    binding_control: str
    ambient_only: bool = False
    default_only: bool = False
    root_dir: str | None = None
    from_cwd: bool = False
    exact: bool = False


def fast_path_ok(command: str, ambient_profile: str, admin_profiles: tuple[str, ...] = ("agent_platform_admin",)) -> bool:
    """True when the command cannot be protected: no coarse keyword, and no aws/terraform word under an admin ambient."""
    if any(word in command for word in COARSE):
        return False
    return not (ambient_profile in admin_profiles and ("aws" in command or "terraform" in command))


def _expand(text: str, home: str) -> str:
    out = text.replace("${HOME}", home).replace("$HOME", home)
    return home + out[1:] if out == "~" or out.startswith("~/") else out


def _inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _names(text: str, directory: str, filename: str) -> bool:
    return directory in text or filename in text


def _is_write_form(simple: sh.Simple, directory: str, filename: str, home: str, base_cwd: str) -> bool:
    for op, target in simple.redirects:
        if op.endswith((">", ">|")):
            resolved = posixpath.normpath(posixpath.join(_expand(simple.cwd or base_cwd, home), _expand(target, home)))
            if _inside(resolved, directory):
                return True
    command = simple.command
    if command in sh.WRITE_COMMANDS:
        if command != "sed":
            return True
        return any(
            w.startswith("--in-place") or (w.startswith("-") and not w.startswith("--") and "i" in w[1:]) for w in simple.words
        )
    return simple.carries_inline


def tamper_message(tool_name: str, tool_input: Mapping[str, Any], transcript_path: str, home: str, cwd: str) -> str | None:
    """A deny reason when the call writes into the transcript's directory; None otherwise (also when no path)."""
    if not transcript_path:
        return None
    directory = posixpath.dirname(transcript_path)
    filename = posixpath.basename(transcript_path)
    way = "read it with cat, tail or the Read tool, or cat it into a file outside that directory and analyse the copy"
    if tool_name in EDIT_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(raw, str) or not raw:
            return None
        path = posixpath.normpath(posixpath.join(cwd, _expand(raw, home)))
        return (
            f"protected-action-gate: tamper: {tool_name} on {path} writes into the session transcript directory; {way}."
            if _inside(path, directory)
            else None
        )
    command = tool_input.get("command")
    if tool_name not in ("Bash", "Monitor") or not isinstance(command, str):
        return None
    expanded = _expand(command, home).replace("~/", home + "/")
    if not _names(expanded, directory, filename):
        return None
    reason = f"protected-action-gate: tamper: this command names the session transcript and writes; {way}."
    try:
        parsed = sh.walk(command)
    except ValueError:
        return reason
    if parsed.heredoc:
        return reason
    for simple in parsed.commands:
        text = " ".join(
            [*(_expand(w, home) for w in simple.words), _expand(simple.cwd or "", home), *(t for _, t in simple.redirects)]
        )
        if (
            _names(text, directory, filename) or simple.cwd and _inside(_expand(simple.cwd, home), directory)
        ) and _is_write_form(simple, directory, filename, home, cwd):
            return reason
    return None


def _live_root(path: str, live_roots: Mapping[str, str]) -> str:
    """The live root's last path part, "unknown" when the tail is not literal, "" when no live root."""
    parts = posixpath.normpath(path).split("/")
    if any(_NON_LITERAL.search(p) for p in parts[-2:]):
        return "unknown"
    return parts[-1] if "/".join(parts[-2:]) in live_roots else ""


def _terraform_parts(simple: sh.Simple) -> tuple[str | None, str | None, list[str]]:
    words = simple.words[1:]
    chdir = None
    while words and words[0].startswith("-chdir"):
        chdir = words[0].split("=", 1)[1] if "=" in words[0] else None
        words = words[1:]
    norm = [("-" + w.lstrip("-") if w.startswith("--") else w) for w in words]
    norm = [w[:-5] if w.endswith("=true") else w for w in norm]
    return chdir, (norm[0] if norm else None), norm[1:]


def _root_of(simple: sh.Simple, chdir: str | None, input_cwd: str) -> tuple[str, bool]:
    if chdir is not None:
        if chdir.startswith(("/", "$", "~")) or not simple.cwd:
            return chdir, True
        return posixpath.join(simple.cwd, chdir), True
    if simple.cwd:
        return simple.cwd, True
    return input_cwd, False


def _class(contract: Mapping[str, Any], cls: str) -> Mapping[str, Any]:
    for entry in contract.get("classes") or []:
        if isinstance(entry, dict) and entry.get("id") == cls:
            return entry
    return {}


def _class_info(contract: Mapping[str, Any], cls: str) -> tuple[str, str]:
    spec = _class(contract, cls)
    return str(spec.get("approval", "")), str(spec.get("binding_control", ""))


def _tf_hits(simple: sh.Simple, contract: Mapping[str, Any], input_cwd: str) -> list[Hit]:
    spec = _class(contract, "tf-state-change")
    live_roots = (contract.get("gate") or {}).get("live_roots") or {}
    chdir, sub, rest = _terraform_parts(simple)
    root_text, named = _root_of(simple, chdir, input_cwd)
    mutating = (
        sub in (spec.get("subcommands") or [])
        or (sub == "state" and any(w in (spec.get("state_subcommands") or []) for w in rest[:1]))
        or (sub == "workspace" and any(w in (spec.get("workspace_subcommands") or []) for w in rest[:1]))
        or (sub == "init" and any(w in (spec.get("init_flags") or []) for w in rest))
    )
    if not mutating:
        return []
    target = _live_root(root_text, live_roots)
    if not target:
        return []
    approval, control = _class_info(contract, "tf-state-change")
    root_dir = (
        None
        if target == "unknown"
        else posixpath.normpath(root_text if root_text.startswith("/") else posixpath.join(input_cwd, root_text))
    )
    return [Hit("tf-state-change", target, approval, control, root_dir=root_dir, from_cwd=not named)]


def _option_value(words: list[str], flags: tuple[str, ...]) -> str | None:
    for i, word in enumerate(words):
        for flag in flags:
            if word == flag and i + 1 < len(words):
                return words[i + 1]
            if word.startswith(flag + "="):
                return word.split("=", 1)[1]
    return None


def _entry_spec(simple: sh.Simple, entry_points: list[str]) -> str | None:
    words = simple.words
    operand = None
    if simple.command in ("bash", "sh", "source", "."):
        operand = next((w for w in words[1:] if not w.startswith("-")), None)
    for spec in entry_points:
        path, *args = spec.split()
        script = operand if operand is not None else words[0]
        if script.endswith(path) and all(a in words for a in args):
            return spec
    return None


def _flag_values(words: list[str]) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(words):
        flag, eq, rest = words[i].partition("=")
        if flag not in _NAME_FLAGS:
            i += 1
            continue
        if eq:
            values.append(rest)
        i += 1
        while i < len(words) and not words[i].startswith("-"):
            values.append(words[i])
            i += 1
    return values


def _secret_name(part: str, ssm: bool) -> str:
    if (
        not part
        or part.startswith(_LOADED_PREFIXES)
        or "SUBSTX" in part
        or _NON_LITERAL.search(part)
        or any(c.isspace() for c in part)
    ):
        return "unknown"
    if ssm:
        name = "unknown" if part.startswith("arn:") else part.split(":", 1)[0]
    else:
        name = "unknown" if ":" in part else part
    return name or "unknown"


def _named_secrets(words: list[str]) -> list[str]:
    if "get-parameters-by-path" in words or any(w.partition("=")[0] in _UNNAMEABLE_FLAGS for w in words):
        return ["unknown"]
    values = _flag_values(words)
    ssm = "ssm" in words
    names = [_secret_name(part, ssm) for value in values for part in value.split(",")]
    return list(dict.fromkeys(names)) or ["unknown"]


def _declared_reads(table: Any, spec: str) -> list[str]:
    reads = table.get(spec) if isinstance(table, dict) else None
    if isinstance(reads, list) and reads and all(isinstance(r, str) and r for r in reads):
        return list(reads)
    return ["unknown"]


def _secret_hits(simple: sh.Simple, contract: Mapping[str, Any]) -> list[Hit]:
    spec = _class(contract, "secret-read")
    approval, control = _class_info(contract, "secret-read")
    exact = spec.get("target_case") == "exact"
    words = simple.words
    targets: list[str] = []
    if simple.command == "aws" and (
        ("secretsmanager" in words and any(w in _SECRET_OPS for w in words))
        or ("ssm" in words and "--with-decryption" in words and any(w in _SSM_OPS for w in words))
    ):
        targets.extend(_named_secrets(words))
    entry = _entry_spec(simple, spec.get("entry_points") or [])
    if entry is not None:
        targets.extend(_declared_reads(spec.get("entry_point_reads"), entry))
    return [Hit("secret-read", t, approval, control, exact=exact) for t in dict.fromkeys(targets)]


def _inline_secret(inline: list[str], contract: Mapping[str, Any]) -> list[Hit]:
    approval, control = _class_info(contract, "secret-read")
    if any("get_secret_value" in code or _DECRYPT_RE.search(code) for code in inline):
        return [Hit("secret-read", "unknown", approval, control)]
    return []


def _is_admin(profile: str | None, admins: list[str]) -> bool:
    return profile in admins


def _selected_profiles(simple: sh.Simple, inline: list[str], admins: list[str]) -> list[str]:
    found: list[str] = []
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "TF_VAR_aws_profile"):
        if simple.env.get(key) in admins:
            found.append(simple.env[key])
    words = simple.words
    for i, word in enumerate(words):
        if word == "--profile" and i + 1 < len(words) and words[i + 1] in admins:
            found.append(words[i + 1])
        found.extend(m for m in _PROFILE_RE.findall(word) if m in admins)
    for code in [*inline, *simple.code]:
        found.extend(m for m in _PROFILE_RE.findall(code) if m in admins)
    return found


def _contacts_aws(simple: sh.Simple) -> bool:
    if simple.command == "aws":
        return True
    if simple.command != "terraform":
        return False
    _, sub, rest = _terraform_parts(simple)
    offline = ["fmt", "validate", "version", "providers", "graph", "help", "-help", "-version"]
    return not (sub is None or sub in offline or (sub == "init" and "-backend=false" in rest))


def _tf_var_profile(simple: sh.Simple) -> str | None:
    value = simple.env.get("TF_VAR_aws_profile")
    words = simple.words
    for i, word in enumerate(words):
        if word == "-var" and i + 1 < len(words) and words[i + 1].startswith("aws_profile="):
            value = words[i + 1].split("=", 1)[1]
        elif word.startswith("-var=aws_profile="):
            value = word.split("=", 2)[2]
    return value


def _admin_hits(simple: sh.Simple, contract: Mapping[str, Any], inline: list[str], ambient: str, input_cwd: str) -> list[Hit]:
    gate = contract.get("gate") or {}
    admins = list(gate.get("admin_profiles") or [])
    spec = _class(contract, "admin-credential")
    approval, control = _class_info(contract, "admin-credential")
    hits = [Hit("admin-credential", p, approval, control) for p in _selected_profiles(simple, inline, admins)]
    profiles = spec.get("entry_point_profiles") or {}
    entry = _entry_spec(simple, list(profiles))
    if entry is not None and isinstance(profiles[entry], str) and profiles[entry]:
        hits.append(Hit("admin-credential", profiles[entry], approval, control))
    if not _contacts_aws(simple):
        return hits
    if simple.command == "terraform":
        chdir, _, _ = _terraform_parts(simple)
        root_text, _ = _root_of(simple, chdir, input_cwd)
        parts = posixpath.normpath(root_text).split("/")
        override = _tf_var_profile(simple)
        if "/".join(parts[-2:]) in (spec.get("admin_default_roots") or []) and (
            override is None or _is_admin(override, admins)
        ):
            hits.append(Hit("admin-credential", admins[0], approval, control, default_only=True))
    if _is_admin(ambient, admins):
        explicit = simple.env.get("AWS_PROFILE")
        flag = _option_value(simple.words, ("--profile",)) if simple.command == "aws" else None
        if not ((explicit and not _is_admin(explicit, admins)) or (flag and not _is_admin(flag, admins))):
            hits.append(Hit("admin-credential", ambient, approval, control, ambient_only=True))
    return hits


def _merge(hits: list[Hit]) -> list[Hit]:
    merged: dict[tuple, Hit] = {}
    for hit in hits:
        key = (hit.cls, hit.target, hit.root_dir, hit.from_cwd)
        old = merged.get(key)
        if old is None:
            merged[key] = hit
        else:
            merged[key] = Hit(
                hit.cls,
                hit.target,
                hit.approval,
                hit.binding_control,
                old.ambient_only and hit.ambient_only,
                old.default_only and hit.default_only,
                hit.root_dir,
                hit.from_cwd,
                hit.exact,
            )
    return list(merged.values())


def classify(command: str, contract: Mapping[str, Any], ambient_profile: str, input_cwd: str) -> list[Hit]:
    """Protected hits for a Bash command; raises ValueError on a lexer error."""
    secret_spec = _class(contract, "secret-read")
    entry_points = [spec.split()[0] for spec in secret_spec.get("entry_points") or []]
    parsed = sh.walk(command, entry_points)
    hits = _inline_secret(parsed.inline, contract)
    for simple in parsed.commands:
        if simple.command == "terraform":
            hits.extend(_tf_hits(simple, contract, input_cwd))
        hits.extend(_secret_hits(simple, contract))
        hits.extend(_admin_hits(simple, contract, parsed.inline, ambient_profile, input_cwd))
    return _merge(hits)
