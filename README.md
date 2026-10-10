# ChaosPilot

AI-driven chaos engineering for cloud-native resilience. Python package: `chaosai`.

Phase 2 provides live target bring-up, diagnostics, seeding and checkout verification.
The Phase 1 `run` command still uses bundled, synthetic fixtures and never connects to
Docker, the target, or a real LLM. The graph follows the Phase 0 source notes; metrics and findings
illustrate possible outcomes and are not observations of the running application. The policy
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

Commands deferred to later phases print `not implemented yet` and exit 2: `map`, `load`,
`baseline`, `fault`, `inject`, `rollback`, `kill`, `plan`, `replay`.

## Live target commands (Phase 2)

Configure `.env` yourself from `.env.example`; it is ignored by Git. Set a Gemini key locally
for doctor's key-presence check (no model is called). If `admin@test.com` already exists,
set `SEED_PASSWORD` to its existing password; seed does not reset existing accounts.
The default password is for local test accounts only. All generated load users share it.

Run these commands from this directory with `.venv` active:

```sh
chaosai up
chaosai status
chaosai doctor
chaosai seed
chaosai seed                  # confirm repeatability: no new accounts/catalog entries
chaosai smoke
pytest -q -m integration      # readiness + one additional checkout
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
A scoped read of existing account emails avoids duplicate registration requests, which can
hang in the target's unwrapped auth controllers. It never reads password hashes or container
environment values. Public catalog lists (including pagination) identify existing slugs;
admin APIs create two categories and four no-image products with stock 100000, string numeric
fields and `featured: "true"`. Re-running adds only missing accounts/items. Stock/category
conflicts stop with a precise reason instead of guessing an undocumented update endpoint.
Run one seed process at a time. Use `chaosai seed --users 30` or `SEED_USERS` to choose the count.
The auth service limits each IP to 100 requests per 15 minutes; large seeds or repeated smoke
runs may hit this limit. Successful registrations remain and will be skipped on the next seed.

`smoke` browses products, logs in as the seeded admin, creates one pending order using the
frontend's `paymentMethod: "stripe"`, retrieves it and validates owner/items/totals. Each run
creates an order and can trigger the target's Resend handler. It never retries checkout:
HTTP 500 can occur after order persistence. It checks the `notifications` queue (routing key
`order.created`) via management Basic auth, observes consumer count and ready/total messages
before and at three six-second intervals after checkout, and reads bounded notification logs
for a matching order ID. Queue identity/consumer/trend problems, unavailable metrics/API/logs
or absent log receipt produce warnings and do not fail successful checkout. Receipt and stable
queue counts are evidence only; they do not prove acknowledgement or actual Resend delivery.
There is no MailHog receipt check. See target-notes section 11 for the vendor contract.

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
