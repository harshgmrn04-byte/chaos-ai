# ChaosPilot architecture — frozen implementation

The CLI coordinates deterministic measurement and safety around two LLM data transforms.
See the [README diagram and S1–S10](../README.md), [safety contracts](safety.md) and
[audit](freeze-audit.md). The target remains an independent read-only MIT checkout.

## Components and data flow

| Component | Responsibility / contract |
|---|---|
| config.py / cli.py | Environment defaults, private SecretStr credentials, command validation and human-facing diagnostics. Offline skips dotenv. |
| target.py / doctor.py | Bounded SDK socket resolution; exact project-label access; scoped state/port/public GET diagnostics. Ordinary doctor does not call an LLM. |
| mapper.py / overrides.yaml | Parse Compose declarations without expanding/reading private env files, merge explicit corrections, protect external nodes. Flows are touch-sets, not call chains. |
| seed.py / target_auth.py / target_db.py | Idempotent seed and fresh scoped existence/bcrypt checks before login; no invalid-credential probing or password resets. |
| load.py / metrics.py / baseline.py | Threads with private synchronous HTTP clients; shared admission/token caches, request samples, scoped CPU/memory/queue observations and pure statistics. |
| catalogue.py / planner.py / planner_facts.py | Flat AI proposals, curated revision facts, graph/kind/journey validation, heuristic top-up/diversity, deterministic ranks/IDs and clamps. |
| orchestrator.py | Lease → reconcile → doctor-lite → map → fresh/eligible reused baseline → plan → approval/experiment/cooldown loop → analysis/report. |
| experiment.py / runner.py / evaluate.py | Measured state machine and isolated fixture path; pure readiness, verdict, impact and recovery evaluation. |
| safety.py / faults/ / watchdog.py | Scope, policy, durable undo, POSIX locks, independent duration/control threads, metric/blast-radius evaluation, signals and rollback. No model calls. |
| scoring.py / analyzer.py | Pure penalties/severity/rank, bounded evidence packets, citation validation and templated fallback. Analysis happens after safety cleanup. |
| report.py / templates/ | Saved-data-only, escaped HTML/Markdown with inline assets, plan-ranked cards, readable UTC timestamps, scaled latency chart and recorded markers. |

For each live experiment: APPROVE → PRECHECK → INJECT (attempt) → OBSERVE (apply returned)
→ UNDO (restoration begins) → RECOVER → RESULT. Events are recorded when phases occur.
A journal done timestamp supplies the measured undo boundary, including external rollback;
legacy records use observation time. A skipped precheck or denied CPU configuration has no
fault impact. Failed cleanup, unresolved undo, KILL, signals or measured cooldown failure
stop further experiments. Container restoration and HTTP recovery are distinct facts.

Samples belong by request start time to half-open measurement intervals. Warm-up is discarded.
Statistics describe HTTP requests, not multistep journey duration. Load does not retry failed
orders after HTTP 500/timeouts because an order may already have persisted; only middleware
401 permits a single re-authenticated retry. Auth requests retain fresh credential preflight.
The shared target IP limiter is never reset or bypassed.

## Models and persisted contracts

`chaosai/models.py` is the pydantic v2 source of truth. Models reject unknown fields and
nonfinite values; upstream API shapes allow documented additional fields. New fields have
conservative defaults for older artifacts.

| Contract | Main evidence |
|---|---|
| Graph / Node / Edge / Flow | Local/external kind, exact container mapping, eligibility, declarations, touch-set membership, parsed/override provenance. |
| Baseline / JourneyStats / Thresholds | Counts/errors/percentiles/RPS/low_sample, drift stability, container/queue observations, hypothesis and abort floors. |
| ExperimentProposal / PlannerOutput | Flat LLM-facing fields; no executable Docker instruction. |
| ExperimentSpec / PolicyDecision / Clamp | Requested and final numeric values/reason/hypothesis, rank, allowed/denial, source and every correction. |
| JournalData / UndoRecord / LoadUndoRecord | Version, exact container identity/original state or process-owned workers; pending/done/time/stop request. |
| Sample / Event / ExperimentResult | Request start/status/latency, phase notes, during/after, skip/abort, measured recovery, self-heal, rollback/cleanup and evidence limits. |
| Finding / Score / Summary / AnalysisTrace | Validated explanatory citations and fixes; deterministic penalties/ranks; per-call source/fallback provenance. |
| RunMeta | UTC ID/time, approvals, target project, fixture/measured and baseline/planner/analyzer sources, stop reason. |

```text
runs/<UTC-id>/
  run.json graph.json baseline.json plan.json decisions.json source-facts.json
  journal.json doctor.json
  experiments/<id>/{events.jsonl,samples.jsonl,containers.jsonl,result.json}
  cooldown/<id>/{samples.jsonl,readiness.json}
  llm/<task>-<hash>.json analysis.json findings.json scores.json summary.json
  report.html report.md
.chaosai/  latest graph/baseline, resolved model, KILL, operation/active locks
```

