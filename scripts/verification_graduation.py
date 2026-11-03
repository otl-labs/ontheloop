"""Real differential-admission producer for the verification graduation registry (VF-05/VF-06).

Materializes a runnable kernel check (``scripts.verification_checks``) from a registry row's
``check_spec``, repointing path/cwd to a given tree root, and executes the differential
admission gate against a REAL git worktree -- never a simulated revert (Decision 55):

- Kernel entries (VF-06 c2): revert leg checks out origin/main in a temp worktree (the check
  is self-contained per its check_spec).
- Brand-new verifiers (VF-06 c3): the verifier does not exist on origin/main, so the revert
  leg checks out HEAD in a temp worktree and reverts only the covered changed files to their
  origin/main content, then runs the verifier subprocess there.

Import-pure: no filesystem or network access at import time. Worktree/materialize/revert
failures raise ``GraduationError`` (fail-loud) -- there is no silent "none graduated" path for
an error; an empty candidate set is the only legitimate case for recording nothing.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from scripts.checks._scaffolding import _excluded_and_absent, _excluded_heavy_import_names
from scripts.checks.verification.validate_verifier_hermeticity import _verifier_is_non_hermetic
from scripts.verification_checks import (
    CANONICAL_SLOTS,
    BaseCheck,
    CheckResult,
    CheckStatus,
    CommandExitZeroCheck,
    CommandOutputMatchesCheck,
    FileAbsentCheck,
    FileExistsCheck,
    GrepCountCheck,
    MetricUnderThresholdCheck,
    TestSelectorCheck,
    is_admitted,
)

ROOT = Path(__file__).resolve().parent.parent


class GraduationError(RuntimeError):
    """Raised on any worktree/materialize/revert failure (fail-loud, Decision 55)."""


# ---------------------------------------------------------------------------
# Registry loader: config/agent/verification_registry/entries/<check_id>.yaml (VF-01 re-grain)
# ---------------------------------------------------------------------------
#
# The registry is a directory of one YAML mapping per check_id, keyed by filename -- never a
# manifest or an index. entries/deprecated/ is a reserved, loader-excluded retirement subtree
# (git mv a record there to retire it). REGISTRY_DIR_REL/LEGACY_FLAT_BASENAME are shared, public
# path vocabulary (composed from path SEGMENTS, never joined into one literal string) so a sibling
# module needing the pre-migration flat path -- e.g. the flat-file-resurrection leg, checking it
# never reappears -- imports these constants instead of re-deriving the literal segment-adjacent
# form the standing sweep (VP step 14 / registry-flat-path-no-live-refs) is built to catch.

ENTRIES_DIRNAME = "entries"
DEPRECATED_DIRNAME = "deprecated"
LEGACY_FLAT_BASENAME = "registry.yaml"
REGISTRY_DIR_REL = Path("config") / "agent" / "verification_registry"
REGISTRY_ENTRIES_REL = REGISTRY_DIR_REL / ENTRIES_DIRNAME


def shard_path_for(check_id: str, repo_root: str | Path | None = None) -> Path:
    """The on-disk path a graduated record for `check_id` lives (or would be written) at."""
    root = Path(repo_root) if repo_root is not None else ROOT
    return root / REGISTRY_ENTRIES_REL / f"{check_id}.yaml"


def load_entries(repo_root: str | Path | None = None) -> list[dict]:
    """The live registry: every record under entries/, excluding entries/deprecated/, sorted by
    filename for deterministic order.

    Discovery is a directory glob, never a manifest or a cached count (no index to drift from the
    filesystem). ``Path.glob("*.yaml")`` is single-level (non-recursive), so entries/deprecated/
    is excluded structurally by the glob itself, not by a documented convention or a name filter.
    Raises GraduationError (fail-loud, Decision 55) on a malformed or non-mapping shard -- a
    silent skip would make a live record invisible to the differential gate without a trace.
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    entries_dir = root / REGISTRY_ENTRIES_REL
    if not entries_dir.is_dir():
        return []

    import yaml as _yaml  # noqa: PLC0415

    rows: list[dict] = []
    for path in sorted(entries_dir.glob("*.yaml")):
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GraduationError(f"load_entries: malformed shard {path.relative_to(root)}: {exc}") from exc
        if not isinstance(data, dict):
            raise GraduationError(f"load_entries: shard {path.relative_to(root)} is not a mapping")
        rows.append(data)
    return rows


