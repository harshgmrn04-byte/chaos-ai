# ChaosPilot progress

- [x] Phase 0 — Target reconnaissance
- [x] Phase 1 — Scaffold and walking skeleton
- [x] Phase 2 — Bring-up, seed, smoke test, payment gate, doctor
- [x] Phase 3 — Application mapper
- [x] Phase 4 — Load generator, metrics, baseline
- [x] Phase 5 — Faults, safety layer, watchdog, kill switch
- [x] Phase 6 — Experiment runner and Day-1 demo
- [x] Phase 7 — LLM layer and AI planner
- [x] Phase 8 — Extended faults and automatic run
- [x] Phase 9 — Analyzer, scoring, report
- [x] Phase 10 — Freeze and polish (implementation/offline checks; measured sample/live verification pending)
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

### Phase 5 human verification review — 2026-10-11

Reviewed the human's Terminal A attachment and pasted Terminal B output; no new Docker or
HTTP commands were run by the coding agent. The 20-second Redis stop reports Applied and
Rollback complete. The separate 120-second stop reports Rolled back from another terminal;
Terminal B reports one undo record restored. The final scoped Docker listing shows all ten
containers running, Redis healthy with a recent start. These logs support automatic and
explicit cross-terminal rollback and restored container state.

Terminal B's kill command reports KILL set but zero records restored. Terminal A shows no
separate active-fault kill test, signal interruption, or Interrupted; rollback complete line.
Therefore active-fault kill recovery and Ctrl-C recovery remain unverified by these logs.
No catalog response, measured impact or explicit browser observation was supplied; site
impact/application recovery also remain unverified. This is a partial live verification,
not a failure report. The implemented-phase checkbox stays checked; no Phase 6 code was started.

The human also ran rollback --clear-kill successfully. Do not infer a code defect from a
zero-record rollback/kill after recovery: there may simply be no pending undo records.
Repeat an active fault for each remaining test, invoke kill only after Applied, and separately
press Ctrl-C during another active fault. Inspect catalog behavior during/after the fault.
Retain the Phase 5 You verify list above until these remaining observations are supplied.

Updated workspace BUILD_PLAN.md section 2 with this evidence and corrected its stale Phase 3
"fixes pending" status from previously documented completed fixes. The user explicitly
requested the build-plan update; it is outside the chaos-ai Git repository. Corrected the
catalog probe in docs/safety.md from /api/products to /products, matching target-notes section
2's explicit no-/api route contract. No safety thresholds or Phase 6 requirements changed.


## Phase 6 — 2026-10-11

Implemented the measured single-experiment runner exported by runner.py, with lifecycle code
in experiment.py and pure evaluation in evaluate.py. APPROVE/PRECHECK/INJECT/OBSERVE/UNDO/
RECOVER/RESULT events are persisted alongside samples and scoped one-second container telemetry.
Reuses Phase 4 load/metrics and Phase 5 scope/policy/journal/lease/Watchdog/signals; no live LLM,
planner or analyzer calls. Final policy-clamped thresholds are applied to a deep copy, leaving
saved baseline evidence unchanged. Readiness waits at most 60 seconds, including threaded load
preparation; a timeout records a skip. Preparation stops between bounded calls before joining.

An independent pre-apply timer requests undo directly at the duration cap. A separate half-second
control observer detects KILL and second-terminal done records even during main-thread SDK I/O.
A cancellation check inside the operation lock prevents delayed application after cancellation.
Journal restoration timestamps preserve measured boundaries across second-terminal rollback;
legacy completed records without timestamps use their observation time. Rollback runs on normal
completion, watchdog abort, KILL, external rollback, exception, SIGINT and SIGTERM. It precedes
thread joins; all clients/threads are closed/joined while signals are ignored during cleanup.
Active-lease denial does not replay another process's fault. Failed undo remains pending and
sets rollback_ok=false with an aborted result.

Pure half-open during/after statistics, impact, verdict and recovery use synthetic-testable
functions. Hypothesis latency uses the existing multiple/+100ms floor helper; >=20 samples in
both baseline and during are required to support a verdict. Impact retains sparse descriptive
breaches with explicit evidence limitations. Recovery needs ALL journeys in three consecutive,
complete, nonempty 1s windows with p95 <=1.5x baseline and hypothesis error-rate limit, ending
within 120 seconds after undo. Empty/sparse journeys never imply health. Sealed live windows
exclude in-flight requests; final calculation drains the buffer. Rollback/container restoration
is explicitly separate from application recovery. Kill self-heal is observed before our undo.

Added measured inject CLI with duration/error override, policy clamps, approval/non-TTY refusal
and readable impact/recovery/abort output. Live run performs one deterministic Redis stop,
reusing a saved baseline or measuring one if absent. Its report includes measured evidence and
no fixture scores/findings; these remain Phase 9. Offline fixtures and dry runs are preserved.
Artifacts include complete result.json, events.jsonl, samples.jsonl, container state/restart
changes, skip/abort reasons, approval decision and bounded logs (<=40 lines, truncated and
redacted after exact scope rechecks for target/direct dependents). Added executable Day-1 script
with doctor, baseline creation/reuse, normal/strict experiments and a separate active-fault kill
that waits for an OBSERVE event plus pending journal record. KILL is never silently cleared.
Updated README, docs/safety.md, docs/load-baseline.md and docs/experiments.md.

