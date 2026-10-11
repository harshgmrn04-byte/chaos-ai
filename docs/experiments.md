# Measured experiments — Phases 6–8

The measured runner is exported by `chaosai/runner.py`; `chaosai/experiment.py` owns the
single-experiment lifecycle and `evaluate.py` contains pure evidence functions. Phase 8 adds
CPU/traffic mechanisms and `orchestrator.py` for sequential grounded plans. Kill, stop, pause,
CPU limitation and traffic spikes are supported subject to mechanism-specific safety checks.
`fault apply` remains a low-level mechanism test without load or a metric watchdog; traffic
spikes need the prepared workers provided by the measured runner. See [llm-planning.md](llm-planning.md).

## Run one experiment

With the already-running target seeded, activate the virtual environment in chaos-ai:

```sh
source .venv/bin/activate
chaosai doctor
chaosai baseline
chaosai inject stop redis --duration 30
chaosai inject stop redis --duration 30 --abort-error-rate 0.05
bash scripts/day1_demo.sh
```

`inject` requires a saved `.chaosai/baseline.json`. It prints the final spec, hypothesis,
abort rules and every policy clamp, then prompts y/n/a/q. `--yes` approves it explicitly;
without a TTY and without `--yes` it refuses before accessing Docker. Each experiment starts
its own steady load; no separate `chaosai load` process is needed. Load creates durable orders
and may cause the sample app to send notifications through its existing Resend configuration.
Use the correct previously seeded credentials in configuration; never test incorrect passwords.

`chaosai run` now executes a grounded plan in rank order, at most eight ranked entries.
The sequence is scoped journal reconciliation → doctor-lite → map → baseline → planner →
[approval → measured runner → measured recovery/readiness and cooldown] for each selected entry.
No unjournaled container state is repaired. Doctor-lite checks the configured scoped services,
health, ports and public catalogue GETs without an LLM/key probe. A missing/stopped/paused
service stops the run. The active-fault lease covers the whole live run, including preparation,
approval and cooldown, so another injector cannot enter between experiments.

```sh
chaosai run --dry-run
chaosai run --max-experiments 4
chaosai run --yes --max-experiments 4 --reuse-baseline
```

Approval supports y/n/a/q; `a` approves remaining selected entries. Without a TTY or `--yes`,
run refuses before Docker access. Approvals are durably persisted before calling the runner.
`--max-experiments N` accepts 1–8 and selects the first N ranked entries; skipped/denied entries
consume a selected slot. The complete plan/decision trace remains available. `--reuse-baseline`
is opt-in and reuses only stable measurements with age >=0 and <3600 seconds. Missing, stale,
future-dated or unstable baselines trigger a fresh measurement. Without the flag, measure a
fresh baseline. A newly measured unstable baseline still fails the unchanged S9 check.

Dry-run checks existing journals without restoring them, refuses pending undo/KILL and writes
and prints the plan, sources, final reasons, clamps and selected entries without approval or
fault application. It performs doctor-lite and may measure a baseline (normal bounded load),
so it is a live preview. `run --offline --yes` and `run --offline --dry-run` retain the original
three synthetic fixtures without Docker, target access, dotenv, network or waiting.

After each measured runner returns, the orchestrator requires successful rollback and cleanup,
reconciles journals again, then measures new cooldown samples with the existing load generator.
It requires both unchanged S9 readiness and three consecutive complete, nonempty one-second
recovery windows, plus at least 20 seconds of cooldown. Preparation counts toward a 60-second
readiness deadline. Sparse journeys can prevent continuation; the next experiment never starts
on container state alone. Each next runner independently repeats S9 before injection.
Cooldown `samples.jsonl` and `readiness.json` are saved separately from the experiment evidence;
a subsequent readiness pass never rewrites an experiment's `recovered=false` result.

