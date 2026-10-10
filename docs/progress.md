# ChaosPilot progress

- [x] Phase 0 — Target reconnaissance
- [x] Phase 1 — Scaffold and walking skeleton
- [x] Phase 2 — Bring-up, seed, smoke test, payment gate, doctor
- [x] Phase 3 — Application mapper
- [ ] Phase 4 — Load generator, metrics, baseline
- [ ] Phase 5 — Faults, safety layer, watchdog, kill switch
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