def _ref_resolves(ref: str, root: Path) -> bool:
    result = _run_git(["rev-parse", "--verify", "-q", ref], root)
    return result.returncode == 0


def _shard_paths_at_ref(ref: str, root: Path) -> list[str]:
    """Repo-relative shard file paths at `ref`, excluding entries/deprecated/. Empty when the
    entries/ directory does not exist at `ref` (git ls-tree on an absent path prints nothing,
    exit 0 -- distinguished from a genuine git failure by the caller checking `ref` resolved)."""
    rel_dir = REGISTRY_ENTRIES_REL.as_posix()
    result = _run_git(["ls-tree", "-r", "--name-only", ref, "--", rel_dir], root)
    if result.returncode != 0:
        return []
    deprecated_prefix = f"{rel_dir}/{DEPRECATED_DIRNAME}/"
    return sorted(
        line
        for line in result.stdout.splitlines()
        if line.strip() and line.endswith(".yaml") and not line.startswith(deprecated_prefix)
    )


def entries_at_ref(ref: str, repo_root: str | Path | None = None) -> list[dict] | None:
    """The registry baseline at git ref `ref`, spanning both the sharded and legacy-flat layouts.

    Four branches (both required so a lost baseline never silently misreads every live record as
    newly added -- differential admission costs ~7.1s/record, i.e. ~56 minutes over 476 records):
      (i)   `ref` resolves, entries/ present at `ref`      -> the shard rows at that ref.
      (ii)  `ref` resolves, entries/ absent, legacy flat
            registry.yaml present at `ref`                 -> the legacy flat entries (pre-migration ref).
      (iii) `ref` resolves, BOTH layouts absent             -> GraduationError (fail loud, Decision 55) --
            never an empty baseline.
      (iv)  `ref` itself does not resolve                   -> None (advisory TOLERATE; the caller prints
            a skip reason and skips the differential leg, matching _marker_guard's
            "SKIP: origin/main unreachable" posture).

    A caller distinguishes (iv) from a genuinely empty baseline via the None/list[dict] return
    type -- None means "could not determine", never "determined empty."
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    if not _ref_resolves(ref, root):
        return None  # (iv)

    import yaml as _yaml  # noqa: PLC0415

    shard_paths = _shard_paths_at_ref(ref, root)
    if shard_paths:  # (i)
        rows: list[dict] = []
        for rel in shard_paths:
            shown = _run_git(["show", f"{ref}:{rel}"], root)
            if shown.returncode != 0:
                continue
            try:
                data = _yaml.safe_load(shown.stdout)
            except Exception:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    flat_rel = (REGISTRY_DIR_REL / LEGACY_FLAT_BASENAME).as_posix()
    flat_shown = _run_git(["show", f"{ref}:{flat_rel}"], root)
    if flat_shown.returncode == 0:  # (ii) legacy pre-migration layout
        try:
            data = _yaml.safe_load(flat_shown.stdout)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        entries = data.get("entries") or []
        return entries if isinstance(entries, list) else []

    # (iii) ref resolves but neither layout exists there -- fail loud, never an empty baseline.
    raise GraduationError(
        f"entries_at_ref: ref {ref!r} resolves but neither {REGISTRY_ENTRIES_REL.as_posix()}/ nor "
        f"{flat_rel} exists there -- refusing to return an empty baseline (would misread every live "
        "record as newly added)"
    )


# ---------------------------------------------------------------------------
# Materialization: registry row (check_spec) -> a runnable kernel check
# ---------------------------------------------------------------------------


def _repoint_path(path: str, tree_root: str | Path | None) -> str:
    if tree_root is None:
        return path
    p = Path(path)
    return str(p) if p.is_absolute() else str(Path(tree_root) / p)


def materialize_check_in_tree(row: dict, tree_root: str | Path | None) -> BaseCheck:
    """Build a runnable kernel check from a registry row, repointed at ``tree_root``.

    ``tree_root`` of None means "the live tree" (no repointing of path/cwd fields).
    Raises GraduationError on an unknown slot or a check_spec missing a required key.
    """
    slot = row.get("primitive_slot")
    if slot not in CANONICAL_SLOTS:
        raise GraduationError(
            f"check_id={row.get('check_id')!r}: unknown primitive_slot {slot!r} (not in CD.29 CANONICAL_SLOTS)"
        )

    spec = row.get("check_spec") or {}
    check_id = row.get("check_id", "graduated-check")
    cwd = str(tree_root) if tree_root is not None else None

    def _require(*keys: str) -> None:
        missing = [k for k in keys if k not in spec]
        if missing:
            raise GraduationError(f"check_id={check_id!r} slot={slot!r}: check_spec missing required key(s): {missing}")

    if slot == "command_exit_zero":
        _require("command")
        return CommandExitZeroCheck(name=check_id, command=list(spec["command"]), cwd=cwd)
    if slot == "command_output_matches":
        _require("command", "expected")
        return CommandOutputMatchesCheck(
            name=check_id,
            command=list(spec["command"]),
            expected=spec["expected"],
            use_regex=bool(spec.get("use_regex", False)),
            cwd=cwd,
        )
    if slot == "file_presence":
        _require("path")
        path = _repoint_path(spec["path"], tree_root)
        mode = spec.get("mode", "exists")
        if mode not in ("exists", "absent"):
            raise GraduationError(f"check_id={check_id!r}: file_presence mode must be 'exists' or 'absent', got {mode!r}")
        return FileAbsentCheck(name=check_id, path=path) if mode == "absent" else FileExistsCheck(name=check_id, path=path)
    if slot == "grep_count":
        _require("path", "pattern")
        path = _repoint_path(spec["path"], tree_root)
        return GrepCountCheck(
            name=check_id,
            path=path,
            pattern=spec["pattern"],
            operator=spec.get("operator", "eq"),
            count=int(spec.get("count", 0)),
        )
    if slot == "test_selector":
        _require("node_id")
        return TestSelectorCheck(name=check_id, node_id=spec["node_id"], cwd=cwd)
    if slot == "metric_under_threshold":
        _require("command", "threshold")
        return MetricUnderThresholdCheck(
            name=check_id,
            command=list(spec["command"]),
            threshold=float(spec["threshold"]),
            cwd=cwd,
        )
    raise GraduationError(f"check_id={check_id!r}: slot {slot!r} has no materializer wired up")  # pragma: no cover


def materialize_check(row: dict) -> BaseCheck:
    """Materialize a check against the live tree (no tree-root repointing)."""
    return materialize_check_in_tree(row, None)


# ---------------------------------------------------------------------------
# Real git worktree revert
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")


def _provision_venv_symlink(wt_path: Path, repo_root: Path) -> Path | None:
    """Symlink ``repo_root``'s ``.venv`` into the worktree, if ``repo_root`` has one.

    Keyed off the ``repo_root`` ARGUMENT, never the module-level ``ROOT`` -- a synthetic
    fixture repo (tests build these under tmp_path, with neither a `.venv` nor a
    `bin/venv-python`) must stay unprovisioned so its own environment-probe tests can still
    exercise the unrunnable-interpreter path. Without this, `bin/venv-python` invoked inside
    the worktree resolves its own location via `$0` and re-derives `REPO_ROOT` relative to
    cwd (see `bin/venv-python`), landing on `<worktree>/.venv/bin/python` -- absent on a bare
    `git worktree add`, since `.venv` is gitignored and was never checked out. Returns the
    created symlink path, or None if `repo_root` carries no `.venv` to provision.
    """
    real_venv = repo_root / ".venv"
    if not real_venv.exists():
        return None
    wt_venv = wt_path / ".venv"
    wt_venv.symlink_to(real_venv, target_is_directory=True)
    return wt_venv


@contextlib.contextmanager
def git_worktree(ref: str, repo_root: Path | None = None) -> Iterator[Path]:
    """Check out ``ref`` into a temporary git worktree; remove it on exit.

    Raises GraduationError on any git failure (fail-loud, Decision 55).
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    tmp_parent = tempfile.mkdtemp(prefix="verif-graduation-")
    wt_path = Path(tmp_parent) / "wt"
    add_result = _run_git(["worktree", "add", "--detach", str(wt_path), ref], root)
    if add_result.returncode != 0:
        shutil.rmtree(tmp_parent, ignore_errors=True)
        raise GraduationError(f"git worktree add failed for ref {ref!r}: {add_result.stderr.strip()}")
    venv_symlink = _provision_venv_symlink(wt_path, root)
    try:
        yield wt_path
    finally:
        # Explicit unlink before `worktree remove`/`rmtree` even though both were verified not
        # to follow the symlink into the real .venv -- a 432MB destructive failure mode is not
        # a property to rely on implicitly.
        if venv_symlink is not None and venv_symlink.is_symlink():
            venv_symlink.unlink()
        remove_result = _run_git(["worktree", "remove", "--force", str(wt_path)], root)
        if remove_result.returncode != 0:
            _run_git(["worktree", "prune"], root)
        shutil.rmtree(tmp_parent, ignore_errors=True)


