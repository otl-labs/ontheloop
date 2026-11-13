"""Shared pytest fixtures for the test suite."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Prevent any subprocess that spawns validate.py from recursing into a full
# lint/test/coverage cycle.  The guard in validate.py main() checks this var
# and exits immediately when it is >= 1.
os.environ.setdefault("_VALIDATE_DEPTH", "1")
os.environ.setdefault("_COVERAGE_SUBPROCESS", "1")

_LLM_CLI_NAMES: frozenset[str] = frozenset({"gemini", "gemini.CMD", "claude", "copilot"})


def _get_executor_env_vars() -> frozenset[str]:
    """Import _EXECUTOR_ACC_VARS from step_runner, or fall back to a hardcoded set.

    Importing from step_runner ensures conftest stays in sync automatically as
    new executor-mode env vars are added.  The fallback prevents conftest from
    breaking if step_runner is unreachable (e.g. early CI bootstrap).
    """
    try:
        from scripts.executor.step_runner import _EXECUTOR_ACC_VARS  # noqa: PLC0415

        return _EXECUTOR_ACC_VARS | {"COPILOT_STEP_TIMEOUT_SECS"}
    except ImportError:
        return frozenset(
            {
                "SKIP_CI_WAIT",
                "SKIP_CODE_REVIEW",
                "COPILOT_MODEL_EXECUTION",
                "COPILOT_MODEL_PLANNING",
                "CI_FIX_RETRIES",
                "COPILOT_STEP_TIMEOUT_SECS",
            }
        )


@pytest.fixture(autouse=True)
def _clear_executor_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip executor-mode env vars so they never leak into test assertions.

    Derives the var list from scripts.executor.step_runner._EXECUTOR_ACC_VARS
    so new executor env vars are automatically covered without updating conftest.
    """
    for var in _get_executor_env_vars():
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_push_context_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip GITHUB_EVENT_NAME/GITHUB_EVENT_BEFORE for every test.

    GITHUB_EVENT_NAME is a GitHub Actions default env var present for the WHOLE job
    process (e.g. "push" throughout main-validate's push-triggered run), not just for
    actual git operations. Left unmasked, scripts.checks._common.push_context_base()
    would activate unconditionally for every test in a push-triggered CI run, breaking
    mocked-subprocess tests that assume today's non-push get_changed_files() /
    get_status_aware_diff() / get_changed_source_files() behaviour. Individual
    push-context tests opt back in via their own monkeypatch.setenv.
    """
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)


_NONEXISTENT_AWS_DIR_SEGMENT = "nonexistent-aws-config-dir"


@pytest.fixture(autouse=True)
def _hermetic_aws_profile(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """L1 (rec-2484): force boto3.Session(profile_name=...) to raise ProfileNotFound everywhere.

    Consolidates the former _clear_aws_credential_env fixture. Deletes the ambient
    credential-signal env vars scripts.aws_profile.resolve_aws_profile and boto3 consult
    (AWS_PROFILE, AWS_DEFAULT_PROFILE, AWS_ACCESS_KEY_ID, AWS_LAMBDA_FUNCTION_NAME) and
    OTL_OPS_VERBS_TRANSPORT (an agent environment exports it after cutover; without this the
    clients' Function URL nodes would take the execute-api path) -- a
    delete-credential-signals design, never a fake AWS_ACCESS_KEY_ID, which would flip
    resolve_aws_profile to None and silently break named-profile resolution
    (tests/test_aws_profile.py) -- and redirects AWS_CONFIG_FILE / AWS_SHARED_CREDENTIALS_FILE
    to a nonexistent path so no real profile (named or default) can resolve regardless of what
    is on disk in ~/.aws, identically on dev and CI. Also disables the EC2 instance-metadata
    credential provider so the default chain cannot fall through to IMDS. The OIDC
    main-validate runner exports AWS_ACCESS_KEY_ID into the job env before pytest, which
    would otherwise flip named-profile assertions to None only on CI; deleting it keeps unit
    tests deterministic across local and CI. @pytest.mark.integration tests opt out -- they
    need real AWS access. Uses get_closest_marker (not own_markers) so a class- or
    module-level @pytest.mark.integration decorator is honoured, not just a method-level one.
    """
    if request.node.get_closest_marker("integration") is not None:
        return
    for var in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_LAMBDA_FUNCTION_NAME",
        "OTL_OPS_VERBS_TRANSPORT",
    ):
        monkeypatch.delenv(var, raising=False)
    nonexistent_dir = tmp_path / _NONEXISTENT_AWS_DIR_SEGMENT
    monkeypatch.setenv("AWS_CONFIG_FILE", str(nonexistent_dir / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(nonexistent_dir / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.fixture(autouse=True)
def _block_unmocked_aws_client(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """L2 (rec-2484): raise loudly on any un-mocked AWS client construction.

    botocore.session.Session.create_client is the single chokepoint underneath
    boto3.client(), boto3.resource(), and Session().client() -- patching it here catches
    every construction path in one place, mirroring the _block_llm_cli_subprocess idiom
    above. L1 already makes profile resolution fail (ProfileNotFound) for the common case;
    this layer is defense-in-depth against the residual paths where a client could still be
    built (e.g. the boto3 default credential chain, which does not require a named profile
    at all). Tests that genuinely construct a client -- mocked (moto, Stubber) or live -- opt
    in with @pytest.mark.aws. This is a separate opt-out from L1's @pytest.mark.integration:
    a live-AWS integration test needs @pytest.mark.aws too to fully bypass both layers. Uses
    get_closest_marker (not own_markers) so a class- or module-level @pytest.mark.aws
    decorator is honoured, not just a method-level one. boto3/botocore are
    deliberately excluded from requirements-fast.txt (rec-2485, ~3GB of heavy wheels) -- this
    fixture is autouse and runs for every test in every tier, so the import is guarded the
    same way _get_executor_env_vars/_isolate_plans_jsonl above guard
    their own optional imports: a genuinely-absent botocore means no test in this process can
    construct a real client anyway, so the guard has nothing to do.
    """
    if request.node.get_closest_marker("aws") is not None:
        return

    try:
        import botocore.session as _botocore_session  # noqa: PLC0415
    except ImportError:
        return

    def _guarded_create_client(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Unit test constructed a real AWS client (boto3.client/resource/Session().client) "
            "without mocking it. Mock the client (moto, unittest.mock.patch, botocore Stubber) "
            "and mark the test @pytest.mark.aws, or mark it @pytest.mark.integration (+ "
            "@pytest.mark.aws) if it legitimately needs real AWS access (rec-2484)."
        )

    monkeypatch.setattr(_botocore_session.Session, "create_client", _guarded_create_client)


@pytest.fixture(autouse=True)
def _isolate_plans_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect PLANS_JSONL to a per-test temp file.

    Prevents executor-plan tests from writing to the tracked
    logs/.execution-plans.jsonl.  Tests that explicitly patch PLANS_JSONL
    themselves will simply override this fixture's value.
    """
    try:
        import scripts.executor.plan as _plan_mod  # noqa: PLC0415

        monkeypatch.setattr(_plan_mod, "PLANS_JSONL", tmp_path / "plans.jsonl")
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _isolate_selection_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect DEBUG_MANIFEST_PATH to a per-test temp file.

    Prevents tests/validate/ orchestrator tests that drive _validate.main() --pre
    (with the real emit_manifest) from writing to the tracked
    logs/debug/selection-manifest.json.  Tests that explicitly patch
    DEBUG_MANIFEST_PATH (or call emit_manifest with an explicit repo_root)
    themselves will simply override this fixture's value.
    """
    try:
        import scripts.checks.deps.affected_tests as _at  # noqa: PLC0415

        monkeypatch.setattr(_at, "DEBUG_MANIFEST_PATH", tmp_path / "selection-manifest.json")
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _patch_write_run_summary():  # type: ignore[misc]
    """Prevent executor tests from writing real logs/runs/*.json artifacts.

    Patches write_run_summary at the module level so individual tests do not
    need to remember to mock it themselves.  Yields the patcher so tests that
    need the real function can call ``.stop()`` / ``.start()``.
    """
    patcher = patch(
        "scripts.execute_recommendation.write_run_summary",
        return_value=None,
    )
    patcher.start()
    yield patcher
    patcher.stop()


@pytest.fixture(autouse=True)
def _allow_network_for_integration(request: pytest.FixtureRequest) -> None:
    """Re-enable sockets for @pytest.mark.integration tests.

    --disable-socket in pyproject.toml addopts blocks network I/O globally.
    Integration tests need real AWS/S3 access; this fixture selectively lifts
    the block when the test node carries @pytest.mark.integration.
    Integration skip-fixtures (e.g. tests/test_ducklake_spike.py)
    must request this fixture so the probe's own network call runs only after
    sockets are restored. Uses get_closest_marker (not own_markers) so a class-
    or module-level @pytest.mark.integration decorator is honoured, not just a
    method-level one.
    """
    if request.node.get_closest_marker("integration") is None:
        return
    from pytest_socket import enable_socket  # noqa: PLC0415

    enable_socket()


@pytest.fixture(autouse=True)
def _block_llm_cli_subprocess(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against CI/local drift where LLM CLIs exist on dev but not on Ubuntu CI runners.

    Uses get_closest_marker (not own_markers) so a class- or module-level
    @pytest.mark.integration decorator is honoured, not just a method-level one.
    """
    if request.node.get_closest_marker("integration") is not None:
        return

    import subprocess as _sp  # noqa: PLC0415

    _orig_run = _sp.run

    def _guarded_run(args, *a, **kw):  # type: ignore[no-untyped-def]
        cmd = args[0] if isinstance(args, (list, tuple)) else args
        name = Path(str(cmd)).name
        if name in _LLM_CLI_NAMES:
            raise RuntimeError(
                f"Unit test reached LLM CLI '{cmd}' without mocking. "
                "Patch subprocess.run or the calling function before this point, "
                "or mark the test @pytest.mark.integration."
            )
        return _orig_run(args, *a, **kw)

    monkeypatch.setattr(_sp, "run", _guarded_run)
