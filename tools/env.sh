# Source this to use the tools fetched by tools/host-tools.sh.
_exo_os_root="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root"
export PATH="$_exo_os_root/usr/bin:$PATH"
export LD_LIBRARY_PATH="$_exo_os_root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$(echo "$_exo_os_root"/usr/lib/python3*/site-packages)${PYTHONPATH:+:$PYTHONPATH}"
export EXO_OS_FIRMWARE="$_exo_os_root/usr/share/edk2/x64/OVMF_CODE.4m.fd"
unset _exo_os_root
export QEMU_MODULE_DIR="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root/usr/lib/qemu"
