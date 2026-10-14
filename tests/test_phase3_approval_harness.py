"""A failed deliberate answer must not consume the next query as consent."""
import shlex
import subprocess
from pathlib import Path

import pytest


ACCEPT = Path(__file__).resolve().parents[1] / "tools/accept-phase3.sh"


@pytest.mark.parametrize("decision", ["allow", "pending", "deny"])
def test_phase3_requires_exact_resolution_before_typing_next_query(tmp_path, decision):
    source = ACCEPT.read_text()
    outcome = source[source.index("approval_outcome() {"):source.index('\nsource "$here/tools/accept-common.sh"')]
    stage = source[source.index("# 3. approval card"):source.index("# 4. a panel")]
    script = f"""
set -euo pipefail
report={shlex.quote(str(tmp_path / 'report'))}
pass=0 fail=0
sleep() {{ :; }}
bar() {{ :; }}
type_in() {{ printf 'TYPED %s\\n' "$1"; }}
press() {{ printf 'PRESSED %s\\n' "$1"; }}
layers() {{ echo 'namespace: exo-surface-bar namespace: exo-surface-card'; }}
shot() {{ :; }}
ssh_vm() {{ [[ $* == *'test -e /usr/bin/htop'* ]] && echo installed; }}
as_exo() {{
  case "$*" in
    *'approval.requested'*) echo a_fixture ;;
    *'approval.resolved'*) echo {decision} ;;
    *'log tail'*) echo exo-gate ;;
    *) return 99 ;;
  esac
}}
result() {{ printf '%s %s %s\\n' "$1" "$2" "$3"; }}
{outcome}
{stage}
type_in NEXT_QUERY
"""
    run = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=5)
    assert "PRESSED F6\nPRESSED y" in run.stdout
    if decision == "allow":
        assert run.returncode == 0, run.stdout + run.stderr
        assert "approval-card 1" in run.stdout and "TYPED NEXT_QUERY" in run.stdout
    else:
        assert run.returncode != 0 and "approval-card 0" in run.stdout
        assert "TYPED NEXT_QUERY" not in run.stdout
