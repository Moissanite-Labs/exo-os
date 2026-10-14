"""Execute the sourced return proof with controlled guest replies, never a VM."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'tools/recovery-return-proof.sh'

# The guest stand-in uses real owned files for content hashes and permission modes.
# Its named owners and mount layout are fixtures, not claims about Linux isolation.
REMOTE = r'''
import hashlib,json,os,pathlib,stat,sys
root=pathlib.Path(sys.argv[1]); command=sys.argv[2]
config=json.loads((root/'config.json').read_text())
with (root/'calls.jsonl').open('a') as log: log.write(json.dumps(command)+'\n')
def read(name): return (root/name).read_text().strip()
def digest(name): return hashlib.sha256((root/name).read_bytes()).hexdigest()
def mode(name,owner): return f'{stat.S_IMODE((root/name).stat().st_mode):o} {owner} {owner}'
if command=='bootctl list --json=short': print((root/'menu.json').read_text())
elif command.startswith('bootctl set-oneshot '): (root/'oneshot').write_text(command)
elif command=='systemctl reboot':
    (root/'boot').write_text(config['next_boot'])
    if config.get('damage_audit_on_reboot'): (root/'helper-audit').write_text('changed protected state')
elif command=='cat /proc/sys/kernel/random/boot_id': print(read('boot'))
elif command=='wait':
    assert os.environ['BOOT_TIMEOUT']=='900' and os.environ['READY_TIMEOUT']=='900'
    print(read('boot'))
elif command=='ready':
    assert os.environ['READY_TIMEOUT']=='900'
elif command=='cat /sys/class/dmi/id/product_uuid': print(config['uuid'])
elif command.startswith('findmnt '): print(config['mounts'][command.split()[-1]])
elif command=='cat /etc/exo-recovery-proof': print(read('marker'))
elif command=='cat /home/exo/exo-recovery-proof; findmnt -n -o FSROOT /home': print(read('home')+config['mounts']['/home'])
elif command=='cat /var/lib/exo/exo-recovery-proof; findmnt -n -o FSROOT /var/lib/exo': print(read('store-marker')+config['mounts']['/var/lib/exo'])
elif command.startswith('find /var/lib/exo/exo '): print(digest('store'))
elif command.startswith('sha256sum /var/lib/exo-ledger/manifest.json'): print(digest('signer-identity'))
elif command.startswith('find /var/lib/exo-ledger/private '): print(digest('signer-keys'))
elif command.startswith('stat -c ') and '/var/lib/exo-ledger' in command:
    print(mode('signer','root')); print(mode('signer/private','exo-ledger'))
elif command.startswith('stat -c ') and '/var/lib/exo-sysd' in command: print(mode('helper','root'))
elif command.startswith('stat -c ') and '/var/lib/exo-egress' in command: print(mode('egress','exo-egress'))
elif command=='exo-model-spend-admin status': print(read('spend-status'))
elif command=='cat /var/lib/exo/model-spend/policy.json': print(read('spend-policy'))
elif command.startswith('sha256sum /var/lib/exo/model-spend/'): print(digest('spend-policy')+digest('spend-journal'))
elif command.startswith('sha256sum /var/lib/exo-sysd/sysd.key'): print(digest('helper-key'))
elif command.startswith('sha256sum /var/lib/exo-sysd/audit.log'): print(digest('helper-audit'))
elif 'PUBLIC_KEY.read_bytes()' in command: print(digest('helper-public'))
elif command.startswith('as-exo /opt/exo/bin/exo-os system status'):
    with (root/'helper-audit').open('a') as log: log.write('\nnormal-root status receipt')
elif command.startswith('systemctl start '): pass
elif command=='authority check': assert read('authority')=='committed operation'
elif command.startswith('spend check '): assert read('spend-journal')=='stopped journal'
elif command=='egress check': assert read('egress-journal')=='reserved operation'
elif command=='as-exo /opt/exo/bin/exo log verify': print('verified')
else: raise AssertionError('unexpected controlled guest command: '+command)
'''


@pytest.fixture
def controlled_return(tmp_path):
    guest = tmp_path / 'guest'
    guest.mkdir()
    values = {'boot': 'recovery-boot', 'marker': 'broken-system', 'home': 'newer-home',
              'store-marker': 'newer-store', 'store': 'protected store', 'signer-identity': 'public identity',
              'signer-keys': 'synthetic private-key fixture', 'authority': 'committed operation',
              'spend-status': 'stopped status', 'spend-policy': 'zero paid policy', 'spend-journal': 'stopped journal',
              'helper-key': 'synthetic helper-key fixture', 'helper-audit': 'committed root audit',
              'helper-public': 'public helper identity', 'egress-journal': 'reserved operation'}
    for name, value in values.items():
        (guest / name).write_text(value)
    for name, permissions in [('signer', 0o755), ('signer/private', 0o700), ('helper', 0o700), ('egress', 0o700)]:
        (guest / name).mkdir()
        (guest / name).chmod(permissions)
    config = {'next_boot': 'normal-boot', 'uuid': 'fixture-uuid',
              'mounts': {'/': '/@', '/home': '/@home', '/var/lib/exo': '/@store',
                         '/var/lib/exo-ledger': '/@signer', '/var/lib/exo-sysd': '/@sysd', '/var/lib/exo-egress': '/@egress'}}
    (guest / 'config.json').write_text(json.dumps(config))
    (guest / 'menu.json').write_text(json.dumps([{'id': 'exo-recovery-checkpoint.conf'}, {'id': 'exo-normal.efi'}]))
    remote = tmp_path / 'remote.py'
    remote.write_text(REMOTE)
    here = tmp_path / 'project'
    (here / '.venv/bin').mkdir(parents=True)
    (here / '.venv/bin/python').symlink_to(sys.executable)
    out = tmp_path / 'out'
    out.mkdir()
    def sha(name): return hashlib.sha256((guest / name).read_bytes()).hexdigest()
    baseline = {'store_before': sha('store'), 'signer_identity_before': sha('signer-identity'),
                'signer_keys_before': sha('signer-keys'), 'spend_before': values['spend-status'],
                'spend_policy_before': values['spend-policy'], 'spend_hash_before': sha('spend-policy') + sha('spend-journal'),
                'helper_key_before': sha('helper-key'), 'helper_audit_before': sha('helper-audit'),
                'helper_pub_before': sha('helper-public')}
    # Original10 hashes the preserved audit, then status appends a root receipt.
    # Model that service effect before entering the separately counted return.
    with (guest / 'helper-audit').open('a') as log:
        log.write('\nrecovery-root status receipt')
    assert sha('helper-audit') != baseline['helper_audit_before']
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    def run(*, counts=(10, 0, 10)):
        assignments = {'here': str(here), 'out': str(out), 'qemu': str(process.pid), 'id': 'checkpoint',
                       'EXO_OS_VM_UUID': 'fixture-uuid', **baseline}
        script = 'set -Eeuo pipefail\n' + '\n'.join(f'{k}={shlex.quote(v)}' for k, v in assignments.items())
        script += f'\npass={counts[0]}; fail={counts[1]}; required_checks={counts[2]}\n'
        script += f'\nssh_vm() {{ {shlex.quote(sys.executable)} {shlex.quote(str(remote))} {shlex.quote(str(guest))} "$1"; }}\n'
        script += '''boot_timeout=900; ready_timeout=900
export BOOT_TIMEOUT=900 READY_TIMEOUT=900
accept_wait_ssh() { [[ $1 == 900 && $2 == recovery-boot && $3 == 'cat /proc/sys/kernel/random/boot_id' ]] || return 1; ssh_vm wait; }
accept_system_ready() { ssh_vm ready; }
authority_probe() { ssh_vm "authority $1"; }
spend_journal_probe() { ssh_vm "spend $1 $2"; }
egress_journal_probe() { ssh_vm "egress $1"; }
accept_verify() { [[ $(cat) == verified ]]; }
result() { [[ $1 == return-to-normal && $2 == 1 ]] || return 1; pass=$((pass+1)); }
'''
        script += f'source {shlex.quote(str(HELPER))}\nprintf "COUNTERS=%s,%s,%s\\n" "$pass" "$fail" "$required_checks"\n'
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=10)
    yield guest, out, config, run
    process.terminate()
    process.wait(timeout=5)


def test_actual_return_helper_records_separate_return_and_eleven_counts(controlled_return):
    guest, out, _, run = controlled_return
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    (out / 'shell-output.txt').write_text(result.stdout)
    assert 'COUNTERS=11,0,11' in result.stdout
    proof = json.loads((out / 'recovery-return-proof/result.json').read_text())
    assert proof['original_checks'] == 10 and proof['return_checks_separate'] is True
    assert proof['return_checks'] == 1 and proof['required_total_checks'] == 11
    assert proof['recovery_boot_id'] == 'recovery-boot' and proof['normal_boot_id'] == 'normal-boot'
    assert proof['root'] == '/@' and proof['vm_uuid'] == 'fixture-uuid'
    assert (out / 'recovery-return-proof/original-check-count.txt').read_text() == '10\n'
    calls = [json.loads(line) for line in (guest / 'calls.jsonl').read_text().splitlines()]
    assert any(c.startswith('bootctl set-oneshot ') for c in calls)
    assert 'systemctl reboot' in calls and 'wait' in calls and 'ready' in calls
    assert 'authority check' in calls and 'spend check checkpoint' in calls and 'egress check' in calls


@pytest.mark.parametrize('damage', ['unchanged_boot', 'wrong_uuid', 'wrong_root', 'marker', 'store',
                                    'signer-identity', 'signer-keys', 'authority', 'helper-key',
                                    'helper-audit', 'helper-public', 'spend-status', 'spend-policy',
                                    'spend-journal', 'egress-journal', 'signer_mode', 'helper_mode', 'egress_mode'])
def test_actual_return_helper_refuses_changed_persistent_or_boot_state(controlled_return, damage):
    guest, out, config, run = controlled_return
    if damage == 'unchanged_boot':
        config['next_boot'] = 'recovery-boot'
    elif damage == 'wrong_uuid':
        config['uuid'] = 'unrelated-vm'
    elif damage == 'wrong_root':
        config['mounts']['/'] = '/@snapshots/recovery'
    elif damage == 'helper-audit':
        config['damage_audit_on_reboot'] = True
    elif damage.endswith('_mode'):
        (guest / {'signer_mode': 'signer/private', 'helper_mode': 'helper', 'egress_mode': 'egress'}[damage]).chmod(0o755)
    else:
        (guest / damage).write_text('changed protected state')
    (guest / 'config.json').write_text(json.dumps(config))
    result = run()
    assert result.returncode != 0
    assert 'COUNTERS=11,0,11' not in result.stdout
    assert not (out / 'recovery-return-proof/result.json').exists()
    assert not (out / 'recovery-return-proof/RESULT').exists()


@pytest.mark.parametrize('menu', ['not JSON', '[{"id":"one.efi"},{"id":"two.efi"}]', '[]'])
def test_invalid_or_ambiguous_menu_refuses_before_oneshot(controlled_return, menu):
    guest, out, _, run = controlled_return
    (guest / 'menu.json').write_text(menu)
    assert run().returncode != 0
    assert not (guest / 'oneshot').exists()
    assert not (out / 'recovery-return-proof/result.json').exists()


@pytest.mark.parametrize('counts', [(9, 0, 10), (10, 1, 10), (10, 0, 11)])
def test_incomplete_original_checks_refuse_before_oneshot(controlled_return, counts):
    guest, out, _, run = controlled_return
    assert run(counts=counts).returncode != 0
    assert not (guest / 'oneshot').exists()
    assert not (guest / 'calls.jsonl').exists()
    assert not (out / 'recovery-return-proof').exists()
