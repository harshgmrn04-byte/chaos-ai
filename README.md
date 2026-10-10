# ChaosPilot

AI-driven chaos engineering for cloud-native resilience. Python package: `chaosai`.

Phase 2 provides live target bring-up, diagnostics, seeding and checkout verification.
Phase 3 maps Compose deterministically, with manual overrides and Mermaid output.
Phase 4 adds live threaded load, scoped telemetry and measured baselines.
The `run` command maps Compose and measures a live baseline by default; `--offline` uses
bundled fixtures without target access. Experiments and findings remain synthetic;
real fault execution and analysis arrive in later phases. The policy
decisions, rollback and recovery evidence are also fixtures. Real safety controls and scoring
arrive in later phases.

```sh
cd chaos-ai
bash scripts/bootstrap.sh
source .venv/bin/activate
ruff check .
pytest -q -m "not integration"
python -m chaosai run --offline --yes
```

Bootstrap requires Python >=3.11 and creates `.venv`. To choose an interpreter explicitly,
use `PYTHON=/path/to/python3.11 bash scripts/bootstrap.sh`.

The CLI prints the generated `runs/<timestamp>/report.html` path. Open it in a browser.
The HTML has inline CSS and requires no network. Run JSON, plan, decisions, findings, scores,
summary and per-experiment JSON/JSONL evidence are stored alongside it. `journal.json` is empty
because no faults are applied. Run directories and credentials are git-ignored.

```sh
python -m chaosai run --offline             # interactive y/n/a/q approval per experiment
python -m chaosai run --offline --dry-run   # plan + report, no simulated execution or approvals
python -m chaosai report runs/<timestamp>  # regenerate report from saved artifacts
chaosai --help
```

Without a TTY, execution requires `--yes`. `n` skips, `a` approves the remaining experiments,
and `q` writes a report for experiments already completed. EOF or Ctrl-C at the approval
prompt also quits cleanly. Dry runs and entirely skipped plans have no assessed score.

Commands deferred to later phases print `not implemented yet` and exit 2: `fault`,
`inject`, `rollback`, `kill`, `plan`, `replay`.

## Application graph (Phase 3)

```sh
chaosai map --mermaid
chaosai map --run-dir runs/<timestamp>  # update an existing run's graph
chaosai map --offline                  # packaged graph; no target files
```

Mapping prints nodes, ports, dependency evidence and flows, writes runs/<timestamp>/graph.json
and .chaosai/graph.json, and optionally writes docs/graph.mmd. No Docker, HTTP, LLM or dotenv
read occurs. TARGET_DIR and COMPOSE_PROJECT may be exported in your shell; their defaults
match this checkout. env_file declarations are parsed without opening their private contents.
CORS/allowed-origin settings are excluded and recorded in sources.ignored_config. Flows render
as `touches: a, b, c`, ordered from user entry point to deepest dependencies. Isolated tool
nodes are non-injectable by default with a reason; explicit node overrides can change that.
See [docs/overrides.md](docs/overrides.md) for override format/inference rules, and
[docs/mapper-cross-check.md](docs/mapper-cross-check.md) for every dependency difference from
the target notes. The shipped overrides define browse, login and checkout participants.

## Load and baseline (Phase 4)

Retain the seeded load users' current password in SEED_PASSWORD. An empty value, absent
account, mismatch or unavailable DB/bcrypt verification skips that user without sending login.
Run after starting the target and seeding it:

```sh
chaosai load --users 10 --duration 30
chaosai baseline --quick       # 10s discarded warm-up + 40s measurement
chaosai baseline               # 30s discarded warm-up + 120s measurement
chaosai baseline --duration 120
```

These commands create real pending orders and trigger the target's notification consumer.
CHECKOUT_RPS defaults to 1 globally across workers, including a possible retry after 401.
HTTP 500 and timeouts never cause an order retry. AUTH_RPS is limited to 0.1 (90 requests
per 15 minutes) to leave headroom under auth's IP limiter; run only one load process at a time.
Recent seed/smoke calls also consume that IP limit, so allow its 15-minute window to clear
if you see 429s. Tokens are cached per account and renewed by credential-verified login
on use after ten minutes, and after 401. No unsafe refresh-token error probe is needed.

The offered journey mix is 10% homepage, 60% browse, 10% login and 20% checkout; caps reduce
actual admitted login/checkout traffic. Samples describe individual HTTP requests, so browse
normally contributes three samples. Requests time out after five seconds. Workers pause
0.2–1.0s between journeys and can be added/removed through LoadGenerator without resetting
caps or token caches; all worker threads, including retiring ones, are bounded to 150.

Baseline saves runs/<timestamp>/baseline.json and .chaosai/baseline.json. The run also contains
samples.jsonl, containers.jsonl, queues.jsonl and probes.json. Stability requires each journey
to have samples in both halves and its p95 to differ by at most 30% of the first half. Quick
mode has few login samples, so repeat a full window to assess stability. Inspect error rates
as well: stable latency alone does not establish health. See [docs/load-baseline.md](docs/load-baseline.md)
for windowing, threshold derivation and telemetry semantics. Defaults and overrides are in
.env.example; LOAD_USERS sets baseline workers and BASELINE_DURATION_S sets the full window.

## Live target commands (Phase 2)

