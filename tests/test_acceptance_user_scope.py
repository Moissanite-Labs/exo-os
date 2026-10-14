"""Execute remote-shell parsing with a wrapper stub, without changing host UID."""
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
HARNESS = TOOLS / "accept-common.sh"
WRAPPERS = ["accept-phase1.sh", "accept-phase2.sh", "accept-phase3.sh",
            "accept-ledger.sh", "accept-isolation.sh", "accept-model-spend.sh",
            "diagnose-atspi.sh"]


def run_as_exo(tmp_path, wrapper, command, *, stdin="", words=None):
    # This stub represents the identity/environment boundary. It never calls
    # runuser, sudo or setuid. Both shells keep the real test process's UID.
    stub = tmp_path / "as-exo"
    stub.write_text("""#!/bin/bash
printf '%s\\n' "$#" "$1" > "$SCOPE_DISPATCH"
printf '%s' "${3:-}" > "$SCOPE_BODY"
export ACCEPT_SCOPE=exo-stub
if [[ $1 == /usr/bin/bash ]]; then shift; exec /bin/bash "$@"; fi
exec "$@"
""")
    stub.chmod(0o755)
    stage = tmp_path / "stage"
    stage.write_text("""#!/bin/bash
printf '%s|%s|%s\\n' "$1" "$ACCEPT_SCOPE" "$(id -u)" >> "$SCOPE_STAGES"
printf '%s|%s|%s\\n' "$1" "$ACCEPT_SCOPE" "$(id -u)"
""")
    stage.chmod(0o755)
    payload = tmp_path / "payload"
    payload.write_text(command)
    definition = re.search(r"^as_exo\(\) \{.*\}$", (TOOLS / wrapper).read_text(), re.M).group()
    call = 'as_exo "$command"' if words is None else "as_exo " + shlex.join(words)
    script = f"""result() {{ :; }}
ssh_vm() {{ /bin/sh -c "$1"; }}
source {shlex.quote(str(HARNESS))}
{definition}
command=$(cat {shlex.quote(str(payload))}; printf .)
command=${{command%.}}
{call}
"""
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "ACCEPT_SCOPE": "outer-stub", "SCOPE_DISPATCH": str(tmp_path / "dispatch"),
           "SCOPE_BODY": str(tmp_path / "body"), "SCOPE_STAGES": str(tmp_path / "stages")}
    run = subprocess.run(["/bin/bash", "-c", script], input=stdin, text=True,
                         capture_output=True, env=env, timeout=10)
    return run, tmp_path / "dispatch", tmp_path / "body"


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("command,labels", [
    ("stage first && stage second; stage third", ["first", "second", "third"]),
    ("stage pipeline-left | stage pipeline-right", ["pipeline-right"]),
    ("stage simple", ["simple"]),
])
def test_every_stage_runs_inside_user_wrapper(tmp_path, wrapper, command, labels):
    run, dispatch, body = run_as_exo(tmp_path, wrapper, command)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [f"{label}|exo-stub|{os.getuid()}" for label in labels]
    all_labels = ["pipeline-left", "pipeline-right"] if "pipeline-left" in command else labels
    assert sorted((tmp_path / "stages").read_text().splitlines()) == sorted(
        f"{label}|exo-stub|{os.getuid()}" for label in all_labels)
    assert dispatch.read_text() == "3\n/usr/bin/bash\n"
    assert body.read_text() == command


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_heredoc_data_and_substitutions_do_not_escape_user_shell(tmp_path, wrapper):
    literal = "quotes ' \" ; && | $HOME $(stage BAD) `stage BAD` \\ end"
    command = f"cat <<'DATA'\n{literal}\nDATA\nstage after-heredoc\nstage \"$(stage substitution)\"\nstage \"`stage backticks`\""
    run, _, body = run_as_exo(tmp_path, wrapper, command)
    uid = os.getuid()
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [literal, f"after-heredoc|exo-stub|{uid}",
                                       f"substitution|exo-stub|{uid}|exo-stub|{uid}",
                                       f"backticks|exo-stub|{uid}|exo-stub|{uid}"]
    assert body.read_text() == command


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_invalid_syntax_reaches_user_shell_and_cannot_run_outer_stage(tmp_path, wrapper):
    command = "stage must-not-run && ("
    run, dispatch, body = run_as_exo(tmp_path, wrapper, command)
    assert run.returncode != 0 and not run.stdout
    assert dispatch.read_text() == "3\n/usr/bin/bash\n"
    assert body.read_text() == command


@pytest.mark.parametrize("data", ["", "one'quote", "\"; stage BAD; #", "$HOME $(stage BAD) `stage BAD`",
                                  "backslash \\ tab\t line\nlast\n"])
def test_shell_metacharacters_remain_literal_data(tmp_path, data):
    command = "printf '%s' " + shlex.quote(data) + "\n\n"
    run, _, body = run_as_exo(tmp_path, "accept-phase3.sh", command)
    assert run.returncode == 0 and run.stdout == data, run.stderr
    assert body.read_text() == command
    assert not (tmp_path / "stages").exists()


def test_stdin_and_status_are_preserved(tmp_path):
    data = "stdin ' $HOME $(stage BAD) `stage BAD`\n"
    run, _, _ = run_as_exo(tmp_path, "accept-phase3.sh", "cat; stage finished; exit 7", stdin=data)
    assert run.returncode == 7
    assert run.stdout == data + f"finished|exo-stub|{os.getuid()}\n"


def test_legacy_simple_command_words_are_preserved(tmp_path):
    run, _, body = run_as_exo(tmp_path, "accept-phase3.sh", "stage simple", words=["stage", "simple"])
    assert run.returncode == 0, run.stderr
    assert run.stdout == f"simple|exo-stub|{os.getuid()}\n"
    assert body.read_text() == "stage simple"
