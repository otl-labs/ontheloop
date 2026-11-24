"""Verbs egress-container-control and egress-container-request (PLAN-persona-verdict-container-egress).

egress-container-control runs the container probe on Docker's default bridge with no launcher, no proxy and no rules, so each
refusal in the filtered job is shown to be the control's. It expects raw_ip_443, unlisted_name and dns_txt_system reached and
reports every other case. egress-container-request prints the fixed probe request the launcher reads on stdin.
Stdlib only; the pinned reference is read from config/persona_image.yaml as separate image and digest lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from scripts.personas import egress_container_probe as probe
from scripts.personas import egress_floor_container as floor

ROOT = Path(__file__).resolve().parents[2]
PROBE_FILE = ROOT / "scripts" / "personas" / "egress_container_probe.py"
PULL_TIMEOUT = 300
RUN_TIMEOUT = 120
EXPECT_REACHED = ("raw_ip_443", "unlisted_name", "dns_txt_system")
REQUEST = {"persona": "fixture_probe", "provider": "deepseek/", "input": {}}
LINE = "CONTAINER-PROBE case="


def request_main(argv: Sequence[str] = ()) -> int:
    print(json.dumps(REQUEST))
    return 0


def run_argv(image: str, in_dir: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "10001:10001", "--pids-limit", "256", "--memory", "2g", "-v", f"{in_dir}:/run/persona/in:ro",
        "--entrypoint", "python", image, "-I", "/run/persona/in/egress_container_probe.py",
    ]  # fmt: skip


def parse(output: str) -> dict[str, str]:
    got: dict[str, str] = {}
    for line in output.splitlines():
        if line.startswith(LINE):
            fields = dict(part.split("=", 1) for part in line[len("CONTAINER-PROBE ") :].split() if "=" in part)
            got[fields.get("case", "")] = fields.get("got", "")
    return got


def judge(got: dict[str, str]) -> tuple[list[str], list[str]]:
    bad = [case for case in EXPECT_REACHED if got.get(case) != "reached"]
    reported = [f"{case}={got.get(case, 'missing')}" for case in got if case not in EXPECT_REACHED]
    return bad, reported


def main(argv: Sequence[str] = ()) -> int:
    image, digest = floor.read_image_ref(ROOT)
    ref = f"{image}@{digest}"
    pulled = subprocess.run(["docker", "pull", ref], capture_output=True, text=True, timeout=PULL_TIMEOUT, check=False)
    if pulled.returncode != 0:
        print("CONTAINER-CONTROL FAIL pull")
        return 1
    with tempfile.TemporaryDirectory() as in_dir:
        os.chmod(in_dir, 0o755)
        target = Path(in_dir) / "egress_container_probe.py"
        target.write_bytes(PROBE_FILE.read_bytes())
        os.chmod(target, 0o644)
        done = subprocess.run(
            run_argv(ref, in_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_TIMEOUT,
            check=False,
        )
    output = done.stdout + done.stderr
    got = parse(output)
    for case in probe.CASES:
        print(f"CONTAINER-CONTROL case={case[0]} got={got.get(case[0], 'missing')}")
    bad, reported = judge(got)
    print("CONTAINER-CONTROL reported " + " ".join(reported))
    print("CONTAINER-CONTROL PASS" if not bad else "CONTAINER-CONTROL FAIL " + " ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
