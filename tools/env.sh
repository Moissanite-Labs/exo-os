# Source this to use the tools fetched by tools/host-tools.sh.
_exo_os_root="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root"
export PATH="$_exo_os_root/usr/bin:$PATH"
export LD_LIBRARY_PATH="$_exo_os_root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# The tools root may be absent (a native mkosi host) or hold more than one Python: add every
# site-packages directory that exists, and leave PYTHONPATH alone if there is none.
for _exo_os_sp in "$_exo_os_root"/usr/lib/python3*/site-packages; do
  if [[ -d $_exo_os_sp ]]; then PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$_exo_os_sp"; fi
done
if [[ -n ${PYTHONPATH:-} ]]; then export PYTHONPATH; fi
unset _exo_os_sp
export EXO_OS_FIRMWARE="${EXO_OS_FIRMWARE:-$_exo_os_root/usr/share/edk2/x64/OVMF_CODE.4m.fd}"
export EXO_OS_FIRMWARE_VARS="${EXO_OS_FIRMWARE_VARS:-$_exo_os_root/usr/share/edk2/x64/OVMF_VARS.4m.fd}"
unset _exo_os_root
export QEMU_MODULE_DIR="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root/usr/lib/qemu"