Verification: ruff check . passed; pytest -q -m "not integration" passed (356 tests, three
human integration tests deselected); python -m chaosai run --offline --yes passed and produced
runs/20261011-003939/report.html. bash -n scripts/day1_demo.sh and git diff --check passed.
Tests cover half-open boundaries, latency floors/equality, sample-count verdict bounds, sparse/
empty recovery, error limit equality, 120s recovery boundary/timeout, S9 skip/preparation timeout,
normal duration, independent timer during blocked observation, cancellation before late apply,
real Watchdog with final clamped thresholds, KILL, second-terminal rollback, exception after
mutation, load/preparation/cleanup failures, SIGINT/SIGTERM, ignored signals throughout cleanup,
startup reconciliation, active-lease denial, failed retained undo, kill self-heal, log scope/
redaction/bounds, actual load preparation cancellation, measured CLI/live single-experiment
pipeline, offline isolation and dry-run non-execution. No Docker/live HTTP/integration/real LLM,
.env read or key inspection was performed. Read-only target status remains clean. Pre-existing
.DS_Store and untracked =, checkout, login, retrieve remain untouched and excluded from commit.
Local commit message: feat: experiment runner and day-1 demo. Never pushed.
The human explicitly requested a root BUILD_PLAN.md checkpoint and continuation prompt;
that documentation update is outside this repository and does not start a later phase.

Open issues: live impact, Watchdog abort and application recovery require human verification.
Sparse login/checkout may prevent three nonempty 1s windows despite restored containers; a
low-sample verdict may be inconclusive. Auth's 100/15-minute IP budget can contaminate repeated
runs or cause S9 to skip; allow a fresh rate-limit window and inspect 429s. The strict error
threshold is a requested limit, not a guarantee that a sufficient sustained breach will occur.
In-flight Docker mutations/operation locks cannot be interrupted; bounded SDK calls can delay
restoration and cleanup beyond the requested timer/readiness deadline. Failed/replaced containers
or unreadable journals need explicit repair/replay; no unrelated resource is repaired. Later
phases (AI, extended faults, automatic multiple experiments and real scoring) were not started.

**Phase 6 "You verify (Docker)" (copied from BUILD_PLAN.md):**
- `chaosai inject stop redis --duration 30` -> result shows impact and recovery.
- `chaosai inject stop redis --duration 30 --abort-error-rate 0.05` -> aborted by the watchdog
  and rolled back.
- Run the Day-1 demo script once: `bash scripts/day1_demo.sh`.

Run from chaos-ai with the target already up and seeded; activate .venv and run doctor first.
Create a baseline with chaosai baseline if none is saved. The demo leaves KILL set; after the
injector exits and rollback is reviewed, explicitly run chaosai rollback --clear-kill.

## Phase 7 — 2026-10-11

Implemented synchronous Gemini/mock/none transport with the installed official google-genai
SDK API inspected locally. Runtime discovery filters generateContent models, excludes non-text
families and ranks stable/versioned flash, flash-lite and pro models. Human GEMINI_MODEL
selection is supported; up to four candidates are probed, permission/not-found failures try
the next, a winner is persisted in .chaosai/llm.json and a later 404 re-resolves once. Explicit
2/4/8-second 429/503 retries use bounded requests with SDK automatic retries disabled. JSON
schema output is pydantic-validated, rejected schemas use prompted JSON mode, and invalid
JSON retries once before LLMUnavailable. Run-local hash caches retain validated and malformed
responses and resolution probes. Errors expose safe hints, never raw SDK payloads or keys.
Keys are obtained only from environment through config.py; no credentials enter artifacts.

Added flat ExperimentProposal/PlannerOutput and catalogue/probe/model-cache contracts in
models.py, catalogue metadata for all six fault types, and packaged prompts/planner.md.
Graph/baseline/catalogue inputs are JSON quoted as untrusted data. Semantic validation drops
unknown/external/non-injectable/containerless/ambiguous targets, incompatible kinds/faults,
unmapped journeys, invalid ranks and empty hypotheses/reasons. Duplicate fault/target pairs
are removed; deterministic top-ups reserve diversity even after eight same-type proposals.
Heuristic priority uses fan-in, unknown criticality, node kind and journey impact, with short
templated reasons. Final ranks 1..N order estimated blast radius from lower to higher.
All accepted specs pass the existing policy gate; hypothesis bounds/inapplicable intensity
also produce clamps. Final plan and complete requested/final/clamp/reason/source decision
trace are persisted; heuristic top-ups retain their own source. Baseline data is unchanged.

Added doctor --llm with explicit resolved-model/JSON PASS/FAIL and safe hints, and plan
[--offline] with ranked reasons, sources and clamp output. Live plan uses a saved baseline,
with no Docker, load or HTTP measurement. Offline plan uses fixture graph/baseline and
heuristics without target/dotenv access. Live run now uses the planner but still executes
at most one supported kill/stop/pause experiment through the unchanged Phase 6 lifecycle.
CPU and traffic entries are planning only, netem remains unselected stretch, and no Phase 8
mechanisms/multi-experiment live run or Phase 9 analysis/scoring were implemented. The
original three-example synthetic offline run is preserved, including IDs/order and mock
fixture caches; even direct offline pipeline calls with Gemini settings cannot open a real
Gemini provider. Updated README, experiments.md, safety.md and added llm-planning.md.

**Explicit Phase 6 deferral (human authorization):** the user authorized Phase 7 despite the
checkpoint requiring live verification. Human doctor and smoke passed. Run 20261011-005504
was blocked by an existing KILL flag. After explicit rollback --clear-kill, 20261011-005640
skipped injection because the saved baseline has stable=false. Original baseline
20261010-235955 contained 51 login requests, all HTTP 429; latest precheck requests succeeded.
Neither attempt applied a fault. Impact, application recovery, Watchdog abort, active-fault
KILL, Ctrl-C recovery and the completed Day-1 walkthrough remain **pending, not passed**.
No baseline data, readiness condition, threshold or auth limiter was altered to bypass this
gap. The Phase 6 checkbox records implementation completion only, not live verification.

Actual agent verification, using the existing .venv:
- ruff check .: passed.
- pytest -q -m "not integration": 407 passed, three integration tests deselected. One
  installed google-genai/Python 3.14 deprecation warning; no test failures. Unit tests block
  Docker, sockets, subprocess network operations and dotenv reads.
