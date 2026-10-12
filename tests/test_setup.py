import subprocess
import sys

from exo.gate import load_policies

from exo_os import setup


def test_setup_creates_store_rules_and_machine_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("EXO_PRINCIPAL", "pat")
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.setattr("exo_os.changes.DEFAULT_CHECKS", [])
    assert setup.main() == 0
    store = tmp_path / "home" / "principals" / "pat"
    assert (store / "machine" / ".git").exists()
    text = (store / "policies" / "gates.yaml").read_text()
    assert 'tool: "system_package_*"' in text
    pol = load_policies(store)
    rules = pol.gates.rules if hasattr(pol, "gates") else pol["gates"]["rules"]
    assert any(r["match"].get("tool") == "system_package_*" and r["decision"] == "ask" for r in rules)
    assert setup.main() == 0  # second run changes nothing
    assert (store / "policies" / "gates.yaml").read_text() == text
    out = subprocess.run([sys.executable, "-m", "exo.cli", "log", "verify", "--root", str(store)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
