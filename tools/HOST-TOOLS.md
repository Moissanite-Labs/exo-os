`host-tools.sh` asks pacman for the complete requested inventory, including packages already installed on the host. Downloads and cached packages must pass detached-signature and revoked-key checks before extraction.

Each installation is built under `EXO_OS_TOOLS/generations/gen-*` at its final absolute path. The script checks command entrypoints, firmware, helper files and Python imports before publication. On a fresh prefix, `root`, `bin` and `venv` are compatibility symlinks through `current`.

Replacing that single symlink publishes all three components together. A Python supervisor holds a nonblocking advisory lock for the whole installation. Concurrent runs fail without changing the active generation.

Existing generations, including failed candidates, remain for manual inspection and cleanup. Never delete the target of `current`, and keep any generation used by an existing shell or build. Failed downloads leave no unverified `.part` files.

Legacy prefixes with real `root`, `bin` and `venv` directories remain unchanged. New generations publish through `current`. Updated `env.sh`, image builder and VM runner select its validated absolute path.

Existing callers using direct legacy paths continue to use the old installation. Source the updated `env.sh` to use the new generation. Moving a legacy venv would invalidate its absolute paths, so the installer never relocates it. Partial legacy directories also remain untouched during recovery. Unknown symlinks and file layouts are refused rather than followed or removed.

Tests use local package fixtures, temporary signing keys and stubbed pacman/curl. They verify preservation and publication behavior. They do not establish native Arch tool execution or VM boot.

When combined with builder provenance changes, include `tools/host-tools-generation.py` in the builder input digest. `env.sh` now uses that helper to select the active generation.
