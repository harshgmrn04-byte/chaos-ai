# ChaosPilot progress

- [x] Phase 0 — Target reconnaissance
- [x] Phase 1 — Scaffold and walking skeleton
- [x] Phase 2 — Bring-up, seed, smoke test, payment gate, doctor
- [x] Phase 3 — Application mapper
- [x] Phase 4 — Load generator, metrics, baseline
- [x] Phase 5 — Faults, safety layer, watchdog, kill switch
- [ ] Phase 6 — Experiment runner and Day-1 demo
- [ ] Phase 7 — LLM layer and AI planner
- [ ] Phase 8 — Extended faults and automatic run
- [ ] Phase 9 — Analyzer, scoring, report
- [ ] Phase 10 — Freeze and polish
- [ ] Phase 11 — Optional stretch

## Phase 0 — 2026-10-11

Built `docs/target-notes.md` with all ten requested sections, eleven endpoint contracts, source paths/line evidence, deployment/runtime details, and explicitly labelled hypotheses. Inspected target revision `bae94402773bc7fd7d05dadd4062dffe2d1c2057`; no target writes, Docker commands, network calls, dependency installs, `.env` reads, or feature code.

Key findings: this checkout already bypasses Stripe for placeholder keys; orders persist before broker publication and can remain after HTTP 500; the consumer queue is `notifications`; Redis has no application fallback; email uses Resend rather than MailHog, and caught send failures can be stored as sent. Register defaults to USER; README promotion only updates an existing user and does not seed a catalog.

Assumptions: documentation describes checked-in source only. No assumptions about live environment, data, dependency timing, payment completion or email delivery. Open questions and the Phase 0 human verification list are in target-notes section 10. No later phase started.

Verification: Phase 0 source-only completeness check passed (10 sections, 11 endpoint contracts each citing routes/controllers, explicit source references checked for file existence and line bounds, checklist for Phases 0–11 with only 0 checked). Documentation whitespace check passed. Target tracked working tree was clean before and after recon. `ruff check .` and `pytest -q -m "not integration"` were attempted from chaos-ai but could not run: both commands are unavailable (exit 127). No tooling/dependencies or tests were added for this documentation-only phase; the Phase 1 scaffold is still pending. Human check from BUILD_PLAN.md: skim the checkout and Stripe sections; note anything surprising.

## Phase 1 — 2026-10-11

Built the Python >=3.11 package scaffold, bootstrap script, console/module entry points,
environment defaults, all section 4 pydantic v2 contracts and typed nested structures.
Added timestamped run directories, atomic/fsynced JSON replacement and append-only JSONL IO.
Mock JSON responses are validated, retried once on invalid data and cached by task/prompt hash;
`none` and the pending Gemini provider raise `LLMUnavailable`, with deterministic fixture
fallbacks in the planner and analyzer. Offline forces `none` and skips dotenv loading.

The walking skeleton runs map -> baseline -> plan -> per-experiment approval -> simulated
results -> analysis -> fixture scores -> report. Bundled graph data follows target-notes
(all 10 Compose services, 3 non-injectable external services, auth host port 3004, the
`notifications` queue, Resend rather than MailHog on the email path). Three synthetic
experiments cover Redis stop, PostgreSQL pause and product-service CPU limitation.
The HTML report uses inline CSS and escapes untrusted text; report regeneration reads stored
artifacts. Reports explicitly identify all simulated evidence. README documents the current CLI.

Approval is real: y/n/a/q, invalid-answer retry, all-remaining state, clean quit on EOF/Ctrl-C
at the prompt, and refusal without TTY unless `--yes`. Skipped/quit experiments contribute
no results, findings or scores. `--dry-run` writes the plan/report without execution or
approval. No completed experiments means "not assessed" rather than a resilience score.
All 14 other commands, including replay, print "not implemented yet" and exit 2.

Verification: `bash scripts/bootstrap.sh` passed using installed Python 3.14 (the default
shell `python3` is 3.9); the initial sandbox install could not resolve PyPI, and the authorized
network retry succeeded. `source .venv/bin/activate`; `ruff check .` passed;
`pytest -q -m "not integration"` passed (51 tests); `python -m chaosai run --offline --yes`
passed and created `runs/20261010-220932/report.html` (run IDs use UTC). `chaosai --help`
passed and lists all 16 commands. Tests cover every model's JSON round-trip, invalid contracts,
artifact IO and collision handling, mock validation/caching, configuration, stage order,
approval choices/refusal, dry runs, report regeneration/escaping and all CLI placeholders.
Unit tests block network and dotenv reads; temporary test writes stay under chaos-ai/tests/.

