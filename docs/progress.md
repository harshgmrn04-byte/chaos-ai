# ChaosPilot progress

- [x] Phase 0 — Target reconnaissance
- [x] Phase 1 — Scaffold and walking skeleton
- [ ] Phase 2 — Bring-up, seed, smoke test, payment gate, doctor
- [ ] Phase 3 — Application mapper
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
