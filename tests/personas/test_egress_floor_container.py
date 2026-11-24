"""egress_floor_container: rendered rules and order, drop-in, sudoers, manifest, snapshot parity, pull, verify read-backs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from scripts.personas import egress_floor as base
from scripts.personas import egress_floor_container as F

ROOT = Path(__file__).resolve().parents[2]


def test_the_proxy_reply_rule_sits_after_the_fail_closed_jump_and_before_the_172_drop() -> None:
    rules = F.floor_rules(999)
    reply = "-A EGRESS_FLOOR -o br-persona -p tcp -m owner --uid-owner 999 -m tcp --sport 18443 -j RETURN"
    assert rules.index(reply) == rules.index("-A EGRESS_FLOOR -d 172.16.0.0/12 -j DROP") - 1
    assert rules.index(reply) > rules.index("-A EGRESS_FLOOR -j EGRESS_FAIL_CLOSED")
    assert [r for r in rules if r != reply] == base.render_rules()["v4_floor"]


def test_bridge_chains_admit_one_port_and_drop_every_forward_in_v4_and_v6() -> None:
    chains = F.bridge_rules()
    assert chains["v4"]["EGRESS_BRIDGE_IN"] == [
        "-A EGRESS_BRIDGE_IN -d 172.31.250.1/32 -p tcp -m tcp --dport 18443 -j RETURN",
        "-A EGRESS_BRIDGE_IN -j DROP",
    ]
    assert chains["v6"]["EGRESS_BRIDGE_IN"] == ["-A EGRESS_BRIDGE_IN -j DROP"]
    for key in ("v4", "v6"):
        assert chains[key]["EGRESS_BRIDGE_FWD"] == [
            "-A EGRESS_BRIDGE_FWD -i br-persona -j DROP",
            "-A EGRESS_BRIDGE_FWD -o br-persona -j DROP",
        ]
    assert F.bridge_jumps() == ("-A INPUT -i br-persona -j EGRESS_BRIDGE_IN", "-A FORWARD -j EGRESS_BRIDGE_FWD")


def test_dropin_leaves_the_services_running_with_a_root_only_socket_and_never_touches_daemon_json() -> None:
    assert F.DROPIN_TEXT == "[Socket]\nSocketMode=0600\nSocketUser=root\nSocketGroup=root\n"
    assert str(F.DROPIN).endswith("docker.socket.d/zz-egress-floor.conf")
    assert "/etc/docker" not in Path(F.__file__).read_text(encoding="utf-8")
    assert "mask" not in Path(F.__file__).read_text(encoding="utf-8").split("def apply")[1]


def test_sudoers_denies_first_then_allows_exactly_two_launcher_lines_with_no_wildcard() -> None:
    lines = F.SUDOERS_LINES.splitlines()
    assert lines[0] == "runner ALL=(ALL) !ALL" and len(lines) == 3
    assert lines[1].endswith("egress_launcher.py probe") and lines[2].endswith("egress_launcher.py run")
    allow = "runner ALL=(root) NOPASSWD: /usr/bin/python3 -I -S /usr/local/lib/persona-egress/"
    assert all(line.startswith(allow) for line in lines[1:])
    assert "*" not in F.SUDOERS_LINES and "SETENV" not in F.SUDOERS_LINES


def test_network_argv_is_internal_without_ipv6_on_the_fixed_bridge() -> None:
    argv = F.network_argv()
    assert argv[1:4] == ["network", "create", "--internal"] and "--ipv6=false" in argv
    assert argv[argv.index("--subnet") + 1] == "172.31.250.0/24" and argv[argv.index("--gateway") + 1] == "172.31.250.1"
    assert "com.docker.network.bridge.name=br-persona" in argv and argv[-1] == "persona-egress"


def test_image_reference_is_read_as_separate_lines_and_matches_the_pin() -> None:
    image, digest = F.read_image_ref(ROOT)
    pin = yaml.safe_load((ROOT / "config/persona_image.yaml").read_text(encoding="utf-8"))
    assert (image, digest) == (pin["image"], pin["digest"])
    assert json.loads(F.snapshot_text(image, digest))["image"] == f"{image}@{digest}"


def test_image_reader_refuses_a_file_without_a_digest(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/persona_image.yaml").write_text("image: x\ndigest: null\n")
    with pytest.raises(ValueError):
        F.read_image_ref(tmp_path)


def test_snapshot_provider_rows_match_the_three_yaml_files() -> None:
    egress = yaml.safe_load((ROOT / "config/persona_verdict_egress.yaml").read_text(encoding="utf-8"))["provider_hosts"]
    config = yaml.safe_load((ROOT / "docs/contracts/project-configuration.yaml").read_text(encoding="utf-8"))["settings"]
    keys = {
        r["variable"]
        for r in config
        if str(r.get("id", "")).startswith("ports.run_persona.litellm.api_key") and r.get("variable")
    }
    tiers = yaml.safe_load((ROOT / "docs/contracts/inference-provider.yaml").read_text(encoding="utf-8"))["model_id_formats"][
        "litellm_tier_models"
    ]
    prefixes = {
        str(r["default_model"]).split("/", 1)[0] + "/"
        for r in tiers.values()
        if isinstance(r, dict) and "/" in str(r.get("default_model", ""))
    }
    assert set(F.PROVIDERS) == set(egress) == prefixes
    for prefix, row in F.PROVIDERS.items():
        assert row["host"] == egress[prefix] and row["key_variable"] in keys
        assert row["base_variable"] == prefix.split("/")[0].upper() + "_API_BASE"
        assert row["key_variable"] == prefix.split("/")[0].upper() + "_API_KEY"
    assert F.PROVIDERS["deepseek/"]["auth"] == "bearer" and F.PROVIDERS["anthropic/"]["auth"] == "x-api-key"
    assert (
        F.PROVIDERS["deepseek/"]["chat_path"] == "/chat/completions"
        and F.PROVIDERS["anthropic/"]["chat_path"] == "/v1/messages"
    )


def test_install_writes_modes_and_a_manifest_the_launcher_accepts(tmp_path: Path) -> None:
    target = tmp_path / "persona-egress"
    snapshot = F.snapshot_text("registry.example/img", "sha256:" + "a" * 64)
    assert F.install_files(snapshot, ROOT, target) == (os.geteuid() == 0)
    manifest = json.loads((target / "manifest.json").read_text())["files"]
    assert set(manifest) == {*F.INSTALLED, "snapshot.json"}
    for name, digest in manifest.items():
        assert F.sha256_text((target / name).read_bytes()) == digest and oct((target / name).stat().st_mode & 0o777) == "0o644"
    assert oct(target.stat().st_mode & 0o777) == "0o755"


def test_pull_has_its_own_timeout_and_the_pinned_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], int]] = []

    def sh(argv: list[str], timeout: int = base.TIMEOUT) -> tuple[int, str]:
        calls.append((list(argv), timeout))
        return (0, "") if argv[0] != "useradd" else (9, "")

    monkeypatch.setattr(F, "sh", sh)
    monkeypatch.setattr(F, "proxy_uid", lambda: 999)
    monkeypatch.setattr(base, "_run", lambda argv: (0, ""))
    monkeypatch.setattr(base, "UNIT_PATH", Path("/tmp/x-unit"))
    monkeypatch.setattr(F, "DROPIN", Path("/tmp/x-dropin/zz.conf"))
    monkeypatch.setattr(F, "TEARDOWN_PATH", Path("/tmp/x-teardown.service"))
    monkeypatch.setattr(F, "install_files", lambda s: True)
    monkeypatch.setattr(F, "write_sudoers", lambda: True)
    assert F.apply() is True
    pull = next(c for c in calls if len(c[0]) > 1 and c[0][1] == "pull")
    image, digest = F.read_image_ref()
    assert pull[1] == 300 and pull[0][2] == f"{image}@{digest}"
    order = [c[0][1] if len(c[0]) > 1 else c[0][0] for c in calls]
    assert order.index("pull") > order.index("network")
    stops = [c[0] for c in calls if c[0][:2] == ["systemctl", "stop"]]
    assert stops == [["systemctl", "stop", "docker.service", "docker.socket"]]
    assert not any("mask" in c[0] for c in calls)
    assert ["systemctl", "start", F.TEARDOWN_UNIT] in [c[0] for c in calls]
    assert Path("/tmp/x-teardown.service").read_text(encoding="utf-8") == F.teardown_text()


def test_main_refuses_when_not_root(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(F.os, "geteuid", lambda: 1000)
    assert F.main([]) == 1 and "not-root" in capsys.readouterr().out


def test_docker_major_parses_and_refuses_old_versions() -> None:
    assert F.docker_major("29.9.3\n") == 29 and F.docker_major("25.0.1") == 25 and F.docker_major("") == 0


def _stat(uid: int, gid: int, mode: int) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))


def test_socket_read_back_accepts_containerd_0660_root_and_refuses_other_bits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o140660))
    assert F._socket_ok(F.CONTAINERD_SOCKET, None, False)
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o140666))
    assert not F._socket_ok(F.CONTAINERD_SOCKET, None, False)
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 1, 0o140660))
    assert not F._socket_ok(F.CONTAINERD_SOCKET, None, False)
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o140660))
    assert not F._socket_ok(F.DOCKER_SOCKET, 0o600, True)
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o140600))
    assert F._socket_ok(F.DOCKER_SOCKET, 0o600, True)


def test_a_non_root_member_of_gid_zero_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F.grp, "getgrgid", lambda g: type("G", (), {"gr_mem": ["root"]})())
    assert F._gid0_clean()
    monkeypatch.setattr(F.grp, "getgrgid", lambda g: type("G", (), {"gr_mem": ["root", "runner"]})())
    assert not F._gid0_clean()


def test_ancestor_ownership_and_mode_are_refused_when_group_or_other_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o040755))
    assert F.ancestors_ok(str(tmp_path))
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o040775))
    assert not F.ancestors_ok(str(tmp_path))
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(0, 0, 0o040757))
    assert not F.ancestors_ok(str(tmp_path))
    monkeypatch.setattr(F.os, "stat", lambda p: _stat(1000, 0, 0o040755))
    assert not F.ancestors_ok(str(tmp_path))


def _fake_sh(monkeypatch: pytest.MonkeyPatch, version: str, live: str = "false", live_code: int = 0) -> None:
    def sh(argv: list[str], timeout: int = 30) -> tuple[int, str]:
        if "{{.Server.Version}}" in argv:
            return 0, version
        if "{{json .LiveRestoreEnabled}}" in argv:
            return live_code, live
        return 0, ""

    monkeypatch.setattr(F, "sh", sh)
    monkeypatch.setattr(base, "_run", lambda argv: (0, ""))
    monkeypatch.setattr(F, "proxy_uid", lambda: None)


def test_verify_reports_docker_below_26_and_never_gates_on_live_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sh(monkeypatch, "25.0.0")
    got = dict(F.verify())
    assert got["docker_version"] is False and got["proxy_user"] is False and "live_restore_off" not in got
    for live in ("true", "false"):
        _fake_sh(monkeypatch, "29.9.3", live=live)
        got = dict(F.verify())
        assert got["docker_version"] is True and not any("live" in name for name in got)
    assert {
        "install_hashes",
        "sudoers",
        "socket_dropin",
        "network",
        "image_digest",
        "install_ancestors",
        "teardown_unit",
    } <= set(got)


def test_live_restore_is_observed_as_true_false_or_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sh(monkeypatch, "29.9.3", live="true\n")
    assert F.live_restore_observation() == "true"
    _fake_sh(monkeypatch, "29.9.3", live="false")
    assert F.live_restore_observation() == "false"
    _fake_sh(monkeypatch, "29.9.3", live="garbage")
    assert F.live_restore_observation() == "unknown"
    _fake_sh(monkeypatch, "29.9.3", live="true", live_code=1)
    assert F.live_restore_observation() == "unknown"


def test_banner_prints_the_observation(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _fake_sh(monkeypatch, "29.9.3", live="true")
    monkeypatch.setattr(F.os, "geteuid", lambda: 0)
    monkeypatch.setattr(F, "apply", lambda: True)
    F.main([])
    assert "EGRESS-FLOOR docker-live-restore true" in capsys.readouterr().out


def test_teardown_unit_text_has_three_dash_prefixed_stops_in_order_and_binds_to_the_agent() -> None:
    text = F.teardown_text()
    lines = text.splitlines()
    assert "BindsTo=agent.service" in lines and "After=agent.service" in lines
    assert "Type=oneshot" in lines and "RemainAfterExit=yes" in lines and "ExecStart=/bin/true" in lines
    stops = [line for line in lines if line.startswith("ExecStop")]
    assert len(stops) == 3 and all(line.startswith("ExecStop=-/") for line in stops)
    assert "-w -t mangle -I EGRESS_BRIDGE_IN 1 -i br-persona -j DROP" in stops[0]
    assert "pkill -KILL -u persona-egress-proxy" in stops[1]
    assert "timeout 30 " in stops[2] and stops[2].endswith(f"docker kill {F.CONTAINER}")


def _shown(stops: list[list[str]], active: str = "active", binds: str = "agent.service", ignore: str = "yes") -> str:
    rows = [f"ExecStop={{ path={a[0]} ; argv[]={' '.join(a)} ; ignore_errors={ignore} ; start_time=[n/a] }}" for a in stops]
    return "\n".join([f"ActiveState={active}", f"BindsTo={binds}", *rows])


def test_teardown_unit_read_back_refuses_inactive_unbound_or_unignored_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    good = F.teardown_stops()
    monkeypatch.setattr(F, "sh", lambda argv, timeout=30: (0, _shown(good)))
    assert F.teardown_unit_ok()
    for bad in (
        _shown(good, active="inactive"),
        _shown(good, binds=""),
        _shown(good, ignore="no"),
        _shown(good[:2]),
        _shown(good[::-1]),
        "",
    ):
        monkeypatch.setattr(F, "sh", lambda argv, timeout=30, bad=bad: (0, bad))
        assert not F.teardown_unit_ok()


def test_teardown_stops_use_absolute_binaries_and_the_launchers_container_name() -> None:
    from scripts.personas import egress_launcher as launcher

    assert F.CONTAINER == launcher.CONTAINER and F.PROXY_USER == launcher.PROXY_USER
    assert all(argv[0].startswith("/") for argv in F.teardown_stops())


def test_verify_reads_back_the_texts_as_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f"
    target.write_text(F.SUDOERS_LINES)
    assert F._text_is(target, F.SUDOERS_LINES) and not F._text_is(target, "x") and not F._text_is(tmp_path / "none", "x")
    monkeypatch.setattr(
        F,
        "sh",
        lambda argv, timeout=30: (
            0,
            json.dumps(
                [
                    {
                        "Internal": True,
                        "EnableIPv6": False,
                        "IPAM": {"Config": [{"Subnet": F.SUBNET, "Gateway": F.GATEWAY}]},
                        "Options": {"com.docker.network.bridge.name": F.BRIDGE},
                    }
                ]
            ),
        ),
    )
    assert F._network_ok()
    monkeypatch.setattr(F, "sh", lambda argv, timeout=30: (0, json.dumps([{"Internal": False}])))
    assert not F._network_ok()


def test_the_install_hash_read_back_detects_a_changed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    F.install_files(F.snapshot_text("i", "sha256:" + "b" * 64), ROOT, tmp_path)
    monkeypatch.setattr(F, "INSTALL_DIR", tmp_path)
    assert F._install_ok()
    (tmp_path / "egress_proxy.py").write_text("tampered")
    assert not F._install_ok()


def test_image_digest_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    image, digest = F.read_image_ref()
    monkeypatch.setattr(F, "sh", lambda argv, timeout=30: (0, json.dumps([f"{image}@{digest}"])))
    assert F._image_ok()
    monkeypatch.setattr(F, "sh", lambda argv, timeout=30: (0, json.dumps(["other@sha256:" + "c" * 64])))
    assert not F._image_ok()


def test_jump_replaces_an_existing_rule_and_inserts_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(F, "sh", lambda argv, timeout=30: (seen.append(list(argv)), (0, ""))[1])
    assert F._jump_at("iptables", F.bridge_jumps()[0])
    assert any("-D" in c for c in seen) and seen[-1][-6:] == ["INPUT", "1", "-i", "br-persona", "-j", "EGRESS_BRIDGE_IN"]
