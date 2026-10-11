#!/usr/bin/env bash
# Run from chaos-ai with the target already up and seeded. No build/down/reset commands.
# Every measured inject starts its own steady load and writes its result/sample evidence.
# Prepare chaosai sandbox first for sustained demo traffic; original source stays unchanged.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi
export LOGIN_RATE_CAP="${LOGIN_RATE_CAP:-0.5}"

if [[ -f .chaosai/KILL ]]; then
  echo 'KILL is already set. Inspect recovery, then explicitly run:'
  echo '  chaosai rollback --clear-kill'
  exit 1
fi

echo '1. Check scoped target readiness: chaosai doctor'
chaosai doctor
if [[ -f .chaosai/baseline.json ]]; then
  echo '2. Reusing .chaosai/baseline.json; S9 will verify current readiness.'
  echo '   To refresh it yourself: chaosai baseline'
else
  echo '2. Create a measured baseline: chaosai baseline'
  chaosai baseline
fi

echo '3. Measure Redis stop (approve with y):'
echo '   chaosai inject stop redis --duration 30'
chaosai inject stop redis --duration 30

echo '4. Strict watchdog limit (approve with y):'
echo '   chaosai inject stop redis --duration 30 --abort-error-rate 0.05'
chaosai inject stop redis --duration 30 --abort-error-rate 0.05

echo '5. Start a separate fault and invoke kill only while its undo record is pending.'
echo '   Background: chaosai inject stop redis --duration 120 --yes'
# Snapshot run names first; poll durable records rather than sleeping and guessing.
marker="$(python - <<'PY'
from pathlib import Path
print(','.join(p.name for p in Path('runs').glob('*') if p.is_dir()))
PY
)"
chaosai inject stop redis --duration 120 --yes &
inject_pid=$!
# On interruption, persist KILL and roll back before waiting for the injector.
trap 'echo "Demo interrupted; invoking chaosai kill"; chaosai kill; wait "$inject_pid" || true; exit 130' INT TERM
if python - "$marker" "$inject_pid" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

previous = set(sys.argv[1].split(','))
pid = int(sys.argv[2])
deadline = time.monotonic() + 90  # Includes bounded preparation and 60s S9.
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit('Injector exited before active fault; kill demo not established.')
    for run in Path('runs').glob('*'):
        if run.name in previous:
            continue
        journal = run / 'journal.json'
        events = run / 'experiments' / 'redis-stop' / 'events.jsonl'
        try:
            data = json.loads(journal.read_text())
            observed = any(json.loads(line)['phase'] == 'OBSERVE'
                           for line in events.read_text().splitlines())
            active = any(not r['done'] for r in data['records'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if active and observed:
            print(f'Active measured fault confirmed: {journal}', flush=True)
            sys.exit(0)
    time.sleep(0.5)
sys.exit('No active measured fault observed; kill demo not established.')
PY
then
  echo '   Fault is active: chaosai kill'
  chaosai kill
  wait "$inject_pid"
else
  echo 'No active fault established. Invoking kill for cleanup; this is NOT a successful demo.'
  chaosai kill
  wait "$inject_pid" || true
  exit 1
fi
trap - INT TERM

echo 'Inspect each result.json for actual verdict, impact, abort reason and rollback_ok.'
echo 'A strict threshold does not guarantee a breach; empty/sparse recovery evidence can time out.'
echo 'KILL remains set. After reviewing recovery, reset explicitly with:'
echo '  chaosai rollback --clear-kill'
