"""Execute the shell only with fake bootstrap/CLI/git; no live step is available."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# This subprocess shim mocks every CLI invocation and replaces config before executing
# the script's local artifact validators. It cannot load dotenv or create a Docker client.
SHIM = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, ROOT)
from chaosai import config
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.models import Baseline, Event, ExperimentResult, RunMeta
from chaosai.runs import write_json
from datetime import UTC, datetime
root = Path.cwd()
config.load_config = lambda: Settings(
    project_root=root,
    target_dir=Path(os.environ.get('TARGET_DIR') or root / 'target'), llm_provider='mock')
if sys.argv[1] == '-':
    sys.argv = sys.argv[1:]
    exec(compile(sys.stdin.read(), '<mocked-local-validator>', 'exec'))
elif sys.argv[1] == '-c':
    exec(sys.argv[2])
elif sys.argv[1:3] == ['-m', 'chaosai']:
    command = sys.argv[3]
    with Path(os.environ['MOCK_LOG']).open('a') as log:
        log.write(' '.join(sys.argv[3:]) + '\\n')
    if command == os.environ.get('MOCK_FAIL'):
        sys.exit(7)
    if command == 'run':
        run = root / 'runs/mock-run'
        write_json(run / 'run.json', RunMeta(
            run_id='mock-run', started_at=datetime.now(UTC), planner_source='mock',
            analyzer_source='mock', target_project='ecommerce-microservices',
            approvals=[], offline=False, data_source='measured'))
        baseline = fixture('baseline', Baseline)
        write_json(run / 'baseline.json', baseline)
        write_json(run / 'journal.json', [])
        mode = os.environ.get('MOCK_EVIDENCE', 'complete')
        if mode == 'fixture':
            meta = json.loads((run / 'run.json').read_text())
            meta['data_source'] = 'fixture'
            write_json(run / 'run.json', meta)
        if mode == 'contaminated':
            baseline.journeys['login'].error_rate = 0.5
            write_json(run / 'baseline.json', baseline)
        for i, fault in enumerate(['stop', 'pause', 'kill', 'stop']):
            result = fixture('results', list[ExperimentResult])[0].model_copy(deep=True)
            result.spec.id = f'exp-{i}'
            result.spec.fault = 'stop' if mode == 'one-type' else fault
            result.events = [Event(t=1, phase='OBSERVE', note='MOCK applied')]
            if mode == 'skipped':
                result.skipped = True
            if mode == 'unrecovered':
                result.recovered = False
                result.recovery_time_s = None
            if mode == 'sparse':
                result.verdict = 'inconclusive'
            if mode == 'failed-cleanup':
                result.cleanup_ok = False
            if mode == 'no-observe':
                result.events = []
            if mode in {'exception-abort', 'watchdog-abort'}:
                result.aborted = True
                result.verdict = 'aborted'
                result.watchdog_aborted = mode == 'watchdog-abort'
            write_json(run / 'experiments' / str(i) / 'result.json', result)
        (run / 'report.html').write_text('MOCK report only')
        if mode != 'no-report':
            print('Report: ' + str(run / 'report.html'))
else:
    raise SystemExit('Unexpected Python invocation in mock')
"""


