"""The fixed root launcher (PLAN-persona-verdict-container-egress; T4.32 c7 container leg).

Runs only as /usr/local/lib/persona-egress/egress_launcher.py through one sudoers rule: `egress_launcher.py probe|run`.
Stdlib only; imports nothing from the checkout. It takes one JSON request on stdin and no path, network, engine, image or
entrypoint from its caller: the image, the provider rows, the network and every flag are constants or the root-owned
snapshot. The key reaches only the key-adding proxy's stdin; the container gets a placeholder and the proxy's address.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

INSTALL_DIR = Path("/usr/local/lib/persona-egress")
RUN_DIR = Path("/run/persona-egress")
MANIFEST_NAME = "manifest.json"
SNAPSHOT_NAME = "snapshot.json"
FILES = ("egress_launcher.py", "egress_proxy.py", "egress_container_probe.py", "egress_trees.py", SNAPSHOT_NAME)
DOCKER = "/usr/bin/docker"
PYTHON = "/usr/bin/python3"
PRLIMIT = "/usr/bin/prlimit"
TREES_MOUNT = "/trees"
MAIN_TREE = INSTALL_DIR / "trees" / "main"
TREE_SECONDS = 300
TREE_CHUNK = 1 << 20
NETWORK = "persona-egress"
CONTAINER = "persona-egress-run"
PROXY_USER = "persona-egress-proxy"
GATEWAY = "172.31.250.1"
PROXY_PORT = 18443
DECOY_PORT = 18444
PLACEHOLDER = "placeholder-no-key"
CONTAINER_UID = 10001
IN_MOUNT = "/run/persona/in"
EVENTS_MOUNT = "/run/persona/events"
MAX_REQUEST = 64 * 1024 * 1024
MAX_RESULT = 4 * 1024 * 1024
MAX_COUNTS_LINES = 4096
MAX_COUNTS_LINE = 256
COUNT_KEYS = {"ok", "outage", "inflight"}
SETTLE_SECONDS = 0.3
BOUNDS = {"probe": 120, "run": 3600}
GONE_SECONDS = 30.0
GONE_STEP = 0.5
KEYS = {"persona", "provider", "input", "key", "trees", "head_tree_bytes"}
PERSONA = re.compile(r"^[a-z][a-z0-9_]*$")
EXIT_REFUSED = 2


class Refused(Exception):
    pass


def refuse(reason: str) -> NoReturn:
    print(f"LAUNCHER-REFUSED {reason}", file=sys.stderr)
    raise SystemExit(EXIT_REFUSED)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Refused("duplicate key")
        out[key] = value
    return out


def parse_request(raw: bytes, mode: str, providers: dict[str, Any]) -> dict[str, Any]:
    if len(raw) > MAX_REQUEST:
        raise Refused(f"request over {MAX_REQUEST // (1024 * 1024)} MiB")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except UnicodeDecodeError as exc:
        raise Refused("request is not UTF-8") from exc
    except ValueError as exc:
        raise Refused("request is not valid JSON") from exc
    if not isinstance(data, dict):
        raise Refused("top level is not an object")
    if set(data) - KEYS:
        raise Refused("unknown key in request")
    persona, provider = data.get("persona"), data.get("provider")
    if not isinstance(persona, str) or not PERSONA.match(persona):
        raise Refused("persona is not a lowercase identifier")
    if not isinstance(provider, str) or provider not in providers:
        raise Refused("provider is not in the snapshot")
    if not isinstance(data.get("input"), dict):
        raise Refused("input is not an object")
    key = data.get("key")
    if mode == "probe":
        if "key" in data:
            raise Refused("probe mode takes no key")
    else:
        if not isinstance(key, str) or not key:
            raise Refused("run mode needs a non-empty key string")
        if any(not 0x21 <= ord(c) <= 0x7E for c in key):
            raise Refused("key has a character outside printable ASCII")
    return data


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_install(directory: Path = INSTALL_DIR, own: Path | None = None) -> dict[str, Any]:
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        info = os.stat(directory / MANIFEST_NAME)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused("manifest is not root-owned and closed to group and other write")
        for name in FILES:
            path = directory / name
            if sha256_of(path) != manifest["files"][name]:
                raise Refused(f"{name} does not match the manifest")
        if own is not None and sha256_of(own) != manifest["files"]["egress_launcher.py"]:
            raise Refused("the running launcher does not match the manifest")
        snapshot = json.loads((directory / SNAPSHOT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f"install check failed: {type(exc).__name__}") from exc
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("providers"), dict) or not snapshot.get("image"):
        raise Refused("snapshot is malformed")
    return snapshot


def proxy_argv(provider: dict[str, Any], directory: Path = INSTALL_DIR) -> list[str]:
    host = str(provider["host"]).rsplit(":", 1)[0]
    return [
        PYTHON, "-I", "-S", str(directory / "egress_proxy.py"), "--bind", GATEWAY, "--port", str(PROXY_PORT),
        "--upstream", host, "--chat-path", str(provider["chat_path"]), "--auth", str(provider["auth"]),
    ]  # fmt: skip


def counts_line(raw: bytes) -> dict[str, int] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != COUNT_KEYS:
        return None
    if any(type(v) is not int or v < 0 for v in value.values()):
        return None
    return {k: int(value[k]) for k in sorted(COUNT_KEYS)}


def read_counts(session: Session, stream: Any) -> None:
    for _ in range(MAX_COUNTS_LINES):
        raw = stream.readline(MAX_COUNTS_LINE)
        if not raw:
            return
        if not raw.endswith(b"\n"):
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(MAX_COUNTS_LINE)
            continue
        parsed = counts_line(raw)
        if parsed is not None:
            session.upstream = parsed
    while stream.readline(MAX_COUNTS_LINE):
        pass


def container_argv(
    mode: str,
    image: str,
    persona: str,
    provider: dict[str, Any],
    in_dir: str,
    events_dir: str,
    role: str | None = None,
    trees: dict[str, str] | None = None,
) -> list[str]:
    argv = [
        DOCKER, "run", "--rm", "--name", CONTAINER, "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", f"{CONTAINER_UID}:{CONTAINER_UID}", "--pids-limit", "256",
        "--memory", "2g", "--dns", "0.0.0.0", "--network", NETWORK,
        "-v", f"{in_dir}:{IN_MOUNT}:ro", "-v", f"{events_dir}:{EVENTS_MOUNT}",
        *[a for name, host in sorted((trees or {}).items()) for a in ("-v", f"{host}:{TREES_MOUNT}/{name}:ro")],
        "-e", f"{provider['base_variable']}=http://{GATEWAY}:{PROXY_PORT}",
        "-e", f"{provider['key_variable']}={PLACEHOLDER}",
    ]  # fmt: skip
    if mode == "probe":
        return [
            *argv, "-e", f"PERSONA_PROBE_CHAT_PATH={provider['chat_path']}", "--entrypoint", "python", image,
            "-I", f"{IN_MOUNT}/egress_container_probe.py",
        ]  # fmt: skip
    tail = ["--role", role] if role else []
    return [
        *argv, image, "--contract", f"/app/docs/contracts/personas/{persona}.yaml",
        "--input", f"{IN_MOUNT}/input.json", "--events", f"{EVENTS_MOUNT}/events.json", *tail,
    ]  # fmt: skip


def read_events(events_dir: str) -> dict[str, Any] | None:
    path = os.path.join(events_dir, "events.json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT:
            return None
        data = os.read(fd, MAX_RESULT)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def kill_container() -> None:
    try:
        subprocess.run([DOCKER, "kill", CONTAINER], capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def container_listed() -> bool:
    try:
        done = subprocess.run(
            [DOCKER, "ps", "-a", "-q", "--filter", f"name=^/{CONTAINER}$"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return done.returncode != 0 or bool(done.stdout.strip())


def proxy_survivors() -> list[int]:
    try:
        uid = pwd.getpwnam(PROXY_USER).pw_uid
    except KeyError:
        return []
    found = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                if os.stat(f"/proc/{entry}").st_uid == uid:
                    found.append(int(entry))
            except OSError:
                continue
    return found


def wait_until(gone: Any) -> bool:
    waited = 0.0
    while True:
        if gone():
            return True
        if waited >= GONE_SECONDS:
            return False
        threading.Event().wait(GONE_STEP)
        waited += GONE_STEP


class Session:
    def __init__(self) -> None:
        self.proxy: subprocess.Popen[bytes] | None = None
        self.dirs: list[str] = []
        self.lock_fd: int | None = None
        self.decoy: socket.socket | None = None
        self.started = False
        self.upstream: dict[str, int] | None = None
        self.reader: threading.Thread | None = None
        self.extract: subprocess.Popen[bytes] | None = None

    def cleanup(self) -> bool:
        kill_container()
        if self.extract is not None:
            if self.extract.poll() is None:
                self.extract.kill()
            self.extract.wait()
        if self.proxy is not None and self.proxy.poll() is None:
            self.proxy.kill()
            self.proxy.wait()
        if self.decoy is not None:
            self.decoy.close()
        for path in self.dirs:
            shutil.rmtree(path, ignore_errors=True)
        if self.lock_fd is not None:
            os.close(self.lock_fd)
        self.proxy, self.dirs, self.lock_fd, self.decoy = None, [], None, None
        if not self.started:
            return True
        proxies_gone = wait_until(lambda: not proxy_survivors())
        container_gone = wait_until(lambda: not container_listed())
        return proxies_gone and container_gone


def take_lock(session: Session) -> None:
    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUN_DIR, 0o700)
    fd = os.open(RUN_DIR / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Refused("another launcher holds the lock") from None
    session.lock_fd = fd


def port_in_use(port: int) -> bool:
    try:
        socket.create_connection((GATEWAY, port), timeout=1).close()
    except OSError:
        return False
    return True


def name_in_use() -> bool:
    done = subprocess.run(
        [DOCKER, "ps", "-a", "-q", "--filter", f"name=^/{CONTAINER}$"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    return bool(done.stdout.strip())


def make_dirs(
    session: Session, mode: str, persona_input: dict[str, Any], directory: Path, base: str | None = None
) -> tuple[str, str]:
    if base is None:
        base = tempfile.mkdtemp(prefix="run-", dir=RUN_DIR)
        session.dirs.append(base)
    in_dir, events_dir = os.path.join(base, "in"), os.path.join(base, "events")
    os.mkdir(in_dir, 0o755)
    os.mkdir(events_dir, 0o700)
    os.chmod(in_dir, 0o755)
    os.chown(events_dir, CONTAINER_UID, CONTAINER_UID)
    if mode == "probe":
        shutil.copyfile(directory / "egress_container_probe.py", os.path.join(in_dir, "egress_container_probe.py"))
        os.chmod(os.path.join(in_dir, "egress_container_probe.py"), 0o644)
    target = os.path.join(in_dir, "input.json")
    Path(target).write_text(json.dumps(persona_input), encoding="utf-8")
    os.chmod(target, 0o644)
    return in_dir, events_dir


def start_proxy(session: Session, provider: dict[str, Any], key: str | None, directory: Path) -> None:
    entry = pwd.getpwnam(PROXY_USER)
    session.started = True
    session.proxy = subprocess.Popen(
        proxy_argv(provider, directory),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        user=entry.pw_uid,
        group=entry.pw_gid,
        extra_groups=[],
    )
    assert session.proxy.stdin is not None
    session.proxy.stdin.write((key or "").encode("ascii"))
    session.proxy.stdin.close()
    assert session.proxy.stdout is not None
    session.reader = threading.Thread(target=read_counts, args=(session, session.proxy.stdout), daemon=True)
    session.reader.start()
    for _ in range(50):
        if session.proxy.poll() is not None:
            raise Refused("proxy exited at start")
        if port_in_use(PROXY_PORT):
            return
        threading.Event().wait(0.1)
    raise Refused("proxy did not listen")


def start_decoy(session: Session) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((GATEWAY, DECOY_PORT))
    sock.listen(4)
    session.decoy = sock

    def accept() -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=accept, daemon=True).start()


def run_container(mode: str, argv: list[str]) -> tuple[int | None, bytes]:
    """Run the container; return (exit code or None on timeout, stdout bytes).

    Probe mode merges stderr into stdout and relays it all to stderr, so its stdout result is empty. Run mode keeps the
    container's stdout as data for the result object (never relayed) and relays only its stderr.
    """
    merged = mode == "probe"
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merged else subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=BOUNDS[mode])
    except subprocess.TimeoutExpired:
        kill_container()
        proc.kill()
        proc.communicate()
        return None, b""
    sys.stderr.write((out if merged else (err or b"")[:MAX_RESULT]).decode("utf-8", errors="replace"))
    sys.stderr.flush()
    return proc.returncode, b"" if merged else out[:MAX_RESULT]


def tree_cli(*args: str, data: dict[str, Any] | None = None) -> None:
    argv = [PYTHON, "-I", "-S", str(INSTALL_DIR / "egress_trees.py"), *args]
    try:
        done = subprocess.run(argv, input=json.dumps(data or {}).encode(), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(f"tree check failed: {type(exc).__name__}") from exc
    if done.returncode:
        raise Refused(done.stderr.decode("utf-8", errors="replace").strip().removeprefix("TREES-REFUSED ")[:200] or "trees")


def relay_head(session: Session, base: str, length: int) -> str:
    trees = os.path.join(base, "trees")
    entry = pwd.getpwnam(PROXY_USER)
    os.mkdir(trees, 0o700)
    os.chown(trees, entry.pw_uid, entry.pw_gid)
    session.started = True
    argv = [PRLIMIT, "--nproc=1:1", PYTHON, "-I", "-S", str(INSTALL_DIR / "egress_trees.py"), "extract", "head", str(length)]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, cwd=trees, user=entry.pw_uid,
                            group=entry.pw_gid, extra_groups=[])  # fmt: skip
    session.extract = proc
    assert proc.stdin is not None
    timer = threading.Timer(TREE_SECONDS, proc.kill)
    timer.start()
    try:
        left = length
        while left and (chunk := sys.stdin.buffer.read(min(TREE_CHUNK, left))):
            proc.stdin.write(chunk)
            left -= len(chunk)
        if left:
            raise Refused("head tree stream is short")
        proc.stdin.close()
        if sys.stdin.buffer.read(1):
            raise Refused("head tree stream has trailing bytes")
        code = proc.wait()
    except (BrokenPipeError, ValueError):
        code = proc.wait()
        raise Refused("head tree") from None
    finally:
        timer.cancel()
    head = os.path.join(trees, "head")
    if code != 0 or not os.path.isdir(head) or os.path.islink(head):
        raise Refused("head tree")
    if proxy_survivors():
        raise Refused("a proxy-user process survived the head tree extract")
    return head


def serve(mode: str, session: Session) -> int:
    snapshot = check_install(INSTALL_DIR, Path(__file__).resolve())
    line = sys.stdin.buffer.readline(MAX_REQUEST + 2)
    if not line.endswith(b"\n"):
        raise Refused("request line is not newline-terminated")
    request = parse_request(line[:-1], mode, snapshot["providers"])
    named = request.get("trees") or []
    tree_cli("check-request", data={"mode": mode, **{k: request[k] for k in ("trees", "head_tree_bytes") if k in request}})
    if "main" in named:
        tree_cli("check-main", str(MAIN_TREE))
    provider = snapshot["providers"][request["provider"]]
    take_lock(session)
    base = tempfile.mkdtemp(prefix="run-", dir=RUN_DIR)
    session.dirs.append(base)
    mounts: dict[str, str] = {}
    if "head" in named:
        mounts["head"] = relay_head(session, base, request["head_tree_bytes"])
    elif sys.stdin.buffer.read(1):
        raise Refused("unexpected bytes after the request")
    if "main" in named:
        mounts["main"] = str(MAIN_TREE)
    if port_in_use(PROXY_PORT) or port_in_use(DECOY_PORT) or name_in_use():
        raise Refused("the proxy port or the container name is in use")
    in_dir, events_dir = make_dirs(session, mode, request["input"], INSTALL_DIR, base)
    start_proxy(session, provider, request.get("key"), INSTALL_DIR)
    if mode == "probe":
        start_decoy(session)
    session.started = True
    role = provider.get("role") if mode == "run" else None
    argv = container_argv(mode, snapshot["image"], request["persona"], provider, in_dir, events_dir, role, mounts)
    code, stdout = run_container(mode, argv)
    if mode == "run":
        threading.Event().wait(SETTLE_SECONDS)
    events = read_events(events_dir)
    if mode == "probe":
        print(json.dumps({"exit_code": code, "events": events}))
    else:
        text = stdout.decode("utf-8", errors="replace")
        result = {"exit_code": code, "timed_out": code is None, "stdout": text, "events": events, "upstream": session.upstream}
        print(json.dumps(result))
    if code is None:
        return 1
    if mode == "probe":
        passed = code == 0 and isinstance(events, dict) and events.get("result") == "CONTAINER-PROBE PASS"
        return 0 if passed else 1
    return code


def teardown_failed() -> None:
    print("LAUNCHER-TEARDOWN FAIL the container or a proxy process survived", file=sys.stderr)


def main(argv: Sequence[str]) -> int:
    mode = argv[0] if len(argv) == 1 else ""
    if mode not in BOUNDS:
        print("usage: egress_launcher.py probe|run", file=sys.stderr)
        return EXIT_REFUSED
    if os.geteuid() != 0:
        refuse("not root")
    session = Session()

    def stop(signum: int, frame: Any) -> NoReturn:
        if not session.cleanup():
            teardown_failed()
        raise SystemExit(1)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code = 1
    try:
        code = serve(mode, session)
    except Refused as exc:
        print(f"LAUNCHER-REFUSED {exc}", file=sys.stderr)
        code = EXIT_REFUSED
    finally:
        gone = session.cleanup()
    if not gone:
        teardown_failed()
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