Execution failures are recorded and can continue only after rollback, no pending undo,
all expected services running/unpaused and measured cooldown readiness. Failed rollback/cleanup,
unresolved journals, KILL, signals or failed readiness stop the sequence; q exits cleanly.
KILL is never cleared implicitly. `run.json` records approvals, overall/per-item planner sources,
target project, baseline source and stop reason. `plan.json`, `decisions.json` and
`source-facts.json` preserve requested/final reasoning, clamps and grounded source provenance.
Phase 9 reports score recorded during-fault evidence and explain it with validated AI output
or deterministic fallback. Skips/absent during measurements are unscored; sparse scores remain
descriptive. Analysis never establishes recovery or changes safety decisions. See [analysis.md](analysis.md).

## Extended mechanisms

CPU uses `container.update` with CFS quota and, when explicitly configured, period 100000µs.
The journal snapshots period/quota/NanoCpus before mutation; replay restores and verifies exact
original fields. An original zero period is preserved (implicit 100000µs period), and explicit
unlimited quota -1 is supported. Original quota 0 and NanoCpus configurations are refused before
mutation: the installed SDK omits zero CPU values, rejects nano_cpus, and Docker treats zero as
unchanged, so those original fields cannot be restored exactly through update. The common
unconfigured quota 0 is therefore not eligible for CPU injection. No normalization, container
recreation or target configuration patch is performed. Legacy CPU records that cannot be restored
exactly remain pending. See [safety.md](safety.md) for inspected SDK/Engine evidence.

Traffic adds policy-clamped workers (2–8x, maximum 150 including retiring workers); extras select
the chosen mapped journey or the normal weighted mix for `all`. Existing checkout/auth rate
caps, credential verification and timeout semantics remain shared. The journal records the load
owner PID and original worker count, fsynced before worker startup. Undo removes and joins extras
before recording completion. Every selected injectable journey participant is scope-checked;
no container is mutated. The watchdog exempts no container from traffic blast-radius checks.

A second-terminal rollback requests a live traffic owner's cleanup durably and reports pending
until that owner joins extras and acknowledges undo. Retry rollback after it exits; KILL remains
set when kill was used. Replay marks an orphan load record done only after proving its owner
process no longer exists. A live/unknown/reused PID remains pending conservatively.

## Lifecycle and safety

APPROVE → PRECHECK → INJECT → OBSERVE → UNDO → RECOVER → RESULT.

Before live injection, replay pending journals while holding the active-fault lease. Malformed
or failed journals deny further injection. A set KILL flag also denies injection and is never
silently cleared. The same lease covers preparation, injection, restoration and recovery.
Scope checks and fsynced write-before-apply ordering are reused from Phase 5.

S9 has a 60-second readiness deadline, including load preparation. Preparation runs in a thread;
on timeout the runner records a skip and stops preparation between bounded credential/catalog
calls. No workers start when preparation misses the deadline. Cleanup can take longer than
the readiness deadline while the current bounded SDK/HTTP call finishes. Each journey must
have observed samples, error rate <= baseline error + final hypothesis error allowance, and
p95 <= 1.5x baseline. Windows adapt from 5–20 seconds; journeys too sparse for ten samples in
20s still require at least one observation. Missing/zero-sample baselines and unstable baselines
cannot establish readiness. S9's 1.5x limit intentionally has no absolute latency floor.

One-second scoped container polling feeds the unchanged S5 Watchdog: aggregate five-second
windows, adaptive journey windows, five-second grace, minimum samples, consecutive breaches,
absolute p95 floors and immediate blast-radius protection. The runner deep-copies thresholds
and applies the **final policy-clamped** abort/hypothesis values; saved baseline data is unchanged.

An independent timer starts before applying the fault, so apply/stop time counts toward the
hard duration cap. It requests restoration directly without depending on Watchdog/main-thread
progress. A separate control thread checks KILL and completed journal records every 0.5s,
including while the main thread is doing SDK I/O. Cancellation is checked inside the operation
lock before writing/applying a new fault, preventing delayed application after the deadline.
Rollback cannot interrupt an in-flight Docker mutation or operation lock; bounded SDK calls can
therefore delay actual restoration. Successful journal restoration is timestamped for measured
boundaries, including second-terminal rollback; legacy completed records without timestamps use
the observation time instead.

