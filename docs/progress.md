# ChaosPilot progress

- [x] Phase 0 — Target reconnaissance
- [ ] Phase 1 — Scaffold and walking skeleton
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
