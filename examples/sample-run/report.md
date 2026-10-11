# ChaosPilot — 20261011-035048

Measured evidence; container rollback does not establish HTTP recovery.

Started: 2026-10-11 03:50:48.372+00:00. Project: ecommerce-microservices. Planner: heuristic. Analyzer: gemini:models/gemini-3.8-flash.

Overall: 55.0/100 (weak).

Scores describe the recorded experiments; untested dependencies remain unknown.

Stop reason: none.

## Ranked weaknesses

- critical: Watchdog or operator ended the experiment (experiment-03). Fix: Add request deadlines and a circuit breaker for stalled dependencies.
- critical: Watchdog or operator ended the experiment (experiment-04). Fix: Add bounded dependency deadlines and retry with backoff; test reconnect recovery.
- high: Terminating the auth-service container triggered an immediate failure across login requests, causing the experiment to abort due to login error rate exceeding the 0.5 safety threshold. (experiment-01). Fix: Review container restart policy and configure automated healthchecks for auth-service.

## What held up

- experiment-02: Hypothesis held within the observed window

## Baseline

Created 2026-10-11 03:53:24.429+00:00. Stable=True (drift, not absence of errors).

- browse: n=3597, errors=0.0, p95=6.463483200059272ms, rps=29.975.
- checkout: n=92, errors=0.0, p95=26.073272950452523ms, rps=0.7666666666666667.
- homepage: n=194, errors=0.0, p95=13.655304401981994ms, rps=1.6166666666666667.
- login: n=72, errors=0.0, p95=272.59426409764274ms, rps=0.6.
- all: n=3955, errors=0.0, p95=11.158678498031827ms, rps=32.958333333333336.

| Threshold | Hypothesis | Abort |
|---|---|---|
| Error rate | 0.02 | 0.5 |
| p95 multiple | 2.0 | 10.0 |
| p95 increment (ms) | 100.0 | 750.0 |
| Non-target containers leaving running | — | > 3 |
| Minimum samples / consecutive breaches | 20 baseline and during per journey for verdict | 10 / 2 |

Latency breach exceeds max(multiple * baseline p95, baseline p95 + increment).
S9/recovery use max(1.5x baseline p95, baseline p95 + hypothesis floor); final card specs supply experiment limits.

## Experiments (plan rank order)

### 1. experiment-01 — kill / auth-service

Hypothesis: Journeys remain within baseline-derived hypothesis limits.. Reason: Test kill on auth-service: 1 dependents, 1 unknown edges, 1 mapped journeys; verify failure handling for this dependency..

Verdict: aborted. Skipped=False; .
Recovered=True; seconds=10.0; rollback=True; cleanup=True.

Score=49; E=40.0, L=3.360847923168886, R=0.0, B=2.5, A=5.0.

- APPROVE at 2026-10-11 03:53:54.403+00:00: Policy-clamped experiment awaiting approval.
- PRECHECK at 2026-10-11 03:53:54.594+00:00: Reconciling journals; measuring S9 readiness (up to 60s).
- PRECHECK at 2026-10-11 03:54:20.253+00:00: All journeys near baseline; sparse windows provide limited evidence.
- INJECT at 2026-10-11 03:54:20.254+00:00: Applying kill to auth-service.
- OBSERVE at 2026-10-11 03:54:20.407+00:00: Fault applied; load and one-second watchdog active.
- UNDO at 2026-10-11 03:54:35.521+00:00: login: error rate exceeds 0.5.
- RECOVER at 2026-10-11 03:54:35.674+00:00: Container restoration and HTTP recovery assessed separately.
- RESULT at 2026-10-11 03:54:51.071+00:00: rollback_ok=True; recovered=True; skipped=False.

- Evidence limit: Recovery requires every journey in three healthy windows (1–20s from baseline RPS).
- Evidence limit: Sparse (&lt;20) journey p95/error rates are descriptive; they cannot confirm a hypothesis.
- Evidence limit: Container restoration does not establish application/HTTP recovery.

Finding: high — Terminating the auth-service container triggered an immediate failure across login requests, causing the experiment to abort due to login error rate exceeding the 0.5 safety threshold..
Interpretation (root cause unproven unless cited source establishes it): Hypothesized failure of login requests due to unavailable auth-service process during container kill fault, with no redundant replicas or fast failover handling in place.

