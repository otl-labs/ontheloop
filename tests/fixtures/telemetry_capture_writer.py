"""The in-memory telemetry writer and the capture pass helpers shared by the runner's mirror tests.

Moved from tests/telemetry_capture/test_runner.py so test_runner.py and test_runner_catalog.py share them without importing
each other (fixtures are exempt from the cross-test import guard). The writer is parametrised by the catalog its describe
names (meta_schema, data_path_is_smoke), which a test may change between passes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.telemetry_capture import runner
from scripts.telemetry_capture.state import CaptureState, state_dir
from scripts.telemetry_capture.transport import Budget, WriterClient
from tests.fixtures.turn_capture_corpus import SID, assistant, hook, prompt, text_block

REF = "example/project"
PIN = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref={}'
URL = "https://abc.lambda-url.eu-west-2.on.aws"
ON = {"CLAUDE_CODE_REMOTE": "true"}


class Writer:
    """An in-memory telemetry writer at the HTTP seam: identical re-sends are no-ops, a diverged key is 409."""

    def __init__(
        self,
        *,
        max_items: int = 200,
        meta_schema: str = "ducklake_smoke",
        data_path_is_smoke: bool = True,
        tenant: bool = True,
    ) -> None:
        self.max_items, self.tenant = max_items, tenant
        self.meta_schema, self.data_path_is_smoke = meta_schema, data_path_is_smoke
        self.posts: list[dict[str, Any]] = []
        self.raw: list[bytes] = []
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.objects: set[str] = set()
        self.answers: list[Any] = []
        self.unverified = 0

    def __call__(self, method: str, url: str, data: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        assert 0 < timeout <= 130
        self.raw.append(data)
        if method == "PUT":
            self.objects.add(hashlib.sha256(data).hexdigest())
            return 200, b""
        payload = json.loads(data)
        self.posts.append(payload)
        status, body = self.answer(payload)
        return status, json.dumps(body).encode()

    def appends(self) -> list[dict[str, Any]]:
        return [p for p in self.posts if p["action"] == "append_telemetry"]

    def answer(self, payload: dict[str, Any]) -> tuple[int, Any]:
        action = payload["action"]
        if action == "describe":
            caps = {"max_invoke_body_bytes": 5242880, "max_rows_per_table": 2000, "max_rows_per_request": 5000}
            blob = {"version": 1, "inline_threshold_bytes": 65536, "cap_bytes": 8388608, "max_items": self.max_items}
            return 200, {
                "ok": True,
                "actions": ["append_telemetry", "begin_blobs", "describe", "register_tenant"],
                "verbs": {"append_telemetry": {"caps": caps}},
                "catalog": {"meta_schema": self.meta_schema, "data_path_is_smoke": self.data_path_is_smoke},
                "blob_protocol": blob,
            }
        if action == "register_tenant":
            self.tenant = True
            return 200, {"ok": True, "created": True}
        if action == "begin_blobs":
            items = [
                {"existed": True, "upload": None}
                if i["content_sha256"] in self.objects
                else {"existed": False, "upload": {"url": "https://bucket.s3.invalid/" + i["content_sha256"], "headers": {}}}
                for i in payload["items"]
            ]
            return 200, {"ok": True, "items": items}
        if self.answers:
            scripted = self.answers.pop(0)
            if scripted is not None:
                return scripted
        if not self.tenant:
            return 403, {"ok": False, "error_type": "tenant_unresolved"}
        tables: dict[str, dict[str, int]] = {}
        staged: dict[tuple[str, str], dict[str, Any]] = {}
        for batch in payload["batches"]:
            counts = tables.setdefault(batch["table"], {"submitted": 0, "collapsed": 0, "inserted": 0})
            for index, row in enumerate(batch["rows"]):
                counts["submitted"] += 1
                if "content_sha256" in row and "content" not in row and row["content_sha256"] not in self.objects:
                    self.unverified += 1
                    return 422, {"ok": False, "error_type": "blob_not_verified", "row": index}
                key = (batch["table"], row["external_ref"])
                held = self.rows.get(key, staged.get(key))
                if held is not None and held != row:
                    return 409, {"ok": False, "error_type": "grain_conflict", "table": batch["table"], "row": index}
                if held is None:
                    staged[key] = row
                    counts["inserted"] += 1
        self.rows.update(staged)
        return 200, {"ok": True, "tables": tables}


def factory(writer: Writer):
    def make(env, state, budget, **kw):
        return WriterClient(URL, lambda body: {}, budget, http=writer, sleep=lambda s: None, rng=lambda: 0.0)

    return make


def write(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
    return tmp_path / f"{SID}.jsonl"


def repo(tmp_path: Path, enabled: bool = True) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "telemetry").mkdir(parents=True)
    (root / "config" / "telemetry" / "capture.yaml").write_text(f"enabled: {str(enabled).lower()}\n", encoding="utf-8")
    return root


def records(n: int = 1, ref: str = REF) -> list[dict[str, Any]]:
    out = [hook("h0", 0.5, "SessionStart:startup", command=PIN.format(ref))]
    for i in range(n):
        out += [prompt(f"u{i}", 1 + 10 * i, f"p{i}", f"go {i}"), assistant(f"a{i}", 2 + 10 * i, f"m{i}", [text_block("ok")])]
    return [*out, prompt(f"u{n}", 1 + 10 * n, f"p{n}", f"go {n}")]


def run(tmp_path: Path, writer: Writer, *, final: bool = False, env=ON, seconds: float = 600.0, **kw: Any):
    return runner.run_pass(
        tmp_path / f"{SID}.jsonl",
        SID,
        "Stop",
        final=final,
        budget=Budget.start(seconds),
        env=env,
        repo_root=kw.pop("repo_root", None) or (tmp_path / "repo" if (tmp_path / "repo").exists() else repo(tmp_path)),
        client_factory=kw.pop("client_factory", factory(writer)),
        sleep=lambda s: None,
        **kw,
    )


def state(tmp_path: Path) -> CaptureState:
    return CaptureState(state_dir(tmp_path / f"{SID}.jsonl", SID))
