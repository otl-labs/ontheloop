"""Verb egress-floor-container: the floor for a covered job that runs the persona image (rec-5549)

Run once as root right after the filter and the checkout (sudo python3 -I .../run.py egress-floor-container). It reuses
egress_floor.py's chains and fail-closed unit unchanged, adds the container variant's rules, leaves the container services
running with the Docker socket open to root alone (a unit drop-in, never daemon.json, which the filter's agent restores),
creates the internal network, pulls the pinned image by digest, installs the root launcher and its helpers with a hash
manifest, and writes the sudoers rule that lets the runner user run that launcher and nothing else. Then reads every piece
back as root. Stdlib only; rules, paths, ports, names and the subnet are module constants.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from scripts.personas import egress_floor as base

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = "br-persona"
NETWORK = "persona-egress"
SUBNET = "172.31.250.0/24"
GATEWAY = "172.31.250.1"
PROXY_PORT = 18443
PROXY_USER = "persona-egress-proxy"
BRIDGE_IN = "EGRESS_BRIDGE_IN"
BRIDGE_FWD = "EGRESS_BRIDGE_FWD"
INSTALL_DIR = Path("/usr/local/lib/persona-egress")
INSTALLED = ("egress_launcher.py", "egress_proxy.py", "egress_container_probe.py")
SNAPSHOT = "snapshot.json"
MANIFEST = "manifest.json"
DROPIN = Path("/etc/systemd/system/docker.socket.d/zz-egress-floor.conf")
DROPIN_TEXT = "[Socket]\nSocketMode=0600\nSocketUser=root\nSocketGroup=root\n"
DOCKER_SOCKET = "/var/run/docker.sock"
CONTAINERD_SOCKET = "/run/containerd/containerd.sock"
PYTHON = "/usr/bin/python3"
LAUNCHER = f"{INSTALL_DIR}/egress_launcher.py"
SUDOERS_LINES = (
    "runner ALL=(ALL) !ALL\n"
    f"runner ALL=(root) NOPASSWD: {PYTHON} -I -S {LAUNCHER} probe\n"
    f"runner ALL=(root) NOPASSWD: {PYTHON} -I -S {LAUNCHER} run\n"
)
PULL_TIMEOUT = 300
MIN_DOCKER_MAJOR = 26
IMAGE_FILE = "config/persona_image.yaml"
PROVIDERS = {
    "deepseek/": {
        "host": "api.deepseek.com:443",
        "key_variable": "DEEPSEEK_API_KEY",
        "base_variable": "DEEPSEEK_API_BASE",
        "auth": "bearer",
        "chat_path": "/chat/completions",
    },
    "anthropic/": {
        "host": "api.anthropic.com:443",
        "key_variable": "ANTHROPIC_API_KEY",
        "base_variable": "ANTHROPIC_API_BASE",
        "auth": "x-api-key",
        "chat_path": "/v1/messages",
    },
}


def sh(argv: Sequence[str], timeout: int = base.TIMEOUT) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, type(exc).__name__
    return done.returncode, done.stdout


def proxy_uid() -> int | None:
    try:
        return pwd.getpwnam(PROXY_USER).pw_uid
    except KeyError:
        return None


def floor_rules(uid: int) -> list[str]:
    rules = base.render_rules()["v4_floor"]
    reply = f"-A {base.FLOOR} -o {BRIDGE} -p tcp -m owner --uid-owner {uid} -m tcp --sport {PROXY_PORT} -j RETURN"
    at = rules.index(f"-A {base.FLOOR} -d 172.16.0.0/12 -j DROP")
    return [*rules[:at], reply, *rules[at:]]


def bridge_rules() -> dict[str, dict[str, list[str]]]:
    return {
        "v4": {
            BRIDGE_IN: [
                f"-A {BRIDGE_IN} -d {GATEWAY}/32 -p tcp -m tcp --dport {PROXY_PORT} -j RETURN",
                f"-A {BRIDGE_IN} -j DROP",
            ],
            BRIDGE_FWD: [f"-A {BRIDGE_FWD} -i {BRIDGE} -j DROP", f"-A {BRIDGE_FWD} -o {BRIDGE} -j DROP"],
        },
        "v6": {
            BRIDGE_IN: [f"-A {BRIDGE_IN} -j DROP"],
            BRIDGE_FWD: [f"-A {BRIDGE_FWD} -i {BRIDGE} -j DROP", f"-A {BRIDGE_FWD} -o {BRIDGE} -j DROP"],
        },
    }


def bridge_jumps() -> tuple[str, str]:
    return (f"-A INPUT -i {BRIDGE} -j {BRIDGE_IN}", f"-A FORWARD -j {BRIDGE_FWD}")


def network_argv() -> list[str]:
    return [
        base.binary("docker"), "network", "create", "--internal", "--ipv6=false", "--subnet", SUBNET, "--gateway", GATEWAY,
        "-o", f"com.docker.network.bridge.name={BRIDGE}", NETWORK,
    ]  # fmt: skip


def read_image_ref(root: Path = ROOT) -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in (root / IMAGE_FILE).read_text(encoding="utf-8").splitlines():
        key, colon, value = line.partition(":")
        if colon and key in ("image", "digest") and key not in values:
            values[key] = value.split("#", 1)[0].strip().strip("\"'")
    if "image" not in values or not values.get("digest", "").startswith("sha256:"):
        raise ValueError(f"{IMAGE_FILE} has no image and digest lines")
    return values["image"], values["digest"]


def snapshot_text(image: str, digest: str) -> str:
    return json.dumps({"image": f"{image}@{digest}", "providers": PROVIDERS}, sort_keys=True) + "\n"


def sha256_text(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_text(snapshot: str, root: Path = ROOT) -> str:
    files = {name: sha256_text((root / "scripts" / "personas" / name).read_bytes()) for name in INSTALLED}
    files[SNAPSHOT] = sha256_text(snapshot.encode("utf-8"))
    return json.dumps({"files": files}, sort_keys=True) + "\n"


def _chains(tool: str, chains: dict[str, list[str]]) -> bool:
    ok = True
    for chain, rules in chains.items():
        ok = base._chain(tool, chain, rules) and ok
    return ok


def _jump_at(tool: str, rule: str) -> bool:
    mangle = base._tables(tool)
    spec = shlex.split(rule)
    parent = spec[1]
    check = [*mangle, "-C", parent, *spec[2:]]
    if sh(check)[0] == 0:
        sh([*mangle, "-D", parent, *spec[2:]])
    return sh([*mangle, "-I", parent, "1", *spec[2:]])[0] == 0


def install_files(snapshot: str, root: Path = ROOT, target: Path = INSTALL_DIR) -> bool:
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(target, 0o755)
    for name in INSTALLED:
        shutil.copyfile(root / "scripts" / "personas" / name, target / name)
        os.chmod(target / name, 0o644)
    (target / SNAPSHOT).write_text(snapshot, encoding="utf-8")
    (target / MANIFEST).write_text(manifest_text(snapshot, root), encoding="utf-8")
    for name in (SNAPSHOT, MANIFEST):
        os.chmod(target / name, 0o644)
    return all(os.stat(target / n).st_uid == 0 for n in (*INSTALLED, SNAPSHOT, MANIFEST))


def write_sudoers() -> bool:
    pending = base.SUDOERS.with_name(base.SUDOERS.name + ".new")
    pending.write_text(SUDOERS_LINES, encoding="utf-8")
    os.chmod(pending, 0o440)
    if sh(["visudo", "-cf", str(pending)])[0] != 0:
        pending.unlink(missing_ok=True)
        return False
    os.replace(pending, base.SUDOERS)
    return True


def docker_restart() -> bool:
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(DROPIN_TEXT, encoding="utf-8")
    ok = sh(["systemctl", "daemon-reload"])[0] == 0
    ok = sh(["systemctl", "stop", "docker.service", "docker.socket"])[0] == 0 and ok
    ok = sh(["systemctl", "start", "docker.socket"])[0] == 0 and ok
    return sh(["systemctl", "start", "docker.service"])[0] == 0 and ok


def apply() -> bool:
    ok = sh(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", PROXY_USER])[0] in (0, 9)
    uid = proxy_uid()
    if uid is None:
        return False
    rules = base.render_rules()
    bridges = bridge_rules()
    ok = base._chain("iptables", base.FAIL, rules["v4_fail"]) and base._chain("iptables", base.FLOOR, floor_rules(uid)) and ok
    ok = base._chain("ip6tables", base.FLOOR, rules["v6_floor"]) and ok
    ok = _chains("iptables", bridges["v4"]) and _chains("ip6tables", bridges["v6"]) and ok
    ok = base._jump("iptables") and base._jump("ip6tables") and ok
    for tool in ("iptables", "ip6tables"):
        for jump in bridge_jumps():
            ok = _jump_at(tool, jump) and ok
    base.UNIT_PATH.write_text(base.unit_text(), encoding="utf-8")
    ok = sh(["systemctl", "daemon-reload"])[0] == 0 and ok
    ok = sh(["systemctl", "start", base.UNIT])[0] == 0 and ok
    ok = docker_restart() and ok
    image, digest = read_image_ref()
    docker = base.binary("docker")
    sh([docker, "network", "rm", NETWORK])
    ok = sh(network_argv())[0] == 0 and ok
    ok = sh([docker, "pull", f"{image}@{digest}"], PULL_TIMEOUT)[0] == 0 and ok
    ok = install_files(snapshot_text(image, digest)) and ok
    return write_sudoers() and ok


def _rules(tool: str, chain: str) -> list[str]:
    return base._rules_of(tool, chain)


def ancestors_ok(path: str) -> bool:
    real = os.path.realpath(path)
    parts = Path(real).parents
    for item in (Path(real), *parts):
        try:
            info = os.stat(item)
        except OSError:
            return False
        if info.st_uid != 0 or info.st_mode & 0o022:
            return False
    return True


def _sys_path_dirs() -> list[str]:
    reply = sh([PYTHON, "-I", "-S", "-c", "import json,sys;print(json.dumps(sys.path))"])[1]
    try:
        return [p for p in json.loads(reply) if isinstance(p, str) and os.path.isdir(p)]
    except ValueError:
        return []


def docker_major(text: str) -> int:
    head = text.strip().split(".", 1)[0]
    return int(head) if head.isdigit() else 0


def _socket_ok(path: str, mode: int | None, strict_other: bool) -> bool:
    try:
        info = os.stat(path)
    except OSError:
        return False
    if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o007:
        return False
    return not strict_other or mode is None or (info.st_mode & 0o777) == mode


def _gid0_clean() -> bool:
    try:
        return all(m == "root" for m in grp.getgrgid(0).gr_mem)
    except KeyError:
        return False


def _network_ok() -> bool:
    try:
        data = json.loads(sh([base.binary("docker"), "network", "inspect", NETWORK])[1])[0]
        config = data["IPAM"]["Config"][0]
        return bool(
            data["Internal"]
            and not data["EnableIPv6"]
            and config["Subnet"] == SUBNET
            and config["Gateway"] == GATEWAY
            and data["Options"]["com.docker.network.bridge.name"] == BRIDGE
        )
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def _image_ok() -> bool:
    try:
        image, digest = read_image_ref()
    except (OSError, ValueError):
        return False
    out = sh([base.binary("docker"), "image", "inspect", "--format", "{{json .RepoDigests}}", f"{image}@{digest}"])[1]
    try:
        return f"{image}@{digest}" in json.loads(out)
    except (ValueError, TypeError):
        return False


def _install_ok() -> bool:
    try:
        manifest = json.loads((INSTALL_DIR / MANIFEST).read_text(encoding="utf-8"))["files"]
        return all(sha256_text((INSTALL_DIR / name).read_bytes()) == want for name, want in manifest.items()) and set(
            manifest
        ) == {
            *INSTALLED,
            SNAPSHOT,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _text_is(path: Path, want: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == want
    except OSError:
        return False


def verify() -> list[tuple[str, bool]]:
    uid = proxy_uid()
    want = floor_rules(uid) if uid is not None else []
    bridges = bridge_rules()
    in_jump, fwd_jump = bridge_jumps()
    checks = [
        ("proxy_user", uid is not None),
        ("v4_floor", uid is not None and _rules("iptables", base.FLOOR) == want),
        ("v4_fail_closed", _rules("iptables", base.FAIL) == base.render_rules()["v4_fail"]),
        ("v6_floor", _rules("ip6tables", base.FLOOR) == base.render_rules()["v6_floor"]),
        ("v4_output_jump", base._first_output_rule("iptables") == f"-A OUTPUT -j {base.FLOOR}"),
        ("v6_output_jump", base._first_output_rule("ip6tables") == f"-A OUTPUT -j {base.FLOOR}"),
        ("exit_hook", base._hook_unit_ok()),
    ]
    for tool, key in (("iptables", "v4"), ("ip6tables", "v6")):
        for chain, rules in bridges[key].items():
            checks.append((f"{key}_{chain.lower()}", _rules(tool, chain) == rules))
        first_input = [r for r in base._run([*base._tables(tool), "-S", "INPUT"])[1].splitlines() if r.startswith("-A ")][:1]
        first_forward = [r for r in base._run([*base._tables(tool), "-S", "FORWARD"])[1].splitlines() if r.startswith("-A ")][
            :1
        ]
        checks.append((f"{key}_bridge_input_jump", first_input == [in_jump]))
        checks.append((f"{key}_bridge_forward_jump", first_forward == [fwd_jump]))
    checks += [
        ("docker_socket_root_only", _socket_ok(DOCKER_SOCKET, 0o600, True)),
        ("containerd_socket_root_only", _socket_ok(CONTAINERD_SOCKET, None, False) and _gid0_clean()),
        ("socket_dropin", _text_is(DROPIN, DROPIN_TEXT)),
        ("network", _network_ok()),
        ("image_digest", _image_ok()),
        ("install_hashes", _install_ok()),
        ("sudoers", _text_is(base.SUDOERS, SUDOERS_LINES)),
        ("live_restore_off", sh([base.binary("docker"), "info", "--format", "{{.LiveRestoreEnabled}}"])[1].strip() == "false"),
        (
            "docker_version",
            docker_major(sh([base.binary("docker"), "version", "--format", "{{.Server.Version}}"])[1]) >= MIN_DOCKER_MAJOR,
        ),
        ("install_ancestors", ancestors_ok(str(INSTALL_DIR))),
        ("python_ancestors", ancestors_ok(PYTHON) and all(ancestors_ok(p) for p in _sys_path_dirs())),
    ]
    return checks


def banner() -> None:
    print("EGRESS-FLOOR docker " + sh([base.binary("docker"), "version", "--format", "{{.Server.Version}}"])[1].strip())
    print("EGRESS-FLOOR iptables " + sh([base.binary("iptables"), "-V"])[1].strip())


def main(argv: Sequence[str]) -> int:
    if os.geteuid() != 0:
        print("EGRESS-FLOOR FAIL not-root")
        return 1
    applied = apply()
    checks = [("apply", applied), *verify()]
    banner()
    return 0 if base.report(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
