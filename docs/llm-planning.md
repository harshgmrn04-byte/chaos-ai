# LLM transport and planning — Phase 7

`chaosai/llm.py` provides synchronous `complete_json(task, system, user, model_cls)`.
Gemini uses the installed official google-genai SDK's `Client.models.list()` and
`generate_content(model=..., contents=..., config=...)`, with JSON MIME type and the
pydantic class as `response_schema`. The SDK API surface was inspected locally; no model
or network probe was run by the coding agent. No new dependency was added.

## Providers and runtime model resolution

Configuration belongs to config.py. `LLM_PROVIDER` accepts gemini, mock or none. Without
an explicit provider, a Gemini/Google key selects Gemini, otherwise none. Keys are obtained
only from process environment in code, are wrapped in SecretStr and are never persisted
or logged. CLI configuration can load the human's dotenv file; offline skips it entirely.
The transport's standalone entry point reads process configuration without loading dotenv.
`LLM_TIMEOUT_S` defaults to 60 seconds per SDK request. The SDK's automatic retries are
disabled so our retry budget is explicit.

`GEMINI_MODEL` overrides discovery. Otherwise the provider uses the persisted model in
`.chaosai/llm.json`, or lists models supporting generateContent. It excludes embedding,
imagen, veo, tts, live, image, audio, robotics and gemma names. Stable models precede preview/
experimental models; within each group it prefers flash, then flash-lite, then pro, ranking
numeric versions highest first. It probes at most four candidates with validated tiny JSON,
trying the next candidate on not-found/permission failures. The winner is cached. A later
404 causes one re-resolution excluding the stale candidate, then failure falls back.
An explicit override is probed; an inaccessible override fails with a configuration hint.

429/503 calls back off 2, 4 and 8 seconds, then fail. Invalid structured JSON retries once;
continued invalid output raises LLMUnavailable. A rejected response schema falls back to JSON
mode with the schema in the prompt and the same pydantic validation afterward. SDK error
payloads are not displayed. Client cleanup and all callers' deterministic fallbacks remain
independent of model availability.

Validated responses are cached in the run's llm directory by SHA-256(task, system, user).
Cache hits are revalidated. Malformed responses are retained with invalid-attempt suffixes;
model resolution probe responses have separate hashes. No credentials are part of prompts or
cache keys. The none provider never reads a response cache or opens a client. Phase 9 replay
has a separate cache-only reader that never instantiates the transport; report regeneration
reads saved artifacts without LLM calls. See [analysis.md](analysis.md).

## Planning and trace

`PlannerOutput` contains flat `ExperimentProposal` objects; graph, baseline summary,
catalogue and max-eight/min-three constraints are JSON quoted as explicitly untrusted data.
The prompt instructs the model to ignore instructions within evidence/names/log/HTTP text.
LLM output has no Docker tools or executable commands. The flat fault field accepts unknown
strings so semantic validation can drop one unsupported proposal while retaining others.
Invalid JSON/data shapes instead trigger retry then a complete deterministic fallback.

The prompt also receives curated `source_facts` with source paths and the inspected target
revision from target-notes.md. These are source observations, not runtime measurements;
they are included only for the named target app and nodes present in its graph. Redis has
no unavailable-cache fallback, orders persist before broker publication, and PostgreSQL is
a shared container dependency. The prompt distinguishes these observations from hypotheses
and rejects claims of proven overload capacity based only on baseline statistics.

For Redis/RabbitMQ/PostgreSQL experiments and traffic spikes, final displayed reasons use
deterministic factual templates. An unsupported AI reason therefore cannot survive in these
plan rows, even if the model ignores the prompt. Each correction is recorded as a `reason`
Clamp, preserving original AI text in requested.reason and the corrected text in final.reason,
with source evidence or the capacity-claim limitation explaining the change. This applies
to heuristic planning too; other targets retain model explanations and remain unverified.
The source packet is a snapshot to maintain if the target changes, not automatic source
discovery. Original synthetic run examples and historical saved plans are not rewritten.

