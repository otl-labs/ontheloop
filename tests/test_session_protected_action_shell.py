"""Unit tests for scripts/session/protected_action_shell.py (100% line floor)."""

from __future__ import annotations

import pytest

from scripts.session import protected_action_shell as sh

EP = ["scripts/ci/neon_api_key.sh"]


def words(text: str, eps: list[str] | None = None) -> list[list[str]]:
    return [c.words for c in sh.walk(text, eps or []).commands]


def test_simple_commands_carry_env_and_directory() -> None:
    walk = sh.walk("A=1 B=2 && export AWS_PROFILE=x && cd sub && aws s3 ls")
    last = walk.commands[-1]
    assert last.env == {"A": "1", "B": "2", "AWS_PROFILE": "x"}
    assert last.cwd == "sub" and last.command == "aws"
    assert sh.walk("pushd /a && cd b && ls").commands[-1].cwd == "/a/b"
    assert sh.walk("cd ~/x && cd $Y && ls").commands[-1].cwd == "$Y"
    assert sh.walk("env -C d terraform apply").commands[0].cwd == "d"
    assert sh.walk("cd").commands[0].words == ["cd"]
    assert sh.walk("export 1 X=2").commands == []
    only = sh.walk("A=1\nls")
    assert only.commands[0].env == {"A": "1"}
    assert sh.walk("FOO='a b' ls").commands[0].env == {"FOO": "a b"}


def test_wrapper_words_skip_to_the_command_word() -> None:
    for prefix in (
        "env -i X=1",
        "command",
        "exec",
        "nohup",
        "nice -n 5",
        "time -p",
        "timeout -k 5 9",
        "sudo -E",
        "stdbuf -oL",
    ):
        assert words(f"{prefix} terraform apply")[0][0] == "terraform", prefix
    assert words("xargs -I{} aws s3 ls")[0][0] == "aws"
    assert words("watch -n1 bash x.sh")[0][0] == "bash"
    assert words("timeout 9 scripts/ci/neon_api_key.sh run", EP)[0][0] == "scripts/ci/neon_api_key.sh"
    assert words("env X=1 scripts/ci/neon_api_key.sh", EP)[0][0] == "scripts/ci/neon_api_key.sh"
    assert words("sudo tee /x")[0][0] == "tee"
    assert words("nice python3 -c x")[0][0] == "python3"
    assert words("env echo hi") == []
    assert sh.walk("env A=1 terraform apply").commands[0].env == {"A": "1"}
    assert sh.walk("env --chdir=d terraform apply").commands[0].cwd == "d"
    assert sh.walk("env -Cd terraform apply").commands[0].cwd == "d"
    assert sh.walk("sudo env -C d terraform apply").commands[0].cwd == "d"


def test_inline_code_is_collected_by_receiver() -> None:
    assert sh.walk("python3 -c 'a'").inline == ["a"]
    assert sh.walk("python3.12 -Ic 'b'").inline == ["b"]
    assert sh.walk("perl -e 'c'").inline == ["c"]
    assert sh.walk("perl -pe 'd'").inline == ["d"]
    assert sh.walk("node -p 'e'").inline == ["e"]
    assert sh.walk("node --eval 'f'").inline == ["f"]
    assert sh.walk("node --eval=g").inline == ["g"]
    assert sh.walk("python3 -c'h'").inline == ["h"]
    assert sh.walk("python3 -c").inline == [""]
    assert sh.walk("node --eval").inline == [""]
    assert sh.walk("bin/venv-python -m mod --flag x").inline == []
    assert sh.walk("bin/venv-python --version").inline == []
    assert sh.walk("bin/venv-python -").inline == []
    assert sh.walk("python3 <<'EOF'\nbody\nEOF\n").inline == ["body\n"]
    assert sh.walk("git commit -F - <<'EOF'\nbody\nEOF\n").inline == []
    assert sh.walk('python3 <<< "here"').inline == ["here"]
    assert sh.walk("python3 <<< bare").inline == ["bare"]
    assert sh.walk("cat <<< x").inline == []
    assert sh.walk("echo 'a' \"b\" <<< x").heredoc
    with pytest.raises(ValueError):
        sh.walk("python3 <<< 'unterminated")


def test_here_string_and_arithmetic_shift_hide_no_line() -> None:
    for head in ("cat <<< x", "echo $((1<<2))", "((x = 1<<2))", "echo $(( (1<<2) ))", "echo '$((1<<2))'"):
        assert ["terraform", "apply"] in words(f"{head}\nterraform apply"), head
    assert sh.walk("echo $((1<<2)").commands is not None
    assert sh.walk("echo '<<<' \"<<<\"\nls").commands[-1].words == ["ls"]
    assert sh.walk('echo "a\\"b" \'c\'\nls').commands[-1].words == ["ls"]
    assert sh.walk("x=$((1<<2)); ls").commands[-1].words == ["ls"]
    assert sh.walk("cat <<A\nx\nA\nls").heredoc


def test_tails_and_substitutions_are_walked() -> None:
    assert ["terraform", "apply"] in words(r"find . -exec terraform apply \;")
    assert ["terraform", "apply"] in words(r"find . -execdir terraform apply \;")
    assert ["terraform", "apply"] in words("bash x.sh run -- terraform apply", ["x.sh"])
    assert ["aws", "s3"] in words("echo $(aws s3)")
    assert ["aws", "s3"] in words("echo `aws s3`")
    assert ["aws", "s3"] in words('bash -c "aws s3"')
    assert ["aws", "s3"] in words('eval "aws s3"')
    assert ["aws", "s3"] in words("a; b | c && d || aws s3 &\n")
    assert ["ls"] in words("ls > out 2>&1")
    assert sh.walk("ls > out 2>&1").commands[0].redirects == [(">", "out"), (">&", "1")]
    assert sh.walk("ls >").commands[0].redirects == [(">", "")]
    assert sh.walk("a &> o && b >> p || c >| q").commands[0].redirects == [("&>", "o")]
    assert sh.walk("f ((").commands is not None
    with pytest.raises(ValueError):
        sh.walk("ls 'unterminated")


def test_interpreter_kind_and_entry_matching() -> None:
    assert sh.interpreter_kind("/usr/bin/python3.12") == "python"
    assert sh.interpreter_kind("bin/venv-python") == "python"
    assert sh.interpreter_kind("perl") == "perl"
    assert sh.interpreter_kind("node") == "node"
    assert sh.interpreter_kind("ruby") is None
    assert sh.is_entry("./scripts/ci/x.sh", ["scripts/ci/x.sh"])
    assert not sh.is_entry("scripts/ci/y.sh", ["scripts/ci/x.sh"])
    walk = sh.Walk()
    assert walk.commands == [] and walk.inline == [] and not walk.heredoc
    assert sh.Simple([], {}, None).command == ""