@pytest.fixture
def shell_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/clean_run.sh", tmp_path / "scripts/clean_run.sh")
    (tmp_path / "scripts/bootstrap.sh").write_text('echo bootstrap >> "$MOCK_LOG"\n')
    (tmp_path / ".venv/bin").mkdir(parents=True)
    shim = tmp_path / ".venv/bin/python"
    shim_source = tmp_path / "python-shim.py"
    shim_source.write_text(f"ROOT = {str(ROOT)!r}\n" + SHIM)
    shim.write_text(
        "#!/bin/bash\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(shim_source))
        + ' "$@"\n'
    )
    shim.chmod(0o755)
    (tmp_path / "target").mkdir()
    (tmp_path / "target/docker-compose.yml").write_text("services: {}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("""#!/usr/bin/env bash
set -eu
echo "git $*" >> "$MOCK_LOG"
if [[ "$1" == clone ]]; then
  mkdir -p "$3"
  echo 'services: {}' > "$3/docker-compose.yml"
fi
""")
    git.chmod(0o755)
    for denied in ("docker", "chaosai"):
        path = bin_dir / denied
        path.write_text('#!/usr/bin/env bash\necho "UNEXPECTED LIVE TOOL" >&2\nexit 99\n')
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("MOCK_LOG", str(tmp_path / "calls.log"))
    monkeypatch.delenv("MOCK_FAIL", raising=False)
    monkeypatch.delenv("MOCK_EVIDENCE", raising=False)
    monkeypatch.delenv("TARGET_DIR", raising=False)
    return tmp_path


def execute(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # subprocess.run is prohibited by the unit isolation fixture. Popen runs only this
    # sandbox copy with fake executables; every live CLI operation is intercepted above.
    process = subprocess.Popen(
        ["bash", "scripts/clean_run.sh", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output, _ = process.communicate(timeout=20)
    return subprocess.CompletedProcess(process.args, process.returncode, output)


def calls(root: Path) -> list[str]:
    return (root / "calls.log").read_text().splitlines()


def test_reuse_order_and_measured_completion(shell_workspace: Path) -> None:
    result = execute(shell_workspace)
    assert result.returncode == 0, result.stdout
    assert calls(shell_workspace) == [
        "bootstrap",
        "up",
        "sandbox",
        "doctor",
        "seed",
        "smoke",
        "run --yes --max-experiments 4",
        "smoke",
    ]
    assert "Completed measured experiments: 4; fault types: 3" in result.stdout
    assert "Report:" in result.stdout


def test_owned_pinned_clone(shell_workspace: Path) -> None:
    result = execute(shell_workspace, "--clone")
    assert result.returncode == 0, result.stdout
    clone, checkout = calls(shell_workspace)[1:3]
    assert clone.endswith(str(shell_workspace / ".chaosai/target-source"))
    assert "checkout --detach bae94402773bc7fd7d05dadd4062dffe2d1c2057" in checkout
    assert calls(shell_workspace)[3] == "up"
    assert (shell_workspace / "target/docker-compose.yml").read_text() == "services: {}\n"


@pytest.mark.parametrize("step", ["up", "sandbox", "doctor", "seed", "smoke", "run"])
def test_failed_live_step_stops_sequence(
    shell_workspace: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    monkeypatch.setenv("MOCK_FAIL", step)
    result = execute(shell_workspace)
    assert result.returncode != 0
    assert calls(shell_workspace)[-1].split()[0] == step
    assert "Retain runs and journals" in result.stdout


@pytest.mark.parametrize(
    "mode",
    [
        "skipped",
        "unrecovered",
        "one-type",
        "fixture",
        "contaminated",
        "sparse",
        "failed-cleanup",
        "no-observe",
        "no-report",
        "exception-abort",
    ],
)
def test_false_completion_never_passes(
    shell_workspace: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("MOCK_EVIDENCE", mode)
    result = execute(shell_workspace)
    assert result.returncode != 0
    assert calls(shell_workspace)[-1] == "run --yes --max-experiments 4"
    assert "Reproduction checks passed" not in result.stdout
    assert (shell_workspace / "runs/mock-run/report.html").is_file()


@pytest.mark.parametrize("block", ["kill", "pending", "malformed"])
def test_safety_preflight_blocks_before_bring_up(shell_workspace: Path, block: str) -> None:
    state = shell_workspace / ".chaosai"
    state.mkdir()
    if block == "kill":
        (state / "KILL").write_text("retained")
    else:
        journal = shell_workspace / "runs/old/journal.json"
        journal.parent.mkdir(parents=True)
        if block == "malformed":
            journal.write_text("{bad secret input")
        else:
            journal.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "records": [
                            {
                                "resource": "load",
                                "id": "pending",
                                "project": "ecommerce-microservices",
                                "fault": "traffic_spike",
                                "original_workers": 1,
                                "owner_pid": 1,
                            }
                        ],
                    }
                )
            )
    result = execute(shell_workspace)
    assert result.returncode != 0 and calls(shell_workspace) == ["bootstrap"]
    assert "bad secret input" not in result.stdout
    if block == "kill":
        assert (state / "KILL").read_text() == "retained"
    else:
        assert journal.is_file()


def test_existing_clone_and_patch_on_sibling_refused(shell_workspace: Path) -> None:
    (shell_workspace / ".chaosai/target-source").mkdir(parents=True)
    result = execute(shell_workspace, "--clone")
    assert result.returncode != 0 and calls(shell_workspace) == ["bootstrap"]
    (shell_workspace / "patches").mkdir()
    (shell_workspace / "patches/example.patch").write_text("MOCK patch")
    result = execute(shell_workspace)
    assert result.returncode != 0 and "up" not in calls(shell_workspace)


def test_active_lease_blocks_before_bring_up(shell_workspace: Path) -> None:
    from chaosai.safety import file_lock

    with file_lock(shell_workspace / ".chaosai/active.lock", blocking=False):
        result = execute(shell_workspace)
    assert result.returncode != 0 and calls(shell_workspace) == ["bootstrap"]


def test_measured_watchdog_abort_counts_after_proven_recovery(shell_workspace, monkeypatch):
    monkeypatch.setenv("MOCK_EVIDENCE", "watchdog-abort")
    result = execute(shell_workspace)
    assert result.returncode == 0, result.stdout
