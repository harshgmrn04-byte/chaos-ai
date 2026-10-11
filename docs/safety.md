# Deterministic safety — Phases 5–8

All live fault and measured experiment operations are deterministic; none call an LLM.
Phase 7 `run` obtains an AI/heuristic plan before entering this lifecycle. Planning emits
data only: proposals are checked against graph/catalogue, bounded by policy and traced.
Approval, S9, the scope guard, journal, timers, Watchdog and KILL remain deterministic.
`fault apply` runs a low-level mechanism test. `inject` and live `run` attach load, one-second
container polling, the S5 Watchdog, S9 precheck and measured recovery. `run --offline` retains
synthetic fixtures. See [experiments.md](experiments.md) for the full Phase 6 lifecycle and
[../scripts/day1_demo.sh](../scripts/day1_demo.sh) for the walkthrough.

## Contracts and recovery

`scope_guard(target, graph, client, project)` resolves the graph node's container name,
reloads it and requires the exact Compose project label. Unknown/ambiguous, external,
non-injectable and containerless nodes are denied before Docker access. A fault additionally
checks its allowed node kinds and requires a running, unpaused container. Kill supports
applications; stop supports applications/databases/caches/brokers; pause supports every
injectable local kind. MailHog remains non-injectable in the default graph.

`policy_gate` returns a deep-copied PolicyDecision and sequential Clamp records: duration
5–120 seconds (CLI default 30), CPU 0.1–0.5, traffic multiplier 2–8 and at most 150 total
workers, abort error 0.05–0.9 and abort p95 multiplier 2–20. Caller-provided active fault and
completed experiment counters deny concurrent faults and a ninth experiment. Traffic is
denied if its minimum multiplier cannot fit. Phase 8 implements CPU/traffic subject to the
additional restoration/worker checks below.

`FaultContext` connects the spec, graph, SDK client from target.py, Settings and UndoJournal.
`Fault.apply` holds the process-shared operation lock across scope validation, writing the
undo record, fsyncing the file and directories, and the Docker mutation. Each record contains
container name, ID, project, fault and original running/paused/CPU period/quota/NanoCpus.
Only after successful restoration and state verification is it marked done. `Fault.undo` and
`UndoJournal.replay_all` are idempotent. Rollback also accepts empty legacy fixture journals
(`[]`); unknown nonempty legacy or malformed journals cause a visible failure.

A separate OS lock holds the single active fault lease for the CLI command. Rollback acquires
the operation lock before discovering journals, so it waits for an in-flight apply and cannot
miss its new journal. Records replay in reverse order, attempt every record/journal even if
another fails, check scope again and refuse a replacement container with a reused name.
The lock is automatically released on process death. This coordination assumes one workspace
and POSIX locks (the macOS/Linux deployment); separate workspace copies need external
coordination. Record/write methods are used under the operation lock by Fault.apply.

SIGINT/SIGTERM set an abort event and unwind the current operation, then replay on context
exit. Repeated signals are ignored during cleanup, and previous handlers are restored.
SIGKILL/power loss cannot execute a handler; a new CLI injection reconciles pending journals
before proceeding, or run `chaosai rollback` yourself. Keep journals until recovery completes.
The kill command persists KILL before opening Docker, so a Docker connection failure still
leaves the flag set. The low-level holding loop checks it every 0.5 seconds between SDK
observations. SDK calls have target.py's bounded timeouts and can delay cleanup; duration is
measured after apply returns. A failed rollback is reported and remains retryable.

Rollback restores recorded containers, without trying to repair unrelated pre-existing target
failures. Phase 6 implements journal reconciliation and measured S9 precheck. A successful
rollback proves running/unpaused/resource restoration, not HTTP readiness, broker
resubscription or application recovery. In particular, the RabbitMQ reconnect and Redis
fallback hypotheses in target-notes still need measured verification.

## Watchdog S5

The pure `evaluate(WatchdogWindow, Thresholds, Baseline, WatchdogState)` returns a fresh state
and optional reason. WatchdogWindow contains elapsed seconds, journey statistics and latest
container states. WatchdogState holds target container **names**, independent per-window
consecutive counters and a latched abort reason. An aggregate `all` baseline is required.

`live_window` builds a five-second aggregate window. Each eligible journey uses
`clamp(min_samples / baseline_rps, 5, 20)` seconds; rates below `min_samples / 20` use only
the aggregate. Windows are half-open and begin no earlier than the injection start. The
baseline's low_sample flag does not override this S5 rate test. After five seconds of grace,
windows require at least min_samples and consecutive_breaches observations of error rate
strictly above the threshold or latency strictly above metrics.latency_limit_ms. Equality
never breaches. Missing/undersampled/healthy windows reset that window's counter. Defaults
are ten samples, two consecutive evaluations, error rate 0.5, latency 10x plus 750ms floor.

Blast radius trips immediately, including during metrics grace, when more than the configured
number of baseline-running, non-target containers leave running. A running container whose
healthcheck says unhealthy has not left running. Scoped telemetry now records disappearing
previously seen containers as missing; paused/exited states are not overwritten by an old
healthcheck status. Pre-existing stopped containers and foreign observations do not count.

The Watchdog thread reads SampleBuffer and ContainerStatsPoller snapshots every one second,
sets the supplied abort event and latches its reason. It fails closed on evaluation failure.
Its telemetry freshness is the caller's poller interval (default five seconds); configure
one-second polling for a live safety runner. Missing observations do not prove failure;
unreachable Docker/read failures are still logged by the poller. No new HTTP, Docker poller,
or load generator is started by the watchdog itself.

