# Source this to use the tools fetched by tools/host-tools.sh.
_exo_os_root="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root"
export PATH="$_exo_os_root/usr/bin:$PATH"
export LD_LIBRARY_PATH="$_exo_os_root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# The tools root may be absent (a native mkosi host) or hold more than one Python: put every
# site-packages directory that exists ahead of any existing PYTHONPATH, and leave it alone if there
# is none.
_exo_os_dirs=
for _exo_os_sp in "$_exo_os_root"/usr/lib/python3*/site-packages; do
  if [[ -d $_exo_os_sp ]]; then _exo_os_dirs="${_exo_os_dirs:+$_exo_os_dirs:}$_exo_os_sp"; fi
done
if [[ -n $_exo_os_dirs ]]; then export PYTHONPATH="$_exo_os_dirs${PYTHONPATH:+:$PYTHONPATH}"; fi
unset _exo_os_sp _exo_os_dirs
export EXO_OS_FIRMWARE="${EXO_OS_FIRMWARE:-$_exo_os_root/usr/share/edk2/x64/OVMF_CODE.4m.fd}"
export EXO_OS_FIRMWARE_VARS="${EXO_OS_FIRMWARE_VARS:-$_exo_os_root/usr/share/edk2/x64/OVMF_VARS.4m.fd}"
unset _exo_os_root
export QEMU_MODULE_DIR="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root/usr/lib/qemu"