Not all commands produce all files: standalone inject stores experiment evidence without a
full run report, plan stores no measurements, dry-run has no fault results. Baseline/load
commands store their own sample/telemetry/probe artifacts. Do not infer completion from file
presence alone. Report regeneration reads saved derived artifacts; replay recomputes derived
analysis/scores and leaves original measurements/plan unchanged.

## Safety boundary and recovery

Graph eligibility plus exact Compose labels protects every container access; journal replay
also verifies container ID to refuse replacement-name reuse. One workspace lease covers the
complete automatic run; a separate operation lock serializes journal-before-apply against
rollback. Atomic replace plus file/directory fsync precedes mutation. Undo verifies restoration
before marking done, tries remaining records on failure and retains unresolved evidence.

Measured experiments use independent pre-apply timers, one-second Watchdog and half-second
KILL/external-undo observation. SIGINT/SIGTERM unwind into rollback while repeated signals
are ignored during cleanup. Cleanup joins threads/closes clients. SIGKILL cannot run cleanup;
restart/replay restores pending actions. In-flight SDK operations may delay restoration.
Low-level `fault apply` is a mechanism check without measured precheck/metric Watchdog.

S9 requires a stable, nonempty baseline and near-baseline observed samples within 60s.
Latency stability only measures <=30% half-window p95 drift for eligible >=20-sample journeys.
Recovery needs all journeys in three consecutive complete nonempty one-second windows within
120s, p95 <=1.5× baseline and the hypothesis error limit. Cooldown requires fresh readiness,
measured recovery and >=20s; it cannot rewrite the experiment's recovery result. Sparse login
or throttling may make these gates impossible to establish in a given run.

CPU quota 0 / NanoCpus originals are refused because inspected update semantics cannot restore
those fields exactly. Traffic undo joins added workers; another process requests owner cleanup
without claiming it already happened. Unknown/live/reused PIDs remain conservative pending undo.
Clean-run checks journals/KILL before Compose can recreate anything and keeps all volumes.
Direct `chaosai up` rechecks this state and holds the active/operation locks through build/start.

## AI transport, resolution and fallback

Provider gemini/mock/none is configured locally; offline forces none (direct fixture tests
may explicitly use mock). Model IDs are resolved at runtime, not embedded in source.
A configured override is probed; otherwise cached model or models.list candidates supporting
generateContent are filtered by family, stable/preview status and version. Up to four are
probed; not-found/permission failures move to another, and one later not-found causes refresh.
The winner is cached in .chaosai/llm.json. See [LLM details](llm-planning.md).

SDK automatic retries are disabled; explicit 429/503 backoff is 2/4/8s. Calls use bounded
requests, pydantic response schemas (schema rejection falls back to prompted JSON mode),
and one invalid-output retry. Failures become safe LLMUnavailable diagnostics. Every planner
failure falls back to heuristics; analyzer failures fall back per finding/summary. Transport
or cache failure cannot disable safety or stop measurement merely because AI is unavailable.

Response hashes cover task/system/user. Replay has no transport object and reads exact,
validated caches; missing/malformed/semantically invalid caches use explicit fallback. Changed
prompts can miss old hashes. Planner receives quoted graph/baseline/catalogue/source facts;
analyzer receives bounded quoted evidence including <=3 untrusted log excerpts. Keys and raw
HTTP bodies are absent. Findings must cite supplied evidence strings and match identity;
causal language still needs human review. Severity/score/rank/summary fixes are canonical code.

## Decision trace and tradeoffs

`decisions.json` retains requested/final specs, clamps, reasons, denials and per-item sources.
Source-grounded reason corrections preserve the original model explanation. `run.json` and
`analysis.json` record mixed model/heuristic sources and fallback reasons. Skips are diagnostic,
unscored, and never promoted into measured findings or a successful sample submission.

| Choice | Benefit / tradeoff | Alternative rejected |
|---|---|---|
| Deterministic safety, two explanatory LLM stages | Auditable failure boundary, less model complexity | Docker tool-calling LLM or agent framework |
| Files and validated JSON/JSONL | Inspectable provenance/replay, no database setup; volume can grow | Prometheus/Grafana/SQLite stack |
| Threads and synchronous clients | Bounded worker ownership, direct cleanup | Async orchestration |
| Exact resource undo | Fail closed on unsupported metadata; CPU coverage limited | Normalize limits or recreate containers |
| Human approval on by default | Concrete experiment review; --yes explicit for reproduction | Silent unrestricted injection |
| Measured all-journey recovery | No inference from healthy containers; sparse windows can block demo | Adaptive/aggregate recovery substitute |
| Read-only target plus conditional patches | Reproducible source provenance | Edit third-party source in place |
| Cached replay + deterministic fallback | Works without venue Wi-Fi | Require live AI for reports |
| Skip disk/netem/re-planning in freeze | Avoid host risk and new privileged paths | Expand fault catalogue during polish |

[Final verification](final-verification.md) remains pending for measured impacts/safety/recovery,
run diversity, visual report/replay, reproduction and the representative sample. This document
describes implemented behavior, not proof that live checks passed.