- During login: n=11, errors=1.000000, p95=2.905ms; baseline n=72, errors=0.000000, p95=272.594ms.
- Abort reason: login: error rate exceeds 0.5.
- Recovered=True; recovery_time_s=10.0; self_healed=False; rollback_ok=True; cleanup_ok=True.

- Fix: Review container restart policy and configure automated healthchecks for auth-service.
- Fix: Deploy redundant auth-service instances behind a load balancer to prevent single point of failure.
- Fix: Implement circuit breakers with bounded timeouts and retry/backoff policies for auth-service dependents.
### 2. experiment-02 — traffic_spike / browse

Hypothesis: Journeys remain within baseline-derived hypothesis limits.. Reason: Test whether browse stays within baseline-derived limits under bounded extra load; overload capacity is unverified..

Verdict: confirmed. Skipped=False; .
Recovered=True; seconds=6.0; rollback=True; cleanup=True.

Score=99; E=0.0, L=0.6090963451306963, R=0.0, B=0.0, A=0.0.

- APPROVE at 2026-10-11 03:55:13.526+00:00: Policy-clamped experiment awaiting approval.
- PRECHECK at 2026-10-11 03:55:13.721+00:00: Reconciling journals; measuring S9 readiness (up to 60s).
- PRECHECK at 2026-10-11 03:55:39.380+00:00: All journeys near baseline; sparse windows provide limited evidence.
- INJECT at 2026-10-11 03:55:39.382+00:00: Applying traffic_spike to browse.
- OBSERVE at 2026-10-11 03:55:39.414+00:00: Fault applied; load and one-second watchdog active.
- UNDO at 2026-10-11 03:56:09.613+00:00: Duration complete; restoring containers.
- RECOVER at 2026-10-11 03:56:09.614+00:00: Container restoration and HTTP recovery assessed separately.
- RESULT at 2026-10-11 03:56:20.737+00:00: rollback_ok=True; recovered=True; skipped=False.

- Evidence limit: Recovery requires every journey in three healthy windows (1–20s from baseline RPS).
- Evidence limit: Sparse (&lt;20) journey p95/error rates are descriptive; they cannot confirm a hypothesis.
- Evidence limit: Container restoration does not establish application/HTTP recovery.

Finding: low — Hypothesis held within the observed window.
Interpretation (root cause unproven unless cited source establishes it): Root cause is unproven; the recorded result describes an observation.

- Verdict: confirmed; skipped=False.
- During all: n=2392, errors=0.000000, p95=6.600ms; baseline n=3955, errors=0.000000, p95=11.159ms.
- During browse: n=2304, errors=0.000000, p95=5.022ms; baseline n=3597, errors=0.000000, p95=6.463ms.
- During checkout: n=22, errors=0.000000, p95=22.246ms; baseline n=92, errors=0.000000, p95=26.073ms.
- During homepage: n=45, errors=0.000000, p95=15.319ms; baseline n=194, errors=0.000000, p95=13.655ms.
- During login: n=21, errors=0.000000, p95=277.685ms; baseline n=72, errors=0.000000, p95=272.594ms.
- Recovered=True; recovery_time_s=6.0; self_healed=None; rollback_ok=True; cleanup_ok=True.
- Deterministic score: 99/100.

- Fix: Measure capacity; bound admission, queues and database pool concurrency.
### 3. experiment-03 — pause / postgres

Hypothesis: Journeys remain within baseline-derived hypothesis limits.. Reason: Source PostgreSQL container serves auth, product and order databases; test pause impact and recovery without assuming a measured outcome..

Verdict: aborted. Skipped=False; .
Recovered=True; seconds=10.0; rollback=True; cleanup=True.

Score=28; E=40.0, L=20.0, R=0.0, B=7.5, A=5.0.

