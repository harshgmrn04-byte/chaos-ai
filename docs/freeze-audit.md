> Historical Phase 10 audit. Later user-authorized runtime fixes, dashboard and live
> verification are recorded in [runtime-fixes.md](runtime-fixes.md) and
> [final-verification.md](final-verification.md). Earlier pending statements below
> describe the checkpoint at the time.

# Phase 10 invariant and bug audit

Source inspection on 2026-10-11 covered CLI/doctor/seed/smoke, config/target access,
runner/orchestrator/experiment, faults/journals/signals, load/evaluate/Watchdog,
planner/LLM/analyzer/scorer/report/templates and bootstrap/Day-1 scripts. No target writes,
live Docker/HTTP/load/model calls, integration tests or private .env/key reads occurred.
Unit tests use mocked providers/SDK/HTTP and block sockets/real Docker/dotenv.

| Invariant reviewed | Evidence / outcome |
|---|---|
| LLM data-only and deterministic fallback | Flat proposals/Finding/Summary validation, catalogue/graph/policy checks, heuristic/templated fallback; replay never creates transport. No injection tools in prompts. |
| S1 exact project scope / external exclusion | target.require_scope, safety.scope_guard, journal ID/project checks, scoped credential/DB/log/telemetry access; third-party external nodes denied. |
| S2 policy/approval | Deep-copy clamps, max eight selected entries, one-workspace lease, traffic <=150; Approver ensures TTY or explicit --yes. |
| S3 durable-before-mutation / exact undo | Operation lock across record/fsync/apply, identity/restore verification before done; CPU zero/NanoCpus refused; joined worker acknowledgment. |
| S4 cleanup/signals | Measured cleanup before joins, replay on exits/startup, repeated signals ignored; unresolved journals fail closed, no unjournaled repair. |
| S5/S6/S7 independent controls | One-second Watchdog, independent pre-apply duration timer, half-second KILL/external rollback control; in-flight bounded calls can delay actual restoration. Low-level fault is explicitly not a measured runner. |
| S9/recovery/scoring evidence | S9 readiness and sparse samples unchanged; 3 nonempty all-journey 1s windows, independent cooldown; skipped/no-during unscored, scores deterministic. |
| S10 / reproducibility | No destructive reset/prune/volume/network changes; clean_run preflight blocks pending undo/KILL/active lease before bring-up; clone is inside chaos-ai. |
| Untrusted text / report isolation | Quoted bounded evidence, credential-line suppression, autoescape on, inline assets only, sample/output path confinement. Logs remain imperfectly redactable; review before publishing. |

## Confirmed fixes and polish

1. `safe_admin_login` previously discarded the unexpected HTTP status after a matching
   credential recheck. `TargetRequestError` now retains only the numeric HTTP status, which
   is included in the actionable failure message. No response body/key/URL is retained,
   no retry is added, credential preflight remains mandatory. This lets future smoke/seed
   failures distinguish confirmed 429 from a generic transport/readiness failure. Historical
   smoke status remains unknown; the old evidence is unchanged.
2. Automatic run previously parsed the latest baseline even when measuring fresh evidence.
   A corrupt unused cache could therefore block the default fresh run. Now only opt-in
   reuse reads it, and unreadable reuse caches cause fresh measurement. Stability, age,
   readiness and recovery checks remain unchanged; unreadable journals still fail closed.
3. `chaosai up` previously lacked injector coordination and pending-undo checks: Compose
   recreation could invalidate the container identity needed for exact restoration. Bring-up
   now holds the existing active lease and operation lock through Compose execution, refuses
   KILL/pending or malformed journals and never attempts implicit replay/repair. Script
   preflight is retained for early feedback; the direct command closes the bring-up race.
4. Report flows used arrows implying a linear call chain; now labeled touch-sets. Display
   polish adds consistent UTC milliseconds, chart time/latency labels with bucket scale,
   recorded event markers and readable threshold tables. INJECT is an attempt, UNDO begins
   restoration, and events outside the sample span are not invented/clamped into the chart.
   No data, score, severity, ordering, synthetic/measured banner or escaping changes.

Existing supplied evidence shows runtime constraints, not additional confirmed code bugs:
latency-drift stable=true can coexist with auth errors; 100/15-minute budget can block checkout;
regular 0.5/s login admission produces sparse one-second recovery windows; restored containers
cannot prove recovery; RabbitMQ observations are intermittent; exact CPU restore limits are
intentional safe refusals. No threshold/limiter/scoring bypass or unsupported target patch.

## Verification boundary

Mock regressions cover status preservation/redaction/no retry, fresh-run corrupt-cache behavior,
report labels/event bounds/escaping, clean-run ordering/cloning/failure/pending-undo/KILL and
truthful completion validation. See [progress](progress.md) for actual commands/results.
[Final human verification](final-verification.md), visual QA, real-run replay, completed
measured sample and demo video remain pending. No live success is inferred from mock checks.
