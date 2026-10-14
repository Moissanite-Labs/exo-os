#!/usr/bin/env bash
# Phase 1 acceptance (ROADMAP.md): boot the built image (vmtest profile) and check, with no manual steps:
#   1. it reaches a desktop: greetd logs the person in, Hyprland runs (screenshot saved)
#   2. exod starts at login, comes back after being killed, and an offline RuleFake chat completes
#   3. the store verifies against the signer in the VM and an independently copied public checkpoint on this host
#   4. memory (PSS per process) with Chromium and three web apps open is written down
# Results: image/out/accept-phase1.txt. Exit code 0 only if checks 1 to 3 pass (4 is a measurement).
# The ROADMAP's real-provider chat criterion remains pending an authorized test key/spend cap.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd -P)"
out="$here/image/out"
report="$out/accept-phase1.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { accept_as_exo "$@"; }
pass=0 fail=0
result() {  # name ok detail
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
shot() {  # name: screenshot from inside the guest (grim), since QEMU cannot dump a GL scanout
  ssh_vm "cd /tmp && as-exo grim /tmp/$1.png && cat /tmp/$1.png" > "$out/$1.png" 2>/dev/null
}

accept_offline_chat() {
  # Called only after accept_start has verified the disposable VM's identity. RuleFake is
  # the core's supported no-network provider; replace every role and every fallback.
  ssh_vm 'cd /tmp && as-exo /opt/exo/bin/python -' <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['EXO_HOME']) / 'config.json'
data = json.loads(p.read_text())
data['models'] = {
    'roles': {role: ['fake:scripted'] for role in
              ('main', 'aux', 'decision', 'eval', 'fast', 'standard', 'deep')},
    'fake': {'reply': 'Hello from offline Exo OS.'},
}
p.write_text(json.dumps(data, indent=2) + '\n')
p.chmod(0o600)
PY
  local models reply chat_ok=0
  models=$(as_exo /opt/exo/bin/exo config roles)
  if ! printf '%s\n' "$models" | "$here/.venv/bin/python" -c '
import sys
rows = [line.split() for line in sys.stdin if line.strip()]
expected = {"main", "aux", "decision", "eval", "fast", "standard", "deep"}
sys.exit(0 if len(rows) == 7 and {r[0] for r in rows} == expected
         and all(len(r) == 2 and r[1] == "fake:scripted" for r in rows) else 1)
'; then
    result offline-chat 0 'Refusing chat: resolved roles are not exclusively fake:scripted.'
    return 1
  fi
  as_exo systemctl --user restart exod
  # TCG can take longer than RestartSec to load the Python core.
  sleep 10
  reply=$(as_exo "sh -c 'printf \"Say hello in five words.\\n\" | timeout 120 /opt/exo/bin/exo chat --title offline-acceptance'" 2>&1)
  if printf '%s\n' "$reply" | grep -Fxq 'Hello from offline Exo OS.'; then chat_ok=1; fi
  result offline-chat "$chat_ok" \
    'Core RuleFake, all seven roles fake:scripted, deterministic response; real-provider criterion pending.'
}

source "$here/tools/accept-common.sh"
accept_start 1 3072

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
accept_offline_chat

# 3. Stop writers and export a coherent store copy plus independently root-pinned public evidence.
# The host has no signer socket or private service keys. Its portable verifier checks the
# exported signed prefix, without claiming a fresh off-machine witness.
as_exo systemctl --user stop exod
vm_verify=$(as_exo /opt/exo/bin/exo log verify 2>&1 | tail -1)
# Obtain the trust pin directly from the protected root manifest, independently of
# the exo-owned export directory. A substituted copy cannot nominate its own key.
ssh_vm 'cat /var/lib/exo-ledger/manifest.json' | "$here/.venv/bin/python" -c '
import json,sys
manifest=json.load(sys.stdin)
entries=[entry for entry in manifest["streams"].values()
         if entry["ledger_path"] == "/var/lib/exo/exo/principals/exo/ledger.sqlite"]
assert len(entries) == 1, "expected exactly one protected projection binding"
json.dump(entries[0]["identity"],sys.stdout)
' > "$out/authority-identity.json"
export_dir=$(ssh_vm 'as-exo /opt/exo/bin/python -' <<'PY_PORTABLE'
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from exo.ledger_authority.factory import open_authority
from exo.ledger_authority.verify import verify_files

source = Path('/var/lib/exo/exo/principals/exo')
authority = open_authority(source / 'ledger.sqlite')
head = authority.head()
destination = Path(tempfile.mkdtemp(prefix='exo-portability-'))
shutil.copytree(source, destination / 'exo', symlinks=True,
                ignore=shutil.ignore_patterns('ledger.sqlite', 'ledger.sqlite-wal', 'ledger.sqlite-shm'))
# SQLite backup takes a coherent read snapshot, even if the source uses WAL.
with sqlite3.connect((source / 'ledger.sqlite').as_uri() + '?mode=ro', uri=True) as original:
    with sqlite3.connect(destination / 'exo/ledger.sqlite') as copy:
        original.backup(copy)
(destination / 'identity.json').write_text(json.dumps(authority.identity))
with (destination / 'checkpoints.jsonl').open('w') as output:
    checkpoint = authority.head(0)
    output.write(json.dumps(checkpoint) + '\n')
    while checkpoint['seq'] < head['seq']:
        records = authority.read_since(checkpoint['seq'], min(64, head['seq'] - checkpoint['seq']))
        assert records, 'authority checkpoint export stopped before captured head'
        for record in records:
            checkpoint = record['checkpoint']
            output.write(json.dumps(checkpoint) + '\n')
    assert checkpoint == head and authority.head() == head, 'writer advanced during portable export'
assert verify_files(destination / 'exo/ledger.sqlite', destination / 'identity.json',
                    destination / 'checkpoints.jsonl')['ok'], 'exported snapshot does not match signed history'
print(destination)
PY_PORTABLE
)
[[ $export_dir =~ ^/tmp/exo-portability-[A-Za-z0-9_]+$ ]]
rm -rf "$out/store-copy" && mkdir -p "$out/store-copy"
ssh_vm "tar -C $export_dir -cf - ." | "$here/.venv/bin/python" -c '
import sys,tarfile
with tarfile.open(fileobj=sys.stdin.buffer,mode="r|") as archive:
    archive.extractall(sys.argv[1],filter="data")
' "$out/store-copy"
host_verify=$("$here/.venv/bin/python" -m exo.ledger_authority.verify \
  --ledger "$out/store-copy/exo/ledger.sqlite" --identity "$out/authority-identity.json" \
  --checkpoints "$out/store-copy/checkpoints.jsonl" 2>&1 | tail -1)
rows=$("$here/.venv/bin/python" -c 'import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db: print(db.execute("select count(*) from events").fetchone()[0])' \
  "$out/store-copy/exo/ledger.sqlite")
vm_ok=0; host_ok=0
if echo "$vm_verify" | accept_verify; then vm_ok=1; fi
if echo "$host_verify" | accept_verify; then host_ok=1; fi
result "store-portable" "$([[ $vm_ok == 1 && $host_ok == 1 ]] && echo 1 || echo 0)" \
  "vm: $vm_verify | host signed prefix: $host_verify | ledger rows: $rows; public identity copied separately over authenticated VM connection"
as_exo systemctl --user start exod

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

accept_finish