Post-validation rejects unknown/external/non-injectable/containerless or ambiguous targets,
wrong node kinds, unknown/stretched faults, unmapped journeys, invalid ranks and empty reasons/
hypotheses. Traffic targets mapped baseline journeys or all, subject to worker policy.
Duplicate fault/target pairs are dropped. Heuristics select by fan-in, unknown criticality,
node kind and journey touch count; round-robin fault choices supply diversity. Selection
reserves room for missing types even when AI proposes eight of one type. Final ranks are
1..N in estimated low-to-high blast radius order (journey count, then dependent fan-in).
IDs are deterministic, so model-supplied strings cannot become artifact paths.

Every accepted spec passes S2's policy_gate. Duration, abort limits, CPU/traffic intensities
and worker budget preserve the existing deterministic bounds. Hypothesis allowances are
bounded to error [0,1] and p95 multiple [1,20]; inapplicable intensity is cleared, each with
a Clamp record. Baseline evidence is never modified. `plan.json` contains final specs;
`decisions.json` is a list of PolicyDecision objects with requested/final specs, reasons,
clamps and source = gemini:<model>, mock or heuristic. Heuristic top-ups retain their own
source; the run records the LLM source only if a validated LLM item survives.

If the graph/budget cannot provide three distinct types, the smaller plan is returned with
an explicit warning. An empty injectable graph yields an empty plan. `netem_delay` is
catalogued but not plannable (Phase 11 stretch). `cpu_limit` and `traffic_spike` have Phase 8 mechanisms; CPU execution may be refused
when the current CPU configuration cannot be restored exactly (see safety.md).

## CLI and phase boundary

The human runs these from chaos-ai after activating .venv and adding their key locally:

```sh
chaosai doctor --llm
chaosai plan
chaosai plan --offline
```

Doctor --llm adds a resolved-model/tiny validated JSON probe and PASS/FAIL plus a safe hint;
ordinary doctor makes no LLM call. Non-offline plan requires an existing saved baseline and
maps the target read-only, with no Docker client, load or HTTP measurement. It writes a new
run's graph, baseline, plan and decisions and prints ranked reasons, sources and clamps.
Offline plan uses the packaged graph/baseline and heuristics, skipping target and dotenv.
Planning against stable=false is allowed for inspection; it never changes readiness status.

Phase 8 live run uses the grounded planner and executes selected supported specs in rank order
through the measured runner, requiring cleanup and measured cooldown readiness between entries.
The complete plan/decisions and a source-facts snapshot are retained; run metadata records all
planner sources and approvals. Dry-run prints plan/decisions without fault execution. Offline run preserves the original three synthetic evidence
examples and their IDs/order; its mock/none providers cannot open a real Gemini client, even
when passed Gemini settings directly. Phase 9 adds analysis/scoring after experiment cleanup;
these stages never control injection, rollback or recovery.

## Deferred live evidence

The human explicitly authorized Phase 7 despite the Phase 6 verification checkpoint. Doctor
and smoke passed. Run 20261011-005504 was blocked by KILL. After explicit rollback --clear-kill,
20261011-005640 skipped injection because the saved baseline was unstable. The original
20261010-235955 baseline had 51 login requests, all HTTP 429; latest precheck requests succeeded.
Neither run applied a fault. Impact, application recovery, Watchdog abort, active-fault KILL,
Ctrl-C recovery and the completed Day-1 walkthrough remain pending. No threshold, saved
baseline, auth limiter or readiness check was weakened to bypass this evidence gap.

The human subsequently verified doctor --llm (Gemini JSON validated), live plan and offline
plan on 2026-10-11. All Phase 7 functional human checks passed. The explanation-grounding
follow-up uses mocks only and requires no additional live probe or waiting period.
By explicit user instruction, fresh-baseline and remaining Phase 6 live checks are deferred
to final verification at the end of Phase 10; they remain pending throughout implementation.
