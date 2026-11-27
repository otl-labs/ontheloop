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
from src.turn_capture import host_run, refs, transcript

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ":environment:persona-verdict"
WORKFLOW_REF = "/.github/workflows/persona-verdict.yml@refs/heads/main"
INFERENCE_PROVIDER = "docs/contracts/inference-provider.yaml"
PROJECT_CONFIGURATION = "docs/contracts/project-configuration.yaml"
KEY_ROW_PREFIX = "ports.run_persona.litellm.api_key."  # pragma: allowlist secret
JOB_ROLES = {"run": "primary", "run-fallback": "warm_fetched_escape_hatch"}
SETTINGS = Path(".claude") / "settings.json"
MAX_STDIN = 8 * 1024 * 1024
MAX_READS = 2000
TREE_CHUNK = 1 << 20
ROUTE = re.compile(r"^[a-z][a-z0-9_]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGITS = re.compile(r"^[0-9]{1,20}$")
ATTEMPT = re.compile(r"^[0-9]{1,6}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
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


def role_provider(root: Path, role: str) -> str:
    """The role's LiteLLM prefix with its slash, read from inference-provider.yaml (the sole provider home)."""
    doc = _load_yaml(root / INFERENCE_PROVIDER)
    try:
        tiers = doc["tier_model"]["tiers"]
        models = doc["model_id_formats"]["litellm_tier_models"]
        tier = next(t for t in tiers if isinstance(t, dict) and t.get("role") == role)
        name = str(tier["selection_ref"]).rsplit(".", 1)[-1]
        default = str(models[name]["default_model"])
    except (KeyError, TypeError, StopIteration):
        raise Refusal("provider") from None
    if "/" not in default:
        raise Refusal("provider")
    return default.split("/", 1)[0] + "/"


def key_parameter(root: Path, role: str) -> str:
    doc = _load_yaml(root / PROJECT_CONFIGURATION)
    rows = doc.get("settings") if isinstance(doc, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") == KEY_ROW_PREFIX + role:
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
    if route not in execute.keyed_personas(root):
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


def job_role(env: Mapping[str, str]) -> str:
    """The role this job runs: PERSONA_ROLE must be a known role and pair with GITHUB_JOB, else a refusal."""
    role, job = env.get("PERSONA_ROLE", ""), env.get("GITHUB_JOB", "")
    if role not in JOB_ROLES.values() or JOB_ROLES.get(job) != role:
        raise Refusal("role")
    return role


def tree_names(root: Path, route: str) -> list[str]:
    contract = execute._contract(root, route) or {}  # noqa: SLF001 -- the one contract loader
    caps = contract.get("capabilities") or []
    return [c.split(":", 1)[1] for c in caps if isinstance(c, str) and c.startswith("read_tree:") and ":" in c]


def _stream_file(fd: int, path: Path) -> None:
    with open(path, "rb") as handle:
        while chunk := handle.read(TREE_CHUNK):
            _write_all(fd, chunk)


def _tree_fields(route: str, work: Path, root: Path) -> tuple[dict[str, Any], Path | None]:
    names = tree_names(root, route)
    if not names:
        return {}, None
    fields: dict[str, Any] = {"trees": names}
    if "head" not in names:
        return fields, None
    tar = work / "prepared" / "head.tar"
    try:
        size = tar.stat().st_size
    except OSError:
        raise Refusal("head_tree") from None
    fields["head_tree_bytes"] = size
    return fields, tar


def request(env: Mapping[str, str], work: Path, root: Path) -> int:
    """Return 0 after writing exactly one request object to fd 1, else 2 with nothing on stdout and one marker on stderr."""
    try:
        role = job_role(env)
        route, body = prepared_request(work, root)
        provider = role_provider(root, role)
        tree_fields, tar = _tree_fields(route, work, root)
    except Refusal as exc:
        print(f"PERSONA-REQUEST REFUSED {exc}", file=sys.stderr, flush=True)
        return EXIT_REFUSED
    print(f"PERSONA-REQUEST role={role} provider={provider}", file=sys.stderr, flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved, saved_err = os.dup(1), os.dup(2)
    scratch = tempfile.NamedTemporaryFile("w+b", dir=env.get("RUNNER_TEMP") or None, delete=False)
    failure: str | None = None
    try:
        try:
            parameter = key_parameter(root, role)
            creds = assume_role(env)
            os.dup2(scratch.fileno(), 1)
            os.dup2(scratch.fileno(), 2)
            key = _ssm_value(creds, env.get("PERSONA_AWS_REGION", ""), parameter)
            line = {"persona": route, "provider": provider, "input": body, "key": key, **tree_fields}
            _write_all(saved, json.dumps(line).encode("utf-8") + b"\n")
            if tar is not None:
                _stream_file(saved, tar)
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


def sidecar_bytes(env: Mapping[str, str], root: Path, agent: str) -> bytes | None:
    ref = project_ref(root)
    if env.get("GITHUB_JOB", "") not in JOB_ROLES:
        return None
    host: dict[str, Any] = {
        "kind": "gha",
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "job": env.get("GITHUB_JOB", ""),
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
        "agent": agent,
        "content_policy": "withheld",
        "host": host,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def run_ref(env: Mapping[str, str]) -> str | None:
    """The run's telemetry_agents entity_ref (retry ordinal 1), rendered as the producer does; None for invalid run facts."""
    run_id, attempt, job = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", ""), env.get("GITHUB_JOB", "")
    if not (DIGITS.match(run_id) and ATTEMPT.match(attempt) and job in JOB_ROLES):
        return None
    host = host_run.HostRun("", "", "withheld", run_id, attempt, job, 1, "workflow_run", "", WORKFLOW_PATH, "", "", None)
    return refs.litellm_run_ref(refs.litellm_root_ref(host_run.render_host_run_ref(host)))


def served_models(events: Any) -> list[str]:
    turns = events.get("turns") if isinstance(events, dict) else None
    models: set[str] = set()
    for turn in turns if isinstance(turns, list) else []:
        rec = turn.get("record") if isinstance(turn, dict) else None
        model = (rec.get("model") or rec.get("requested_model")) if isinstance(rec, dict) else None
        if isinstance(model, str) and model:
            models.add(model)
    return sorted(models)


def started_at(results: Path, route: str) -> str | None:
    doc = _json(results / f"telemetry-{route}.json")
    for batch in (doc.get("batches") if isinstance(doc, dict) else None) or []:
        for row in (batch.get("rows") if isinstance(batch, dict) and batch.get("table") == "telemetry_agents" else None) or []:
            value = row.get("session_started_at") if isinstance(row, dict) else None
            if isinstance(value, str):
                return value
    return None


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
    status = results / f"telemetry-{route}.status"
    agent = execute.registry_key(root, route)
    if agent is None:
        status.write_text(f"exit {EXIT_REFUSED} {STATUS_LABELS[EXIT_REFUSED]} registry_key\n", encoding="utf-8")
        return
    sidecar = sidecar_bytes(env, root, agent)
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


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def primary_outage(result: dict[str, Any] | None) -> bool:
    """True only for a non-success result whose proxy counts show no 2xx and an outage or a request left in flight."""
    if result is None or (result["exit_code"] == 0 and result["timed_out"] is False):
        return False
    upstream = result.get("upstream")
    if not isinstance(upstream, dict) or not all(_count(upstream.get(k)) for k in ("ok", "outage", "inflight")):
        return False
    return upstream["ok"] == 0 and (upstream["outage"] >= 1 or upstream["inflight"] >= 1)


def record(env: Mapping[str, str], work: Path, root: Path, raw: bytes) -> int:
    job = env.get("GITHUB_JOB", "")
    result = launcher_result(raw)
    code = _record(env, work, root, result)
    if job == "run" and code == 0:
        with open(env.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as handle:
            handle.write(f"primary_outage={'true' if primary_outage(result) else 'false'}\n")
    return code


def _reads(events: Any) -> tuple[list[dict[str, Any]], bool] | None:
    turns = events.get("turns") if isinstance(events, dict) else None
    if not isinstance(turns, list):
        return None
    reads: list[dict[str, Any]] = []
    dropped = False
    for turn in turns:
        rows = turn.get("reads") if isinstance(turn, dict) else None
        if rows is None:
            continue
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                return None
            if len(reads) >= MAX_READS:
                dropped = True
            else:
                reads.append(row)
    return reads, dropped


def _clean_path(value: Any) -> str:
    return "/".join(p for p in str(value).split("/") if p not in ("", "."))


def _not_examined(body: Any, reads: list[dict[str, Any]]) -> list[str] | None:
    hits = body.get("reference_hits") if isinstance(body, dict) else None
    if not isinstance(hits, dict):
        return None
    read = {
        _clean_path(r.get("path"))
        for r in reads
        if r.get("tree") == "head" and r.get("op") == "read" and r.get("result") == "ok"
    }
    out: set[str] = set()
    for term in hits.get("terms") or []:
        for hit in (term.get("hits") or []) if isinstance(term, dict) else []:
            path = _clean_path(hit.get("path")) if isinstance(hit, dict) else ""
            if path and hit.get("in_bundle") is False and path not in read:
                out.add(path)
    return sorted(out)


def _record(env: Mapping[str, str], work: Path, root: Path, result: dict[str, Any] | None) -> int:
    doc = _json(work / "prepared" / "routes.json")
    routes = [r for r in (doc.get("routes") if isinstance(doc, dict) else None) or [] if isinstance(r, str) and ROUTE.match(r)]
    results = work / "results"
    results.mkdir(parents=True, exist_ok=True)

    ref = run_ref(env)

    def put(route: str, value: dict[str, Any]) -> None:
        value = {**value, "run_ref": ref} if ref else value
        (results / f"{route}.json").write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    if len(routes) != 1:
        for route in routes:
            put(route, {"route": route, "failure": "input_unusable"})
        return 0
    route = routes[0]
    failure_file = _json(work / "prepared" / f"failure-{route}.json")
    if (work / "prepared" / f"failure-{route}.json").exists():
        failure = failure_file.get("failure") if isinstance(failure_file, dict) else None
        put(
            route,
            {
                "route": route,
                "failure": failure if isinstance(failure, str) and failure else "input_unusable",
                "reads_unknown": True,
            },
        )
        return 0
    if result is None:
        put(route, {"route": route, "failure": "launcher_no_result", "reads_unknown": True})
        return 0
    _telemetry(env, work, results, route, result.get("events"), root)
    try:
        slots = execute.guidance_slots(root, route)
    except ValueError:
        put(route, {"route": route, "failure": "input_unusable"})
        return 0
    guidance: dict[str, str] = {}
    if slots:
        body = _json(work / "prepared" / f"input-{route}.json")
        for slot in slots:
            value = body.get(slot) if isinstance(body, dict) else None
            if not isinstance(value, str):
                put(route, {"route": route, "failure": "input_unusable"})
                return 0
            guidance[slot] = value
    recorded: dict[str, Any] = {
        "route": route,
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "stdout": result["stdout"],
        "image_digest": image_pin.load_pin(root).digest,
    }
    if guidance:
        recorded["guidance"] = guidance
    if ref:
        header = result["events"].get("header") if isinstance(result.get("events"), dict) else None
        digest = header.get("rendered_prompt_sha256") if isinstance(header, dict) else None
        recorded["session_started_at"] = started_at(results, route)
        recorded["definition_sha256"] = digest if isinstance(digest, str) and HEX64.match(digest) else None
        recorded["served_models"] = served_models(result.get("events"))
    found = _reads(result.get("events"))
    resolution = _json(work / "prepared" / "resolution.json")
    recorded["trees"] = {
        "head": resolution.get("head_sha") if isinstance(resolution, dict) else None,
        "main": checked_out_sha(root),
    }
    if found is None:
        recorded["reads_unknown"] = True
    else:
        recorded["reads"] = found[0]
        if found[1]:
            recorded["reads_dropped"] = True
        unseen = _not_examined(_json(work / "prepared" / f"input-{route}.json"), found[0])
        if unseen is not None:
            recorded["not_examined"] = unseen
    put(route, recorded)
    return 0


def record_main(argv: Sequence[str] = ()) -> int:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    return record(os.environ, Path.cwd(), ROOT, raw if len(raw) <= MAX_STDIN else b"")