Normal completion, watchdog abort, KILL, second-terminal rollback, exceptions, SIGINT and SIGTERM
restore containers and close/join all load, telemetry, control, preparation, observation and timer
resources. Signal handlers ignore repeated signals during all cleanup and are restored afterward.
Exceptions persist their class or safe deterministic reason, never raw HTTP/Docker payloads.
Failed rollback stays pending, sets rollback_ok=false and yields an aborted result. Retain journals
and retry `chaosai rollback`. A denied active lease never rolls back another process's fault.

## Evidence and evaluation

Each `runs/<id>/experiments/<experiment-id>/` contains `samples.jsonl`, `events.jsonl`,
`containers.jsonl` and a complete validated `result.json`. The result includes the policy
decision, timeline, during/after n/error/p95/RPS, impact, recovery, self-heal, state/restart
changes, rollback status, abort/skip reason and evidence limitations. Events are persisted as
phases occur, so observation can be inspected before the result exists.

Requests belong by their **start time**: during is [inject, undo), after begins at undo.
Precheck samples remain in the artifact but are excluded from during/after. In-flight requests
are allowed to finish during cleanup. Live readiness/recovery evaluation seals windows behind
the request timeout before judging them; final evaluation uses the drained sample buffer.

Hypothesis p95 breaches use `latency_limit_ms`: p95 must exceed BOTH the configured multiple
and baseline p95 + the 100ms hypothesis floor. Equality holds. Impact lists every journey with
an observed breach, including sparse descriptive evidence. Verdict requires at least 20 baseline
and 20 during samples for each journey assessed: a supported breach is refuted; all sufficiently
sampled journeys holding is confirmed; insufficient evidence is inconclusive. Watchdog, KILL,
external rollback and execution failures yield aborted, independent of later recovery.

Application recovery requires **every** baseline journey to satisfy p95 <= 1.5x baseline and
error rate <= final hypothesis limit in **three consecutive, complete, nonempty one-second
windows**, ending within 120 seconds after restoration. No absolute floor, adaptive window or
aggregate substitute applies to recovery. The reported time is the end of the third qualifying
window, measured from undo. Empty windows reset the streak; a journey with no baseline evidence
cannot recover. A single sample can satisfy a window but does not establish a strong p95 or error
estimate. Sparse login/checkout traffic can prevent a recovery claim despite a healthy app.
`recovered=false` means recovery was not established within the evidence window; it does not
by itself prove persistent failure. Container rollback_ok and application recovered are separate.

For kill, self_healed reflects observations **before our own restoration**. None means it could
not be observed; false means observations never saw it running; true means it was observed
running without our undo. This does not establish application readiness or a restart policy.

Log evidence covers only the target and its direct dependents (incoming graph edges). Every
access reloads and rechecks the exact project label. At most 40 lines per container are retained,
truncated to 300 characters after suppressing secret-bearing lines/known configured and process
secret values and stripping controls. Unavailable logs are labeled unavailable. Unknown unlabeled
secret formats cannot be guaranteed identifiable; excerpts are evidence, never instructions.

## Demo and interpretation

The demo script checks doctor, creates/reuses a baseline, performs the normal Redis measurement,
then the strict 0.05 abort experiment. It starts a **separate** 120s measured fault in the background
with `--yes`, waits for its OBSERVE event and a pending undo record, and only then invokes kill.
If no active fault is established it reports the demo incomplete and invokes kill for cleanup.
It never claims that a threshold breach or recovery happened; inspect actual result fields.
KILL remains set; after reviewing rollback explicitly run `chaosai rollback --clear-kill`.

The target limits auth to 100 requests per 15-minute IP window. Repeated baseline/demo runs can
hit this limit, contaminate baseline/recovery or cause an S9 skip. `LOGIN_RATE_CAP=0.5` reduces
traffic but still shares the existing IP budget. Allow a fresh 15-minute window between live
runs when needed; no limiter reset/bypass is performed. Redis reconnect/hang behavior and real
watchdog/recovery outcomes remain human verification, not inferred from target source.
