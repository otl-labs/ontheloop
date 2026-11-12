"""rec-4891: the adapter's own work stays inside one 40 s deadline; the GitHub transport reads and fails closed."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx

sys.path.insert(0, str(fx.REAL_ROOT / ".claude" / "hooks"))
import merge_gate as mg  # noqa: E402

from scripts.session import merge_checks as mc  # noqa: E402

SHA = "a" * 40


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(mg, "_clock", c)
    return c


def test_retry_path_stays_under_shim_cap(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[float] = []

    def fake_exec(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        asked.append(timeout)
        used = min(timeout, 30.0)
        clock.now += used
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mg, "_exec", fake_exec)
    dl = mg._Deadline()
    start = clock.now
    with pytest.raises(mg._Unavailable) as exc:
        for step in ("fetch", "deepen", "late"):
            mg._git(Path("."), "x", dl=dl, step=step)
    assert "deadline spent" in str(exc.value) and "late" in str(exc.value)
    assert all(t <= mg._BUDGET for t in asked) and clock.now - start <= mg._BUDGET + 25
    assert max(asked) <= 25 and asked[1] <= mg._BUDGET - 25 + 1e-9


def test_slow_reader_get_denies_at_first_chunk_after_deadline(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow(io.BytesIO):
        status = 200

        def read(self, n: int = -1) -> bytes:  # noqa: A003
            clock.now += 3.0
            return b"x" * 10

        def __enter__(self) -> Slow:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(mg, "_urlopen", lambda req, timeout: Slow())
    dl = mg._Deadline()
    with pytest.raises(mg._Unavailable) as exc:
        mg._get_json(("https", "https://api.github.com/repos/o/r"), "rules/branches/main?per_page=100", "rules", dl)
    assert "deadline spent" in str(exc.value)


def test_cleanup_timeout_does_not_change_the_verdict(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exec(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        if "update-ref" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mg, "_exec", fake_exec)
    mg._cleanup(Path("."), 7)


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _http(monkeypatch: pytest.MonkeyPatch, answer: object) -> list[object]:
    seen: list[object] = []

    def fake(req: object, timeout: float) -> object:
        seen.append(req)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mg, "_urlopen", fake)
    return seen


def test_https_transport_urls_and_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = {"total_count": 2, "check_runs": fx.Fixture.green_runs()}
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "pr-validate"}, {"context": "terraform-validate"}]},
        }
    ]
    bodies = [json.dumps(rules).encode(), json.dumps(runs).encode()]
    seen: list[object] = []

    def fake(req: object, timeout: float) -> _Resp:
        seen.append(req)
        return _Resp(bodies[len(seen) - 1])

    monkeypatch.setattr(mg, "_urlopen", fake)
    monkeypatch.setattr(
        mg, "_origin_api", lambda root, owner, repo, dl: ("https", f"https://api.github.com/repos/{owner}/{repo}")
    )
    v = mg._checks_verdict(Path("."), "o", "r", SHA, mg._Deadline())
    assert v.kind == mc.CLEAR
    urls = [r.full_url for r in seen]  # type: ignore[attr-defined]
    assert urls == [
        "https://api.github.com/repos/o/r/rules/branches/main?per_page=100",
        f"https://api.github.com/repos/o/r/commits/{SHA}/check-runs?per_page=100&filter=all",
    ]
    assert all(not any(k.lower() == "authorization" for k in r.headers) for r in seen)  # type: ignore[attr-defined]


@pytest.mark.parametrize("code", [401, 403, 404, 429, 503])
def test_http_errors_are_unavailable_naming_endpoint_and_status(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    _http(monkeypatch, urllib.error.HTTPError("u", code, "m", None, None))  # type: ignore[arg-type]
    with pytest.raises(mg._Unavailable) as exc:
        mg._get_json(
            ("https", "https://api.github.com/repos/o/r"), "rules/branches/main?per_page=100", "rules", mg._Deadline()
        )
    assert str(code) in str(exc.value) and "rules/branches/main" in str(exc.value)


def test_unparseable_and_oversized_bodies_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    for body in (b"{not json", b"x" * (mg._BODY_CAP + 10)):
        _http(monkeypatch, _Resp(body))
        with pytest.raises(mg._Unavailable):
            mg._get_json(("https", "https://api.github.com/repos/o/r"), "p", "rules", mg._Deadline())


def test_read_checks_verdict_on_the_file_seam(tmp_path: Path) -> None:
    f = fx.Fixture(tmp_path)
    green = f.branch_pr(1, "a.txt", "pr\n", name="g")
    red = f.branch_pr(2, "b.txt", "pr\n", name="r")
    f.set_checks(
        red,
        [
            {**fx.Fixture.green_runs()[1], "name": "terraform-validate"},
            {**fx.Fixture.green_runs()[0], "conclusion": "failure"},
        ],
    )
    assert mg.read_checks_verdict(f.work, "otl-labs", "ontheloop", green).kind == mc.CLEAR
    v = mg.read_checks_verdict(f.work, "otl-labs", "ontheloop", red)
    assert v.kind == mc.REFUSE and "pr-validate" in v.reasons[0]


def test_deadline_plus_cleanup_reserve_stays_below_the_shim_limit() -> None:
    shim = (fx.REAL_ROOT / ".claude/hooks/merge_gate.sh").read_text(encoding="utf-8")
    match = re.search(r"timeout -k (\d+) (\d+) ", shim)
    assert match
    assert mg._BUDGET + mg._CLEANUP_RESERVE < int(match.group(2))
