# Analysis, scoring and report — Phase 9

`run` analyzes saved experiment evidence after the existing execution/cleanup lifecycle.
Scoring is pure deterministic code in scoring.py. The LLM receives no Docker tools and
cannot change measurements, verdicts, scores, severity, weakness order or recovery. The
pending Phase 6/8 live checks remain deferred until final verification after Phase 10.

## Formula and evidence boundaries

Score = clamp(100 - E - L - R - B - A, 0, 100), rounded to the nearest integer (Python
round, ties to even). Overall is the mean of scored experiments. Penalties follow
BUILD_PLAN.md section 7:

- E = 40 * min(1, max(0, worst during-minus-baseline error rate) / 0.5).
- L = 20 * min(1, max(0, worst during/baseline p95 - 1) / 4).
- R = 0 for measured recovery within 10s; 25 if recovery is unproven; otherwise
  25 * min(1, recovery seconds / 60).
- B = 10 * distinct impacted baseline journeys / total baseline journeys, excluding all.
- A = 5 for metric/blast-radius Watchdog abort, not operator KILL, signals or failed observers.

Worst error/latency comparisons include each common nonempty baseline/during journey and
aggregate all when recorded. Negative error deltas are zero. A zero baseline p95 with
positive during p95 saturates L; zero-to-zero has no latency penalty. No successful recovery
is inferred from rollback, after statistics or later cooldown readiness. A missing or
inconsistent recovery time is treated as unproven. The measured runner records
watchdog_aborted; old records use the existing metric/blast-radius reason patterns. Original
fixture abort text is recognized as a simulated metric breach.

Skipped or absent during-fault evidence has no score and never enters the overall mean or
ranked weaknesses. Nonempty sparse/inconclusive scores are descriptive only: they do not
confirm hypotheses, measure full app capacity or complete live verification. Partial-run
coverage is stated in the report. Bands: >=85 resilient, >=70 acceptable, >=50 weak, else
fragile. Severity: critical below 30 or aborted with >=2 distinct impacted journeys; high
below 60, medium below 80, low otherwise. Confirmed score >=80 appears under What held up,
never in ranked weaknesses; other scored findings rank by severity, lowest score, plan rank.

## Analyzer and fallback

One shared bounded LLM session makes one Finding call per scored experiment plus one Summary
call. A skipped/unmeasured result receives a deterministic diagnostic without a model call.
Provider none stays offline; mock builds validated responses from the current evidence packet.
The real provider uses Phase 7 transport, retries, schema validation, caching and cleanup.

The prompt contains quoted JSON with spec/hypothesis, verdict, during/after against baseline,
recovery/self-heal, container events, evidence limitations, score and severity. At most three
log excerpts total are included, each <=1000 characters, marked UNTRUSTED DATA. No raw HTTP
bodies, credentials or configuration secrets are included. Curated source facts are supplied;
when source-facts.json exists, its archived facts are used rather than replacing that snapshot.

A Finding must name the correct experiment, have nonempty explanation/fixes, and cite exact
provided evidence strings. Invalid identity/citations or unavailable/invalid model output
falls back per experiment. Severity is overwritten deterministically. Only the Summary's
narrative is explanatory; scores, bands, weakness ordering and top fixes are rebuilt from
canonical scores/findings. Findings and narrative remain interpretations: natural-language
causal claims cannot be fully verified automatically. Reports label root causes unproven
unless cited source establishes them, and the prompt prohibits invented facts or numbers.

Fallback fixes depend on fault and observation: restart/healthcheck review, bounded dependency
deadlines and backoff, circuit breakers, pool/admission limits, safe Redis fallback or broker
outbox/retries. No self-heal observed does not prove a missing restart policy. No injected
fault produces a readiness/measurement diagnostic, not a resilience finding. Every item and
summary records its source/fallback reason in analysis.json; run.json preserves the overall
analyzer source and all distinct analyzer_sources. A stopped live sequence uses fallback
analysis after cleanup rather than starting new model calls.

## Stored reports and replay

```sh
chaosai report runs/<real-run-id>
open runs/<real-run-id>/report.html
# With Wi-Fi off:
chaosai replay runs/<real-run-id>
open runs/<real-run-id>/report.html
```

`report` reads saved JSON/JSONL without LLM, target or dotenv access and regenerates HTML and
Markdown. Jinja autoescape is on for all text, including logs, reasons, findings and JSON.
HTML uses inline CSS/SVG only. Cards follow plan rank and combine spec/hypothesis, verdict,
penalties, recovery/cleanup evidence, events, up to 100 bucket mean-latency sparkline, finding
and fixes. Baseline thresholds, available queue observations, requested/final decision trace,
clamps, scoring method and run/source metadata are included. Samples/report outputs cannot
escape their run directory; CLI destinations must stay inside chaos-ai.

RunMeta.data_source is fixture or measured (default fixture); the live orchestrator explicitly
sets measured. Banner follows data_source independently of offline. Legacy Phase 8 records
with offline=false and explicit measured/reused baseline provenance render as measured;
ambiguous older records retain the conservative fixture default.

`replay` has no transport instance and reads only exact hash-matched, pydantic-validated
Finding/Summary caches from the run's llm directory. Missing/malformed/semantically invalid
responses use deterministic fallback. Planner/plan/decisions, baseline, samples and original
experiment results remain unchanged. Recomputed analysis, scores, summary, metadata and
reports replace their own derived artifacts. Replay never re-resolves a model or injects.
Changed prompts/evidence or old Phase 1 analyzer caches may no longer match; fallback is
explicitly recorded rather than making a network request or claiming cached AI provenance.

Offline demonstration:

```sh
python -m chaosai run --offline --yes
python -m chaosai report runs/<printed-fixture-run-id>
python -m chaosai replay runs/<printed-fixture-run-id>
```

These checks establish fixture/render/replay behavior only. The user-supplied Phase 8 run
20261011-014810 applied zero faults after auth 429s; Phase 9 analysis cannot turn that skip
into a successful experiment, recovery or score. A clean auth window and baseline, >=4 live
experiments across >=3 fault types, smoke and the deferred Phase 6 checks remain pending.
