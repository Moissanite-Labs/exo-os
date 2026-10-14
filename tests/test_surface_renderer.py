"""The VM renderer is explicit, scoped to one service and checked before fixture execution."""
import importlib.util
import io
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SPEC = importlib.util.spec_from_file_location("surface_renderer", TOOLS / "surface-renderer.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def test_cairo_configuration_exists_only_in_vmtest_surface_dropin():
    expected = ROOT / "image/mkosi.profiles/vmtest/mkosi.extra/usr/lib/systemd/user/exo-surface.service.d/20-renderer.conf"
    candidates = [*ROOT.joinpath("image/mkosi.extra").rglob("*"),
                  *ROOT.joinpath("image/mkosi.profiles").rglob("*"), *ROOT.joinpath("image").glob("mkosi.*")]
    declarations = [path for path in candidates if path.is_file() and b"GSK_RENDERER" in path.read_bytes()]
    assert declarations == [expected]
    assert "[Service]\n" in expected.read_text() and "Environment=GSK_RENDERER=cairo\n" in expected.read_text()
    assert not ROOT.joinpath("image/mkosi.extra/usr/lib/systemd/user/exo-surface.service.d/20-renderer.conf").exists()


@pytest.mark.parametrize("raw", ['GSK_RENDERER=cairo\n', 'FOO="a b" "GSK_RENDERER=cairo"',
                                  "FOO='$(never execute)' GSK_RENDERER=cairo"])
def test_declared_renderer_accepts_only_validated_cairo_token_without_evaluation(raw):
    assert renderer.declared_renderer(raw) == "cairo"


@pytest.mark.parametrize("raw", ["", "GSK_RENDERER=gl", "GSK_RENDERER=", "GSK_RENDERER=cairo;",
    "GSK_RENDERER=cairo GSK_RENDERER=cairo", 'GSK_RENDERER="cairo', "NOT_AN_ASSIGNMENT GSK_RENDERER=cairo",
    "GSK_RENDERER=cairo\nBAD=value", "GSK_RENDERER=cairo\x00", "F=" + "x" * renderer.LIMIT])
def test_declared_renderer_refuses_missing_duplicate_malformed_or_oversized_environment(raw):
    with pytest.raises(ValueError):
        renderer.declared_renderer(raw)


@pytest.mark.parametrize("raw", [b"OTHER=hidden\0", b"GSK_RENDERER=gl\0", b"GSK_RENDERER=cairo",
                                 b"GSK_RENDERER=cairo\0GSK_RENDERER=cairo\0", b"x" * (renderer.LIMIT + 1)])
def test_effective_process_renderer_must_match_exactly_once(raw):
    with pytest.raises(ValueError):
        renderer.effective_renderer(raw)
    assert renderer.effective_renderer(b"OTHER=hidden\0GSK_RENDERER=cairo\0") == "cairo"


def service_fixture(monkeypatch, *, declaration="GSK_RENDERER=cairo\n", effective=b"GSK_RENDERER=cairo\0", rotated=False):
    monkeypatch.setenv("EXO_OS_VM_ACCEPTANCE", "1")
    monkeypatch.setattr(renderer.sys, "platform", "linux")
    monkeypatch.setattr(renderer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(renderer.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="exo"))
    monkeypatch.setattr(renderer.Path, "is_dir", lambda _: True)
    reads = []

    def systemctl(argv, **kwargs):
        assert argv[:4] == ["systemctl", "--user", "show", "exo-surface.service"]
        assert kwargs["timeout"] == 10 and kwargs["check"] is True
        name = argv[5]
        reads.append(name)
        return SimpleNamespace(stdout=declaration if name == "Environment" else
                               "43\n" if rotated and reads.count("MainPID") > 1 else "42\n")

    monkeypatch.setattr(renderer.subprocess, "run", systemctl)
    monkeypatch.setattr(renderer.Path, "open", lambda path, mode: io.BytesIO(effective)
                        if str(path) == "/proc/42/environ" and mode == "rb" else pytest.fail("unexpected file"))


def test_live_record_checks_declaration_and_actual_process_without_exporting_other_environment(monkeypatch):
    service_fixture(monkeypatch, effective=b"SECRET=private marker\0GSK_RENDERER=cairo\0")
    record = renderer.inspect_service()
    assert record == {"service": "exo-surface.service", "main_pid": 42, "declared": "cairo", "effective": "cairo"}
    assert "private marker" not in json.dumps(record)
    assert renderer.validate_record(record) == "cairo"


@pytest.mark.parametrize("kwargs", [{"declaration": ""}, {"effective": b"GSK_RENDERER=gl\0"}, {"rotated": True}])
def test_live_inspection_refuses_missing_declaration_effective_mismatch_and_pid_race(monkeypatch, kwargs):
    service_fixture(monkeypatch, **kwargs)
    with pytest.raises(ValueError):
        renderer.inspect_service()


@pytest.mark.parametrize("patch", [{"effective": "gl"}, {"declared": "default"}, {"main_pid": True},
                                  {"main_pid": 0}, {"service": "other.service"}, {"extra": "cairo"}])
def test_record_validation_rejects_mismatched_or_malformed_metadata(patch):
    record = {"service": "exo-surface.service", "main_pid": 42, "declared": "cairo", "effective": "cairo"}
    with pytest.raises(ValueError):
        renderer.validate_record({**record, **patch})


def test_both_vm_harnesses_pass_only_the_validated_service_renderer_to_fixtures():
    # Keep a direct link from validated installed state to the two fixture launch paths.
    for name in ("accept-phase3.sh", "diagnose-atspi.sh"):
        source = TOOLS.joinpath(name).read_text()
        validation = 'renderer=$(printf \'%s\\n\' "$renderer_record" | "$here/.venv/bin/python" "$here/tools/surface-renderer.py" --validate)'
        assert validation in source and "GSK_RENDERER=$renderer" in source
        assert source.index(validation) < source.index("GSK_RENDERER=$renderer")
    phase3 = TOOLS.joinpath("accept-phase3.sh").read_text()
    assert phase3.index('systemctl --user restart exo-surface') < phase3.index('renderer_record=$(as_exo')


@pytest.mark.parametrize("name,budget", [("accept-phase3.sh", "90"), ("diagnose-atspi.sh", "120")])
def test_real_fixture_launch_expression_propagates_validated_renderer(tmp_path, name, budget):
    source = TOOLS.joinpath(name).read_text()
    begin = source.index('probe_output=$(as_exo')
    end = source.index("printf '%s\\n' \"$probe_output\"", begin)
    launch = source[begin:end]
    adapter = tmp_path / "adapter.py"
    adapter.write_text('''import json, os, shlex, subprocess, sys
args = shlex.split(sys.argv[1])
assert args[:3] == ['timeout', sys.argv[2], 'env']
env = dict(os.environ)
for item in args[3:]:
    if '=' not in item: break
    key, value = item.split('=', 1)
    env[key] = value
assert env['GSK_RENDERER'] == 'cairo'
assert env['GDK_BACKEND'] == 'wayland' and env['EXO_OS_VM_ACCEPTANCE'] == '1'
print(json.dumps({'gsk_renderer': env['GSK_RENDERER']}))
''')
    record = {"service": "exo-surface.service", "main_pid": 42, "declared": "cairo", "effective": "cairo"}
    script = f"""set -euo pipefail
renderer=$(printf '%s\\n' {shlex.quote(json.dumps(record))} | {shlex.quote(sys.executable)} {shlex.quote(str(TOOLS / 'surface-renderer.py'))} --validate)
guest_dir=/run/exo-atspi.fixture
image_source={'a' * 40}
diagnostic_source={'b' * 40}
probe_rc=0
as_exo() {{ {shlex.quote(sys.executable)} {shlex.quote(str(adapter))} "$1" {budget}; }}
{launch}
printf '%s\\n' "$probe_output"
[[ $probe_rc == 0 ]]
"""
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout) == {"gsk_renderer": "cairo"}
