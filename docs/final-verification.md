# Final human verification — pending after Phase 10

Run live commands yourself from chaos-ai with .venv active. The coding agent ran mock/offline
checks only. Keep terminal output, UTC run IDs, results, samples and journals locally; share
sanitized diagnostics without credentials. Do not interpret waiting, a selected spec, S9 skip,
container rollback or script exit alone as measured impact/recovery.

## Preserve existing evidence

- Phase 8 dry-run 20261011-014504 passed, without faults; baseline stable/error-free.
- Run 20261011-014810 injected zero faults, with checkout S9 0/10 and failed cooldown.
  Baseline login errors 21/47, precheck 26/26 and cooldown 25/25 auth HTTP 429s. Its
  stable=true is drift, not an uncontaminated baseline. Empty journal and rollback_ok do
  not establish recovery; recovered=false/recovery_time_s=null remains authoritative.
- Smoke failed at admin login after a matching-credential recheck. Rate limiting is likely,
  but its historical response status was not recorded. The Phase 10 fix retains the status
  for future unexpected-HTTP admin failures; it cannot recover the old missing evidence.
- Baseline 20261011-011425 stays stable=false (32.28% checkout drift vs unchanged 30%).
- RabbitMQ observations remain intermittent. CPU quota 0/NanoCpus originals are refused;
  refusal is not completion. Fixture scores 53/17/95, overall 55, are synthetic.

