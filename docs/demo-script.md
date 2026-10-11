# Judge demo: app → AI → failure → recovery → findings → dashboard

Prepare the app, a measured baseline and the saved full run before speaking. Full reproduction
was verified at `20261011-035048`: four fault types, four measured recoveries, score 55/100.
Fresh Gemini plan: `20261011-040607`. The submitted report truthfully records mixed analysis.
This walkthrough follows the requested order. Use a live single fault plus the saved full
run for breadth; do not wait for an entire four-experiment baseline/batch during the talk.

## Prepare once

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai doctor
chaosai smoke
# Dashboard is already running; restart only if needed:
chaosai dashboard
```

Open `http://localhost:3000`, the terminal, `http://127.0.0.1:8765/reports/20261011-035048`
and the dashboard. On dashboard, uncheck Follow latest and choose `20261011-035048`
when showing the completed full run. Enable Follow latest for current live evidence.
If a fault job is already active, finish it first. Never run parallel load commands.

## Seven-minute judge walkthrough

| Time | Show / do | Say |
|---|---|---|
| 0:00–0:45 | App at localhost:3000: browse products; show passing smoke | “This is our healthy, local ecommerce sandbox: ten Compose services, real browse/login/checkout journeys.” Smoke proves a pending order, not payment completion. |
| 0:45–1:30 | Saved AI plan `runs/20261011-040607/plan.json` and decisions.json; architecture | “Gemini proposes ranked, source-grounded experiments. Deterministic Python validates scope and policy. External providers cannot receive faults.” |
| 1:30–3:15 | Terminal: `LOGIN_RATE_CAP=1 chaosai inject stop redis --duration 30 --abort-error-rate 0.05`; approve y | Read target, duration, hypothesis and limits. While it runs, reload the product page to show impact. Read actual verdict/abort reason and rollback/recovery result; do not promise a breach before measurement. |
| 3:15–4:00 | App refresh and passing smoke, or show measured recovery evidence | “The watchdog detects trouble and restores the journaled fault. Readiness, recovery and cooldown decide whether later experiments can proceed.” Automatic remediation here restores the failed service; application code fixes are recommendations. |
| 4:00–5:15 | Completed measured report: 55/100, four fault types, ranked weaknesses, fixes | Show a PostgreSQL outage card: checkout failures and 10s/14s recovery. Show traffic spike holding up. Explain score is deterministic, AI explains cited evidence, fallback survives unavailable AI. |
| 5:15–6:40 | Dashboard LAST: Overview → Experiments → Findings → Service map | Show p95/errors, phase markers, CPU/memory/restarts/queue, timeline, recovery and severity ranking. Inspect the same run ID as the report. Missing telemetry is shown unavailable. |
| 6:40–7:00 | Finish on dashboard/report | “We safely expose dependency weaknesses, restore the sandbox and provide evidence-backed fixes. Reports replay without live AI or new faults.” |

If baseline freshness has expired, measure `chaosai baseline` before the talk. The local auth
sandbox removes the original repeated 15-minute wait; it retains authentication with a
6000-request local budget. Do not claim automatic source-code repair or online AI re-planning
between experiments: runtime adaptation consists of deterministic safety/readiness/recovery
checks. A skipped fault is a skip. KILL ends sampling; inspect undo and check HTTP separately.

## Three-minute submission video

Use the same order condensed to 2:55: healthy app 20s, AI/map 25s, labelled live fault clip
45s, detection/rollback/recovery 25s, measured report/ranking 35s, dashboard 25s. Capture
preparation and long waits outside the video; label clips with actual run IDs. Record with
macOS Cmd+Shift+5, then verify sound, duration and playback. The video and submission form
remain human deliverables. Never display credentials, private env files or raw logs.
