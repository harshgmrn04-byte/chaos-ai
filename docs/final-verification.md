# Verification record — 2026-10-11

The user authorized scoped live verification and runtime/dashboard fixes after repeated
failed waiting attempts. This supersedes the earlier human-only schedule. Historical
failures and synthetic fixtures remain unchanged and explicitly labelled.

## Completed

- Full `bash scripts/clean_run.sh`: exit 0, run `20261011-035048`. Scoped build/start,
  owned auth sandbox, doctor, seed, smoke, fresh stable error-free 120s baseline, four
  actual fault types, exact undo, cleanup, measured recovery, cooldown and final smoke.
  Kill/auth, browse traffic spike, pause/PostgreSQL and stop/PostgreSQL recovered in
  10s, 6s, 10s, 14s. Three watchdog aborts were successful safety responses. Score: 55/100 weak.
- Three integration tests: passed (654 unit tests deselected at that checkpoint).
- Fresh explicitly approved Gemini planner: `20261011-040607`, ranked eight validated specs.
  Default timeout increased to the verified 60s; transport failures still fall back.
- Day-1 script: exit 0. `040603` measured Redis browse impact and 10s recovery; `040831`
  strict watchdog aborted at 0.05, undo/cleanup passed and recovery measured at 8s.
  `041308` detected pending journal plus OBSERVE before KILL; owner recorded Kill switch
  requested, journal done and cleanup/rollback true. KILL stopped sampling, so recovered=false
  remains authoritative; do not claim HTTP recovery from that result alone.
- Ctrl-C during active Redis fault `20261011-041633`: exit 130, interrupted-by-signal
  result, journal done, rollback/cleanup true. Dashboard-approved Redis fault `041508`
  crossed the watchdog limit, restored successfully and measured 6s HTTP recovery.
- Measured report rendered in browser through the loopback dashboard. Saved run history,
  score, metrics, experiment phases, findings, telemetry and explicit approval controls reviewed.
- Real-run cache-only replay succeeded: `chaosai replay runs/20261011-035048`. This path
  constructs no live transport. Physical Wi-Fi disabling was not performed.
- Measured sample exported with provenance, hashes and secret review in examples/sample-run.
  Original baseline/plan/results/samples retained; replay only regenerates analysis/report.
- Ruff and 657 non-integration unit tests passed after the watchdog/approval/timeout fixes.

The short integration baseline was legitimately unstable. For the safety walkthrough,
the still-fresh verified full baseline was selected through the validated cache path;
S9 rechecked current HTTP readiness. Neither measurement was edited.

## Original “You verify” requirements

Copied from BUILD_PLAN.md for operator review (live outcomes above distinguish completed
checks from remaining presentation work):

- Phase 6: `chaosai inject stop redis --duration 30` → result shows impact and recovery;
  `chaosai inject stop redis --duration 30 --abort-error-rate 0.05` → watchdog abort and
  rollback; run `bash scripts/day1_demo.sh` once.
- Phase 8: `chaosai run --dry-run`, then `chaosai run` (approve each), at least four
  experiments covering three fault types, app healthy afterward (`chaosai smoke`).
  Fresh reproduction used explicit `--yes` authorization; an approval prompt was separately
  exercised by the Day-1 walkthrough.
- Phase 9: a real `report.html` opens readably; with Wi-Fi off `chaosai replay <run>` works.
  Cache-only execution passed; the physical Wi-Fi-off presentation remains operator review.
- Phase 10: run `scripts/clean_run.sh` from scratch, start to finish (completed above).

## Remaining human work

1. Rehearse the requested judge flow using [demo-script.md](demo-script.md).
2. Record and check the <=3-minute video (audio/playback/duration).
3. Open README, architecture, measured report, video and public repository/deployment link;
   confirm the submission form's deliverable names, push the reviewed local commit yourself,
   complete the team form and submit early. No push was performed by the agent.

Limitations: intermittent RabbitMQ management telemetry; exact CPU quota-0/NanoCpus
restoration refusal; sparse journey statistics remain descriptive; target dependency
weaknesses are reported rather than hidden. Netem/disk and online AI re-planning are
unimplemented. Rollback restores injected faults; it does not rewrite application code.
