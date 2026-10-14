#!/usr/bin/env bash
# Phase 2 acceptance (ROADMAP.md): every system change gated, logged, reversible. Boots the vmtest image
# and checks:
#   1. 20+ scripted changes each leave a record: a machine-repo commit with a diff (profile changes) or a
#      pre and post snapshot (package install), and signed ledger rows
#   2. a broken change (an unknown keyboard layout) is put back by itself, with one plain line
#   3. each change is undone with one command, and the undo is itself logged
#   4. the person's account cannot run root commands, and exo-sysd refuses anything off its list
#   5. the OS plugin exports no bootloader or reboot tools (not a whole-core sandbox test)
# Results: image/out/accept-phase2.txt
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-phase2.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { accept_as_exo "$@"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
ledger_rows() { as_exo exo-os-ledger-count; }

source "$here/tools/accept-common.sh"
accept_start 2 1536
accept_keyring
as_exo systemctl --user is-active --quiet exod

# 1. twenty changes
changes=(
  "set display.scale 1.25" "set display.text_scale 1.2" "set keyboard.layouts us,ara" "set keyboard.layouts us,ara,pk"
  "set keyboard.repeat_rate 30" "set keyboard.repeat_delay 400" "set pointer.natural_scroll on"
  "set pointer.sensitivity 0.2" "set idle.dim_seconds 900" "set idle.lock_seconds 3600"
  "set idle.screen_off_seconds 1800" "set look.gaps 4" "set look.border 2" "set surface.language ur"
  "app add WhatsApp --url https://web.whatsapp.com/ --key SUPER+1 --workspace 2"
  "app add Gmail --url https://mail.google.com/ --key SUPER+2 --workspace 3"
  "app add Drive --url https://drive.google.com/ --key SUPER+3 --workspace 4"
  "app remove Drive" "set look.theme dark"
)
before_rows=$(ledger_rows)
before_commits=$(as_exo "git -C /var/lib/exo/exo/principals/exo/machine rev-list --count HEAD")
ok=0
for c in "${changes[@]}"; do
  res=$(as_exo /opt/exo/bin/exo-os --json $c 2>&1)
  if echo "$res" | grep -q '"ok": true'; then ok=$((ok + 1)); else
    echo "      change failed: $c: $(echo "$res" | tr '\n' ' ' | cut -c1-900)" | tee -a "$report"; fi
done
install=$(as_exo /opt/exo/bin/exo-os system install htop --reason acceptance 2>&1)
[[ $install == Installed* ]] && ok=$((ok + 1))
after_rows=$(ledger_rows)
after_commits=$(as_exo "git -C /var/lib/exo/exo/principals/exo/machine rev-list --count HEAD")
diffs=$(as_exo "git -C /var/lib/exo/exo/principals/exo/machine log -${#changes[@]} --format=%h --shortstat" | grep -c 'changed')
pre=$(echo "$install" | sed -n 's/.*before and after: \([0-9]*\), \([0-9]*\)\..*/\1/p')
post=$(echo "$install" | sed -n 's/.*before and after: \([0-9]*\), \([0-9]*\)\..*/\2/p')
n=$(( ${#changes[@]} + 1 ))
result "20-changes" "$([[ $ok == "$n" && $((after_commits - before_commits)) == ${#changes[@]} && $diffs == ${#changes[@]} && $((after_rows - before_rows)) -ge $((2 * ${#changes[@]} + 1)) && $pre =~ ^[1-9][0-9]*$ && $post =~ ^[1-9][0-9]*$ && $post -gt $pre ]] && echo 1 || echo 0)" \
  "$ok/$n applied; commits +$((after_commits - before_commits)), each with a diff: $diffs; ledger rows +$((after_rows - before_rows)); install snapshots $pre, $post"

# 2. a broken change puts itself back
broken_rc=0
broken=$(as_exo /opt/exo/bin/exo-os set keyboard.layouts us,zz 2>/dev/null) || broken_rc=$?
kb=$(as_exo /opt/exo/bin/exo-os show keyboard.layouts)
live=$(as_exo hyprctl getoption input:kb_layout | head -1)
reverted=$(as_exo "/opt/exo/bin/exo log tail -n 3" | grep -c 'change.reverted')
result "broken-reverts" "$([[ $broken_rc == 1 && $broken == 'That did not work, so I put it back.' && $kb == *pk* && $live == *pk* && $reverted -ge 1 ]] && echo 1 || echo 0)" \
  "said: \"$broken\"; profile $kb; live $live; ledger change.reverted rows in last 3: $reverted"

# 3. undo with one command, logged; then undo the install
u1=$(as_exo /opt/exo/bin/exo-os undo 2>&1)
theme=$(as_exo /opt/exo/bin/exo-os show look.theme)
u2=$(as_exo /opt/exo/bin/exo-os undo 2>&1)
theme2=$(as_exo /opt/exo/bin/exo-os show look.theme)
logged=$(as_exo "/opt/exo/bin/exo-os history -n 2")
installed_before_undo=$(ssh_vm 'pacman -Q htop')
sysundo=$(as_exo /opt/exo/bin/exo-os system undo "$pre" "$post" 2>&1)
htop=$(ssh_vm 'test -e /usr/bin/htop && echo present || echo gone')
pkg=$(ssh_vm 'pacman -Q htop 2>&1 | head -1')
result "undo" "$([[ $u1 == Undo:* && $theme == light && $u2 == Undo:* && $theme2 == dark && $logged == *Undo:* && $installed_before_undo == htop\ * && $sysundo == "Put the system back as it was at snapshot $pre." && $htop == gone ]] && echo 1 || echo 0)" \
  "undo: $theme, undo of the undo: $theme2; system undo: \"$sysundo\"; htop $htop ($pkg)"

# 4. no root outside the named list
as_exo /opt/exo/bin/exo-os system status >/dev/null
sudo_rc=$(as_exo "sudo -n true >/dev/null 2>&1; echo \$?")
refused=$(as_exo "/opt/exo/bin/python -c \"
import secrets, socket
from exo_os.sysd.protocol import VERSION, MAX_REPLY, encode, decode, read_line
for a, args in (('shell', {'cmd': 'id'}), ('package.install', {'names': ['--overwrite=*']}), ('package.remove', {'names': ['exo-os']})):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(10)
        sock.connect('/run/exo-sysd.sock')
        sock.sendall(encode({'version': VERSION, 'request_id': secrets.token_hex(32), 'action': a, 'args': args}))
        reply = decode(read_line(sock, MAX_REPLY, 10))
    expected = 'not a package name' if a == 'package.install' else 'does not do'
    assert reply.get('ok') is False and expected in reply.get('error', ''), reply
    print('refused', a)
\"" | grep -c refused)
sock=$(ssh_vm 'stat -c "%a %U %G" /run/exo-sysd.sock')
other_rc=0
other=$(ssh_vm "runuser -u greeter -- python3 -c \"import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/exo-sysd.sock')\"" 2>&1) || other_rc=$?
result "root-door" "$([[ $sudo_rc =~ ^[1-9][0-9]*$ && $refused == 3 && $sock == '660 root exo-sys' && $other_rc != 0 && $other == *Permission* ]] && echo 1 || echo 0)" \
  "sudo -n exit $sudo_rc; off-list requests refused: $refused/3; socket $sock; another account: ${other:0:60}"

# 5. the OS plugin exports no boot or reboot tools; core shell isolation is separate work
tools=$(as_exo "/opt/exo/bin/python -c \"import exo_os.plugin as p; print(' '.join(t[0] for t in p.TOOLS))\"")
result "no-boot-tools" "$([[ -n $tools && $tools == *machine_set* && $tools != *boot* && $tools != *reboot* && $tools != *bootloader* ]] && echo 1 || echo 0)" "tools: $tools"

verify=$(as_exo /opt/exo/bin/exo log verify 2>&1 | tail -1)
ledger_ok=0
if echo "$verify" | accept_verify; then ledger_ok=1; fi
result ledger-verifies "$ledger_ok" "$verify"
accept_finish