- python -m chaosai run --offline --yes: passed; synthetic report at
  runs/20261011-010827/report.html. No injected faults or target observations.
- python -m chaosai plan --offline: passed; eight ranked heuristic specs and policy trace at
  runs/20261011-010828/{plan,decisions}.json. No load, injection, HTTP or LLM calls.
- git diff --check: passed; read-only target git status clean.

Tests cover valid mock output, individual invalid target/fault/kind/rank/journey rejection,
deduplication, type top-up, diversity reservation, numeric clamps/request preservation,
worker-budget denial, unavailable/malformed retry fallback, immutable unstable baseline,
empty/restricted graphs, quoted prompt constraints, SDK model ranking/filtering, four-probe
limit, permission/not-found candidate fallback, explicit override, cached model reuse,
one re-resolution, exact backoff, schema rejection fallback, output retry/validation/cache,
cache-resolution failure, environment provider selection, client timeout/cleanup, doctor
flag/results, offline CLI isolation and planner integration with one supported measured fault.
No real LLM, Docker/live HTTP, integration test, .env read or key inspection was performed
by the coding agent. Pre-existing .DS_Store and untracked =, checkout, login, retrieve remain
untouched and excluded from this commit. Local commit: feat: llm layer and ai planner.
Nothing pushed; Phase 8 not started.

Open issues: real Gemini model/key availability and schema support require the human probe;
existing auth rate limiting can still contaminate baselines or prevent S9 readiness. Saved
stable=false remains authoritative. Sparse journey recovery, RabbitMQ queue-depth visibility,
in-flight SDK rollback timing and the pending Phase 5/6 live evidence remain as documented.
The heuristic returns fewer than three fault types with an explicit warning if the graph/
worker budget cannot support them. Replay CLI and real analysis/scoring remain later work.