## Human Docker verification

**You verify (Docker), copied from BUILD_PLAN.md:** `chaosai fault apply stop redis --duration 20`
shows the site degrading; `chaosai rollback` restores it; pressing Ctrl-C mid-fault also
restores; `chaosai kill` from a second terminal works.

In both terminals:

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
export COMPOSE_PROJECT=ecommerce-microservices
```

Use the real target project's label if it differs. These commands assume the documented
container names and host ports. Confirm the scoped target is already up; no build/down,
volume deletion or cleanup is needed:

```sh
docker compose --project-name "$COMPOSE_PROJECT" --file ../ecommerce-microservices/docker-compose.yml ps -a
chaosai rollback --clear-kill
```

Apply and automatic undo (terminal A; type `y`):

```sh
chaosai fault apply stop redis --duration 20
```

While it holds the fault, run in B and browse the catalog at http://localhost:3000:

```sh
docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --format '{{.Names}}\t{{.Status}}'
curl --max-time 8 -sS -o /dev/null -w 'catalog HTTP %{http_code}\n' http://localhost:3002/products
```

After A reports rollback complete, repeat the Docker state check and catalog request.
Redis must be running. HTTP may take longer to recover; inspect it separately from Docker.

Explicit rollback: start this in A, then run the B commands as soon as A prints Applied:

```sh
# Terminal A
chaosai fault apply stop redis --duration 120
```

```sh
# Terminal B
chaosai rollback
docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --format '{{.Names}}\t{{.Status}}'
```

A should exit promptly after the done record; Redis must be running again. Repeating rollback
should restore zero records. Test pause recovery too if desired:
`chaosai fault apply pause product-service --duration 20` and in B `chaosai rollback`.

Ctrl-C recovery: in A, type `y`, wait for Applied, then press Ctrl-C:

```sh
chaosai fault apply stop redis --duration 120
```

The command exits 130 and reports rollback complete. Confirm from B:

```sh
docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --format '{{.Names}}\t{{.Status}}'
chaosai rollback
```

Kill switch: start in A, then use B immediately after Applied:

```sh
# Terminal A
chaosai fault apply stop redis --duration 120
```

```sh
# Terminal B
chaosai kill
test -f .chaosai/KILL
docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --format '{{.Names}}\t{{.Status}}'
```

B restores Redis directly; A exits early. After A exits, reset explicitly:

```sh
chaosai rollback --clear-kill
test ! -e .chaosai/KILL
```

Finally, test application kill/self-heal in A:

```sh
chaosai fault apply kill order-service --duration 20
```

The command reports observed self-heal true/false before undo. It does not assume that a
Compose restart policy will restart a manually killed container. Verify running state and
HTTP readiness independently; stopping Redis is already sufficient for Phase 5's required
human checks. Do not delete journals or manually change container IDs before recovery.

## Phase 8 exact CPU and traffic restoration

The installed Docker SDK source was inspected without opening a Docker client. Container.update
forwards keyword arguments to APIClient.update_container, whose signature has no nano_cpus and
whose request construction omits zero cpu_period/cpu_quota. The Engine also updates CPU fields
only when nonzero and rejects CFS period/quota updates on an existing NanoCpus configuration:
[Docker Engine update source](https://github.com/moby/moby/blob/master/daemon/container/container_unix.go).
These are SDK/source facts, not verification of the running Docker version.

CPU faults refuse an original CpuQuota=0 or NanoCpus>0 before mutation, since exact metadata
restoration is unavailable through this interface. Explicit unlimited CpuQuota=-1 is supported;
a zero CpuPeriod is preserved throughout. Originals are durable before update; restoration
sends only changed, supported nonzero values and verifies period/quota/NanoCpus exactly.
Legacy non-restorable records stay pending; replay never passes an unsupported nano_cpus
argument or marks an ignored zero update complete. No container recreation or unjournaled
normalization is used. The default target declares no resource limits; do not assume its live
CPU configuration or claim that a denied CPU spec ran. Other supported faults remain usable.

JournalData version 1 accepts the existing container records and a distinct LoadUndoRecord
(resource=load, project, owner_pid, original_workers, stop_requested, done/done_at). Empty
legacy lists remain harmless, malformed data remains a denial. A traffic record contains no
invented container/original CPU state. Owner undo joins extras before marking done. Another
process persists stop_requested and leaves the live owner's undo pending. The runner notices
this at its half-second control boundary and restores using its actual generator; the request
time is not used as the measured restoration timestamp. A dead PID proves its workers died;
a live/inaccessible/reused PID is conservatively unresolved. No signal is sent to another PID.

Live automatic run holds the existing lease for its full duration. Startup reconciliation
restores only scoped journaled state and requires all configured services running/unpaused;
missing or unjournaled stopped/paused state is a denial, never repaired. Dry-run validates
journals and refuses unresolved undo without replay. Doctor-lite remains read-only. Every
runner keeps S9, S5, independent duration timer, half-second KILL, operation lock, journal,
signal cleanup and measured recovery. Explicit cleanup_ok/interrupted result flags prevent
continuation after failed component cleanup or a signal even when container rollback succeeds.
The signal handler ignores repeat signals immediately while unwinding preparation/cleanup.

Continuation additionally requires journal reconciliation, fresh measured readiness/recovery
and >=20s cooldown, then the next runner's own S9 check. Prior recovery evidence is never
rewritten. Human Phase 6/8 Docker verification and a fresh baseline are explicitly deferred
until final verification at the end of Phase 10; these remain pending, not passed.