# Allowlisted interpreter tokens the environment probe recognises, longest-first so a
# whole-word match on "bin/venv-python" is found before "python" is ever attempted at the
# same span -- see _probed_interpreter_tokens for why order alone isn't the whole story.
_INTERPRETER_ALLOWLIST: tuple[str, ...] = ("bin/venv-python", "python3", "python", "bash", "sh")

_INTERPRETER_TOKEN_RE = re.compile(r"\b(?:" + "|".join(re.escape(tok) for tok in _INTERPRETER_ALLOWLIST) + r")\b")

# Slots whose materialized check carries a `.command` list a subprocess actually execs.
# test_selector always runs via sys.executable (verification_checks.py's TestSelectorCheck),
# so it inherits the parent interpreter and has no tree-relative resolution to break;
# grep_count/file_presence never spawn a subprocess at all. Probing either would be a no-op
# at best and a wrong-headed assertion at worst.
_PROBEABLE_SLOTS: frozenset[str] = frozenset({"command_exit_zero", "command_output_matches", "metric_under_threshold"})


def _probed_interpreter_tokens(command: list[str]) -> list[str]:
    """Distinct allowlisted interpreter tokens appearing anywhere in `command` (not just
    argv[0]) -- a bash- or timeout-headed row can still invoke bin/venv-python in its body
    (29 of 139 cohort rows have this shape; a head-only probe is blind to all 29). Matched on
    regex word boundaries, never bare substrings: a naive substring scan finds "sh" inside
    "shard", "push", "shell", "shutil", "sha256" (42 spurious hits across the live corpus).
    Because re.finditer finds leftmost non-overlapping matches, a "bin/venv-python" occurrence
    is consumed whole before the engine ever considers the "python" tail inside it as a
    separate start position, so the same token instance is never double-counted.
    """
    text = " ".join(command)
    seen: list[str] = []
    for match in _INTERPRETER_TOKEN_RE.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