Configure `.env` yourself from `.env.example`; it is ignored by Git. Set a Gemini key locally
for doctor's key-presence check (no model is called). If `admin@test.com` already exists,
set `ADMIN_PASSWORD` to its existing password; seed does not reset existing accounts.
Keep `SEED_PASSWORD` set to the existing load users' password. New/default admins use
SEED_PASSWORD when ADMIN_PASSWORD is unset. The default password is for local test accounts only;
all generated load users share it.

Run these commands from this directory with `.venv` active:

```sh
chaosai up
chaosai status
chaosai doctor
chaosai seed
chaosai seed                  # confirm repeatability: no new accounts/catalog entries
chaosai smoke
pytest -q -m integration      # readiness, checkout and quick baseline (creates orders)
```

`up` runs `docker compose --project-name COMPOSE_PROJECT up -d --build` in `TARGET_DIR`;
it returns after Compose startup, so wait for services to become ready and re-run `doctor`
if necessary. Build output is withheld to avoid leaking credentials. It never removes volumes.
Relative `TARGET_DIR` is resolved against chaos-ai, so commands work from other directories.
Docker SDK access tries `DOCKER_HOST`, the active CLI context, the Docker Desktop user socket,
then `/var/run/docker.sock`; every candidate must answer a ping. Container access is filtered
and checked by the exact Compose project label, including before SQL execution and log reads.

`status` lists scoped service/state/health/host ports and fails for missing, stopped, paused,
unhealthy or unpublished services. `doctor` prints a PASS/FAIL table with fix hints for Python,
Docker, all ten target services, host TCP ports, documented catalog GET probes, key presence
and `.env` existence. It checks every currently published host port and does not invent health
endpoints. A missing LLM key fails doctor even though seed/smoke require no LLM.

`seed` creates `admin@test.com` and by default `loaduser001@test.com` through `loaduser020@test.com`,
then promotes admin with the documented PostgreSQL UPDATE and logs in for a new ADMIN token.
Before each register/login, scoped PostgreSQL queries check that account's existence.
Existing users are never re-registered. A bounded Node helper in the scoped auth container uses
its installed Prisma/bcrypt to compare the configured password before any login; a mismatch
stops locally with an ADMIN_PASSWORD/SEED_PASSWORD hint. Candidate passwords are supplied as
transient exec environment values, never embedded in command arguments, files or log text;
hashes stay inside the auth container and only existence/match booleans are returned.
No new target file or Python dependency is needed. This avoids the target's HTTP error paths,
which can hang or crash auth. Auth requests use AUTH_TIMEOUT_S=4 (allowed range 3–5 seconds).
Dropped/timed-out registration/login responses trigger fresh database checks and credential
verification; registration may already have committed. No blind retry occurs. A lost registration
response can be reconciled from verified DB state, with a warning. A lost login response still
fails with a readiness/rate-limit hint because no usable token was received. Public catalog lists (including pagination) identify existing slugs;
admin APIs create two categories and four no-image products with stock 100000, string numeric
fields and `featured: "true"`. Before reporting success, seed verifies all requested account
identities and admin role directly in ecommerce_auth, and total/seed/high-stock product counts
in ecommerce_products. The CLI reports these DB verification counts. Re-running adds only
missing accounts/items. Stock/category
conflicts stop with a precise reason instead of guessing an undocumented update endpoint.
Run one seed process at a time. Use `chaosai seed --users 30` or `SEED_USERS` to choose the count.
The auth service limits each IP to 100 requests per 15 minutes; large seeds or repeated smoke
runs may hit this limit. Successful registrations remain and will be skipped on the next seed.

`smoke` browses products, verifies admin existence/password via the same scoped DB/auth
preflight, then logs in as the seeded admin and creates one pending order using the
frontend's `paymentMethod: "stripe"`, retrieves it and validates owner/items/totals. Each run
creates an order and can trigger the target's Resend handler. It never retries checkout:
HTTP 500 can occur after order persistence. It checks the `notifications` queue (routing key
`order.created`) via management Basic auth, observes consumer count and ready/total messages
before and at three six-second intervals after checkout, and reads bounded notification logs
for a matching order ID. Queue identity/consumer/trend problems, unavailable metrics/API/logs
or absent log receipt produce warnings and do not fail successful checkout. Receipt and stable
queue counts are evidence only; they do not prove acknowledgement or actual Resend delivery.
There is no MailHog receipt check. See target-notes section 11 for the vendor contract.
Docker/PostgreSQL/auth-container access is required for credential preflight; if verification
is unavailable, smoke refuses to send an unverified login. Queue and notification-log evidence
remain best-effort after successful checkout.

No payment patch or patch scripts were created: the source already bypasses placeholder
Stripe keys, and no live failure has demonstrated a need. If checkout fails, retain the
precise error and investigate DB/broker versus payment before considering a minimal patch.
The target working tree stays unchanged by this implementation.

Configuration defaults are in `chaosai/config.py`, with variable names in `.env.example`.
`LLM_PROVIDER=mock` serves validated fixtures; `none` uses deterministic fixture fallbacks.
`--offline` forces `none` and skips dotenv loading. Normal runs load configuration through
python-dotenv, defaulting to Gemini when an API key is present, otherwise `none`; Gemini is
unavailable until Phase 7 and safely falls back. No credentials are needed for Phase 1.

Tests use mock providers or the offline fallback, block network and dotenv reads, and keep
temporary files under `tests/.tmp/`. Docker/live HTTP integration tests are marked `integration` and run only by the human.

See `docs/target-notes.md` for source evidence and `docs/progress.md` for phase status.
