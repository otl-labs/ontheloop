"""The run job's two keyed verbs (PLAN-persona-verdict-fixture-run; T4.32 c3 fixture run, c7 container leg).

persona-request builds the one launcher request {persona, provider, input, key} on stdout: the OIDC token is
claim-checked, the persona-verdict role is assumed, and the provider key is read from SSM in-process and written only to the
pipe. persona-record reads the launcher's stdout result on stdin and writes results/<route>.json for post, plus the host-run
sidecar and the telemetry rows file (never sent). Stdlib plus boto3, jsonschema and yaml; imports nothing from
scripts.telemetry_capture, so post never loads it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import boto3
import jsonschema
import yaml

from scripts.personas import execute, oidc_assume, telemetry_rows
from src.persona_hosts import image_pin
from src.turn_capture import host_run, transcript

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ":environment:persona-verdict"
WORKFLOW_REF = "/.github/workflows/persona-verdict.yml@refs/heads/main"
INFERENCE_PROVIDER = "docs/contracts/inference-provider.yaml"
PROJECT_CONFIGURATION = "docs/contracts/project-configuration.yaml"
KEY_ROW = "ports.run_persona.litellm.api_key.primary"  # pragma: allowlist secret
SETTINGS = Path(".claude") / "settings.json"
MAX_STDIN = 8 * 1024 * 1024
ROUTE = re.compile(r"^[a-z][a-z0-9_]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
AGENT = "canary-persona"
WORKFLOW_PATH = ".github/workflows/persona-verdict.yml"
EXIT_REFUSED = 2
STATUS_LABELS = {2: "REFUSED", 3: "DEFERRED", 4: "HALT"}


class Refusal(Exception):
    """A refusal carrying one fixed class word; never a body, token, key or path."""


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def primary_provider(root: Path) -> str:
    """The primary role's LiteLLM prefix with its slash, read from inference-provider.yaml (the sole provider home)."""
    doc = _load_yaml(root / INFERENCE_PROVIDER)
    try:
        tiers = doc["tier_model"]["tiers"]
        models = doc["model_id_formats"]["litellm_tier_models"]
        tier = next(t for t in tiers if isinstance(t, dict) and t.get("role") == "primary")
        name = str(tier["selection_ref"]).rsplit(".", 1)[-1]
        default = str(models[name]["default_model"])
    except (KeyError, TypeError, StopIteration):
        raise Refusal("provider") from None
    if "/" not in default:
        raise Refusal("provider")
    return default.split("/", 1)[0] + "/"


def key_parameter(root: Path) -> str:
    doc = _load_yaml(root / PROJECT_CONFIGURATION)
    rows = doc.get("settings") if isinstance(doc, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") == KEY_ROW:
            home = (row.get("this_repository") or {}).get("current_home")
            if isinstance(home, str) and home:
                return home
    raise Refusal("key_parameter")


def prepared_request(work: Path, root: Path) -> tuple[str, dict[str, Any]]:
    """The one route and its schema-valid input, or a refusal; no network and no secret is touched."""
    doc = _json(work / "prepared" / "routes.json")
    routes = doc.get("routes") if isinstance(doc, dict) else None
    if not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], str) or not ROUTE.match(routes[0]):
        raise Refusal("routes")
    route = routes[0]
    if route not in execute.fixture_personas(root):
        raise Refusal("route_kind")
    if (work / "prepared" / f"failure-{route}.json").exists():
        raise Refusal("failure_file")
    body = _json(work / "prepared" / f"input-{route}.json")
    schema = execute.schema_for(root, route, "input")
    try:
        if schema is None or not isinstance(body, dict):
            raise ValueError("no schema or body")
        jsonschema.validate(body, schema)
    except (ValueError, jsonschema.ValidationError):
        raise Refusal("input") from None
    return route, body