def _probe_interpreter(token: str, revert_worktree: Path) -> tuple[bool, str]:
    """Attempt to launch `token` as `<token> -c ''` with cwd set to `revert_worktree`.

    A relative token (e.g. "bin/venv-python") resolves relative to `cwd` at exec time, which
    is exactly what materialize_check_in_tree's own `cwd=` repointing relies on -- this probe
    exercises the identical resolution path the real check would take. FileNotFoundError/OSError
    (e.g. a synthetic repo with no bin/venv-python at all) is a probe FAILURE, not an exception
    to propagate -- the caller decides whether that failure matters.
    """
    try:
        result = subprocess.run(
            [token, "-c", ""],
            cwd=str(revert_worktree),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    detail = result.stderr.strip() or result.stdout.strip()
    return False, f"exit {result.returncode}" + (f": {detail}" if detail else "")


def _probe_environment_on_fail(row: dict, check: BaseCheck, wt_root: Path) -> None:
    """Before a revert-leg FAIL is attributed to content, prove the revert worktree could
    execute every allowlisted interpreter token the check's command references.

    Raises GraduationError (fail-loud, Decision 55) naming the check_id, the unrunnable token,
    and its exit code/detail the moment any probed token cannot launch -- this is the gate
    that stops an environment defect (e.g. a missing .venv) from being misread as the check
    discriminating between pre- and post-change content. A command whose interpreter falls
    outside the allowlist, or that reaches here via a non-probeable slot, is simply not probed
    -- the probe is a positive detector only and must never manufacture a failure for an
    unrecognised head (two `timeout`-headed rows already live on main; their bodies still
    surface bin/venv-python/bash and get probed via the whole-command scan).
    """
    if row.get("primitive_slot") not in _PROBEABLE_SLOTS:
        return
    command = getattr(check, "command", None)
    if not command:
        return
    for token in _probed_interpreter_tokens(command):
        ok, detail = _probe_interpreter(token, wt_root)
        if not ok:
            raise GraduationError(
                f"check_id={row.get('check_id')!r}: interpreter {token!r} is not runnable in the "
                f"revert worktree ({detail}) -- refusing to admit a FAIL that may be environmental, "
                "not content"
            )


def make_worktree_revert_runner(
    row: dict, ref: str = "origin/main", repo_root: Path | None = None
) -> Callable[[BaseCheck], CheckResult]:
    """Return a revert_runner for ``scripts.verification_checks.is_admitted`` (kernel entries).

    Ignores the ``check`` argument is_admitted passes in and instead materializes the row's own
    check_spec against a real origin/main worktree -- the check parameter exists only to satisfy
    is_admitted's callable interface.
    """

    def revert_runner(_check: BaseCheck) -> CheckResult:
        with git_worktree(ref, repo_root=repo_root) as wt_root:
            reverted_check = materialize_check_in_tree(row, wt_root)
            result = reverted_check.run()
            if result.status == CheckStatus.FAIL:
                _probe_environment_on_fail(row, reverted_check, wt_root)
            return result

    return revert_runner


@dataclass
class DifferentialOutcome:
    admitted: bool
    reason: str
    skipped: bool = False


# rec-2655: a module-level guard has zero leading whitespace (indent 0) -- that's what
# distinguishes it from a function/method-scope importorskip, which this predicate must not match.
_MODULE_LEVEL_IMPORTORSKIP_RE = re.compile(r"^\w[\w.]*\s*=\s*pytest\.importorskip\(\s*['\"]([\w.]+)['\"]")


def _module_level_importorskip_dep(file_path: Path) -> str | None:
    """Return the dependency name of a module-level `pytest.importorskip(...)` guard in
    `file_path`, or None if the file has no such guard (or cannot be read)."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = _MODULE_LEVEL_IMPORTORSKIP_RE.match(line)
        if match:
            return match.group(1)
    return None


def _differential_skip_reason(row: dict, live: CheckResult, repo_root: Path) -> str | None:
    """rec-2655: detect the narrow co-occurrence that makes a non-PASS live result a graceful
    skip rather than a genuine failure -- a test_selector row whose node_id lives in a file with
    a module-level `pytest.importorskip` guard on a deliberately-excluded, genuinely-absent heavy
    dependency, AND a "found no collectors" collection error in the live output. Returns the skip
    reason, or None (fail-closed: every other non-PASS shape stays a hard failure)."""
    if row.get("primitive_slot") != "test_selector":
        return None
    node_id = (row.get("check_spec") or {}).get("node_id", "")
    file_part = node_id.split("::", 1)[0]
    if not file_part:
        return None
    combined_output = f"{live.message or ''} {live.actual or ''}".lower()
    if "found no collectors" not in combined_output:
        return None
    dep = _module_level_importorskip_dep(repo_root / file_part)
    if dep is None:
        return None
    excluded = _excluded_heavy_import_names()
    found = _excluded_and_absent(dep, excluded)
    if found is None:
        return None
    return f"skipped -- node in importorskip-guarded fast-tier-excluded file ({found})"


def run_differential(row: dict, repo_root: Path | None = None, base_ref: str = "origin/main") -> DifferentialOutcome:
    """Kernel-entry differential (VF-06 c2): `base_ref` (default origin/main) must FAIL, HEAD/live must PASS."""
    root = Path(repo_root) if repo_root is not None else ROOT
    head_check = materialize_check_in_tree(row, root)
    live = head_check.run()
    if live.status != CheckStatus.PASS:
        skip_reason = _differential_skip_reason(row, live, root)
        if skip_reason is not None:
            return DifferentialOutcome(admitted=False, skipped=True, reason=skip_reason)
        return DifferentialOutcome(
            admitted=False, reason=f"not admitted -- check does not pass on HEAD: {live.message or live.actual}"
        )

    revert_runner = make_worktree_revert_runner(row, ref=base_ref, repo_root=root)
    if not is_admitted(head_check, revert_runner):
        return DifferentialOutcome(admitted=False, reason="not admitted -- revert did not produce FAIL (tautological)")
    resolved = _run_git(["rev-parse", "--verify", "-q", f"{base_ref}^{{commit}}"], root).stdout.strip() or base_ref
    return DifferentialOutcome(admitted=True, reason=f"admitted -- fails on {resolved}, passes on HEAD")


# ---------------------------------------------------------------------------
# Brand-new verifier differential (VF-06 c3)
# ---------------------------------------------------------------------------


@dataclass
class VerifierDifferentialOutcome:
    admitted: bool
    skipped: bool
    reason: str


def _module_name_for(verifier_file: str) -> str:
    rel = verifier_file[:-3] if verifier_file.endswith(".py") else verifier_file
    return rel.replace("\\", "/").replace("/", ".")


def _run_verifier_subprocess(module_name: str, class_name: str, cwd: Path) -> str:
    """Run the verifier in a fresh subprocess (loads the code at ``cwd``, not this process's cache)."""
    cmd = [sys.executable, "-m", "scripts.verification_graduation", "--run-verifier", module_name, class_name]
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
    if result.returncode not in (0, 1):
        raise GraduationError(
            f"verifier subprocess crashed (rc={result.returncode}) for {module_name}.{class_name}: "
            f"{result.stderr.strip()[:500]}"
        )
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise GraduationError(f"verifier subprocess produced no output for {module_name}.{class_name}: {result.stderr[:300]}")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise GraduationError(f"could not parse verifier subprocess output for {module_name}.{class_name}: {exc}") from exc
    status = payload.get("status")
    if not status:
        raise GraduationError(f"verifier subprocess output missing 'status' for {module_name}.{class_name}: {payload}")
    return status


def run_verifier_differential(
    verifier_file: str,
    class_name: str,
    covered_changed: list[str],
    repo_root: Path | None = None,
) -> VerifierDifferentialOutcome:
    """Brand-new-verifier differential (VF-06 c3, same-PR guard exception (b) backstop).

    HERMETIC: HEAD/live must PASS; a HEAD worktree with ``covered_changed`` reverted to
    origin/main content must FAIL. A verifier that still passes with its covered change
    reverted is tautological and rejected.

    NON_HERMETIC_BY_CONSTRUCTION: cannot yield a reliable fail-on-revert -- returns an
    advisory skip (does not block; documented residue distinct from c2's strict refusal).
    """
    root = Path(repo_root) if repo_root is not None else ROOT
    abs_path = root / verifier_file
    try:
        source = abs_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(abs_path))
    except (FileNotFoundError, SyntaxError) as exc:
        raise GraduationError(f"cannot parse verifier file {verifier_file!r}: {exc}") from exc

    class_node = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    if class_node is None:
        raise GraduationError(f"class {class_name!r} not found in {verifier_file!r}")

    if _verifier_is_non_hermetic(class_node):
        return VerifierDifferentialOutcome(
            admitted=False,
            skipped=True,
            reason=(
                "advisory SKIP -- NON_HERMETIC_BY_CONSTRUCTION new verifier cannot yield a "
                "reliable fail-on-revert differential"
            ),
        )

    module_name = _module_name_for(verifier_file)

    live_status = _run_verifier_subprocess(module_name, class_name, root)
    if live_status != "PASS":
        return VerifierDifferentialOutcome(
            admitted=False, skipped=False, reason=f"not admitted -- verifier status={live_status} at HEAD (expected PASS)"
        )

    with git_worktree("HEAD", repo_root=root) as wt_root:
        if covered_changed:
            checkout = _run_git(["checkout", "origin/main", "--", *covered_changed], wt_root)
            if checkout.returncode != 0:
                raise GraduationError(f"could not revert covered files in worktree: {checkout.stderr.strip()}")
        revert_status = _run_verifier_subprocess(module_name, class_name, wt_root)

    if revert_status == "PASS":
        return VerifierDifferentialOutcome(
            admitted=False, skipped=False, reason="not admitted -- verifier passes even with its covered change reverted"
        )
    if revert_status == "FAIL":
        return VerifierDifferentialOutcome(
            admitted=True, skipped=False, reason="admitted -- fails when covered change reverted, passes at HEAD"
        )
    raise GraduationError(
        f"non-deterministic verifier differential status on revert: {revert_status!r} (expected PASS or FAIL)"
    )


# ---------------------------------------------------------------------------
# Subprocess entry point (invoked as `python -m scripts.verification_graduation --run-verifier ...`)
# ---------------------------------------------------------------------------


def _run_verifier_entry(module_name: str, class_name: str) -> None:
    import asyncio
    import importlib

    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    instance = cls()
    result = asyncio.run(instance.run())
    print(json.dumps({"status": result.status.value, "message": result.message}))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--run-verifier":
        _run_verifier_entry(sys.argv[2], sys.argv[3])
    else:
        print("usage: python -m scripts.verification_graduation --run-verifier <module> <class>", file=sys.stderr)
        sys.exit(2)