- APPROVE at 2026-10-11 03:56:43.116+00:00: Policy-clamped experiment awaiting approval.
- PRECHECK at 2026-10-11 03:56:43.301+00:00: Reconciling journals; measuring S9 readiness (up to 60s).
- PRECHECK at 2026-10-11 03:57:08.994+00:00: All journeys near baseline; sparse windows provide limited evidence.
- INJECT at 2026-10-11 03:57:08.996+00:00: Applying pause to postgres.
- OBSERVE at 2026-10-11 03:57:09.020+00:00: Fault applied; load and one-second watchdog active.
- UNDO at 2026-10-11 03:57:23.142+00:00: all: error rate exceeds 0.5.
- RECOVER at 2026-10-11 03:57:23.193+00:00: Container restoration and HTTP recovery assessed separately.
- RESULT at 2026-10-11 03:57:38.425+00:00: rollback_ok=True; recovered=True; skipped=False.

- Evidence limit: Recovery requires every journey in three healthy windows (1–20s from baseline RPS).
- Evidence limit: Sparse (&lt;20) journey p95/error rates are descriptive; they cannot confirm a hypothesis.
- Evidence limit: Container restoration does not establish application/HTTP recovery.

Finding: critical — Watchdog or operator ended the experiment.
Interpretation (root cause unproven unless cited source establishes it): Root cause is unproven; the recorded result describes an observation.

- Verdict: aborted; skipped=False.
- During all: n=188, errors=0.186170, p95=4497.921ms; baseline n=3955, errors=0.000000, p95=11.159ms.
- During browse: n=159, errors=0.144654, p95=14.478ms; baseline n=3597, errors=0.000000, p95=6.463ms.
- During checkout: n=9, errors=0.777778, p95=5004.103ms; baseline n=92, errors=0.000000, p95=26.073ms.
- During homepage: n=15, errors=0.000000, p95=33.072ms; baseline n=194, errors=0.000000, p95=13.655ms.
- During login: n=5, errors=1.000000, p95=4002.992ms; baseline n=72, errors=0.000000, p95=272.594ms.
- Recovered=True; recovery_time_s=10.0; self_healed=None; rollback_ok=True; cleanup_ok=True.
- Deterministic score: 28/100.
- Abort reason: all: error rate exceeds 0.5.

- Fix: Add request deadlines and a circuit breaker for stalled dependencies.
### 4. experiment-04 — stop / postgres

Hypothesis: Journeys remain within baseline-derived hypothesis limits.. Reason: Source PostgreSQL container serves auth, product and order databases; test stop impact and recovery without assuming a measured outcome..

Verdict: aborted. Skipped=False; .
Recovered=True; seconds=14.0; rollback=True; cleanup=True.

Score=44; E=40.0, L=0.47462761170544154, R=5.833333333333333, B=5.0, A=5.0.

- APPROVE at 2026-10-11 03:58:02.452+00:00: Policy-clamped experiment awaiting approval.
- PRECHECK at 2026-10-11 03:58:02.601+00:00: Reconciling journals; measuring S9 readiness (up to 60s).
- PRECHECK at 2026-10-11 03:58:28.271+00:00: All journeys near baseline; sparse windows provide limited evidence.
- INJECT at 2026-10-11 03:58:28.272+00:00: Applying stop to postgres.
- OBSERVE at 2026-10-11 03:58:28.468+00:00: Fault applied; load and one-second watchdog active.
- UNDO at 2026-10-11 03:58:41.567+00:00: checkout: error rate exceeds 0.5.
- RECOVER at 2026-10-11 03:58:41.680+00:00: Container restoration and HTTP recovery assessed separately.
- RESULT at 2026-10-11 03:59:01.028+00:00: rollback_ok=True; recovered=True; skipped=False.

- Evidence limit: Recovery requires every journey in three healthy windows (1–20s from baseline RPS).
- Evidence limit: Sparse (&lt;20) journey p95/error rates are descriptive; they cannot confirm a hypothesis.
- Evidence limit: Container restoration does not establish application/HTTP recovery.

Finding: critical — Watchdog or operator ended the experiment.
Interpretation (root cause unproven unless cited source establishes it): Root cause is unproven; the recorded result describes an observation.

