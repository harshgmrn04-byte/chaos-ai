# ChaosPilot dashboard

From chaos-ai with the virtual environment active:

```sh
chaosai dashboard
```

Open http://127.0.0.1:8765. Keep this terminal running. The server binds to loopback only;
DASHBOARD_HOST/DASHBOARD_PORT or --host/--port configure it. No frontend build, CDN,
separate Node process or additional dependencies are required.

## Judge walkthrough

1. Overview: select a measured run; show baseline health, applied fault types, recovery and
   resilience score. The latest HTTP error rate is explicitly the last recorded time window.
2. Experiments: select a row to see its p95/error charts, recorded INJECT/UNDO/RECOVER events,
   hypothesis, reason, during metrics, verdict and recovery evidence.
3. Findings: show ranked weaknesses, evidence-based explanations, recommended fixes and the
   analyzer source. Gemini is optional; deterministic fallback remains clearly labelled.
4. Service map: show application/dependency flows. External nodes are protected.
5. Open report: inspect the self-contained report and its deterministic scoring breakdown.

The display refreshes every two seconds. A running experiment's HTTP samples, events,
container observations and RabbitMQ depth appear as files are appended; findings appear
when analysis finishes. An inactive run shows its recorded evidence, not current traffic.
Run history separates measured runs and synthetic fixtures. Disable Follow latest to keep
one run selected during a presentation. Missing telemetry remains unavailable.

## Controls

Prepare sandbox builds the owned local auth image and retains a bounded limiter.
Measure baseline and Check app perform real traffic; checkout creates local orders.
Run 4 experiments and single injection show an explicit approval dialog. Approval covers
that displayed batch and uses the existing CLI --yes path. Cancel applies no fault.
Only one job runs at a time; external CLI load/injection also blocks dashboard jobs.
KILL & roll back remains available during an active fault. KILL persists after cleanup.
Review the journals and restored services, then explicitly choose resume after the runner
exits. Roll back alone does not clear KILL. Closing the server requests SIGINT only for
its own child; normal journal rollback and the independent duration timer remain active.

Fixture walkthrough produces clearly marked synthetic data. It is useful without Docker or
Wi-Fi, but is never evidence of target resilience. Replay of a saved measured run keeps its
original provenance and performs no injection.

The API is read-only except approved /api/jobs and /api/control requests. Host/Origin/CSRF
checks guard changes. No arbitrary-file API, shell command input, dotenv, LLM cache or raw
container-log endpoint is exposed. Use the local CLI for unsupported operations.
