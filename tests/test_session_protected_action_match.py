"""Unit tests for scripts/session/protected_action_match.py (100% line floor)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.session import protected_action_match as match

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load((ROOT / "docs/contracts/protected-actions.yaml").read_text(encoding="utf-8"))
ADMIN = "agent_platform_admin"
T = ROOT / "transcripts"
TRANSCRIPT = "/home/u/.claude/projects/-repo/abc-123.jsonl"
HOME = "/home/u"


def hits(command: str, ambient: str = "agent_platform", cwd: str = "/work/repo") -> set[tuple[str, str]]:
    return {(h.cls, h.target) for h in match.classify(command, CONTRACT, ambient, cwd)}


def one(command: str, cwd: str = "/work/repo") -> match.Hit:
    found = [h for h in match.classify(command, CONTRACT, "agent_platform", cwd) if h.cls == "tf-state-change"]
    assert len(found) == 1, found
    return found[0]


def classes(name: str) -> dict:
    return next(c for c in CONTRACT["classes"] if c["id"] == name)


def test_tf_state_change_covers_every_state_writing_form() -> None:
    spec = classes("tf-state-change")
    tf = ("tf-state-change", "github")
    for sub in spec["subcommands"]:
        assert tf in hits(f"terraform -chdir=terraform/github {sub}"), sub
    for sub in spec["state_subcommands"]:
        assert tf in hits(f"terraform -chdir=terraform/github state {sub} x"), sub
    for sub in spec["workspace_subcommands"]:
        assert tf in hits(f"terraform -chdir=terraform/github workspace {sub} x"), sub
    for flag in spec["init_flags"]:
        assert tf in hits(f"terraform -chdir=terraform/github init {flag}"), flag
    assert tf in hits("terraform -chdir=terraform/github init --migrate-state")
    assert tf in hits("terraform -chdir=terraform/github init -migrate-state=true")
    assert hits("terraform -chdir=terraform/github state list") == set()
    assert hits("terraform -chdir=terraform/github workspace select x") == set()
    assert hits("terraform -chdir=terraform/github init") == set()
    assert one("terraform -chdir=terraform/github apply -auto-approve").approval == "one_shot_at_commit"


def test_root_resolution_from_chdir_cd_pushd_env_c_and_cwd() -> None:
    personal = ("tf-state-change", "personal")
    for command in (
        "cd terraform/personal && terraform apply",
        "pushd terraform/personal && terraform apply",
        "env -C terraform/personal terraform apply",
        "env -Cterraform/personal terraform apply",
        "env --chdir=terraform/personal terraform apply",
    ):
        hit = one(command)
        assert (hit.cls, hit.target) == personal and not hit.from_cwd, command
    from_cwd = one("terraform apply", cwd="/work/repo/terraform/github")
    assert (from_cwd.target, from_cwd.from_cwd) == ("github", True)
    for command in (
        "terraform -chdir=/tmp/wt/terraform/bootstrap apply",
        'terraform -chdir="$W/terraform/bootstrap" apply',
    ):
        assert ("tf-state-change", "bootstrap") in hits(command), command
    for command in ("terraform -chdir=terraform/github/ apply", "terraform -chdir=terraform/personal/../github apply"):
        assert ("tf-state-change", "github") in hits(command), command
    assert ("tf-state-change", "unknown") in hits("terraform -chdir=$ROOT apply")
    assert ("tf-state-change", "unknown") in hits("cd $X && terraform apply")
    assert hits("terraform apply -auto-approve", cwd="/work/repo") == set()
    assert hits("terraform -chdir=/tmp/other/dir apply") == set()
    assert hits("cd rel && terraform -chdir=terraform/github apply")


def test_secret_read_covers_every_value_verb() -> None:
    sr = ("secret-read", "unknown")
    commands = [
        "aws secretsmanager get-secret-value --secret-id x",
        "aws secretsmanager batch-get-secret-value --secret-id-list x",
        "aws --region x --profile agent_platform secretsmanager get-secret-value",
        "python3 -c 'import boto3; get_secret_value()'",
        "python3.12 -Ic 'get_secret_value'",
        "perl -e 'get_secret_value'",
        "node --eval 'get_secret_value'",
        "python3 -c'get_secret_value()'",
        "perl -e'get_secret_value'",
        "node --eval=get_secret_value",
        "python3 -c 'batch_get_secret_value()'",
        "python3 -c 'x(WithDecryption=True)'",
        "python3 -c 'x(\"WithDecryption\": True)'",
        "source scripts/ci/neon_api_key.sh",
        ". scripts/ci/neon_api_key.sh",
        "bash -x scripts/ci/neon_api_key.sh",
        "bin/sync-terraform-assets.sh --tfvars",
        "bash scripts/ci/terraform_github_apply.sh",
        ".github/actions/materialise-tfvars/materialise_tfvars.sh",
        "scripts/ci/neon_api_key.sh",
        "timeout 600 bin/sync-terraform-assets.sh --tfvars",
        "env X=1 scripts/ci/neon_api_key.sh",
        "nohup bash scripts/ci/neon_api_key.sh",
    ]
    for ssm in ("get-parameter", "get-parameters", "get-parameters-by-path", "get-parameter-history"):
        commands.append(f"aws ssm {ssm} --name /a --with-decryption")
    for command in commands:
        assert any(h[0] == "secret-read" for h in hits(command)), command
    assert ("secret-read", "/secret/x") in hits("aws ssm get-parameter --name /secret/x --with-decryption")
    assert ("secret-read", "unknown") in hits("aws ssm get-parameters-by-path --path=/p --with-decryption")
    assert sr in hits("aws secretsmanager get-secret-value")
    for command in (
        "aws ssm get-parameter --name /x",
        "cat scripts/ci/neon_api_key.sh",
        "grep -n x .github/actions/materialise-tfvars/materialise_tfvars.sh",
        "git diff scripts/ci/terraform_github_apply.sh",
        "git add bin/sync-terraform-assets.sh",
        "bin/sync-terraform-assets.sh",
        "aws secretsmanager list-secrets",
        "bash -c true",
    ):
        assert not any(h[0] == "secret-read" for h in hits(command)), command


def test_command_substitution_is_classified() -> None:
    inner = "aws ssm get-parameter --name /secret/neon-api-key --with-decryption --profile agent_platform_admin"
    for wrap in (f'"$({inner} --query Parameter.Value)"', f'"`{inner} --query Parameter.Value`"'):
        found = hits(f"TF_VAR_neon_api_key={wrap} terraform -chdir=terraform/personal apply")
        assert {c for c, _ in found} == {"secret-read", "admin-credential", "tf-state-change"}, found


def test_wrappers_tails_and_shell_strings_are_classified() -> None:
    tf = ("tf-state-change", "github")
    for command in (
        "$HOME/.local/bin/terraform -chdir=terraform/github apply",
        "timeout 600 terraform -chdir=terraform/github apply",
        "env X=1 terraform -chdir=terraform/github apply",
        "nohup terraform -chdir=terraform/github apply",
        'bash -c "terraform -chdir=terraform/github apply"',
        'eval "terraform -chdir=terraform/github apply"',
    ):
        assert tf in hits(command), command
    both = hits("bash scripts/ci/neon_api_key.sh run -- terraform -chdir=terraform/github apply")
    assert {c for c, _ in both} == {"secret-read", "tf-state-change"}


def test_wrapper_options_never_hide_the_command_word() -> None:
    for prefix in ("timeout -k 5 600", "nice -n 5", "sudo -E", "env -i X=1", "env -u NAME", "time -p", "stdbuf -oL", "xargs"):
        assert ("tf-state-change", "github") in hits(f"{prefix} terraform -chdir=terraform/github apply"), prefix
    assert ("tf-state-change", "github") in hits(r"find . -exec terraform -chdir=terraform/github apply \;")
    assert hits("nice -n 5 echo hi") == set()


def test_heredoc_bodies_by_receiver() -> None:
    body = "use --profile agent_platform_admin and terraform -chdir=terraform/github apply"
    data = f"git commit -m \"$(cat <<'EOF'\n{body}\nEOF\n)\""
    assert hits(data) == set()
    assert hits("git commit -F - <<'EOF'\n--profile agent_platform_admin get_secret_value\nEOF\n") == set()
    assert ("tf-state-change", "github") in hits("bash <<EOF\nterraform -chdir=terraform/github apply\nEOF\n")
    body = "import boto3\nboto3.client('x').get_secret_value()\n"
    assert ("secret-read", "unknown") in hits(f"bin/venv-python - <<'EOF'\n{body}EOF\n")
    admin = "boto3.Session(profile_name='agent_platform_admin')\n"
    assert ("admin-credential", ADMIN) in hits(f"bin/venv-python - <<'EOF'\n{admin}EOF\n")
    assert ("secret-read", "unknown") in hits('python3 <<< "x(WithDecryption=True)"')
    tf = ("tf-state-change", "github")
    assert tf in hits("cat <<< x\nterraform -chdir=terraform/github apply")
    assert tf in hits("echo $((1<<2))\nterraform -chdir=terraform/github apply")
    assert tf in hits("((x = 1<<2))\nterraform -chdir=terraform/github apply")
    assert hits("echo 'a <<< b'\nls") == set()


def test_admin_profile_selection_forms() -> None:
    forms = [
        "aws --profile {p} s3 ls",
        "aws --profile={p} s3 ls",
        "AWS_PROFILE={p} aws s3 ls",
        "AWS_DEFAULT_PROFILE={p} aws s3 ls",
        "TF_VAR_aws_profile={p} true",
        "terraform -chdir=terraform/github plan -var aws_profile={p}",
        "terraform -chdir=terraform/github plan -var=aws_profile={p}",
        "terraform -chdir=terraform/github init -backend-config=profile={p}",
        "terraform -chdir=terraform/github init --backend-config=profile={p}",
        "python3 -c \"boto3.Session(profile_name='{p}')\"",
        "python3 -c 'boto3.Session(profile=\"{p}\")'",
        "AWS_PROFILE={p} bin/venv-python -m scripts.aws_identity caller-arn",
        "env AWS_PROFILE={p} bin/venv-python -m scripts.aws_identity caller-arn",
    ]
    for form in forms:
        assert ("admin-credential", ADMIN) in hits(form.format(p=ADMIN)), form
    assert ("admin-credential", ADMIN) in hits("terraform -chdir=terraform/bootstrap plan")
    assert hits("terraform -chdir=terraform/bootstrap plan -var=aws_profile=agent_platform") == set()
    assert hits("TF_VAR_aws_profile=agent_platform terraform -chdir=terraform/personal plan") == set()
    assert ("admin-credential", ADMIN) in hits("terraform -chdir=terraform/personal plan -var aws_profile=" + ADMIN)
    assert hits("aws --profile agent_platform s3 ls") == set()
    default_only = match.classify("terraform -chdir=terraform/bootstrap plan", CONTRACT, "agent_platform", "/r")
    assert default_only and default_only[0].default_only


def test_ambient_rule_gates_only_aws_and_terraform_words() -> None:
    assert ("admin-credential", ADMIN) in hits("aws s3 ls", ambient=ADMIN)
    assert ("admin-credential", ADMIN) in hits("terraform -chdir=terraform/github plan", ambient=ADMIN)
    assert hits('bin/venv-python -c "import boto3"', ambient=ADMIN) == set()
    assert hits("aws --profile=agent_platform s3 ls", ambient=ADMIN) == set()
    assert hits("AWS_PROFILE=agent_platform aws s3 ls", ambient=ADMIN) == set()
    assert hits("terraform fmt", ambient=ADMIN) == set()
    mixed = match.classify("terraform -chdir=terraform/personal plan", CONTRACT, ADMIN, "/r")
    assert len(mixed) == 1 and not mixed[0].ambient_only and not mixed[0].default_only
    plain = match.classify("aws s3 ls", CONTRACT, ADMIN, "/r")
    assert plain[0].ambient_only


def test_tamper_forms() -> None:
    def deny(command: str, cwd: str = "/work") -> str | None:
        return match.tamper_message("Bash", {"command": command}, TRANSCRIPT, HOME, cwd)

    directory = TRANSCRIPT.rsplit("/", 1)[0]
    rel = "~/.claude/projects/-repo/abc-123.jsonl"
    for tool, key in (
        ("Write", "file_path"),
        ("Edit", "file_path"),
        ("MultiEdit", "file_path"),
        ("NotebookEdit", "notebook_path"),
    ):
        assert match.tamper_message(tool, {key: directory + "/x"}, TRANSCRIPT, HOME, "/w"), tool
        assert match.tamper_message(tool, {key: "~/.claude/projects/-repo/x"}, TRANSCRIPT, HOME, "/w"), tool
        assert match.tamper_message(tool, {key: "/elsewhere/x"}, TRANSCRIPT, HOME, "/w") is None
    assert match.tamper_message("Write", {}, TRANSCRIPT, HOME, "/w") is None
    assert match.tamper_message("Write", {"file_path": "/a"}, "", HOME, "/w") is None
    assert match.tamper_message("Read", {"command": "echo x >> " + TRANSCRIPT}, TRANSCRIPT, HOME, "/w") is None
    assert match.tamper_message("Bash", {"command": 5}, TRANSCRIPT, HOME, "/w") is None
    writes = [
        f"echo x >> {TRANSCRIPT}",
        f"echo x > {TRANSCRIPT}",
        f"echo x &>> {TRANSCRIPT}",
        f"echo x >| {TRANSCRIPT}",
        f"echo x | tee {TRANSCRIPT}",
        f"sed -i s/a/b/ {TRANSCRIPT}",
        f"sed --in-place s/a/b/ {TRANSCRIPT}",
        f"cp /x {TRANSCRIPT}",
        f"mv /x {TRANSCRIPT}",
        f"rm {TRANSCRIPT}",
        f"truncate -s 0 {TRANSCRIPT}",
        f"dd of={TRANSCRIPT}",
        f"ln -s /x {TRANSCRIPT}",
        f"python3 -c 'open(\"{TRANSCRIPT}\")'",
        f"bin/venv-python -c 'x' {TRANSCRIPT}",
        f"python3.12 -Ic 'x' {TRANSCRIPT}",
        f"node -e 'x' {TRANSCRIPT}",
        f"python3 -c'open(1)' {TRANSCRIPT}",
        f"python3 <<< 'x' {TRANSCRIPT}",
        f"cat <<EOF > /tmp/x\n{TRANSCRIPT}\nEOF",
        f"cd {directory} && rm *.jsonl",
        "cd ~/.claude/projects/-repo && rm *.jsonl",
        f"sed -i x {rel}",
        f"echo >> {rel}",
        f"cd {directory} && echo x >> abc-123.jsonl",
        f"echo 'unterminated >> {TRANSCRIPT}",
    ]
    for command in writes:
        message = deny(command)
        assert message and "cat" in message, command
    for command in (
        f"cat {TRANSCRIPT}",
        f"tail -n 3 {TRANSCRIPT}",
        f"cat {TRANSCRIPT} > /tmp/copy",
        "echo x >> /tmp/other",
        "ls",
    ):
        assert deny(command) is None, command
    assert deny(f"cd {directory} && echo x > /tmp/elsewhere") is None
    assert deny("echo 'unterminated") is None


def test_fast_path_and_lexer_errors() -> None:
    assert match.fast_path_ok("git status", "agent_platform")
    assert match.fast_path_ok("aws s3 ls", "agent_platform")
    assert not match.fast_path_ok("terraform plan", "agent_platform")
    assert not match.fast_path_ok("aws s3 ls", ADMIN)
    assert match.fast_path_ok("git status", ADMIN)
    with pytest.raises(ValueError):
        match.classify("terraform apply 'unterminated", CONTRACT, "agent_platform", "/r")
    assert match.classify("git status", CONTRACT, "agent_platform", "/r") == []


def test_contract_without_a_class_classifies_nothing_for_it() -> None:
    bare = {"gate": {"live_roots": {}, "admin_profiles": []}, "classes": []}
    assert match.classify("terraform -chdir=terraform/github apply", bare, "agent_platform", "/r") == []


BNT = "bin/sync-terraform-assets.sh --tfvars"
TFVARS = "/secret/agent-platform-terraform-personal-tfvars"


def secret_targets(command: str) -> set[str]:
    return {h.target for h in match.classify(command, CONTRACT, "agent_platform", "/work/repo") if h.cls == "secret-read"}


def test_entry_point_target_is_its_declared_secret() -> None:
    assert secret_targets("bash scripts/ci/neon_api_key.sh run -- echo --name harmless") == {"/secret/neon-api-key"}
    assert secret_targets("bash scripts/ci/neon_api_key.sh --secret-id x --names /y") == {"/secret/neon-api-key"}
    assert secret_targets("bash bin/sync-terraform-assets.sh --tfvars") == {TFVARS}
    assert secret_targets("bash scripts/ci/terraform_github_apply.sh") == {"agent-platform-terraform-github-apply-app"}
    assert all(h.exact for h in match.classify("bash scripts/ci/neon_api_key.sh", CONTRACT, "agent_platform", "/r"))
    bare = {"classes": [{"id": "secret-read", "entry_points": ["scripts/ci/neon_api_key.sh"], "approval": "session"}]}
    assert {h.target for h in match.classify("bash scripts/ci/neon_api_key.sh", bare, "agent_platform", "/r")} == {"unknown"}
    wrong = {**bare, "classes": [{**bare["classes"][0], "entry_point_reads": {"scripts/ci/neon_api_key.sh": []}}]}
    assert {h.target for h in match.classify("bash scripts/ci/neon_api_key.sh", wrong, "agent_platform", "/r")} == {"unknown"}


def test_secret_targets_one_per_name() -> None:
    ssm = "aws ssm {} --with-decryption"
    assert secret_targets(ssm.format("get-parameters --names /a /b")) == {"/a", "/b"}
    assert secret_targets(ssm.format("get-parameters --name /a /b")) == {"/a", "/b"}
    assert secret_targets(ssm.format("get-parameters --name /a --name /b")) == {"/a", "/b"}
    assert secret_targets(ssm.format("get-parameters --names=/a /b")) == {"/a", "/b"}
    assert secret_targets(ssm.format("get-parameters --names /a,/b")) == {"/a", "/b"}
    assert secret_targets(ssm.format("get-parameter --name /a:3 --profile p")) == {"/a"}
    assert secret_targets(ssm.format("get-parameter --name /a:label")) == {"/a"}
    sm = "aws secretsmanager {}"
    assert secret_targets(sm.format("batch-get-secret-value --secret-id-list a b")) == {"a", "b"}
    assert secret_targets(sm.format("batch-get-secret-value --secret-id a b")) == {"a", "b"}
    assert secret_targets(sm.format("get-secret-value --secret-id a --region r")) == {"a"}


def test_unnameable_secret_reads_are_unknown() -> None:
    ssm = "aws ssm {} --with-decryption"
    sm = "aws secretsmanager {}"
    unnameable = [
        ssm.format('get-parameter --name "$(cat f)"'),
        ssm.format("get-parameter --name `cat f`"),
        ssm.format("get-parameter --name $X"),
        sm.format('get-secret-value --secret-id "pre-$(echo x)"'),
        ssm.format('get-parameters --names \'["/a","/b"]\''),
        sm.format("batch-get-secret-value --secret-id-list '[\"a\"]'"),
        sm.format("get-secret-value --secret-id file://x"),
        sm.format("get-secret-value --secret-id fileb://x"),
        ssm.format("get-parameter --name http://x"),
        ssm.format("get-parameter --name https://x"),
        ssm.format("get-parameter --name arn:aws:ssm:r:1:parameter/a"),
        sm.format("get-secret-value --secret-id arn:aws:secretsmanager:r:1:secret:a"),
        sm.format("get-secret-value --secret-id a:b"),
        sm.format("batch-get-secret-value --filters Key=name,Values=a"),
        sm.format("get-secret-value --cli-input-json file://x"),
        sm.format("get-secret-value --cli-input-yaml file://x"),
        sm.format("get-secret-value"),
        sm.format("get-secret-value --secret-id"),
        sm.format("get-secret-value --secret-id=''"),
        ssm.format("get-parameter --name :3"),
        ssm.format("get-parameters-by-path --path /p"),
        ssm.format("get-parameters-by-path --path=/p"),
        "python3 -c 'get_secret_value()'",
    ]
    for command in unnameable:
        assert secret_targets(command) == {"unknown"}, command
    assert secret_targets(ssm.format("get-parameters --names /a $X")) == {"/a", "unknown"}


def test_entry_point_profiles_raise_admin_credential() -> None:
    both = hits(f"bash {BNT}")
    assert both == {("secret-read", TFVARS), ("admin-credential", ADMIN)}
    assert ("admin-credential", ADMIN) not in hits("bash scripts/ci/neon_api_key.sh")
    no_key = {
        **CONTRACT,
        "classes": [{k: v for k, v in c.items() if k != "entry_point_profiles"} for c in CONTRACT["classes"]],
    }
    got = {(h.cls, h.target) for h in match.classify(f"bash {BNT}", no_key, "agent_platform", "/r")}
    assert not any(c == "admin-credential" for c, _ in got)
    merged = match.classify(f"AWS_PROFILE={ADMIN} bash {BNT}", CONTRACT, "agent_platform", "/r")
    assert [h.target for h in merged if h.cls == "admin-credential"] == [ADMIN]


def _script_reads(spec: str) -> tuple[set[str], set[str]]:
    path = spec.split()[0]
    script = (ROOT / path).read_text(encoding="utf-8")
    code = [ln for ln in script.splitlines() if not ln.strip().startswith("#")]
    profiles = set(re.findall(r"AWS_PROFILE=([A-Za-z0-9_]+)", "\n".join(code)))
    if path == "bin/sync-terraform-assets.sh":
        delegate = re.search(r'DELEGATE="?\$REPO_ROOT/([^"\s]+)"?', script)
        assert delegate, "no DELEGATE= in sync-terraform-assets.sh"
        script = (ROOT / delegate.group(1)).read_text(encoding="utf-8")
    script = re.sub(r"\\\n\s*", " ", script)
    variables = dict(re.findall(r'^([A-Za-z_]+)="([^"$]*)"\s*(?:#.*)?$', script, re.MULTILINE))
    names: set[str] = set()
    for raw in script.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        assert "get_secret_value" not in line and "WithDecryption" not in line, (spec, line)
        for var, value in variables.items():
            line = line.replace(f'"${var}"', value).replace(f"${{{var}}}", value).replace(f"${var}", value)
        line = re.sub(r"^(?:(?:if|elif|while|until|then|do|!|\{|\()\s+)+", "", line)
        if not re.search(r"aws\s+(?:ssm|secretsmanager)\b", line):
            continue
        found = match.classify(re.sub(r"[;&|]+\s*$", "", line), CONTRACT, "agent_platform", "/r")
        secret = [h for h in found if h.cls == "secret-read"]
        assert secret or not re.search(r"aws\s+(?:ssm\s+get-parameter|secretsmanager\s+get-secret-value)", line), (spec, line)
        for h in secret:
            assert h.target != "unknown", (spec, line)
            names.add(h.target)
    return names, profiles


def test_entry_point_reads_bind_to_their_scripts() -> None:
    spec = classes("secret-read")
    reads = spec["entry_point_reads"]
    assert set(reads) == set(spec["entry_points"])
    admins = set(CONTRACT["gate"]["admin_profiles"])
    declared = classes("admin-credential")["entry_point_profiles"]
    for entry, declared_names in reads.items():
        names, profiles = _script_reads(entry)
        assert names == set(declared_names), entry
        assert {p for p in profiles if p in admins} == ({declared[entry]} if entry in declared else set()), entry
    assert set(declared) <= set(reads)


def test_option_value_reads_both_flag_forms() -> None:
    assert match._option_value(["aws", "--profile", "p"], ("--profile",)) == "p"
    assert match._option_value(["aws", "--profile=p"], ("--profile",)) == "p"
    assert match._option_value(["aws", "--profile"], ("--profile",)) is None