- Verdict: aborted; skipped=False.
- During all: n=425, errors=0.047059, p95=7.550ms; baseline n=3955, errors=0.000000, p95=11.159ms.
- During browse: n=384, errors=0.000000, p95=5.836ms; baseline n=3597, errors=0.000000, p95=6.463ms.
- During checkout: n=11, errors=1.000000, p95=28.548ms; baseline n=92, errors=0.000000, p95=26.073ms.
- During homepage: n=21, errors=0.000000, p95=12.941ms; baseline n=194, errors=0.000000, p95=13.655ms.
- During login: n=9, errors=1.000000, p95=20.782ms; baseline n=72, errors=0.000000, p95=272.594ms.
- Recovered=True; recovery_time_s=14.0; self_healed=None; rollback_ok=True; cleanup_ok=True.
- Deterministic score: 44/100.
- Abort reason: checkout: error rate exceeds 0.5.

- Fix: Add bounded dependency deadlines and retry with backoff; test reconnect recovery.

## Decision trace

- experiment-01 (heuristic): allowed=True; . Requested reason: Test kill on auth-service: 1 dependents, 1 unknown edges, 1 mapped journeys; verify failure handling for this dependency.. Final reason: Test kill on auth-service: 1 dependents, 1 unknown edges, 1 mapped journeys; verify failure handling for this dependency..
- experiment-02 (heuristic): allowed=True; . Requested reason: Test overload in mapped journey browse under bounded extra load.. Final reason: Test whether browse stays within baseline-derived limits under bounded extra load; overload capacity is unverified..
  - reason: Test overload in mapped journey browse under bounded extra load. -> Test whether browse stays within baseline-derived limits under bounded extra load; overload capacity is unverified. (Baseline latency/error statistics do not establish overload capacity.)
- experiment-03 (heuristic): allowed=True; . Requested reason: Test pause on postgres: 3 dependents, 3 unknown edges, 3 mapped journeys; verify failure handling for this dependency.. Final reason: Source PostgreSQL container serves auth, product and order databases; test pause impact and recovery without assuming a measured outcome..
  - reason: Test pause on postgres: 3 dependents, 3 unknown edges, 3 mapped journeys; verify failure handling for this dependency. -> Source PostgreSQL container serves auth, product and order databases; test pause impact and recovery without assuming a measured outcome. (Grounded in inspected source, not live verification: docs/target-notes.md#1-services-and-deployment; ecommerce-microservices/docker-compose.yml:3-18; ecommerce-microservices/docker-compose.yml:80; ecommerce-microservices/docker-compose.yml:98; ecommerce-microservices/docker-compose.yml:122)
- experiment-04 (heuristic): allowed=True; . Requested reason: Test stop on postgres: 3 dependents, 3 unknown edges, 3 mapped journeys; verify failure handling for this dependency.. Final reason: Source PostgreSQL container serves auth, product and order databases; test stop impact and recovery without assuming a measured outcome..
  - reason: Test stop on postgres: 3 dependents, 3 unknown edges, 3 mapped journeys; verify failure handling for this dependency. -> Source PostgreSQL container serves auth, product and order databases; test stop impact and recovery without assuming a measured outcome. (Grounded in inspected source, not live verification: docs/target-notes.md#1-services-and-deployment; ecommerce-microservices/docker-compose.yml:3-18; ecommerce-microservices/docker-compose.yml:80; ecommerce-microservices/docker-compose.yml:98; ecommerce-microservices/docker-compose.yml:122)

## Methodology

Score = clamp(100 - E - L - R - B - A, 0, 100), rounded to nearest integer.
E=40*min(1,max(0,worst error delta)/0.5); L=20*min(1,max(0,worst p95 ratio-1)/4).
R=0 within 10s; 25 if unproven; otherwise 25*min(1,recovery/60).
B=10*(impacted journeys/total baseline journeys, excluding all); A=5 for Watchdog abort.
Overall is mean of scored experiments; skipped and absent measurements are excluded.
Zero baseline p95: positive during p95 saturates L; zero during p95 gives L=0.
Sparse/inconclusive scores are descriptive. Partial runs do not establish full app resilience.
Bands: >=85 resilient, >=70 acceptable, >=50 weak, otherwise fragile.
Severity: critical below 30 or aborted with >=2 impacted journeys; high below 60; medium below 80; low otherwise.
Confirmed score >=80 belongs under What held up. AI cannot change scores, severity or ranking.