# Three-minute recording plan

Maximum recording: **2:55**. The recording can combine clearly labeled segments from verified
runs; preparation, full baselines, recovery waits and auth windows happen outside recording.
Never imply these long operations complete in three minutes. Record only actual measured
results; if verification is incomplete, narrate the limitation and do not stage a success.
The video and measured sample are still pending. [Final verification](final-verification.md)
lists what must be established before recording.

## Before recording (not in the three minutes)

Activate .venv, configure existing valid credentials privately and keep the target running.
Use one auth/load command at a time. Capture a fresh error-free stable baseline and the
remaining safety experiments, allowing fresh auth windows as needed; waiting is not a pass.
Do not change thresholds/limiter/CPU originals or clear KILL implicitly. Preserve full run IDs,
result files and journal evidence. Store an eligible measured run with >=4 completed
experiments across >=3 fault types; open its report manually and verify cache-only replay with
Wi-Fi off. Read findings for unsupported causal claims. No eligible run is currently supplied.

Commands to capture outside recording (each live command requires its own readiness/budget):

```sh
source .venv/bin/activate
chaosai doctor
chaosai map --mermaid
chaosai baseline
chaosai plan
chaosai run --max-experiments 4 --reuse-baseline
chaosai inject stop redis --duration 30
chaosai inject stop redis --duration 30 --abort-error-rate 0.05
# Separate active-fault KILL segment, terminal A:
chaosai inject stop redis --duration 120 --yes
# Terminal B, only after OBSERVE plus a pending journal record:
chaosai kill
# After A exits and undo is inspected, explicitly:
chaosai rollback --clear-kill
```

Replace `runs/<verified-run-id>` below with the real stored run. `inject` writes result evidence;
use the full automatic run for the report. The strict request does not guarantee an actual
Watchdog abort. KILL with zero pending records is not an active-fault demonstration. If an
experiment skips or recovery remains unproven, narrate that outcome and keep verification pending.

## Timed narration and visible evidence

| Time | Screen / action | Narration supported by evidence |
|---|---|---|
| 0:00–0:20 | Architecture diagram and scoped doctor PASS capture | “ChaosPilot tests our local Compose sandbox. The planner proposes data; deterministic code approves, journals and controls faults.” Mention timestamp/run ID of saved footage. |
| 0:20–0:40 | `chaosai map --mermaid` graph and saved plan reasons/trace | “These flows are components touched, and these reasons are hypotheses grounded in source and baseline. External services are excluded.” |
| 0:40–1:05 | Recorded `chaosai run --max-experiments 4 --reuse-baseline` approval prompt; show typing y | Read the actual fault/target/duration, hypothesis, abort limits and clamps. “This prompt precedes readiness and injection.” Mark it as a segment of the full run. |
| 1:05–1:30 | Same run's OBSERVE event, measured during statistics and subsequent recovery result | State only actual counts/errors/p95 and measured recovery seconds. “Undo restores the container; recovery is measured HTTP evidence.” If unproven, say so. |
| 1:30–1:55 | Separate strict Watchdog segment and/or active KILL footage | Show exact abort reason, pending-before/done-after journal and restored scoped state. Label different run IDs. Never claim Watchdog when the operator ended it. |
| 1:55–2:35 | Actual measured report Summary/cards/thresholds/decision trace | Read actual score and coverage, top weakness, cited evidence and concrete fix. Show one card's E/L/R/B/A and time/latency units; explain that skipped entries are unscored. No fixture-to-target claims. |
| 2:35–2:55 | Wi-Fi off, `chaosai replay runs/<verified-run-id>` and report | “This recomputes from stored evidence and matching validated caches; missing responses use deterministic fallback. No fault is replayed.” Show printed source/fallback evidence when relevant. |

Use genuine footage or visibly labeled saved artifacts. Do not hide S9 skips, unproven
recovery, stopped sequences or synthetic banners. If only fixture evidence is available,
make a clearly labeled offline implementation walkthrough; it does not satisfy the measured
impact/safety demo requirement. Show no private environment, keys, tokens or raw target logs.
Record with macOS Cmd+Shift+5; check duration, audio, report readability and playback before
submission. The public repository link, deployment instructions, architecture and findings
report must also open independently of the video.
