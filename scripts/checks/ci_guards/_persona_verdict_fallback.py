"""The run-fallback pins validate_persona_verdict_chain reaches through _persona_verdict_keyed (rec-5559).

run-fallback is the one extra keyed job: it runs the persona once on the warm_fetched_escape_hatch role when the run job's
proxy saw the primary provider fail (or when a fixture-only drill head asks for it). Its condition, the run and prepare outputs
that feed it, the per-job PERSONA_ROLE literals, its permissions and Environment and its closed job-level key set are pinned
here. KEYED_IF and KEYPREP_OK arrive as parameters and nothing is imported from _persona_verdict_keyed.py (no circular import).
"""

from __future__ import annotations

from typing import Any

VERDICT = ".github/workflows/persona-verdict.yml"
FALLBACK_JOB = "run-fallback"
ROLES = {"run": "primary", FALLBACK_JOB: "warm_fetched_escape_hatch"}
OUTAGE_DISJUNCT = "(needs.run.outputs.primary_outage == 'true' || needs.prepare.outputs.fallback_drill == 'true')"
PERMISSIONS = {"contents": "read", "id-token": "write"}
JOB_KEYS = {"needs", "if", "environment", "runs-on", "timeout-minutes", "permissions", "steps"}
PREPARE_OUTPUTS = {
    "allowlisted": "${{ steps.resolve.outputs.allowlisted }}",
    "fixture": "${{ steps.route.outputs.fixture }}",
    "fallback_drill": "${{ steps.route.outputs.fallback_drill }}",
    "keyed": "${{ steps.route.outputs.keyed }}",
}


def run_fallback_condition(keyed_if: str, keyprep_ok: str) -> str:
    return f"{keyed_if} && {keyprep_ok} && {OUTAGE_DISJUNCT}"


def _map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _persona_id(job: dict[str, Any]) -> str | None:
    for step in job.get("steps") or []:
        if isinstance(step, dict) and step.get("name") == "Run the fixture persona":
            return str(step.get("id"))
    return None


def fallback_problems(jobs: dict[str, Any]) -> list[str]:
    w = f"{VERDICT}: run-fallback"
    out: list[str] = []
    fallback, run = _map(jobs.get(FALLBACK_JOB)), _map(jobs.get("run"))
    want_outputs = {"primary_outage": f"${{{{ steps.{_persona_id(run)}.outputs.primary_outage }}}}"}
    if run.get("outputs") != want_outputs:
        out.append(f"{w}: the run job's outputs are not exactly {want_outputs}")
    if _map(jobs.get("prepare")).get("outputs") != PREPARE_OUTPUTS:
        out.append(f"{w}: prepare's outputs are not exactly {sorted(PREPARE_OUTPUTS)} with their pinned expressions")
    for name, job in jobs.items():
        text = repr({k: v for k, v in _map(job).items() if k != "steps"})
        if name not in ("run", FALLBACK_JOB, "prepare") and ("primary_outage" in text or "fallback_drill" in text):
            out.append(f"{w}: job {name} names primary_outage or fallback_drill")
    for name, role in ROLES.items():
        for step in _map(jobs.get(name)).get("steps") or []:
            if isinstance(step, dict) and step.get("name") == "Run the fixture persona":
                if _map(step.get("env")).get("PERSONA_ROLE") != role:
                    out.append(f"{w}: job {name} PERSONA_ROLE is not exactly {role}")
    if fallback.get("permissions") != PERMISSIONS or fallback.get("permissions") != run.get("permissions"):
        out.append(f"{w}: permissions are not exactly {PERMISSIONS}, run's")
    if fallback.get("environment") != "persona-verdict" or fallback.get("environment") != run.get("environment"):
        out.append(f"{w}: the Environment is not exactly persona-verdict, run's")
    extra = sorted(set(fallback) - JOB_KEYS)
    if extra:
        out.append(f"{w}: job-level keys {extra} are outside the closed set (no strategy, matrix, outputs or env)")
    return out
