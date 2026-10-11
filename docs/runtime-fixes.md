# Runtime corrections — 2026-10-11

The user explicitly authorized runtime fixes, a real-time dashboard and scoped live
verification after repeated failed waiting attempts. This supersedes the earlier Phase 10
freeze and human-only scheduling. Original evidence is retained; no historical samples,
results or baselines were edited. The third-party checkout remains read-only.

## Root causes and changes

- Auth's original shared 100/15-minute budget cannot sustain baseline plus multiple
  experiments. An explicit owned image patch keeps the 100 default and applies a bounded
  6000 budget only to local auth. Installation/restore holds the active/operation locks,
  refuses KILL/pending journals and records original image identity before recreation.
- Credential verification used Docker/PostgreSQL/bcrypt inside every login request. During
  an outage it suppressed requests instead of measuring them. Owned sandbox mode caches
  verified password proof in memory for at most 600s. HTTP rejection/invalid success clears
  proof; transport/server failure retains it. Generic targets retain per-request checks.
  Seeded accounts must remain unchanged during a sandbox run; tokens/passwords are never
  written to the cache or dashboard API.
- Single-digit p95 values amplified small laptop jitter into large relative drift. New
  baseline version 2 records a 100ms absolute allowance alongside 30% relative drift.
  Readiness/recovery use the existing hypothesis absolute latency floor. Errorful/empty
  baselines cannot be reused or used to start injection.
- Three consecutive 1s windows could not contain rate-capped sparse logins. Window width
  now derives from measured baseline RPS (1–20s); every journey still requires real healthy
  samples in three complete consecutive windows within 120s. Missing samples reset recovery.
- Readiness keeps the minimum sample requirement and may extend a short estimated window
  to the full 20s when current cadence is slower. Cooldown preparation has a separate
  bounded measurement budget.
- CPU fields that cannot be restored exactly are flagged unsupported before planning.
  Fault diversity is selected at the requested batch size; denied/skipped faults never count.
- Load/baseline CLI commands now share the experiment lease. Dashboard jobs respect this
  lease and distinguish external CLI work. Run metadata records phases and failure completion.

## Evidence retained during diagnosis

20261011-025715: clean stable baseline, three applied faults, rollback and measured recovery;
sequence stopped at cooldown. Auth-outage login samples were suppressed by verification.
20261011-034258: four applied types (kill, traffic_spike, pause, stop), all rolled back and
recovered (8s, 6s, 10s, 22s). Traffic hypothesis confirmed; PostgreSQL pause/stop crossed
watchdog limits and aborted safely. Kill was inconclusive because only 10 login samples
were measured. This diagnostic run is not substituted for the final sample deliverable.

Fresh reproduction `20261011-035048` passed all four fault types, exact undo, cleanup,
measured recovery and final smoke; score 55/100 (weak). Three live integration tests passed.
The selected report is exported with provenance and hashes in examples/sample-run.
The full run used heuristic planning after a transport failure and mixed Gemini/heuristic
analysis; the separately verified fresh Gemini plan is recorded below.

## AI connectivity verification

The first 30s connection check and a 15s planner check fell back on transport failure.
A short connection probe succeeded; an explicitly authorized 60s planner call then
succeeded as gemini:models/gemini-3.8-flash (run 20261011-040607). Default timeout is now
60s. Models are still resolved at runtime, never pinned in code. Typed proposals and
source/policy grounding still precede any execution. Transport failure remains possible
and selects deterministic fallback with an inspectable reason.

## Safety walkthrough correction

A Redis outage starved the nominal five-second watchdog window: completed five-second
timeouts arrived with request-start timestamps outside that window. When actual samples
are below the unchanged minimum, watchdog windows now extend to at most 20 seconds,
still bounded by injection start and requiring consecutive breaches. A regression test
proves completed timeout failures trigger the watchdog. Approval quit now exits 130 so
serial shell walkthroughs stop instead of continuing to another fault.
