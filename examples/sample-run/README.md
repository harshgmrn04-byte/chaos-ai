# Measured sample deliverable — pending

No eligible completed real run exists in the inspected workspace as of 2026-10-11.
Do not substitute synthetic fixtures or skipped experiments for a successful measured sample.

Inspection covered every existing runs/*/run.json with offline=false and its result files:

| Run | Provenance / result | Eligibility |
|---|---|---|
| 20261011-014504 | Measured dry-run; no result.json files, no faults | Ineligible |
| 20261011-014810 | Measured; one skipped kill entry, zero during-fault samples, empty undo journal, recovered=false/recovery_time_s=null; failed cooldown | Ineligible |

The latter baseline's stable=true is drift stability; login errors were 21/47, with auth
HTTP 429s in all 26 precheck and 25 cooldown login samples. Smoke failed at admin login,
but its response status was not captured. Historical baseline 20261011-011425 remains
stable=false (32.28% checkout p95 drift vs unchanged 30% bound). None was edited.
Standalone inject attempts 20261011-005504 and 20261011-005640 also have zero during-fault
samples and no full run report (the latter is skipped); neither qualifies.
Synthetic run 20261011-020747 has fixture scores 53/17/95, overall 55 (weak); those are not
an assessment of the target and are not copied here.

After [final human verification](../../docs/final-verification.md) establishes a representative
completed measured run, the outstanding submission is report.html, report.md, graph.json,
baseline.json and decisions.json in this directory. Also include a provenance note naming the
original UTC run ID, source revision, run/source metadata, checks, copied-file hashes and
remaining limitations. Inspect/redact any credential-bearing text before publishing; retain
original private evidence unchanged. No raw samples, logs, .env, keys or caches belong here.
Keep original results/journals locally for audit. Copy only after actual injection, adequate
measured evidence, rollback/cleanup, recovery and run-diversity checks are established.

The five report files and real demo video remain pending, not passed. This status file is the
truthful Phase 10 deliverable until measured human evidence qualifies; it does not complete
the final findings-report submission requirement.
