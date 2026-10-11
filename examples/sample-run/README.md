# Measured sample — verified local sandbox

Source UTC run: **20261011-035048**, produced by a successful complete
`bash scripts/clean_run.sh` execution on 2026-10-11. Setup, doctor, DB-verified seed,
pre/post-run smoke, four applied experiments, cooldown, rollback and HTTP recovery passed.
Baseline was stable, all journey samples were nonempty and request errors were zero.
Every undo record is done; the run has no stop reason. Original private evidence is retained.

| Fault / target | Outcome | Measured recovery | Rollback |
|---|---|---|---|
| kill / auth-service | Watchdog abort: login errors | 10s | Verified |
| traffic_spike / browse | Hypothesis confirmed | 6s | Verified |
| pause / postgres | Watchdog abort: aggregate errors | 10s | Verified |
| stop / postgres | Watchdog abort: checkout errors | 14s | Verified |

A watchdog abort is successful safety behavior, not a healthy application outcome. Its sparse
journey samples remain descriptive and cannot confirm a resilience hypothesis. Four fault
types were actually applied and measured. Deterministic score: **55/100, weak**; this is an
assessment of these experiments only, not exhaustive application coverage.

Planner used heuristics after Gemini transport failure. One experiment received validated
Gemini analysis; the remaining experiment/summary calls used clearly recorded deterministic
fallback. The report retains these sources and limitations. The separate AI planner probe
is documented in progress.md without substituting its proposals for executed faults.

[HTML findings report](report.html), [Markdown report](report.md), graph.json, baseline.json
and decisions.json are the five sample artifacts. provenance.json records source/run metadata
and original/copied SHA-256 hashes. Known-secret and token/credential-URL scans passed.
No dotenv, credentials, raw samples, raw logs or LLM caches are included.

Results assess the **owned local auth sandbox**: configurable limiter at 6000/15min,
verified credential proof cached in process memory for at most 600s. Third-party source is
unchanged; the reviewable patch and restoration commands are in ../../patches/README.md.
Baseline measurement version 2 and the declared absolute latency allowances are retained.
Historical failed baselines/runs were not rewritten or promoted into this deliverable.