def assume_role(env: Mapping[str, str]) -> dict[str, str]:
    """Claim-check the OIDC token and assume the persona-verdict role; only fixed marker lines reach stderr."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            jwt = oidc_assume.token(env)
            data = oidc_assume.claims(jwt)
            sub, ref = str(data.get("sub", "")), str(data.get("job_workflow_ref", ""))
            print(f"OIDC-CLAIMS sub={sub} job_workflow_ref={ref}", flush=True)
            if not sub.endswith(ENV_SUFFIX) or ref != env.get("GITHUB_REPOSITORY", "") + WORKFLOW_REF:
                raise Refusal("claims")
            outcome = oidc_assume.assume(oidc_assume.ROLE, jwt, env)
            oidc_assume.report(oidc_assume.ROLE, "ok", outcome)
    except oidc_assume.OidcError:
        raise Refusal("oidc") from None
    if outcome.credentials is None:
        raise Refusal("assume")
    return outcome.credentials


def _ssm_value(creds: Mapping[str, str], region: str, parameter: str) -> str:
    session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )
    try:
        value = session.client("ssm", region_name=region).get_parameter(Name=parameter, WithDecryption=True)
        key = value["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 - class only, never the response or the message
        raise Refusal("ssm") from None
    if not isinstance(key, str) or not key:
        raise Refusal("ssm_empty")
    return key


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def request(env: Mapping[str, str], work: Path, root: Path) -> int:
    """Return 0 after writing exactly one request object to fd 1, else 2 with nothing on stdout and one marker on stderr."""
    try:
        route, body = prepared_request(work, root)
        provider = primary_provider(root)
    except Refusal as exc:
        print(f"PERSONA-REQUEST REFUSED {exc}", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    sys.stdout.flush()
    sys.stderr.flush()
    saved, saved_err = os.dup(1), os.dup(2)
    scratch = tempfile.NamedTemporaryFile("w+b", dir=env.get("RUNNER_TEMP") or None, delete=False)
    failure: str | None = None
    try:
        try:
            parameter = key_parameter(root)
            creds = assume_role(env)
            os.dup2(scratch.fileno(), 1)
            os.dup2(scratch.fileno(), 2)
            key = _ssm_value(creds, env.get("PERSONA_AWS_REGION", ""), parameter)
            _write_all(saved, json.dumps({"persona": route, "provider": provider, "input": body, "key": key}).encode("utf-8"))
        except Refusal as exc:
            failure = str(exc)
        except BaseException:  # noqa: BLE001 - nothing the failure carries may reach a stream
            failure = "error"
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.dup2(saved_err, 2)
        os.close(saved)
        os.close(saved_err)
        scratch.close()
        os.unlink(scratch.name)
    if failure is not None:
        print(f"PERSONA-REQUEST REFUSED {failure}", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    return 0


def request_main(argv: Sequence[str] = ()) -> int:
    return request(os.environ, Path.cwd(), ROOT)


def project_ref(root: Path) -> str | None:
    """Decision 224: project.ref's one home is the --project-ref flag of the SessionStart pin hook in .claude/settings.json."""
    doc = _json(root / SETTINGS)
    try:
        commands = [h.get("command") for g in doc["hooks"]["SessionStart"] for h in g.get("hooks", [])]
    except (KeyError, TypeError, AttributeError):
        return None
    refs: list[str] = []
    for command in commands:
        stem, tokens = transcript.hook_script(command)
        if stem == transcript.PIN_SCRIPT_STEM:
            refs += [transcript.unquoted(t[len(transcript.PIN_FLAG) :]) for t in tokens if t.startswith(transcript.PIN_FLAG)]
    return refs[0] if len(refs) == 1 and host_run.valid_project_ref(refs[0]) else None


def checked_out_sha(root: Path) -> str | None:
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head if SHA.match(head) else None


def sidecar_bytes(env: Mapping[str, str], root: Path) -> bytes | None:
    ref = project_ref(root)
    host: dict[str, Any] = {
        "kind": "gha",
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "job": "run",
        "retry_ordinal": 1,
        "event_name": "workflow_run",
        "repository": env.get("GITHUB_REPOSITORY", ""),
        "workflow_path": WORKFLOW_PATH,
        "ref": env.get("GITHUB_REF", ""),
        "sha": env.get("GITHUB_SHA", ""),
    }
    checkout = checked_out_sha(root)
    if checkout:
        host["checkout_sha"] = checkout
    if ref is None or not host_run.valid_host_facts(host):
        return None
    body = {
        "schema_version": host_run.SCHEMA_VERSION,
        "project_ref": ref,
        "agent": AGENT,
        "content_policy": "withheld",
        "host": host,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def launcher_result(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not {"exit_code", "timed_out", "stdout"} <= set(data):
        return None
    code = data["exit_code"]
    if (
        not (code is None or (type(code) is int))
        or not isinstance(data["timed_out"], bool)
        or not isinstance(data["stdout"], str)
    ):
        return None
    return data


def _telemetry(env: Mapping[str, str], work: Path, results: Path, route: str, events: Any, root: Path) -> None:
    if not isinstance(events, dict):
        return
    sidecar = sidecar_bytes(env, root)
    status = results / f"telemetry-{route}.status"
    if sidecar is None:
        status.write_text("exit 3 DEFERRED\n", encoding="utf-8")
        return
    staging = work / "telemetry-in" / route
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (staging / "host-run.json").write_bytes(sidecar)
    argv = [
        "--events",
        str(staging / "events.json"),
        "--host-run",
        str(staging / "host-run.json"),
        "--out",
        str(results / f"telemetry-{route}.json"),
    ]
    try:
        code = telemetry_rows.main(argv)
    except Exception:  # noqa: BLE001 - recorded, never raised
        code = 4
    if code != 0:
        status.write_text(f"exit {code} {STATUS_LABELS.get(code, 'ERROR')}\n", encoding="utf-8")


def record(env: Mapping[str, str], work: Path, root: Path, raw: bytes) -> int:
    doc = _json(work / "prepared" / "routes.json")
    routes = [r for r in (doc.get("routes") if isinstance(doc, dict) else None) or [] if isinstance(r, str) and ROUTE.match(r)]
    results = work / "results"
    results.mkdir(parents=True, exist_ok=True)

    def put(route: str, value: dict[str, Any]) -> None:
        (results / f"{route}.json").write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    if len(routes) != 1:
        for route in routes:
            put(route, {"route": route, "failure": "input_unusable"})
        return 0
    route = routes[0]
    failure_file = _json(work / "prepared" / f"failure-{route}.json")
    if (work / "prepared" / f"failure-{route}.json").exists():
        failure = failure_file.get("failure") if isinstance(failure_file, dict) else None
        put(route, {"route": route, "failure": failure if isinstance(failure, str) and failure else "input_unusable"})
        return 0
    result = launcher_result(raw)
    if result is None:
        put(route, {"route": route, "failure": "launcher_no_result"})
        return 0
    put(
        route,
        {
            "route": route,
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "stdout": result["stdout"],
            "image_digest": image_pin.load_pin(root).digest,
        },
    )
    _telemetry(env, work, results, route, result.get("events"), root)
    return 0


def record_main(argv: Sequence[str] = ()) -> int:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    return record(os.environ, Path.cwd(), ROOT, raw if len(raw) <= MAX_STDIN else b"")