Assumptions and known gaps: metrics, verdicts, findings, policy decisions, scores and recovery
are synthetic fixtures, not live measurements. Fixture penalties illustrate section 7; the
real scorer is deferred to Phase 9. No real mapper, load, policy gate, journal undo actions,
injector, watchdog, rollback, Docker access or Gemini calls exist yet. `journal.json` stays
empty because nothing is injected. Phase 2 has not started; Phase 0 runtime questions remain
unresolved. The pre-existing tracked `.DS_Store` modification is left out of the commit.

**Phase 1 "You verify" (copied from BUILD_PLAN.md):** open the generated `report.html` in a browser.


## Phase 2 — 2026-10-11

Implemented robust Docker SDK resolution: DOCKER_HOST (honoring SDK environment/TLS settings),
active CLI context endpoint, Docker Desktop's user socket, then the system socket. Each
candidate must ping; failures produce a credential-free fix hint. Container discovery filters
and rechecks exact COMPOSE_PROJECT labels; service access rechecks after reload and rejects
missing/ambiguous/stopped/paused targets. Added TCP and documented HTTP readiness probes.

Implemented doctor (PASS/FAIL table with fix hints, Python, Docker, ten target services,
health/paused state, all published host ports, catalog HTTP probes, LLM key presence and .env
existence), scoped status, and up (explicit project/file, target cwd, up -d --build, bounded
execution, withheld potentially sensitive build output). Relative TARGET_DIR resolves from
chaos-ai rather than the invoking terminal directory.

Seed uses scoped PostgreSQL to read only existing email identities, avoiding duplicate-register
errors that can hang under this target's async error forwarding. Missing admin/load accounts
are registered through the documented API (20 load users by default; --users/SEED_USERS
configurable). The exact documented SQL promotes admin, followed by login for a fresh ADMIN
token. Public category/product lists, including product pagination, identify existing slugs;
admin APIs create two categories and four no-upload, stock-100000 products. No account reset,
password-hash reads, Cloudinary uploads or undocumented product-update route. Existing seed
product stock/category conflicts fail with a precise hint. Run seed serially, not concurrently.

Smoke browses, logs in, creates one pending order with paymentMethod=stripe and retrieves it,
validating identity, owner, items and totals (shipping boundary is strictly subtotal >100).
Checkout is never retried automatically because HTTP 500 can follow persisted orders. Best-effort
notification evidence checks notifications (routing key order.created) identity/consumer count,
ready-count trend against baseline/preceding sample and total counts through RabbitMQ management;
three post-order observations six seconds apart allow for default statistics refresh. Bounded
scoped notification-service logs must contain the routing key and this order ID. Missing or
unavailable evidence produces warnings without failing successful checkout. Receipt does not
prove ack/actual Resend email delivery; no MailHog check.

