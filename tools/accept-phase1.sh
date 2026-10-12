#!/usr/bin/env bash
# Phase 1 acceptance (ROADMAP.md): boot the built image (vmtest profile) and check, with no manual steps:
#   1. it reaches a desktop: greetd logs the person in, Hyprland runs (screenshot saved)
#   2. exod starts at login, comes back after being killed, and a chat turn completes
#   3. the store made in the VM verifies with `exo log verify` in the VM and in plain Exo on this host
#   4. memory (PSS per process) with Chromium and three web apps open is written down
# Results: image/out/accept-phase1.txt. Exit code 0 only if checks 1 to 3 pass (4 is a measurement).
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-phase1.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { ssh_vm "cd /tmp && as-exo $*"; }
pass=0 fail=0
result() {  # name ok detail
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
shot() {  # name: screenshot from inside the guest (grim), since QEMU cannot dump a GL scanout
  ssh_vm "cd /tmp && as-exo grim /tmp/$1.png && cat /tmp/$1.png" > "$out/$1.png" 2>/dev/null
}

: > "$report"
echo "Exo OS phase 1 acceptance, $(date -Is), exo $(cat "$here/exo.pin" | cut -c1-7), exo-os $(git -C "$here" rev-parse --short HEAD)" | tee -a "$report"

"$here/tools/run-vm.sh" --mem "${EXO_OS_VM_MEM:-3072}" ${EXO_OS_VM_ARGS:-} > "$out/qemu.log" 2>&1 &
qemu=$!
trap 'kill $qemu 2>/dev/null' EXIT
for _ in $(seq 1 60); do ssh_vm true 2>/dev/null && break; sleep 3; done
ssh_vm 'systemctl is-system-running --wait' >/dev/null 2>&1

# 1. desktop
for _ in $(seq 1 30); do ssh_vm 'pgrep -u exo -x Hyprland' >/dev/null 2>&1 && break; sleep 2; done
hypr=$(ssh_vm 'pgrep -u exo -x Hyprland' 2>/dev/null)
session=$(ssh_vm "loginctl list-sessions --no-legend | awk '\$3==\"exo\"{print \$1}' | head -1")
stype=$(ssh_vm "loginctl show-session $session -p Type --value" 2>/dev/null)
sleep 8
shot accept-desktop
result "desktop" "$([[ -n $hypr && $stype == wayland ]] && echo 1 || echo 0)" \
  "Hyprland pid=${hypr:-none}, session=${session:-none} type=${stype:-none}, screenshot image/out/accept-desktop.png"
failed=$(ssh_vm 'systemctl --failed --no-legend --plain | awk "{print \$1}" | tr "\n" " "')
ufailed=$(as_exo systemctl --user --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')
echo "      failed units: system [${failed}] user [${ufailed}]" | tee -a "$report"

# 2. exod
for _ in $(seq 1 30); do [[ $(as_exo systemctl --user is-active exod) == active ]] && break; sleep 2; done
pid1=$(as_exo systemctl --user show exod -p MainPID --value)
as_exo kill -9 "$pid1" >/dev/null 2>&1
sleep 6
pid2=$(as_exo systemctl --user show exod -p MainPID --value)
state=$(as_exo systemctl --user is-active exod)
result "exod-restarts" "$([[ $state == active && $pid2 != 0 && $pid2 != "$pid1" ]] && echo 1 || echo 0)" \
  "before kill pid=$pid1, after pid=$pid2 ($state)"
reply=$(as_exo "sh -c 'echo \"Say hello in five words.\" | timeout 120 /opt/exo/bin/exo chat --title acceptance'" 2>&1 | grep -v -e 'warn: cwd' | tail -4)
models=$(as_exo /opt/exo/bin/exo config roles 2>&1 | head -3 | tr '\n' ' ')
result "chat" "$([[ -n $reply && $reply != *rror* ]] && echo 1 || echo 0)" "reply: $(echo "$reply" | tr '\n' ' ' | cut -c1-160) | routes: ${models:0:160}"

# 3. store verifies in the VM and on this host
vm_verify=$(as_exo /opt/exo/bin/exo log verify 2>&1 | tail -1)
rm -rf "$out/store-copy" && mkdir -p "$out/store-copy"
ssh_vm 'tar -C /var/lib/exo/exo/principals -cf - exo' | tar -C "$out/store-copy" -xf -
host_verify=$("$here/.venv/bin/exo" log verify --root "$out/store-copy/exo" 2>&1 | tail -1)
rows=$("$here/.venv/bin/python" -c "
import sqlite3,sys; c=sqlite3.connect('$out/store-copy/exo/ledger.sqlite')
print(c.execute('select count(*) from events').fetchone()[0])" 2>/dev/null || echo '?')
vm_ok=$(echo "$vm_verify" | grep -c '"ok": true'); host_ok=$(echo "$host_verify" | grep -c '"ok": true')
result "store-portable" "$([[ $vm_ok == 1 && $host_ok == 1 ]] && echo 1 || echo 0)" \
  "vm: $vm_verify | host: $host_verify | ledger rows: $rows"

# machine profile change inside the VM, end to end: rendered, Hyprland verified and reloaded, logged
change=$(as_exo /opt/exo/bin/exo-os set keyboard.layouts us,ara,pk 2>&1)
kb=$(as_exo hyprctl getoption input:kb_layout 2>&1 | head -1)
result "profile-change-live" "$([[ $change == *Undo?* && $kb == *ara* ]] && echo 1 || echo 0)" "$change | live: $kb"

# 4. memory with Chromium and three web apps
for url in https://web.whatsapp.com/ https://mail.google.com/ https://drive.google.com/; do
  as_exo "setsid -f chromium --app=$url </dev/null >/dev/null 2>&1"
done
sleep 45
ssh_vm 'python3 - <<"PY"
import glob, os, collections
tot = collections.Counter()
for d in glob.glob("/proc/[0-9]*"):
    try:
        name = open(d + "/comm").read().strip()
        pss = next(int(l.split()[1]) for l in open(d + "/smaps_rollup") if l.startswith("Pss:"))
    except Exception:
        continue
    group = "chromium" if name.startswith(("chromium", "chrome")) else ("exod" if name == "exo" else name)
    tot[group] += pss
m = dict(l.split(":") for l in open("/proc/meminfo"))
print("      MemTotal %s MB, MemAvailable %s MB" % (int(m["MemTotal"].split()[0]) // 1024, int(m["MemAvailable"].split()[0]) // 1024))
for name, kb in tot.most_common(12):
    print("      PSS %-22s %6d MB" % (name, kb // 1024))
PY' | tee -a "$report"
shot accept-apps

echo "passed $pass, failed $fail" | tee -a "$report"
((fail == 0))