**Phase 7 "You verify" (copied from BUILD_PLAN.md):** put GEMINI_API_KEY in chaos-ai/.env
yourself; chaosai doctor --llm is green; chaosai plan prints ranked experiments with reasons;
chaosai plan --offline also works.

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
# Set your own GEMINI_API_KEY in .env locally; do not paste it into chat.
chaosai doctor --llm
chaosai plan
chaosai plan --offline
```

These commands do not inject faults. Non-offline plan requires the saved baseline (it may
inspect an unstable one); doctor also runs the existing read-only target checks. Phase 6's
live verification commands remain listed in its section above, with active-fault KILL and
Ctrl-C verification in safety.md. Do not interpret a successful plan/probe as live fault
verification or clear KILL implicitly.

### Phase 7 follow-up — source grounding and human verification, 2026-10-11

Completed the explanation-quality follow-up in Phase 7 only. Added SourceFact in models.py
and a curated planner_facts.py packet grounded in target-notes.md and target revision
bae94402773bc7fd7d05dadd4062dffe2d1c2057 (read-only source revision rechecked). The prompt
receives quoted source statements and evidence about unavailable Redis having no fallback,
order persistence preceding broker publication and PostgreSQL's shared dependency. These
facts describe inspected source, not runtime guarantees; they apply only to the named app
and graph nodes present. No runtime target/source discovery or credential read was added.

Prompt instructions now distinguish a cache miss from an unavailable cache and distinguish
baseline observations from capacity/recovery hypotheses. Deterministic reason templates
correct Redis, RabbitMQ, PostgreSQL and traffic-spike plan rows even when mock/AI proposals
contain unsupported fallback or overload claims. Each correction is a reason Clamp with
requested/final explanation and supporting source/limitation in decisions.json. Original
model reasons remain inspectable as requested data. Selection, faults, numeric controls,
approval, readiness, Watchdog, KILL and rollback are unchanged. Historical plans and synthetic
offline run examples are preserved; heuristic plans use the same grounded templates.

**Human Phase 7 verification: passed (user-supplied terminal evidence, not agent live calls).**
- chaosai doctor --llm: all checks PASS; models/gemini-3.8-flash resolved, real JSON validated.
- chaosai plan: Gemini source, eight ranked experiments across five fault types, reasons and
  decision trace written to runs/20261011-011101. Artifact review confirmed contiguous ranks,
  applicable targets, all policy decisions allowed, plan matching final decisions, zero clamps
  in that original run and no experiment result files. Explanation issues identified in this
  run motivated the follow-up; the old run is not rewritten or relabeled.
- chaosai plan --offline: heuristic source, eight experiments across five fault types, plan
  and trace at runs/20261011-011241; same validation checks passed, no experiment results.
- API key configuration was demonstrated by the successful probe. No key value or dotenv
  contents were inspected, copied or recorded by the agent.

The subsequent human doctor passed, but quick baseline 20261011-011425 remains stable=false.
Saved samples confirm zero HTTP 429 and zero request errors, but checkout p95 changed from
31.719ms to 21.481ms between halves (32.28%, beyond the unchanged 30% stability bound).
Checkout had 16 samples in each half. Browse/homepage passed; login had 15 total samples and
was excluded from stability, not the reason for failure. RabbitMQ queue-depth RemoteProtocolError
is a separate missing-telemetry issue. The baseline was only read for diagnosis, never edited.

**Latest explicit user deferral:** fresh baseline and all remaining Phase 6 live verification
will be done as final verification at the end of Phase 10. They remain pending, not passed:
impact, application recovery, strict Watchdog abort, active-fault KILL, Ctrl-C recovery and
the completed Day-1 walkthrough. Continuing implementation/offline checks does not establish
these outcomes or authorize bypassing S9/stability. No timed reminder was requested or created.

Actual follow-up checks using the existing .venv:
- ruff check .: passed.
- pytest -q -m "not integration": 415 passed, three integration tests deselected; the same
  installed google-genai/Python 3.14 deprecation warning only.
- python -m chaosai run --offline --yes: passed; runs/20261011-012249/report.html.
- python -m chaosai plan --offline: passed; eight grounded heuristic plan rows and correction
  trace at runs/20261011-012250/{plan,decisions}.json.
- git diff --check: passed; target git status clean.

Added mock regressions for incorrect fallback/capacity claims, four target/fault rationale
corrections and preserved original explanations/provenance, source context and app/node
scoping, independent fact copies, unchanged unstable-baseline/experiment controls, corrected
CLI rows and heuristic grounding. Existing offline fixture and safety tests still pass.
No agent Docker/live HTTP/real LLM calls, .env inspection, auth traffic or new baseline runs
occurred. No waiting or additional manual work is needed for this follow-up. Previously
provided human Phase 7 checks above satisfy its "You verify" list; deferred Phase 6 commands
remain in the Phase 6 section and safety.md for final verification at the end of Phase 10.

Known limits: the curated packet must be maintained if the target revision changes; generic
model explanations for other targets remain unverified hypotheses. New prompt behavior was
verified with mocks, not another paid/live model call; deterministic reason corrections cover
the specific observed claims independently of prompt compliance. RabbitMQ telemetry and
pending live resilience evidence remain unresolved. Phase 8 was not started. Pre-existing
.DS_Store and untracked =, checkout, login, retrieve are untouched and excluded. Follow-up local
commit message: fix: ground planner explanations in target facts. Nothing pushed.

## Phase 8 — 2026-10-11

Implemented extended faults and automatic sequential run only. No Phase 9 analyzer/scoring,
Phase 10 polish workflow or netem was started. Existing offline fixture analysis/scoring
remains unchanged and synthetic; live reports contain no assessed scores or findings.

CPU mechanism uses the installed Docker SDK Container.update for CFS limits, with original
period/quota/NanoCpus journaled and fsynced before mutation. Restoration sends supported values
and verifies exact original fields; explicit unlimited quota -1 and an unchanged implicit
period 0 are supported. Installed SDK inspection and Docker Engine source established that
zero update fields are omitted/ignored and nano_cpus is not an accepted update keyword.
Consequently original CpuQuota=0 and NanoCpus>0 are refused before mutation, preserving exact
restoration instead of normalizing metadata or recreating containers. Legacy non-restorable
CPU records remain pending. This is a documented mechanism limitation, not a successful live
CPU demonstration or a change to the target. SDK-shaped mock tests enforce these semantics.

Traffic spikes increase prepared load workers by the policy-clamped multiplier, maximum 150
including retiring workers. Extras target a mapped journey or the normal weighted mix; shared
checkout/auth admission, credential checks and request timeouts remain intact. LoadUndoRecord
records project, owner PID and original workers durably before starting extras; undo removes
and joins workers before completion. A second-terminal replay requests live-owner cleanup and
retains pending undo until acknowledged. A dead process proves its worker threads died; an
unknown/live/reused PID remains pending conservatively. Existing version-1 container journals
and empty legacy fixture journals remain readable. Failed writes/starts/joins and unsupported
restoration cannot silently become successful undo.

New orchestrator.py performs scoped reconciliation → doctor-lite → map → baseline → grounded
planner → ranked approval/runner/cooldown. It holds the active lease through the complete live
run. Reconciliation restores journaled state only, requires all expected services running and
unpaused, and denies unjournaled failures instead of repairing them. Doctor-lite probes scoped
state/health/ports and documented public GETs without an LLM/key check. Default run measures a
baseline; --reuse-baseline is opt-in and requires stable=true, age >=0 and strictly <3600s.
An unsuitable saved baseline causes measurement; a new unstable baseline still fails strict S9.

The measured runner now supports both extended faults while preserving S1–S10, measured
readiness/recovery, one-second Watchdog, independent pre-apply duration timer, half-second
KILL/rollback control, operation lock, scope, journals and signal cleanup. Traffic rollback
requests never supply a premature restoration timestamp. Cleanup_ok/interrupted fields make
failed resource cleanup/signals explicit. Repeat signals are ignored immediately while
unwinding cleanup. A rejected CPU configuration records no during/after fault impact.

Each continuation requires successful rollback and cleanup, no unresolved undo, all expected
services running/unpaused, fresh measured S9 readiness and three complete nonempty one-second
recovery windows, plus >=20s cooldown. Cooldown has its own sample/readiness artifacts; it
never overwrites an experiment's unproven recovered=false result. Failure results are recorded
with safe exception classes and can continue only after these gates. q, KILL, signals, cleanup
failure, pending undo or failed readiness stop the sequence. KILL is never cleared implicitly.

CLI supports --yes, --dry-run, --max-experiments 1–8, --reuse-baseline and --offline, with
persistent y/n/a/q approvals and non-TTY refusal before Docker access. The limit selects the
first N ranked entries including skips; the entire plan/decision trace is retained. Approvals
are persisted before execution. Dry-run validates pending journals without replay, applies no
fault, prints sources/reasons/clamps/selected entries, and may measure a live baseline with
normal load. Offline run/dry-run preserve original synthetic examples without Docker, target
access, dotenv, network or waiting. run.json records target project, approvals, planner sources,
baseline source and stop reason. source-facts.json preserves the curated revision/evidence;
plan.json/decisions.json preserve requested/final reasons and numerical/grounding clamps.

Actual agent checks, using the existing .venv:
- ruff check .: passed.
- pytest -q -m "not integration": 494 passed, three integration tests deselected. The existing
  installed google-genai/Python 3.14 deprecation warning remains; no test failures.
- python -m chaosai run --offline --yes: passed, synthetic report at
  runs/20261011-014026/report.html.
- python -m chaosai run --offline --dry-run: passed, plan-only fixture report at
  runs/20261011-014027/report.html.
- git diff --check: passed.

New mock-only coverage includes installed SDK zero/nano behavior, supported exact CPU restore,
non-restorable original refusal, durable-before-update/start boundaries, failures after journal
rename/partial mutation, worker cap/join/cleanup, live-owner acknowledgment and dead-owner replay,
rank ordering, y/n/a/q, non-TTY refusal, maximum selection, dry-run, baseline age/stability,
recorded failure continuation, recovery gates, unresolved undo, missing scoped services,
doctor failure, KILL, planning/cooldown signals, failed rollback and outer client cleanup.
Measured runner tests exercise CPU and traffic normal completion, active KILL, SIGINT/SIGTERM
and external rollback. Existing tests still block Docker/network/dotenv; no live integration,
HTTP/load, real LLM, .env/key inspection or target changes were performed by the coding agent.
Unrelated .DS_Store and untracked =, checkout, login, retrieve remain untouched and excluded.
Local commit message: feat: extended faults and automatic run. Nothing pushed.

**Explicit human-authorized Phase 6 deferral:** the user authorized Phase 8 implementation
with all remaining Phase 6 live verification and a fresh baseline deferred until final
verification at the end of Phase 10. This overrides the historical continuation checkpoint;
it does not weaken runtime checks or establish live evidence. Impact, application recovery,
strict Watchdog abort, active-fault KILL, Ctrl-C recovery and the Day-1 walkthrough remain
**pending, not passed**. Phase 8 human Docker checks are deferred/pending too; none were requested
from the human during this implementation session.

At the Phase 8 implementation checkpoint, baseline 20261011-011425 remained stable=false despite zero request errors:
checkout half-window p95 drift was 32.28%, beyond the unchanged 30% bound. Low-sample login
was excluded. No saved baseline data or stability/readiness rule was edited. RabbitMQ queue-depth
telemetry remains unavailable. Sparse recovery windows and the target's shared 100-request /
15-minute auth budget can stop multi-experiment runs; no auth limiter reset/bypass was added.
Bounded SDK/HTTP calls can delay rollback/worker joins. CPU with original zero quota/NanoCpus
cannot execute under exact-restoration requirements, and unknown/reused traffic PIDs may need
manual investigation while retained journals block continuation. These remain open limits.

**Phase 8 "You verify (Docker)" (copied from BUILD_PLAN.md; deferred until end of Phase 10):**
`chaosai run --dry-run`, then `chaosai run` (approve each) completing at least 4 experiments
covering >= 3 fault types, and the app healthy afterwards (`chaosai smoke`).

- [x] chaosai run --dry-run — user-supplied run 20261011-014504 passed; no faults applied.
- [ ] chaosai run — supplied attempt 20261011-014810 injected zero faults; >=4 experiments
  across >=3 fault types and measured recovery/cooldown remain deferred until end of Phase 10.
- [ ] chaosai smoke — supplied attempt failed at login; passing retest deferred until end of Phase 10.

```sh
cd "/Users/harsh/Desktop/Bosch Hackathon Optimised/chaos-ai"
source .venv/bin/activate
chaosai run --dry-run
chaosai run
chaosai smoke
```

At final verification, first complete the explicitly deferred fresh baseline and Phase 6
checks listed above and in experiments.md/safety.md. An S9 skip, mechanism denial, successful
container rollback or synthetic fixture never counts as a completed live experiment/recovery.
Do not silently clear KILL or repair unjournaled state. Phases 9 and 10 remain unchecked.

### Phase 8 human check review and continuation — 2026-10-11

Phase 8 implementation is complete at local commit f474f23; live verification is partial,
so the phase is almost done rather than fully live-verified. The user supplied terminal
output and authorized continuing to Phase 9, with all waiting-related checks and any
confirmed fixes deferred to final verification after Phase 10. No live commands were run
by the agent during this review. No runtime gate or saved baseline was changed.

- Dry-run 20261011-014504 generated a plan/report without faults. Its newly measured
  baseline has stable=true and zero request errors.
- Run 20261011-014810 recorded one approval, but applied zero faults: S9 timed out with
  checkout 0/10 precheck samples, then measured cooldown failed. Journal is empty;
  rollback_ok=true and cleanup_ok=true do not establish application recovery. The result
  remains skipped/inconclusive, recovered=false, recovery_time_s=null.
- Saved samples confirm auth HTTP 429s: 21/47 baseline login requests, all 26 precheck
  login requests, and all 25 cooldown login requests. This new baseline is stable=true
  under the existing drift rule but is rate-limit-contaminated, not clean verification
  evidence. Historical baseline 20261011-011425 still has stable=false; it is unchanged.
- User-supplied smoke failed at admin login; the database recheck confirmed matching
  credentials. Rate limiting is a likely explanation, not a proven smoke response status.
- RabbitMQ emitted RemoteProtocolError warnings, although both new baselines contain
  some queue-depth observations. Visibility is intermittent, not completely restored.

The target has a shared 100-auth-request/15-minute IP window. Allow a fresh window before
final live verification and avoid consecutive fresh dry-run/run baselines. Waiting alone
does not prove readiness and a long sequence can exhaust the budget again. Re-measure a
clean baseline; do not reuse the contaminated one or reset/bypass the limiter. At the end
of Phase 10, complete >=4 injected experiments across >=3 fault types, measured recovery,
passing smoke and the deferred Phase 6 impact/abort/KILL/signals/Day-1 checks. Diagnose and
fix reproducible issues then; no successful recovery or multi-fault demonstration is claimed.

## Phase 9 — 2026-10-11

The user explicitly authorized moving to the next phase after documenting Phase 8 as almost
complete, with waiting-related live verification and confirmed issue fixes deferred until
final verification after Phase 10. Implemented Phase 9 only. Phase 10 and stretch/netem were
not started. Phase checkboxes track implementation, not completion of pending human checks.

Replaced fixture scoring with pure BUILD_PLAN.md section 7 penalties, integer rounding,
overall mean, bands, deterministic severity and ranking. Original example scores remain
53/17/95 (overall 55); postgres-pause severity is now correctly critical and the confirmed
95-point CPU example appears under What held up, excluded from weaknesses. Scores never
come from the model. Skips/absent during measurements are excluded; sparse/inconclusive
scores are descriptive and partial coverage is explicit. Zero baseline latency, unknown
recovery and operator versus Watchdog abort handling are documented in analysis.md. Added
watchdog_aborted metadata without changing the runner's safety/abort/undo/recovery behavior;
legacy metric reason patterns remain usable. Baseline, thresholds and original fixtures
are unchanged.

Added analyzer prompt and per-experiment Finding calls plus one Summary call through the
existing bounded LLM session. Input quotes spec, hypothesis, verdict, baseline/during/after,
measured recovery/self-heal, container events, <=3 bounded untrusted log excerpts, limitations,
scores/severity and archived source facts. Finding identity and exact supplied evidence
citations are validated; empty explanations/fixes, invalid JSON or unavailable responses
fall back per experiment. Severity, all scores, bands, weakness ordering and fixes in the
summary remain deterministic. No self-heal does not prove a missing restart policy. Skipped
or unmeasured results get diagnostics without LLM calls. Stopped live runs use fallback after
cleanup. analysis.json records per-item/summary source and fallback reason; run.json records
analyzer source(s) and data_source=fixture|measured. Existing planner provenance is retained.

Replaced the minimal report with packaged Jinja HTML/Markdown templates, autoescape on for
all text. Inline CSS/SVG only; plan-ordered cards group hypotheses, verdicts, score breakdowns,
events, mean-latency sparklines, observations, finding/fixes and recovery/cleanup evidence.
Includes baseline thresholds, decision trace with requested/final reasons/clamps, ranked
weaknesses, What held up, methodology and run/source metadata. Banner follows data_source,
not offline; explicit legacy measured/reused-baseline provenance is respected, ambiguous
old runs keep the conservative fixture default. Sample/output paths cannot escape their run.

`report <run>` regenerates stored artifacts without target/LLM access. `replay <run>` recomputes
analysis and scores from evidence and exact validated response-cache matches, with no transport
instance, Docker, target, dotenv or network. Cache misses/malformed/semantically invalid entries
use explicit fallback. Replay retains original plan, decisions, baseline and experiment evidence;
only derived analysis/report artifacts change. Matching per-item source provenance is retained.
Offline run and dry-run stay isolated; live run analyzes only after the existing safety cleanup.
Updated README, experiments.md, llm-planning.md and added docs/analysis.md. No dependencies added.

Actual agent checks using the existing .venv:
- ruff check .: passed.
- pytest -q -m "not integration": 584 passed, three integration tests deselected. The existing
  google-genai/Python 3.14 deprecation warning remains; no failures.
- python -m chaosai run --offline --yes: passed; synthetic report at
  runs/20261011-020747/report.html.
- python -m chaosai run --offline --dry-run: passed; unscored plan-only report at
  runs/20261011-020748/report.html.
- python -m chaosai report runs/20261011-020747: passed.
- python -m chaosai replay runs/20261011-020747: passed using deterministic cache-miss fallback;
  mock tests separately confirm exact cached replay with transport creation forbidden.
- git diff --check: passed; read-only target git status clean.

New mock-only tests cover every penalty and boundary, zero latency, unknown recovery,
Watchdog/operator distinction, exclusion of skips/empty evidence, rounding bounds, bands,
severity/rank/held-up split, valid mock calls, invalid JSON retry/fallback, invalid citations/
identity, unavailable summary, model score/severity/rank overrides, archived source facts,
untrusted excerpt bounds, no unsupported restart-policy assertion, cache-only CLI replay,
missing/invalid/semantic caches, mixed source preservation, report sections/order, fixture/
measured/legacy banners, HTML escaping across findings/logs/decisions/baseline, self-contained
assets, sample/output confinement and missing timelines. Existing safety/cleanup tests pass.
No agent live Docker, HTTP/load, real LLM calls or .env/key inspection occurred. Target and
unrelated .DS_Store, =, checkout, login, retrieve remain untouched and excluded from staging.
Local commit message: feat: analyzer, scoring and report. Nothing pushed.

Open issues: natural-language explanations still require human assessment; exact citation
checks cannot prove every causal inference. Changed prompts/evidence can miss old cache keys
and use fallback. No live fault/recovery evidence was created by this phase. CPU configurations
that cannot restore exactly, auth window/budget, sparse recovery and intermittent RabbitMQ
telemetry remain open Phase 8/earlier limits; none was bypassed to obtain a report/score.

**Phase 9 "You verify" (copied from BUILD_PLAN.md; pending for final verification after Phase 10):**
with a real run directory, `chaosai report <run>` opens a readable `report.html`; with Wi-Fi off,
`chaosai replay <run>` still works.

```sh
chaosai report runs/<real-run-id>
open runs/<real-run-id>/report.html
# With Wi-Fi off:
chaosai replay runs/<real-run-id>
open runs/<real-run-id>/report.html
```

Retain the Phase 8 dry-run pass, but complete >=4 actual live experiments across >=3 fault types,
passing smoke, a fresh clean baseline and all deferred Phase 6 impact/application recovery,
Watchdog abort, active-fault KILL, Ctrl-C and Day-1 checks after Phase 10. Allow a clean auth
window when needed; waiting alone never establishes readiness. Investigate and fix reproducible
issues then. Phase 10 remains unchecked and unimplemented.

### Phase 10 handoff prepared — 2026-10-11

At the user's explicit request, updated workspace-root BUILD_PLAN.md with the current Phase 10
continuation checkpoint and a full copy-paste prompt. This is the authorized exception for
that named root document; all other writes stay inside chaos-ai/. Recorded Phase 8/9 commits,
584 passing unit tests, actual partial human evidence, pending checks and final-verification
scheduling. Older Phase 6/8 prompts are historical, not gates against authorized continuation.
Phase 10 implementation has not started; its checkbox remains unchecked.

The saved-HTML report review confirmed expected scores, sections, three SVGs and no external
assets, but Computer Use rejected file:// browser access. No rendered visual review occurred
and no bypass/workaround was attempted. Human visual checks remain pending. Phase 10 polish
candidates are readable timestamps, labeled charts/recorded phase markers and readable baseline
thresholds; these are source-review suggestions, not visually confirmed layout defects.

Final verification after Phase 10 implementation must retain all deferred Phase 6/8/9 checks,
clean baseline/auth window, clean-run reproduction and actual measured sample-report evidence.
No eligible successful real run is established by the known skipped Phase 8 attempt; never
relabel a fixture or skip to complete the sample-run deliverable. Waiting alone, successful
rollback, script exit or selected-spec count cannot prove injection/recovery/diversity.
No live checks, .env reads, target changes or unrelated-file changes occurred in this handoff.
Validation: checkpoint/prompt content and diff whitespace checked. Code/tests were unchanged;
prior Phase 9 checks remain the latest code verification. Handoff committed locally only.


## Phase 10 — 2026-10-11

Implemented the feature freeze only, from handoff b3f7dd7. No Phase 11, netem, new fault,
re-planning, target patch, dependency or relaxed safety/recovery/scoring gate was added.
Phase checkboxes record implementation; deferred human evidence and the measured sample
submission remain pending.

Built safe executable scripts/clean_run.sh: configured existing target or pinned clone under
.chaosai/target-source, existing patch flow only if needed on that clone, venv bootstrap,
KILL/active-lease/pending-or-malformed-journal preflight, scoped Compose up, doctor/seed/smoke,
run --yes --max-experiments 4, report-path/completed-evidence validation and final smoke.
It retains volumes, failed-run output and journals, never resets the target or clears KILL.
A reported reproduction pass requires a stable error-free measured baseline, >=4 completed,
non-skipped supported verdicts with recorded OBSERVE/during evidence, cleanup/exact undo and
measured recovery across >=3 actual fault types. Selected specs/CLI success alone do not pass.
Independent safety demonstrations, visual QA and sample publication still need human review.
Live script steps were never executed by the coding agent; shell subprocess tests use fake
bootstrap/CLI/git plus local artifact validators with config replaced (no dotenv/network).

Completed the final README (deployment, CLI/config, Mermaid, S1–S10, score formula, AI and
fallbacks, offline/replay, limitations and MIT target/patch provenance), docs/architecture.md,
docs/demo-script.md (2:55 recording with preparation/waits outside the video),
docs/freeze-audit.md and the complete serial human checklist in docs/final-verification.md.
examples/sample-run/README.md records the measured deliverable as pending, with inspected run
provenance and required five-file follow-up. No synthetic/skipped report was substituted.
No eligible completed real run exists: measured dry-run 014504 has no results; 014810 has
only a skipped zero-impact entry; standalone inject attempts 005504/005640 have zero during
samples/no full run report. The video and measured findings submission remain human work.

Confirmed fixes from the source/invariant audit:
- Unexpected admin HTTP failures now retain the numeric response status through the mandatory
  matching-credential recheck. No response body/key/URL or automatic retry is added. This makes
  future smoke/seed diagnosis actionable without inventing the historical smoke status.
- Default fresh runs no longer parse an unused latest-baseline cache. Opt-in reuse of an
  unreadable cache measures fresh evidence instead; freshness/stability/S9 remain unchanged.
- Direct chaosai up now refuses KILL/pending/malformed journals and holds the existing active
  lease/operation lock through Compose execution, preventing recreation of an active or
  unresolved journaled container. No automatic replay/repair or container normalization.
- Reports label graph flows as touch-sets, show readable UTC milliseconds and threshold tables,
  and label chart latency/time axes with recorded in-span phase markers. INJECT is an attempt,
  OBSERVE means apply returned and UNDO begins restoration. No invented event/recovery times,
  source/ranking/score changes, external assets or escaping regressions. Rendered visual QA
  remains pending; the existing Computer Use file:// restriction was not bypassed.

Actual agent checks, using the existing .venv:
- .venv/bin/ruff check .: passed.
- .venv/bin/pytest -q -m "not integration": **615 passed, 3 integration tests deselected**.
  The existing installed google-genai/Python 3.14 deprecation warning remains; no failures.
  Initial mock-shell tests exposed a harness shebang issue with the spaced workspace path;
  the quoted wrapper fixed it. Final full suite passed after all code/script changes.
- .venv/bin/python -m chaosai --help: passed. Documentation check matched all 16 commands,
  checked **19 root/subcommand help invocations and documented flags**, and verified **24
  local documentation links**. The external HTTPS target-repository link was checked for
  syntax only, not fetched under the no-network scope.
- bash -n scripts/clean_run.sh scripts/day1_demo.sh: passed. Embedded Python bodies were
  formatted/compiled and executed only against mocked settings/artifacts in unit checks.
- .venv/bin/python -m chaosai run --offline --yes: passed; synthetic report at
  runs/20261011-022844/report.html.
- .venv/bin/python -m chaosai run --offline --dry-run: passed; unscored fixture preview at
  runs/20261011-022845/report.html.
- .venv/bin/python -m chaosai report runs/20261011-022844: passed.
- .venv/bin/python -m chaosai replay runs/20261011-022844: passed, cache-only with explicit
  cache-miss deterministic fallback. Existing mock tests confirm matching cached responses
  work with transport creation forbidden; this is not real-run/Wi-Fi-off verification.
- git diff --check: passed; read-only target git status remains clean.

New regressions cover secret-free HTTP status retention/no retry, unused/corrupt reusable
baseline handling, Compose safety blocks/lease duration, chart scale/recorded marker bounds/
readable times and all prior escaping, and clean-run scoped ordering/pinned cloning/failure
stops/KILL/pending undo/malformed journals/active lease/patch refusal/truthful completion.
No live Docker, HTTP/load, integration test, real LLM call or private .env/key read occurred.
All writes remain inside chaos-ai. Historical evidence, unrelated .DS_Store and untracked
=, checkout, login, retrieve stay untouched and excluded from Phase 10 staging. No run data
is staged. Local commit message: docs: freeze, readme, architecture, clean run. Never push.

Preserved evidence: Phase 8 dry-run 20261011-014504 passed. Run 20261011-014810 injected zero
faults after auth HTTP 429s blocked checkout S9 (0/10) and cooldown. Its stable=true baseline
has 21/47 login errors; all 26 precheck/25 cooldown login requests were 429. Smoke failed at
admin login with matching credentials, but its actual HTTP status remains unknown. Historical
20261011-011425 stays stable=false (32.28% checkout p95 drift vs unchanged 30%). Fixture
scores 53/17/95, overall 55 (weak), remain synthetic; the real target is unassessed.

Open limits: shared 100/15-minute auth budget (preceding/long traffic can exhaust it again),
sparse all-journey 1s recovery (regular 0.5/s login admission leaves gaps), intermittent
RabbitMQ telemetry, exact CPU quota-0/NanoCpus refusal, unknown/live traffic-owner undo,
bounded SDK/HTTP cleanup delays and natural-language interpretation review. Waiting alone
cannot establish readiness. No limiter/threshold/recovery/scoring bypass is authorized.

**Phase 10 You verify (Docker), copied from BUILD_PLAN.md:** run scripts/clean_run.sh from
scratch on your machine, start to finish.

[Complete final human verification commands/checklist](final-verification.md) preserve all
unfinished work: fresh uncontaminated stable baselines, Phase 6 measured impact/recovery,
strict Watchdog abort, active-fault KILL, Ctrl-C and Day-1 walkthrough; Phase 8 >=4 completed
experiments across >=3 actual fault types and passing smoke; Phase 9 rendered real report
and Wi-Fi-off replay; three human integration tests; eligible five-file sample provenance,
<=3-minute video and submission deliverables. All remain **pending, not passed**; the historical
Phase 8 dry-run pass is retained. Run serial auth traffic with fresh windows when needed,
inspect actual evidence and provide sanitized failures for further confirmed fixes within
this freeze. Phase 11 stays unstarted.


## Authorized final follow-up: runtime fixes and live dashboard

The user's request to fix repeated waits, deliver the complete product and add a live
dashboard supersedes the earlier freeze and human-only live verification schedule.
All original third-party source and historical measurements remain unchanged.

Built loopback dashboard with real metrics, event timelines, experiments, telemetry,
service map, severity-ranked findings, reports, explicit approval and safety controls.
Added owned auth image (6000 local requests/15min, original default 100 retained),
bounded verified-credential cache, version-2 stability, measured adaptive recovery,
capability-aware/diverse planning, exclusive load leases, queue polling during faults,
clear run phases, inspectable planner fallback reasons and 60s LLM timeout.
Fixed timeout-starved watchdog windows and approval quit continuing shell demos.
See runtime-fixes.md and dashboard.md for assumptions and details.

Actual checks: ruff passed; 656 unit tests passed, three integration tests deselected
(before the final severity-ranking regression); all three live integration tests passed.
Fresh clean reproduction `20261011-035048` exited 0: stable error-free full baseline,
four applied fault types, exact undo/cleanup, all measured recoveries (10/6/10/14s),
cooldowns and final smoke. Score 55/100 weak; real dependency weaknesses remain.
Gemini plan `040607` succeeded after explicit payload/destination approval. The complete
run used heuristic planner after transport failure and mixed Gemini/fallback analysis.

Day-1 script exited 0: normal Redis impact/recovery `040603`, strict watchdog abort at
0.05 with 8s recovery `040831`, active-pending-journal KILL `041308`, restored/done
journal and clean undo. Owner won the replay race (kill command printed zero records);
the result records Kill switch requested. recovered=false after KILL is retained.
Dashboard-approved `041508` watchdog-aborted and recovered in 6s; active Ctrl-C
`041633` exited 130 with done journal and successful rollback/cleanup.
Real cache-only replay succeeded; no physical network-interface changes were made.

Selected measured report/graph/baseline/decisions exported to examples/sample-run with
provenance, hashes and secret review. Dashboard/report rendered and inspected in browser.
Judge walkthrough follows requested app → AI → fault → restoration → ranked report →
dashboard order; docs/demo-script.md includes seven-minute and <=3-minute video schedules.
Video recording, final human rehearsal, repository push and submission form remain human
work. Only local commit; no push. Historical notes above describe earlier checkpoints.
Unrelated .DS_Store, =, checkout, login, retrieve remain untouched and unstaged.

Open limitations: intermittent RabbitMQ metrics, exact CPU quota-0/NanoCpus refusal,
sparse statistics remain descriptive, runtime adaptation is deterministic safety and
readiness rather than AI online replanning; remediation restores faults and recommends
code fixes. No host-network/disk/netem changes. Target checkout remains read-only.

Original You verify requirements and the actual completed checks are recorded in
final-verification.md. Phase 11 remains unstarted.

Final checks after severity ranking: **657 unit tests passed, 3 integration deselected**;
ruff, script syntax, whitespace and target clean status passed. Final doctor and smoke
passed after Ctrl-C. KILL is clear; no active lease or pending undo. Local dashboard left
running at http://127.0.0.1:8765 with measured run 035048 selected.
