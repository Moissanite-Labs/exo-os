# Source this to use the tools fetched by tools/host-tools.sh.
_exo_os_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
EXO_OS_TOOLS_ACTIVE="$(python3 "$_exo_os_env_dir/host-tools-generation.py" select "${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}")" || return 1
export EXO_OS_TOOLS_ACTIVE
_exo_os_root="$EXO_OS_TOOLS_ACTIVE/root"
export PATH="$_exo_os_root/usr/bin:$PATH"
export LD_LIBRARY_PATH="$_exo_os_root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$(echo "$_exo_os_root"/usr/lib/python3*/site-packages)${PYTHONPATH:+:$PYTHONPATH}"
export EXO_OS_FIRMWARE="${EXO_OS_FIRMWARE:-$_exo_os_root/usr/share/edk2/x64/OVMF_CODE.4m.fd}"
export EXO_OS_FIRMWARE_VARS="${EXO_OS_FIRMWARE_VARS:-$_exo_os_root/usr/share/edk2/x64/OVMF_VARS.4m.fd}"
unset _exo_os_root _exo_os_env_dir
export QEMU_MODULE_DIR="$EXO_OS_TOOLS_ACTIVE/root/usr/lib/qemu"
