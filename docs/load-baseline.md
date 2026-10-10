# Live load and baseline

Phase 4 uses the contracts in target-notes sections 2, 3, 11 and 12. No target file is changed.
The homepage is GET / at FRONTEND_URL, whose default is the published host port 3000.
Browse requests GET /products?page=1&limit=12, GET /products/<percent-encoded database ID>,
then GET /categories. Checkout uses a previously read in-stock product and the documented
order_body helper with quantity 1, shippingAddress and paymentMethod=stripe. It validates the
201 OrderResponse shape. This is pending order creation, not completed payment/email delivery.

Accounts are loaduser001@test.com onward, exactly the identities chaosai seed creates.
Only SEED_PASSWORD is used for load users; the admin account is never used. Preparation and
**every actual login** require fresh scoped PostgreSQL existence and auth-container bcrypt
comparison through the Phase 2 helper. Missing/blank/mismatched credentials or failed evidence
skip the user and warn without sending login or registration. Candidate passwords remain in
config and transient helper/HTTP request memory; they are absent from samples, logs and artifacts.
Concurrent account/password changes between preflight and HTTP remain a target limitation;
a failed login is rechecked, with no immediate blind retry.

Tokens are shared under a per-user lock across all workers. Credential-verified re-login renews
JWTs on use at 600 seconds (configurable downward), well before their 15-minute expiry, and
a checkout 401 clears the cached token and requests re-login. Re-login issues both new tokens;
we do not call refresh-token because the target stores only one token per account and an
invalid refresh token uses its unsafe async error path. If auth admission is unavailable, the
next journey reattempts verified login after admission opens. HTTP 500/timeout orders are never
retried: an order can already have persisted. Only middleware 401 is retried once, with a new
token and another checkout admission. Stop cancels waits for that admission.

Journey selection weights are 10/60/10/20 for homepage/browse/login/checkout. Think time is
0.2–1.0 seconds between selections. Each admitted HTTP request has its own sample, labelled
by journey; RPS and latency therefore describe requests rather than whole multistep journeys.
Latency includes transport and response validation, excludes think time, cap admission,
credential verification and serialized JSONL persistence. HTTP 5xx/4xx, timeouts, transport
failures and invalid response contracts count as errors, using secret-free fixed messages.
Skipped users and admission denials do not pretend to be successful requests.

The shared CHECKOUT_RPS cap (default 1) has no initial burst and survives runtime worker changes.
Auth has a separate AUTH_RPS cap (default/max 0.1), permitting about 90 requests per 900 seconds
and leaving headroom under the target's 100/15-minute IP limiter. Caps apply within one generator,
not across concurrent processes or preceding seed/smoke calls; use one live load command at a
time. Let the limiter's window clear if earlier commands consumed its budget. Actual admitted
journey proportions differ from offered weights. Workers are capped at 150, counting retiring
threads; add_workers/remove_workers/set_workers retain sessions and cap state. Removing workers
signals them immediately; in-flight journeys may finish. stop joins workers and closes their
private httpx clients before Docker is closed.

Warm-up (30 seconds normally, 10 quick) is discarded in memory and samples.jsonl. Requests
started before measurement remain discarded even if completed after warm-up. The measured
window is [t0,t0+duration), normally 120 seconds or 40 quick. After the window, in-flight
requests finish so timeout/errors are included when their start timestamp is in the window.
Samples begun after its end are discarded. JSONL appends are serialized with the memory buffer
under one lock and fsynced. Samples use epoch timestamps and monotonic elapsed latency.

Percentiles use linear interpolation over the sorted (n-1)*p/100 index. Empty stats have n=0
and numerical zeros; **zero samples cannot establish stability**. Each journey must have samples
in both halves, and abs(second_p95-first_p95)/first_p95 must be <=0.30. If first_p95 is zero,
second must also be zero. Quick mode's capped login count is small; use repeated full baselines
to evaluate stability. This is a latency stability test, not a health verdict.

The baseline includes individual journeys plus an "all" aggregate. Thresholds use the aggregate
request error rate (or a count-weighted rate when no "all" is supplied), plus exactly 0.02,
with hypothesis p95_multiple=2. Abort defaults are max_error_rate=0.5, p95_multiple=10,
max_unhealthy_containers=3, min_samples=10 and consecutive_breaches=2. No scoring logic is added
in Phase 4; these measurements supply the future deterministic scorer's inputs from sections
5 and 7 of BUILD_PLAN.md.

Scoped Docker stats are polled every five seconds without streaming. CPU% derives total CPU
and system-time deltas between polls, multiplied by online CPU count (may exceed 100% on multiple
cores); the first observation is zero until a delta is available. Memory uses usage/1024^2 (MiB).
Container baselines average CPU/memory over measured observations and take the final status and
cumulative restart count. All statuses/restarts remain available in containers.jsonl. Reloaded
labels are rechecked before stats; external/foreign containers are ignored. Polls slower than
five seconds resume when their bounded SDK requests complete.

Queue depth is total messages, including unacknowledged deliveries, from the validated GET
/api/queues/%2F/notifications management contract, using configured Basic auth. Baseline queue
depth is the measured mean. A missing queue, denied credentials, invalid/missing metrics or
unavailable API produces a warning and no observation, never an invented zero. Queue depth
alone does not prove Resend delivery. Infrastructure probes use actual scoped published TCP
bindings; missing mapped infrastructure is recorded false. HTTP probes use GET / for frontend
and GET /products?limit=1 for product. Auth GET /me and order GET /orders are sent **without**
Authorization and expect middleware 401; this proves middleware responsiveness, not database
health. Auth's exact read route is confirmed in
`ecommerce-microservices/auth-service/src/routes/authRoutes.ts:25`; its missing-header response
is in `src/middlewares/auth.ts:9-12`. Order's read route/middleware are cited in target-notes
section 2. Notification has no published HTTP port and is observed through Docker/queue data.

Normal run now measures baseline but still produces fixture plans/experiments/findings; its
report must not be used as measured chaos findings. --offline retains fully synthetic baseline
and never opens Docker/HTTP or dotenv. Baseline/load do not call an LLM. Live compatibility,
actual credentials, active ports, telemetry and stability remain human integration checks.