## Setup and serial traffic schedule

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
export COMPOSE_PROJECT=ecommerce-microservices
chaosai status
chaosai doctor
```

Use the actual configured project if different. Keep existing admin/load passwords correct
privately. For an owned clone, export TARGET_DIR to that checkout; the default sibling stays
read-only. All commands use the same configured project and workspace; no parallel target
copies/load generators. Doctor's key/dotenv checks require private local configuration even
when run itself could use heuristic planning.

Before bring-up, inspect `.chaosai/KILL` existence and retained journals without deleting them.
If pending, use `chaosai rollback`; investigate any retained failures before continuing.
After an injector exits and successful undo is reviewed, explicitly use
`chaosai rollback --clear-kill` only when resumption is intended. Never recreate a container
with pending undo, normalize CPU settings, bypass the limiter or repair unjournaled state
through chaos scripts.

Auth has a shared 100-request/15-minute IP budget, including seed/smoke. Run **one** live
traffic command at a time. Allow a fresh limiter window between independent baseline,
impact, abort, KILL, Ctrl-C, Day-1, full-run and integration checks when budget has been used;
do not issue consecutive fresh-baseline dry-run/run commands. Retain error counts/statuses
and inspect readiness afterward. A full 30+120s baseline at 0.5/s can use about 75 logins;
preceding traffic and a later sequence can exhaust the budget again. Lower rates make recovery
sampling sparse. At 0.5/s, regularly admitted logins cannot populate every consecutive 1s
window; neither waiting nor raising the rate guarantees success within the target budget.
Keep all current stability/S9/recovery gates. Supply actual evidence for diagnosis if they
remain blocked; do not label missing recovery a healthy pass or persistent failure by inference.

## 1. Phase 10 from-scratch reproduction

**You verify (Docker), copied from BUILD_PLAN.md:** run `scripts/clean_run.sh` from scratch
on your machine, start to finish.

- [ ] Reuse the configured target or clone inside chaos-ai (choose one):

```sh
bash scripts/clean_run.sh
# Alternative when no checkout exists at the owned clone destination:
bash scripts/clean_run.sh --clone
```

The script bootstraps and performs scoped build/start, doctor/seed/smoke, a fresh-baseline
`run --yes --max-experiments 4`, validates real evidence, prints report path and performs final
smoke. It neither erases volumes nor clears KILL. Record exit code and printed report path.
If doctor fails during startup, inspect scoped startup and rerun once ready; no silent retry
loop is supplied. If seed/smoke consumes auth budget or the run skips/aborts/stops, retain its
artifacts and diagnose; reproduction remains incomplete. This test may require more than a
fresh window because the complete sequence itself consumes budget.

## 2. Fresh uncontaminated baseline

**Phase 4 You verify, copied:** `chaosai baseline --quick`, then a full `chaosai baseline`;
`baseline.json` shows believable numbers and `stable: true`; run it twice and compare.
Run these as independent checks in fresh windows, not back-to-back:

```sh
LOGIN_RATE_CAP=1 LOAD_USERS=10 chaosai baseline --quick
LOGIN_RATE_CAP=0.5 LOAD_USERS=10 chaosai baseline
LOGIN_RATE_CAP=0.5 LOAD_USERS=10 chaosai baseline
```

- [ ] Inspect each printed baseline: stable=true, nonempty journey evidence, zero auth 429s
  and request errors, believable p95/RPS, low_sample flags and unchanged thresholds.
- [ ] Compare full baselines' per-journey stats and half-window drift. Quick/sparse evidence
  cannot substitute for adequate full windows. Queue unavailability must remain explicit.
- [ ] Retain historical baselines unchanged. Refresh `.chaosai/baseline.json` by successful
  new measurement before inject; do not reuse the contaminated historical one.

## 3. Phase 6 measured impact, safety and recovery

**You verify (Docker), copied:**

- `chaosai inject stop redis --duration 30` → result shows impact and recovery.
- `chaosai inject stop redis --duration 30 --abort-error-rate 0.05` → aborted by the watchdog
  and rolled back.
- Run the Day-1 demo script once: `bash scripts/day1_demo.sh`.

Each item uses its own serial readiness/budget check. No external load process is needed.

```sh
chaosai inject stop redis --duration 30
chaosai inject stop redis --duration 30 --abort-error-rate 0.05
```

- [ ] Normal: OBSERVE plus real during samples/impacted journeys, adequate verdict evidence,
  journal done, rollback_ok/cleanup_ok true, measured recovered=true and recovery_time_s.
- [ ] Strict: real metric/blast-radius breach (watchdog_aborted=true), actual abort reason,
  early undo, retained evidence and successful cleanup. Requested 0.05 alone is not an abort.
- [ ] Verify scoped container restoration and HTTP recovery separately. An S9 skip or sparse
  recovered=false is pending evidence, regardless of container health.

Active-fault KILL in two terminals (same directory/configuration):

```sh
# A, approve or use explicit --yes:
chaosai inject stop redis --duration 120 --yes
# B, only while A has OBSERVE and its journal record is still pending:
chaosai kill
test -f .chaosai/KILL
chaosai status
# After A exits, inspect result/journal and explicitly resume if safe:
chaosai rollback --clear-kill
```

- [ ] Preserve pending-before/done-after evidence and A's early abort/rollback result.
  A zero-record kill after undo is not a demonstration. KILL remains set until explicit reset.
  KILL ending sampling does not necessarily establish measured application recovery.

Ctrl-C on a **separate** active experiment:

```sh
# A; wait for OBSERVE/pending journal, then press Ctrl-C:
chaosai inject stop redis --duration 120
# B after A's exit (130):
chaosai rollback
chaosai status
```

- [ ] Signal interruption recorded, journal restored, cleanup OK and scoped target running;
  verify HTTP readiness/smoke separately with auth budget. Retain failed undo if any.
- [ ] Execute and inspect the full Day-1 script once:

```sh
bash scripts/day1_demo.sh
```

It leaves KILL set. Review every result, not just exit code; normal, strict and active KILL
must actually occur. It may stop at readiness/auth failures. Retain artifacts and reset only
explicitly afterward. [safety.md](safety.md) retains Phase 5 low-level automatic and
cross-terminal rollback passes and its remaining site/Ctrl-C/KILL evidence list.

## 4. Phase 8 automatic run and app health

**You verify (Docker), copied:** `chaosai run --dry-run`, then `chaosai run` (approve each)
completing at least 4 experiments covering >=3 fault types, and the app healthy afterwards
(`chaosai smoke`). The historical dry-run already passed; no need to repeat it merely to
consume another baseline. If repeated, give it an independent auth window.

```sh
# Optional new preview with an eligible fresh saved baseline, to avoid duplicate measurement:
chaosai run --dry-run --reuse-baseline
# In its own readiness/budget window; approve each:
chaosai run --max-experiments 4 --reuse-baseline
chaosai smoke
```

A reused baseline must be stable and <1h old; otherwise the command measures again. Inspect
errors yourself before reusing; stable alone is not clean. Smoke uses auth budget too and
creates an order. Record real status if it fails, rather than assuming credentials are wrong.

- [ ] >=4 actually applied/completed, adequately sampled measured experiments across >=3
  actual fault types; OBSERVE/during evidence, non-skipped supported verdicts, exact undo,
  cleanup and measured recovery/readiness/cooldown. Four selected entries alone do not pass.
- [ ] CPU refusal is not a completed type. Do not normalize/recreate target CPU state to
  force coverage; diagnose supported plan outcomes from actual evidence.
- [ ] No unresolved undo, KILL, signal or stopped sequence misrepresented as a full run.
- [ ] Smoke passes browse/login/checkout/retrieve afterward; notification evidence is
  best-effort and does not establish actual Resend delivery.

## 5. Phase 9 real report and Wi-Fi-off replay

**You verify, copied:** with a real run directory, `chaosai report <run>` opens a readable
`report.html`; with Wi-Fi off, `chaosai replay <run>` still works.

```sh
chaosai report runs/<verified-real-run-id>
open runs/<verified-real-run-id>/report.html
# Turn Wi-Fi off, then:
chaosai replay runs/<verified-real-run-id>
open runs/<verified-real-run-id>/report.html
```

- [ ] Visual check: readable layout/timestamps, latency-ms/time-s axes, recorded phase markers,
  threshold table, cards in plan order and escaped untrusted text; no external assets.
- [ ] Genuine measured banner, truthful skips/coverage/recovery, evidence-backed findings,
  deterministic score breakdown/severity/ranking and “What held up” split.
- [ ] Replay succeeds without network; cache misses use recorded fallback. Compare original
  baseline/plan/decisions/results/samples unchanged. Missing caches do not imply cached AI.
  Manually open the report; respect the documented Computer Use file:// security restriction.

## 6. Human integration and submission checks

Run after readiness with its own auth budget (these tests create orders):

```sh
pytest -q -m integration
```

- [ ] All three integration checks pass; provide sanitized output for any failure.
- [ ] Copy only the five eligible measured report artifacts into
  [examples/sample-run](../examples/sample-run/README.md), with provenance and hashes, after
  review for secrets. No samples/.env/raw logs/caches. This deliverable remains pending now.
- [ ] Record <=3-minute video using [demo script](demo-script.md); verify audio/playback/duration.
- [ ] README, architecture, measured findings report, demo video and repo/deployment link
  all open; verify the submission form agrees on these five deliverables.
- [ ] Human pushes the reviewed repository, completes team form and submits 30 minutes early.
  The coding agent never pushes. Phase 11 stays unstarted.
