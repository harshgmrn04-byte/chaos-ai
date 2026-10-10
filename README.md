# ChaosPilot

AI-driven chaos engineering for cloud-native resilience. Python package: `chaosai`.

Phase 1 provides a walking skeleton using bundled, synthetic fixtures. It never connects to
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

All other commands print `not implemented yet` and exit 2: `doctor`, `up`, `status`, `seed`,
`smoke`, `map`, `load`, `baseline`, `fault`, `inject`, `rollback`, `kill`, `plan`, `replay`.

Configuration defaults are in `chaosai/config.py`, with variable names in `.env.example`.
`LLM_PROVIDER=mock` serves validated fixtures; `none` uses deterministic fixture fallbacks.
`--offline` forces `none` and skips dotenv loading. Normal runs load configuration through
python-dotenv, defaulting to Gemini when an API key is present, otherwise `none`; Gemini is
unavailable until Phase 7 and safely falls back. No credentials are needed for Phase 1.

Tests use mock providers or the offline fallback, block network and dotenv reads, and keep
temporary files under `tests/.tmp/`. Docker integration tests will be marked `integration`.

See `docs/target-notes.md` for source evidence and `docs/progress.md` for phase status.
