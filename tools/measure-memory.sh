#!/usr/bin/env bash
# Phase 1, check 4: memory (PSS per process group) with Chromium and three web apps open, in the VM.
# The web apps are not signed in, so they show their sign-in pages; a signed-in WhatsApp or Gmail uses more.
# Stops the VM early if this host's available memory drops under EXO_OS_HOST_FLOOR_MB (default 300).
# Result: image/out/memory.txt
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
floor="${EXO_OS_HOST_FLOOR_MB:-300}"
host_avail() { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo; }

"$here/tools/run-vm.sh" --mem "${EXO_OS_VM_MEM:-2560}" ${EXO_OS_VM_ARGS:-} > "$out/qemu.log" 2>&1 &
qemu=$!
trap 'kill $qemu 2>/dev/null' EXIT
export here  # the fresh bash gets the function from the environment; it needs the path it uses too
export -f ssh_vm  # accept-wait-ssh.py runs its probe through a fresh bash
"$here/.venv/bin/python" "$here/tools/accept-wait-ssh.py" 300 - true \
  || { echo "VM never came up for SSH within 300s; see $out/qemu.log" >&2; exit 1; }
ssh_vm 'timeout 300 systemctl is-system-running --wait >/dev/null' \
  || { echo "systemd never reached running within 300s; see $out/qemu.log" >&2; exit 1; }
for _ in $(seq 1 30); do ssh_vm 'cd /tmp && as-exo systemctl --user is-active -q exo-surface' && break; sleep 2; done
for url in https://web.whatsapp.com/ https://mail.google.com/ https://drive.google.com/; do
  ssh_vm "cd /tmp && as-exo sh -c 'setsid -f chromium --app=$url </dev/null >/dev/null 2>&1'"
done
lowest=$(host_avail)
for _ in $(seq 1 30); do  # about 60 s for the pages to load, watching this host
  a=$(host_avail); ((a < lowest)) && lowest=$a
  if ((a < floor)); then echo "stopped: host available memory fell to ${a} MB" | tee "$out/memory.txt"; exit 1; fi
  sleep 2
done
{
  echo "Exo OS memory in the VM ($(date -Is), exo-os $(git -C "$here" rev-parse --short HEAD), VM RAM ${EXO_OS_VM_MEM:-2560} MB)"
  echo "Chromium with WhatsApp, Gmail and Drive web apps open (sign-in pages; not signed in)"
  ssh_vm 'python3 - <<"PY"
import glob, collections
tot, n = collections.Counter(), collections.Counter()
for d in glob.glob("/proc/[0-9]*"):
    try:
        name = open(d + "/comm").read().strip()
        cmd = open(d + "/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        pss = next(int(l.split()[1]) for l in open(d + "/smaps_rollup") if l.startswith("Pss:"))
    except Exception:
        continue
    if name.startswith(("chromium", "chrome")): g = "chromium (all processes)"
    elif "exo_os.surface" in cmd: g = "exo-surface (prompt bar)"
    elif "exo serve" in cmd or cmd.startswith("/opt/exo/bin/python /opt/exo/bin/exo serve"): g = "exod"
    else: g = name
    tot[g] += pss; n[g] += 1
m = dict(l.split(":") for l in open("/proc/meminfo"))
mb = lambda k: int(m[k].split()[0]) // 1024
print("MemTotal %d MB, MemAvailable %d MB, used %d MB" % (mb("MemTotal"), mb("MemAvailable"), mb("MemTotal") - mb("MemAvailable")))
for g, kb in tot.most_common(14):
    print("  %-28s %6d MB  (%d processes)" % (g, kb // 1024, n[g]))
print("  %-28s %6d MB" % ("sum of PSS", sum(tot.values()) // 1024))
PY'
  echo "host lowest MemAvailable during the run: ${lowest} MB"
} | tee "$out/memory.txt"
ssh_vm "cd /tmp && as-exo grim /tmp/m.png && cat /tmp/m.png" > "$out/memory-apps.png" 2>/dev/null
