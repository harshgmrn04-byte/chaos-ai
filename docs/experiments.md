# Measured experiments — Phase 6

The measured runner is exported by `chaosai/runner.py`; `chaosai/experiment.py` owns the
single-experiment lifecycle and `evaluate.py` contains pure evidence functions. No live
Phase 6 path calls a planner, analyzer or LLM. Only kill, stop and pause are implemented.
Automatic experiment selection, extended faults and multiple live experiments remain later
phases. `fault apply` remains a low-level mechanism test without load or a metric watchdog.

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

`chaosai run` reuses the saved baseline (or measures one when absent), reconciles journals and
runs one deterministic Redis stop. It generates a measured evidence report with **no live
scores or findings**, because analysis/scoring remain Phase 9. `run --dry-run` creates a plan
and report without approval or injection; as before, a missing live baseline may be measured.
`run --offline --yes` and `run --offline --dry-run` retain the fully synthetic fixture pipeline
without Docker, HTTP, target access or dotenv reads.

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