Added target API/result models in models.py, environment defaults/examples, README usage and
human integration commands. Added target-notes section 11 separating official RabbitMQ API
contract from target configuration/source facts and observation-policy decisions. Consulted only
rabbitmq.com: HTTP API reference, rabbitmqctl manual and management documentation (including
3.13's default statistics interval). Credentials/port use the cited Compose defaults, with env
overrides. No .env or real API key was read, printed or committed; no real LLM used.

Verification: source .venv/bin/activate; ruff check . passed; pytest -q -m "not integration"
passed (96 tests; two integration tests deselected). Unit tests forbid sockets, subprocesses,
real Docker constructors and dotenv reads unless explicitly mocked. Coverage includes endpoint
precedence and failure cleanup, scope/refused foreign/paused containers, Compose argv/cwd,
readiness/doctor flags and fix hints, request/response shapes, seed twice/partial seeds/promotion,
catalog pagination/stock guards, queue encoding/auth/metrics/consumer and ready trends,
log correlation/absence, checkout failure without retry, shipping boundary and credential-free
errors. All pydantic contracts round-trip. CLI and seed --help passed; git diff --check passed.
The target tracked/untracked working tree is pristine according to read-only git status.
The pre-existing tracked .DS_Store modification in chaos-ai is excluded from this phase commit.

Known gaps: live Docker/HTTP/checkout and image compatibility are unverified, as requested.
No Docker command (including context inspection), live app request or integration test was run.
No payment patch/scripts were created: source already bypasses placeholder keys and no live
smoke failure has demonstrated a need. Existing admin must use its current password through
SEED_PASSWORD; auth rate limits may require a 15-minute wait for unusually large/repeated seeds.
RabbitMQ's current reference is v4.3 while the target image is v3; the used queue metrics remain
subject to the human live check. The fixture runner remains Phase 1; no later phase implemented.

**Phase 2 "You verify (Docker)" (copied from BUILD_PLAN.md):** `chaosai doctor`, `chaosai seed`,
`chaosai smoke` all green. If smoke fails on payment, apply the patch flow, rebuild order-service,
re-run smoke.

Exact live commands (after configuring chaos-ai/.env yourself and starting Docker Desktop):

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai up
chaosai status
chaosai doctor
chaosai seed
chaosai seed
chaosai smoke
pytest -q -m integration
```

Wait for target services to become ready and re-run doctor if startup is still in progress.
The second seed must create no additional accounts/catalog entries. The integration checkout
creates one additional order. Payment patch flow is conditional on a diagnosed live payment
failure; no patch currently exists to apply.

### Phase 2 auth correction — 2026-10-11

Human live diagnostics showed Login admin crashed/restarted auth-service with Invalid credentials.
The initial seed checked account existence but assumed its configured password matched an existing
admin. Fixed this within Phase 2; no target changes, new Python dependencies or later-phase work.

Each account now gets a fresh scoped PostgreSQL existence check before registration; existing
accounts are never registered again. A bounded read-only Node helper in the scoped auth-service
uses the target's installed Prisma/bcrypt to compare configured credentials before login or
reuse of an existing seed account. Only existence/match booleans leave the container; password
hashes stay inside it. Candidate passwords use transient exec environment values rather than
command arguments/files/log text. Failure to verify refuses auth HTTP calls. ADMIN_PASSWORD
supports an existing admin independently from SEED_PASSWORD for load users; unset falls back
to SEED_PASSWORD. Existing passwords are never reset. New-account passwords are checked locally
for the documented minimum length. Auth HTTP requests use a 4-second timeout, configurable
only within 3–5 seconds.

Lost/timed-out registration/login responses trigger fresh DB and credential checks. A registration
that persisted can be safely reused with an explicit warning; absent users get a readiness/rate
hint. A failed login is not retried and only sends a password-mismatch hint when the DB/bcrypt
recheck actually confirms a mismatch, otherwise a service/readiness/rate-limit hint. Concurrent
account/password changes are reconciled rather than guessed. Smoke shares the exact credential
preflight and requires scoped DB/auth access before attempting login. Queue/log evidence remains
best-effort after checkout; no unverified credential fallback.

After seed, independent PostgreSQL queries verify all requested identities, admin ADMIN role,
and total/seed/high-stock product counts in ecommerce_products. CLI reports these counts.
Added the Known target quirks note and configuration/README instructions. Source facts cited
for the comparison algorithm, installed dependencies/workdir and product schema; human-supplied
runtime evidence is explicitly distinguished from agent checks.

Verification: ruff check . passed; pytest -q -m "not integration" passed (117 tests, two integration
tests excluded). Added MockTransport/DB regressions for wrong existing admin/load passwords (no
HTTP auth), distinct admin/load credentials, missing users, dropped/timeout login with state
rechecks, dropped registration before/after commit, concurrent duplicate registration disconnect,
no duplicate calls on rerun, post-seed DB count/user/role failures despite HTTP success, refused
foreign auth containers, failed credential helpers, short new-admin passwords and timeout bounds.
Every new model round-trips. Node --check passed for the helper's syntax without executing it.
Whitespace checks passed; read-only git status confirmed the target tree pristine. No Docker
command/live HTTP/integration test/real LLM call was run, and no .env or real secret was inspected.
Pre-existing .DS_Store change stays outside the fix commit.

Open issues: human must set ADMIN_PASSWORD to the existing admin's actual password and retain
SEED_PASSWORD for existing load users. Live helper compatibility and end-to-end checkout still
require the commands below. The helper depends on the target's installed Node/Prisma/bcrypt;
if they are unavailable, preflight stops with an actionable error rather than probing unsafe
HTTP paths. No payment patch is justified by these auth diagnostics.

**Phase 2 "You verify (Docker)" (copied from BUILD_PLAN.md):** `chaosai doctor`, `chaosai seed`,
`chaosai smoke` all green. If smoke fails on payment, apply the patch flow, rebuild order-service,
re-run smoke.

Exact re-verification commands (after editing .env locally; target already running):

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai doctor
chaosai seed
chaosai seed
chaosai smoke
pytest -q -m integration
```

Second seed should create no additional accounts/catalog entries and report DB verification
counts again. Both smoke and integration checkout create an order. No target rebuild is needed
for this ChaosPilot-only fix.

## Phase 3 — 2026-10-11

Replaced the mapper stub with deterministic Compose parsing: all services/container names,
image kinds, numeric short/long ports and ranges, list/dict depends_on/environment, build args,
and env_file declarations. Literal URL authorities/plain hosts resolve service/container names
or published localhost host ports; ambiguous/interpolated references are not guessed. Edges
combine field-path evidence without credentials/raw values, refine startup unknown kinds via
URL schemes, and leave criticality unknown. Stripe/Cloudinary/Resend names create non-injectable
external nodes, protected against override reclassification. MailHog remains an isolated tool.

Added pydantic override contracts in models.py, full-node/edge upserts, removal/incident cleanup,
criticality selectors and named flow merges with endpoint/flow/external validation. Shipped
overrides.yaml defines browse/login/checkout participants, including MongoDB reviews/notification
storage. docs/overrides.md documents schema, merge order, evidence, branch/asynchronous flow
semantics and the privacy limitation of env_file declarations. No dotenv/env_file contents are
opened by the mapper; dependencies private to those files require manual overrides. The current
target has no env_file. Interpolation is intentionally not expanded, and provider key values
are never needed. No target notes, target files, Docker state or later-phase modules were changed.

Implemented chaosai map summary tables, per-run and latest .chaosai/graph.json, optional existing
--run-dir and --mermaid docs/graph.mmd. Default run now uses the real mapper and saves both graphs;
--offline preserves the packaged graph fixture (parsed=false), applies overrides and completes
the synthetic pipeline without target access. Map uses exported process config/defaults without
loading .env. Other metrics, experiments, analysis and scores remain Phase 1 fixtures.

Cross-check against target-notes sections 1, 3, 4 and 5 is recorded in docs/mapper-cross-check.md:
all ten local nodes/names/ports and eleven Compose startup dependency pairs match; default output
has 13 nodes, 17 edges, three flows. Three additional auth/product/order -> frontend CORS URL
references are preserved, rather than claimed as actual backend HTTP calls. Cloudinary is named
by Compose but not by the requested sections' dependency lists (confirmed in notes section 2).
All three external edges have unknown protocol without literal URL schemes; Stripe is conditional
and bypassed by placeholders, Cloudinary is upload-dependent, Resend delivery is unverified.
Database/collection granularity, operation/cache-dependent usage and retry/payment/delivery
semantics are graph-contract limits. Frontend HTTP evidence comes from build args and represents
browser calls. No order->auth/product/notification or notification->MailHog edge was invented.

Verification: ruff check . passed; pytest -q -m "not integration" passed (149 tests, two human
integration tests deselected). Added docker-free mapper coverage using tests/fixtures/docker-compose.yml,
confirmed byte-for-byte with cmp against target Compose. Tests cover exact nodes/ports/kinds/edges,
evidence/privacy, service/container/localhost resolution, ambiguity, list/dict syntax, no file
reads for env_file, explicit-only MailHog edges, override merges/removals/upserts/selectors,
non-injectable externals, invalid data, deterministic ordering, shipped flows, fixture offline mode,
CLI run/latest JSON equality, --run-dir, Mermaid escaping, safe errors and model round-trips.
chaosai map --mermaid passed (13 nodes/17 edges); python -m chaosai run --offline --yes completed
and generated a report. git diff --check passed. Read-only target git status is clean. No Docker,
network, integration or real LLM checks ran. The pre-existing tracked .DS_Store modification is
excluded from this phase commit. Only Phase 3 is completed in this session.

Open limits: env_file contents/interpolation are intentionally private/unresolved; use overrides
for those dependencies. Edges indicate declarations/references, not measured runtime criticality
or conditional activation. Offline graph protocols/evidence remain the manually authored Phase 1
fixture and are explicitly identified as such. Live behavior is still a human/later-phase check.

**Phase 3 "You verify" (copied from BUILD_PLAN.md):** `chaosai map` prints a graph you recognise
from the README diagram; fix mistakes via `overrides.yaml`.

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai map --mermaid
```

### Phase 3 mapper review fixes — 2026-10-11

Applied the three requested review corrections within Phase 3. CORS/allowed-origin variable
names (FRONTEND_URL, *_ORIGIN, *_ORIGINS, CORS_*, case-insensitive) are ignored before host or
external-provider inference. Complete Compose field paths are stored, without values, in the
new backward-compatible GraphSources.ignored_config list and shown by the map CLI. The target's
three FRONTEND_URL variables remove auth/product/order -> frontend edges: 13 nodes, 14 edges.

Flow.path now explicitly means components touched, ordered from the user entry point to the
deepest dependency. The CLI and Mermaid show "Flow <name> touches: a, b, c"; Mermaid flows are
separate labels with no call-chain arrows. Re-read target-notes sections 1–5 and checked the
cited source: uncached product detail reads MongoDB reviews (productController.ts:87–110), and
order.created notification processing writes MongoDB after the email attempt
(orderConsumer.ts:33–61). Existing override flow memberships/order are correct and unchanged:
browse includes MongoDB, login includes PostgreSQL, checkout includes asynchronous MongoDB
notification storage. The offline fixture adds MongoDB to browse/checkout to match. Stripe
remains outside the default bypass checkout; cache misses and downstream effects are potential
touches across the journey's paths, not claims every request touches every resource.

Tool nodes with no inbound dependency edges now default to injectable=false and carry an
injectable_reason string. Outbound edges do not enable them. Tool defaults are recomputed after
edge overrides; explicit full-node overrides can set eligibility either way, with an override
reason (or a supplied custom reason). MailHog defaults to false. External services still cannot
be made injectable. Updated models, offline graph snapshot, docs/overrides.md, overrides.yaml
comments, README and mapper-cross-check.md; regenerated docs/graph.mmd. The offline graph snapshot
now matches all parsed nodes/edges/flows/ignored_config while parsed=false indicates that the
offline invocation did not parse Compose. Old graph artifacts remain readable through defaults
for the newly added fields.

Verification: ruff check . passed; pytest -q -m "not integration" passed (156 tests, two human
integration tests deselected). New regressions cover fixture-based ignored CORS records, all
patterns in environment/build args (including no provider inference), isolation/inbound/outbound
tool eligibility and explicit override flips, flow membership and touches display in CLI/Mermaid,
and offline snapshot equality without target/override access. Required Compose fixture comparisons
and exact node/edge/kind/evidence tests still pass. python -m chaosai run --offline --yes completed;
chaosai map --mermaid printed 13 nodes/14 edges, the three ignored fields, all three touch-sets,
and MailHog's false/reason. git diff --check and byte-for-byte Compose fixture cmp passed;
target git status is clean. No Docker/network/integration/real LLM/.env reads were performed.

Known limits remain: env_file contents/interpolation are not expanded; dependency activation,
cache-dependent touches and asynchronous outcomes are source descriptions rather than live
measurements. No new blockers. Pre-existing .DS_Store and untracked =, checkout, login, retrieve
files are untouched and excluded from this commit. No later phase started.

**Phase 3 "You verify" (copied from BUILD_PLAN.md):** `chaosai map` prints a graph you recognise
from the README diagram; fix mistakes via `overrides.yaml`.

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai map --mermaid
```


## Phase 4 — 2026-10-11

Implemented homepage/browse/login/checkout load with the exact documented contracts. Homepage
hits the configured frontend host root; browse lists products, reads a returned database ID,
and lists categories. Workers own synchronous httpx clients, use weighted 10/60/10/20 journey
selection, 0.2–1.0s think time, five-second request deadlines and per-request samples. Samples
are serialized under one lock into memory and append-only/fsynced JSONL. Worker counts can
change through set_workers/add_workers/remove_workers, with 150 total including retiring
threads. Stop cancels pending admissions, joins workers and closes their clients.

Seed account identities match chaosai seed. Preparation and every login reuse Phase 2's scoped
DB existence/bcrypt comparison; absent/blank/mismatched/unverifiable users warn and skip with
no login/registration. Passwords/tokens and raw HTTP/helper errors are absent from sample/log
artifacts. Per-user locked token caches renew on use at ten minutes by verified re-login;
checkout 401 invalidates/re-logins and retries once only with another checkout admission.
HTTP 500/timeouts are never retried because orders can persist before publication fails.
Shared checkout cap defaults to one request/s and survives worker changes. An additional
nonburst auth cap defaults/maxes at 0.1 requests/s (90/15min) due to the known target IP limiter.
Admission denials skip journeys, so observed proportions differ from offered weights. Tokens
are renewed by re-login rather than the unsafe stored-refresh-token error path.

Added pure interpolated percentiles, half-open window stats, per-journey stats/by_journey alias,
stability checking and exact threshold derivation. Full baseline discards 30s warm-up and
measures 120s; --quick uses 10s/40s. Requests started outside the measured window are excluded
from both sample artifacts and stats, including late warm-up completions; in-window requests
are allowed to finish after the window before statistics are finalized. Stability requires
samples in both halves for every journey and a <=30% p95 change relative to the first half.
An "all" aggregate supplies hypothesis error_rate +0.02; p95 multiple 2. Abort constants are
0.5 error rate, 10 p95 multiple, 3 unhealthy containers, 10 minimum samples and 2 consecutive
breaches. No scoring/fault/watchdog phase was started.

Added five-second scoped Docker CPU-delta/memory/status/restart polling and RabbitMQ total
notifications queue depth via the existing validated management contract. CPU is zero before
a prior delta exists; memory is MiB. Baseline CPU/memory and queue depth are measured means;
status and cumulative restarts are the last observations. Missing telemetry warns and is
omitted rather than treated as a zero queue. Published TCP infra probes and frontend/catalog
HTTP probes use config/current scoped bindings. Auth GET /me (confirmed in authRoutes.ts:25)
and order GET /orders expect no-token middleware 401, which proves responsiveness only. Missing
mapped infra is recorded false. Observations/probes are persisted separately in the run.

CLI load and baseline now work; latest baseline is atomically written to .chaosai/baseline.json
as well as the run directory. Normal run replaces the baseline fixture with measurement;
--offline still uses fixtures with no Docker/HTTP/dotenv reads. Plans, experiment outcomes,
findings and scores remain fixtures and the CLI/README state this. Added configuration variables,
README usage and docs/load-baseline.md for data semantics and operational limits. Existing
pre-session .DS_Store and untracked =, checkout, login, retrieve files remain untouched and
excluded from this phase commit. No target files, dependencies or later-phase features changed.

Verification: ruff check . passed; pytest -q -m "not integration" passed (207 tests, three
human integration tests deselected). Tests cover percentile/window/journey/error stats, exact
thresholds, p95 boundaries/missing halves, warm-up/late/end-boundary filtering, concurrent
append-only buffers, shared nonburst rate caps, verified-credential refusal and rechecks with
MockTransport, proactive token renewal, 401-only retry/cache invalidation, timeout/invalid
response redaction, runtime worker changes/private-client cleanup, scoped Docker stats/CPU
formula, queue contract/unavailability, actual TCP/safe HTTP probes, baseline artifacts and
cleanup, env/CLI options and offline fixture isolation. python -m chaosai run --offline --yes
completed with a report; chaosai load --help and chaosai baseline --help passed. Whitespace
checks passed. Read-only target git status is clean. No live Docker/HTTP, integration tests,
real LLM, API keys or .env contents were inspected by the coding agent.

Open issues: actual live compatibility/telemetry/stability remain unverified. One generator's
caps do not coordinate concurrent processes or preceding seed/smoke IP traffic; run serially
and allow the 15-minute auth window to clear if it has been consumed. Quick mode has few login
samples; repeat full windows to establish stability. Latency stability alone does not prove
health; inspect errors and probes. The fresh credential preflight cannot prevent a concurrent
account deletion/password change between comparison and HTTP; failed login is rechecked with
no immediate retry. Every admitted checkout creates a durable pending order and may trigger
Resend in the target. Runtime target config/deployment differences require config overrides.

**Phase 4 "You verify (Docker)" (copied from BUILD_PLAN.md):** `chaosai baseline --quick`,
then a full `chaosai baseline`; `baseline.json` shows believable numbers and `stable: true`;
run it twice and compare.

Exact live commands (target up; retain the current seeded users' SEED_PASSWORD in .env):

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
export LOAD_USERS=10
chaosai seed --users 10
chaosai baseline --quick
chaosai baseline
chaosai baseline
```

Each baseline prints its run-specific baseline.json and latest .chaosai/baseline.json paths.
Compare the two full run files, including per-journey n/errors/p95/RPS, containers, queues,
thresholds and stable. A targeted human integration check is:
`pytest -q -m integration tests/test_target_integration.py::test_live_quick_baseline`.

### Phase 4 review fixes — 2026-10-11

The human reports the original live baselines are stable and repeatable. Re-read updated
BUILD_PLAN.md sections 4 and 5 and target-notes sections 11/12. Applied only these Phase 4
corrections; no watchdog, policy gate, scoring or target changes were introduced.

Thresholds now contain hypothesis p95_floor_ms=100 and abort p95_floor_ms=750, with nonnegative
pydantic fields and backward-compatible defaults for older stored thresholds. derive_thresholds
sets both explicitly. Updated the baseline fixture with floors, the documented 0.02 hypothesis
error rate / 0.5 abort error rate / 10 abort multiple defaults, and low_sample flags. The pure
metrics.latency_limit_ms returns max(multiple * baseline_p95, baseline_p95 + floor_ms); breach
is strictly greater, so both guards must be exceeded. This helper is ready for the future
watchdog without implementing it in this phase.

Extracted the existing queue reader into chaosai/rabbitmq.py; smoke and the queue-depth poller
now share that same queue_snapshot function for Basic credentials, encoded vhost/name, response
validation and URL normalization. Explicit REQUEST_TIMEOUT_S (default ten seconds) applies to
every management request regardless of the caller's client timeout. Previously smoke used ten
seconds while baseline used five; no live evidence proves that difference caused the reported
failure. The poller warns once per run, including safe HTTP status or exception-class diagnostics,
and continues polling/recovering. Raw exception text, response bodies, URLs and credentials are
withheld. Missing metrics remain unavailable rather than zero; filesystem errors report their
type as well. Smoke's queue_snapshot import remains available for existing callers/tests.

LOGIN_RATE_CAP replaces AUTH_RPS, defaults to 1 request/s and accepts positive rates such as
0.5–1. This cap remains shared across workers and token renewal/re-login. Every admitted login
retains fresh scoped account/password verification and now explicitly uses AUTH_TIMEOUT_S=4,
rather than the generic five-second load timeout. Missing/wrong/unavailable credentials still
skip without HTTP login, and failed login still triggers verification before any future attempt.
Other load requests and checkout admission remain unchanged. No auth limiter bypass/reset is
performed. The checked-in target still sets max=100 per 15-minute IP window (auth-service
src/index.ts:19–24); sustained requested rates or repeated runs may therefore receive 429s,
which are recorded as errors. The client rate is a traffic target, not a guarantee of service
admission. Existing AUTH_RPS values in a local .env are ignored; set LOGIN_RATE_CAP instead.

JourneyStats includes low_sample, set true below 20 samples by the pure window stats function
and saved for each journey in baseline.json. Sparse/missing journeys retain descriptive stats
and are excluded from the p95 stability comparison. Eligible journeys require observations in
both halves and <=30% relative p95 change. At exactly 20 they become eligible; with no eligible
journey, stable=false because stability cannot be established. CLI baseline labels low_sample
journeys. Updated README, config examples and docs/load-baseline.md with the current semantics.

Verification: ruff check . passed; pytest -q -m "not integration" passed (228 tests, three
integration tests deselected). New regressions cover floors/defaults and strict AND/equality
latency limits, sparse-journey exclusion and the n=20 boundary, shared smoke/poller URL/auth/
timeout behavior, repeated unavailable polls with one safe status/type warning and recovery,
and 0.5/1 request/s verified login admission with the four-second timeout. Existing credential,
401, concurrent buffer/worker, artifact, scope and offline checks pass. python -m chaosai run
--offline --yes completed with a report; git diff --check passed and read-only target status
is clean. No Docker command, live HTTP, integration test, real LLM, .env read or key inspection
was performed. Pre-existing .DS_Store and untracked =, checkout, login, retrieve files remain
untouched and excluded from the fix commit.

Open issues: the corrected RabbitMQ behavior and increased live login sampling require human
re-verification; if queue reading still fails, its single warning now exposes the reason.
The target's IP limiter can prevent healthy sustained 1 request/s runs. A full 30s warm-up plus
120s measurement at 0.5/s uses approximately 75 auth requests; allow a fresh 15-minute window
between live checks and account for preceding smoke/seed traffic. Sparse journey statistics
are descriptive only; latency stability does not establish low error rates or service health.

**Phase 4 "You verify (Docker)" (copied from BUILD_PLAN.md):** `chaosai baseline --quick`,
then a full `chaosai baseline`; `baseline.json` shows believable numbers and `stable: true`;
run it twice and compare.

Live commands (run each baseline with a fresh auth rate-limit window, rather than consecutively):

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai smoke
LOGIN_RATE_CAP=1 LOAD_USERS=10 chaosai baseline --quick
LOGIN_RATE_CAP=0.5 LOAD_USERS=10 chaosai baseline
LOGIN_RATE_CAP=0.5 LOAD_USERS=10 chaosai baseline
```

Check login n/RPS and low_sample, notifications queue depth (or its one actionable warning),
and thresholds.hypothesis.p95_floor_ms=100 / thresholds.abort.p95_floor_ms=750 in the printed
baseline artifact. Inspect errors/429s alongside stability; compare the two full windows.

## Phase 5 — 2026-10-11

Implemented the deterministic safety building blocks, without starting Phase 6. Fault base
validates graph/kind/duration and resolves containers through scope_guard. Kill applies only
to applications, observes self-heal before undo and starts if still stopped; stop uses
Docker stop(timeout=5) and start; pause uses pause/unpause. All application/rollback mutations
require the exact Compose project label. External, unknown, non-injectable (including the
default isolated MailHog tool), unlabeled and mismatched-project targets are denied.

Policy gate deep-copies specs and records every numerical clamp: duration 5–120, CPU 0.1–0.5,
traffic 2–8 with 150 total workers, abort error 0.05–0.9 and p95 multiple 2–20. Run counters deny
simultaneous faults and a ninth experiment; impossible minimum traffic multipliers are denied.
The low-level CLI defaults to 30s, uses the existing y/n/a/q approver with a LIVE FAULT prompt,
refuses non-TTY injection without --yes and saves graph/decision/journal artifacts.

Added pydantic original-state, versioned journal, watchdog-window and watchdog-state contracts
in models.py. Journal file/containing-directory/run-directory-entry fsyncs precede every Docker
mutation. Records include container name/ID/project and original running/paused/CPU settings.
Rollback verifies restored state, marks done only after success, restores CPU period/quota/
NanoCpus, attempts every pending record/journal, retains failures for retry, and refuses a
replacement container reusing the name. Empty legacy fixture journals are compatible; malformed
or unknown nonempty formats fail closed. OS operation locks serialize record+apply with rollback;
rollback locks before discovering journals. The separate active-fault lease prevents concurrent
CLI injections and is released on process death.

CLI implements fault apply/undo, rollback and kill. New injection reconciles pending journals
before applying. Normal exit, exceptions, SIGINT and SIGTERM invoke rollback; repeated signals
are ignored during cleanup and handlers are restored. The duration holding loop checks KILL
at 0.5s intervals between SDK observations and observes second-terminal done records. Kill
persists/fsyncs the flag before opening Docker and directly replays journals; a Docker failure
still leaves KILL set. Explicit rollback --clear-kill clears only after successful recovery
and with no active fault. Self-heal observation uses the operation lock and excludes our own
rollback, so starting through recovery cannot count as automatic restart.

Watchdog uses pure evaluate, adaptive per-journey windows and a five-second aggregate window.
Rates too low to collect min_samples in 20s are assessed only through the aggregate. Metrics
have five seconds of grace, minimum samples, independent consecutive counters and strictly
combined multiple/+750ms floor checks through metrics.latency_limit_ms. Missing/undersampled/
healthy windows reset their counter. Blast radius is immediate and counts baseline-running
non-target containers that leave running; running-but-unhealthy containers are distinguished
from exited/paused. Scoped telemetry now records disappeared known containers as missing and
does not overwrite exited/paused states with old healthcheck results. The Watchdog thread
reads live SampleBuffer/ContainerStatsPoller snapshots every second, latches the reason and
sets an abort event; unexpected evaluation failure also aborts without exposing raw payloads.

README and docs/safety.md explain contracts, crash/recovery behavior, limitations and exact
Docker/two-terminal checks. Updated placeholder CLI tests and model round-trip coverage.
Pre-existing .DS_Store and untracked =, checkout, login, retrieve files are untouched and
excluded from this phase commit. Target working tree remains clean. No target writes, new
dependencies, Docker command, live HTTP, integration test, real LLM call, .env read or API-key
inspection was performed by the coding agent.

Verification: ruff check . passed. pytest -q -m "not integration" passed: 312 tests, three
human integration tests deselected. New fake-Docker tests cover clamps/counters/worker ceiling,
scope denials, file/directory fsync-before-apply, write failure prevention, crashes before/after
mutation, fresh-object replay, CPU restoration, idempotency, identity/scope/restore failures,
continuing other records/journals, legacy compatibility, in-flight apply/rollback serialization,
active lease, SIGINT/SIGTERM and CLI Ctrl-C recovery, startup reconciliation, kill persistence
on unreachable Docker, explicit flag clearing, self-heal and hard-duration/abort decisions.
Watchdog tests cover grace, n=9/10 boundary, missing windows, one evaluation and a real one-second
sample spike, sustained error/p95 breach, equality/absolute floor/multiple guards, low-rate
aggregate-only decisions, independent counters, adaptive 5–20s bounds, live windows/sample
boundaries, blast-radius thresholds/target/pre-existing/foreign exclusions, missing containers,
old healthchecks and the thread's one-second scheduling/fail-closed path. CLI apply/rollback/
kill --help and python -m chaosai run --offline --yes passed (fixture report generated).
git diff --check passed. Commit: feat: faults, safety layer, watchdog, kill switch (local only).

Open limits: live Docker mechanisms and recovery remain human verification. The low-level
fault command generates no load and attaches no metric watchdog; run-level precheck,
reconciliation and measured experiments remain Phase 6. Rollback restores recorded resources,
not unrelated pre-existing failures or application HTTP/broker readiness. Watchdog telemetry
freshness follows the caller's poller interval (default 5s; use 1s for the future safety runner);
missing initial telemetry or Docker read failures do not establish a blast-radius event.
SDK calls may delay observation/cleanup within their bounded timeouts. POSIX locking covers
one workspace, not multiple independent checkouts. Retain failed journals; a removed/replaced
container or unreadable journal needs repair/manual reconciliation before further injection.

**Phase 5 "You verify (Docker)" (copied from BUILD_PLAN.md):**
`chaosai fault apply stop redis --duration 20` shows the site degrading; `chaosai rollback`
restores it; pressing Ctrl-C mid-fault also restores; `chaosai kill` from a second terminal works.

Exact setup, Docker state/catalog checks, apply/automatic undo, second-terminal rollback,
Ctrl-C recovery, kill/reset and optional pause/application kill are in docs/safety.md.
