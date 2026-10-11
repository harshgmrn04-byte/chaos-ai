#!/usr/bin/env bash
# Human-run reproduction only. Keeps volumes, journals, KILL and the existing target source.
set -euo pipefail
cd "$(dirname "$0")/.."
project_root="$PWD"
clone=false
case "${1:-}" in
  '') ;;
  --clone) clone=true ;;
  --help) echo 'Usage: bash scripts/clean_run.sh [--clone]'; exit 0 ;;
  *) echo 'Usage: bash scripts/clean_run.sh [--clone]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Only --clone is accepted.' >&2; exit 2; }
if "$clone"; then
  # New checkout stays inside chaos-ai and is never substituted for an active undo target.
  export TARGET_DIR="$project_root/.chaosai/target-source"
fi
stage=bootstrap
trap 'echo "Failed during $stage. Retain runs and journals; inspect the failure before retrying." >&2' ERR
bash scripts/bootstrap.sh
stage='safety preflight'
.venv/bin/python - <<'PY'
import sys

sys.excepthook = lambda kind, value, tb: sys.stderr.write(
    f"Validation failed ({kind.__name__}); inspect configuration/journal/artifact shapes locally.\n"
)
from chaosai.config import load_config
from chaosai.models import JournalData
from chaosai.runs import read_json
from chaosai.safety import file_lock

settings = load_config()
if settings.kill_path.exists():
    raise SystemExit(
        "KILL remains set. Review rollback after the injector exits, then explicitly use chaosai rollback --clear-kill."
    )
with file_lock(settings.active_lock_path, blocking=False):
    for path in settings.runs_dir.glob("*/journal.json"):
        raw = read_json(path, object)
        data = JournalData() if raw == [] else JournalData.model_validate(raw)
        if any(not record.done for record in data.records):
            raise SystemExit(
                "Pending undo blocks bring-up/recreation. Run chaosai rollback and investigate retained failures first."
            )
PY
# Signals request cleanup and persist KILL. The runner owns normal/exception rollback.
trap 'echo "Interrupted; requesting KILL and rollback." >&2; .venv/bin/python -m chaosai kill || true; exit 130' INT TERM
if "$clone"; then
  stage='target clone'
  [[ ! -e "$TARGET_DIR" ]] || { echo 'Clone destination exists; configure TARGET_DIR to reuse it instead.' >&2; exit 1; }
  git clone https://github.com/andrelima28439/ecommerce-microservices.git "$TARGET_DIR"
  revision="$(.venv/bin/python -c 'from chaosai.planner_facts import SOURCE_REVISION; print(SOURCE_REVISION)')"
  git -C "$TARGET_DIR" checkout --detach "$revision"
fi
stage='target configuration'
.venv/bin/python - <<'PY'
import sys

sys.excepthook = lambda kind, value, tb: sys.stderr.write(
    f"Validation failed ({kind.__name__}); inspect configuration/journal/artifact shapes locally.\n"
)
from chaosai.config import load_config

settings = load_config()
if not (settings.resolved_target_dir / settings.compose_file).is_file():
    raise SystemExit("TARGET_DIR has no Compose file. Configure an existing target or use --clone.")
print(f"Target: {settings.resolved_target_dir}; Compose project: {settings.compose_project}")
PY
# No payment patch is currently required. Never apply patches to the read-only sibling.
if compgen -G 'patches/*.patch' >/dev/null; then
  stage=patches
  if ! "$clone" || [[ ! -f scripts/apply_patches.sh ]]; then
    echo 'Patch files require --clone and the documented scripts/apply_patches.sh flow; existing source stays read-only.' >&2
    exit 1
  fi
  bash scripts/apply_patches.sh
fi
stage='scoped Compose bring-up'
.venv/bin/python -m chaosai up
stage=doctor
.venv/bin/python -m chaosai doctor
stage=seed
.venv/bin/python -m chaosai seed
stage=smoke
.venv/bin/python -m chaosai smoke
stage='measured run'
# No extra baseline or live dry-run: both would consume the same auth budget again.
# A fresh baseline is measured by run. This can still exhaust the target's auth budget.
run_log="$(mktemp "$project_root/.chaosai/clean-run.XXXXXX")"
.venv/bin/python -m chaosai run --yes --max-experiments 4 | tee "$run_log"
stage='evidence validation'
.venv/bin/python - "$run_log" <<'PY'
import sys

sys.excepthook = lambda kind, value, tb: sys.stderr.write(
    f"Validation failed ({kind.__name__}); inspect configuration/journal/artifact shapes locally.\n"
)
from pathlib import Path
from chaosai.config import load_config
from chaosai.models import Baseline, ExperimentResult, JournalData, RunMeta
from chaosai.runs import read_json

settings = load_config()
paths = [
    line.removeprefix("Report: ").strip()
    for line in Path(sys.argv[1]).read_text().splitlines()
    if line.startswith("Report: ")
]
if len(paths) != 1:
    raise SystemExit("Run produced no unique report path; reproduction is incomplete.")
report = Path(paths[0]).resolve()
if not report.is_relative_to(settings.runs_dir.resolve()) or not report.is_file():
    raise SystemExit("Report is missing or outside configured runs; reproduction is incomplete.")
run = report.parent
meta = read_json(run / "run.json", RunMeta)
baseline = read_json(run / "baseline.json", Baseline)
results = [read_json(p, ExperimentResult) for p in (run / "experiments").glob("*/result.json")]
complete = [
    r
    for r in results
    if not r.skipped
    and not r.aborted
    and r.verdict in {"confirmed", "refuted"}
    and r.rollback_ok
    and r.cleanup_ok
    and r.recovered
    and r.recovery_time_s is not None
    and 0 <= r.recovery_time_s <= 120
    and any(e.phase == "OBSERVE" for e in r.events)
    and any(s.n > 0 for s in r.during.values())
]
raw = read_json(run / "journal.json", object)
journal = JournalData() if raw == [] else JournalData.model_validate(raw)
print(f"Report: {report}")
print(
    f"Completed measured experiments: {len(complete)}; fault types: {len({r.spec.fault for r in complete})}."
)
if (
    meta.data_source != "measured"
    or meta.offline
    or meta.stop_reason
    or settings.kill_path.exists()
    or any(not r.done for r in journal.records)
):
    raise SystemExit(
        "Run is synthetic, stopped, killed or has pending undo; reproduction is incomplete."
    )
if not baseline.stable or any(s.n == 0 or s.error_rate > 0 for s in baseline.journeys.values()):
    raise SystemExit(
        "Baseline is unstable, empty or error-contaminated; reproduction is incomplete."
    )
if len(complete) < 4 or len({r.spec.fault for r in complete}) < 3:
    raise SystemExit(
        "Need >=4 completed measured experiments across >=3 fault types; selected specs/skips do not count."
    )
PY
stage='final smoke'
.venv/bin/python -m chaosai smoke
trap - INT TERM ERR
echo 'Reproduction checks passed. Review measured results, report readability and independent safety demos before claiming final verification.'
