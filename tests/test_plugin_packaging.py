"""Installed-layout discovery through the pinned core's required-executor factory."""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from exo import application
from exo.core.plugins import PluginManager
from exo.core.tools.executor import ExecutorConfigurationError

from exo_os import plugin, setup
from exo_os.sandbox import executor, policy

PROJECT = Path(__file__).resolve().parents[1]
OVERLAY = PROJECT / "image/mkosi.extra"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    import exo

    core = Path(exo.__file__).resolve().parents[2]
    image = tmp_path / "image"
    shutil.copytree(OVERLAY, image)
    plugins = image / "opt/exo/plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    # Exercise the actual generated extra-tree staging without wheel downloads
    # or invoking mkosi. The checked-in overlay alone must also carry the adapter.
    adapter = plugins / "exo-os"
    shutil.rmtree(adapter, ignore_errors=True)
    build = (PROJECT / "tools/build-image.sh").read_text()
    block = build.split("# Runtime plugin folders: required executor mode does not load ambient entry points.\n", 1)[1]
    block = block.split("# End runtime plugin folders.", 1)[0]
    subprocess.run(["bash", "-eu", "-c", block], check=True,
                   env={**os.environ, "here": str(PROJECT), "dep": str(core), "extra": str(image)})
    subprocess.run(["sh", str(OVERLAY / "usr/lib/exo-os/harden-image-modes.sh")],
                   env={**os.environ, "EXO_OS_IMAGE_ROOT": str(image)}, check=True)
    # Root ownership/ancestors are supplied by mkosi, unavailable on a host-user
    # fixture. Retain real type, symlink and write-mode checks inside the image.
    original = Path.lstat
    def image_lstat(path):
        fields = list(original(path))
        fields[4] = 0
        if not path.is_relative_to(image):
            fields[0] &= ~0o022
        return os.stat_result(fields)
    monkeypatch.setattr(Path, "lstat", image_lstat)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(policy.Policy, "load", classmethod(lambda cls: policy.Policy(workspace)))
    monkeypatch.setattr(executor.LinuxExecutor, "trusted_plugin_dirs", (str(plugins),))
    home = tmp_path / "home"
    home.mkdir()
    for name in list(os.environ):
        if name.startswith(("EXO_", "DIVAN_", "XDG_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setenv("EXO_TOOL_EXECUTOR", "exo-os")
    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    (home / "config.json").write_text(json.dumps({
        "principal": "exo", "core_plugins": setup.CORE_PLUGINS, "cwd": str(workspace),
        "models": {"fake": {}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
        "plugin_config": {"exo-gate": {"decision_model": False}}, "learn": {"enabled": False},
    }))
    def forbid_ambient_entry_points(*args, **kwargs):
        pytest.fail("required mode must not discover ambient plugin entry points")
    monkeypatch.setattr(PluginManager, "load_entry_points", forbid_ambient_entry_points)
    return image, plugins


def test_required_factory_loads_installed_os_tools_and_gate(installed):
    _, plugins = installed
    adapter = plugins / "exo-os"
    assert adapter.is_dir(), "exo-os must be an installed folder, not only a distribution entry point"
    for path in (adapter, adapter / "__init__.py", adapter / "plugin.yaml"):
        assert not path.is_symlink() and path.stat().st_mode & 0o022 == 0
    engine = application.create()
    try:
        assert isinstance(engine.registry.executor, executor.LinuxExecutor)
        assert engine.registry.executor_required
        assert not engine.plugins.errors()
        assert set(engine.plugins.plugins) == set(setup.CORE_PLUGINS)
        assert all(record.source == "dir" for record in engine.plugins.plugins.values())
        for name, handler, risk, _ in plugin.TOOLS:
            entry = engine.plugins.tools[name]
            assert entry.handler is handler and entry.spec.toolset == "exo-os" and entry.spec.risk == risk
        assert "machine" in engine.plugins.commands
        assert any(entry.plugin == "exo-gate" for entry in engine.plugins.hooks.entries("pre_tool_call"))
    finally:
        asyncio.run(engine.close())


def test_missing_os_folder_still_refuses_required_startup(installed):
    _, plugins = installed
    shutil.rmtree(plugins / "exo-os", ignore_errors=True)
    with pytest.raises(ExecutorConfigurationError, match="required executor plugins failed to load: exo-os"):
        application.create()


def test_installed_plugin_inventory_refuses_writable_directory(installed):
    _, plugins = installed
    plugins.chmod(0o777)
    with pytest.raises(ExecutorConfigurationError, match="not root-controlled"):
        application.create()
