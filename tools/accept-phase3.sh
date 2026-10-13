#!/usr/bin/env bash
# Phase 3 acceptance (ROADMAP.md): the one surface as the desktop. Boots the vmtest image, scripts Exo's
# offline fake model (no API key needed), types into the real prompt bar with wtype, and checks:
#   1. an app is reached from the prompt bar by typing a few letters
#   2. a question reaches Exo and the answer shows in the bar
#   3. an approval card appears for a risky act, Y answers it, the answer reaches the gate, the act runs
#   4. a panel the agent opens shows in the drawer
#   5. with exod stopped, app launch still works; the disconnected-status screenshot is saved for review
#   6. the surface's memory (PSS), idle
# Screenshots: image/out/p3-*.png. Results: image/out/accept-phase3.txt
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-phase3.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { ssh_vm "cd /tmp && as-exo $*"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
shot() { ssh_vm "cd /tmp && as-exo grim /tmp/$1.png && cat /tmp/$1.png" > "$out/$1.png" 2>/dev/null; }
type_in() { as_exo "wtype '$1'"; }
press() { as_exo "wtype -k $1"; }
layers() { as_exo "hyprctl layers" | { grep -o 'namespace: exo-surface-[a-z]*' || true; } | sort -u | tr '\n' ' '; }
bar() { as_exo "/opt/exo/bin/exo-surface $1"; sleep 1; }
windows() { as_exo "hyprctl clients" | { grep -c "class: $1" || true; }; }

source "$here/tools/accept-common.sh"
accept_start 3 1536
accept_keyring
as_exo systemctl --user is-active --quiet exo-surface

# A scripted model: what a real model would do for these three requests.
as_exo "/opt/exo/bin/python - <<'PY'
import json, pathlib
p = pathlib.Path('/var/lib/exo/exo/config.json'); c = json.loads(p.read_text())
c['models'] = {'fake': {'reply': 'I can help with that.', 'rules': [
  {'match': 'install htop', 'steps': [{'tool': 'system_package_install', 'args': {'names': ['htop'], 'reason': 'so you can see memory use'}}, 'Done. htop is installed.']},
  {'match': 'calls today', 'steps': [{'tool': 'ui', 'args': {'action': 'open', 'kind': 'table', 'title': 'Calls today', 'text': 'Three calls: Lead A, Lead B, Lead C.', 'data': {'columns': ['Who', 'Why'], 'rows': [['Lead A', 'bid due Thursday'], ['Lead B', 'asked for a quote'], ['Lead C', 'follow up']]}}}, 'Three calls today. Lead A first.']},
  {'match': 'hello', 'steps': ['Hello. How can I help?']}]}}
c.setdefault('plugin_config', {}).setdefault('exo-gate', {})['decision_model'] = False
p.write_text(json.dumps(c, indent=2))
PY"
as_exo "systemctl --user restart exod" && sleep 4 && as_exo "systemctl --user restart exo-surface" && sleep 4

# 1. app by a few letters
before=$(windows foot)
bar show; type_in "Termi"; sleep 1; shot p3-palette; press Return; sleep 3
after=$(windows foot)
result "palette-opens-app" "$([[ $after -gt $before ]] && echo 1 || echo 0)" "foot windows $before -> $after (typed 'Termi', Enter)"

# 2. a question
bar show; type_in "hello"; press Return; sleep 6; shot p3-answer
answered=$(as_exo "/opt/exo/bin/python -c \"
import json, sqlite3
db = sqlite3.connect('/var/lib/exo/exo/sessions.sqlite')
rows = [r[0] for r in db.execute('select body from events order by rowid desc limit 200')]
print(sum('Hello. How can I help?' in r for r in rows))\"" 2>/dev/null)
result "question" "$([[ ${answered:-0} -ge 1 ]] && echo 1 || echo 0)" "answer events seen by exod: ${answered:-?}; screenshot p3-answer.png"

# 3. approval card
bar show; type_in "install htop"; press Return
for _ in $(seq 1 20); do [[ $(layers) == *card* ]] && break; sleep 1; done
card=$(layers)
shot p3-card
press y
for _ in $(seq 1 60); do ssh_vm 'test -e /usr/bin/htop' && break; sleep 2; done
htop=$(ssh_vm 'test -e /usr/bin/htop && echo installed || echo missing')
gate=$(as_exo "/opt/exo/bin/exo log tail -n 12" | grep -c 'exo-gate')
result "approval-card" "$([[ $card == *card* && $htop == installed && $gate -ge 1 ]] && echo 1 || echo 0)" \
  "layers while asking: $card; after Y: htop $htop; gate ledger rows in last 12: $gate"

# 4. a panel in the drawer
bar show; type_in "who are my calls today"; press Return
for _ in $(seq 1 20); do [[ $(layers) == *drawer* ]] && break; sleep 1; done
drawer=$(layers)
shot p3-drawer
result "panel-drawer" "$([[ $drawer == *drawer* ]] && echo 1 || echo 0)" "layers: $drawer; screenshot p3-drawer.png"
press Escape

# 5. exod stopped
as_exo "systemctl --user stop exod"; as_exo "pkill -x foot || test \$? = 1"; sleep 3
before=$(windows foot)
bar show; sleep 2; shot p3-no-exod
type_in "Termi"; press Return; sleep 3
after=$(windows foot)
as_exo "/opt/exo/bin/exo-os app open Terminal" >/dev/null
result "works-without-exod" "$([[ $after -gt $before ]] && echo 1 || echo 0)" "foot windows $before -> $after with exod stopped; screenshot p3-no-exod.png"
as_exo "systemctl --user start exod"

# 6. memory
pss=$(ssh_vm "pid=\$(pgrep -u exo -f exo_os.surface | head -1); grep '^Pss:' /proc/\$pid/smaps_rollup | awk '{print int(\$2/1024)}'")
echo "      surface PSS: ${pss:-?} MB; background: $(as_exo hyprctl getoption misc:background_color | head -1)" | tee -a "$report"
accept_finish
