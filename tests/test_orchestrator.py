"""Sequential run control with mocked planner/runner and measured virtual cooldown."""

import signal
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from chaosai import cli, orchestrator, runner
from chaosai.approval import ApprovalRequired, Approver
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.models import (
    Baseline,
    CheckResult,
    ExperimentResult,
    Graph,
    Plan,
    PolicyDecision,
    RunMeta,
)
from chaosai.runs import read_json, write_json
from chaosai.safety import SafetyError, create_kill_flag, policy_gate
from tests.test_evaluate import baseline_for, sample
from tests.test_safety import FakeContainer, spec_for


class Stages:
    def __init__(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        self.settings = settings
        self.order: list[str] = []
        self.calls: list[str] = []
        self.client = SimpleNamespace(close=Mock())
        self.baseline = fixture("baseline", Baseline)
        self.baseline.created_at = datetime.now(UTC)
        self.graph = fixture("graph", Graph)
        self.failure: BaseException | None = None
        self.result_changes: dict[str, object] = {}
        self.ready = True
        self.kill_after = False
        self.unresolved = False
        self.specs = [
            spec_for(fault, target, id=f"exp-{rank}", rank=rank, intensity=intensity)
            for rank, (fault, target, intensity) in enumerate(
                [
                    ("stop", "redis", None),
                    ("pause", "postgres", None),
                    ("cpu_limit", "order-service", 0.2),
                    ("traffic_spike", "browse", 2),
                ],
                1,
            )
        ]

        def reconcile(client: object, settings: Settings, **kwargs: object) -> None:
            self.order.append("reconcile")
            if self.unresolved:
                raise SafetyError("Unresolved undo.")
            if settings.kill_path.exists():
                raise SafetyError("KILL present.")

        def doctor(*args: object) -> list[CheckResult]:
            self.order.append("doctor")
            return [CheckResult(name="ready", passed=True, detail="mock")]

        def mapping(*args: object, **kwargs: object) -> Graph:
            self.order.append("map")
            return self.graph

        def baseline(*args: object, **kwargs: object) -> Baseline:
            self.order.append("baseline")
            return self.baseline

        def planner(*args: object, cache_dir: Path, **kwargs: object) -> tuple[Plan, str]:
            self.order.append("plan")
            decisions = [policy_gate(spec) for spec in self.specs]
            for d in decisions:
                d.source = "mock"
            write_json(cache_dir.parent / "decisions.json", list(reversed(decisions)))
            return Plan(root=self.specs), "mock"

        def execute(
            decision: PolicyDecision, directory: Path, **kwargs: object
        ) -> ExperimentResult:
            self.order.append("runner")
            self.calls.append(decision.experiment_id)
            assert kwargs["lease_held"]
            assert kwargs["approval_choice"] in {"yes", "all"}
            if self.failure:
                error, self.failure = self.failure, None
                raise error
            result = fixture("results", list[ExperimentResult])[0].model_copy(deep=True)
            result.spec, result.decision = decision.final, decision
            result.approval_decision = kwargs["approval_choice"]
            result.rollback_ok = result.recovered = True
            for name, value in self.result_changes.items():
                setattr(result, name, value)
            write_json(directory / "experiments" / result.spec.id / "result.json", result)
            if self.kill_after:
                create_kill_flag(settings)
            return result

        def cooldown(*args: object) -> bool:
            self.order.append("cooldown")
            return self.ready

        monkeypatch.setattr(orchestrator, "get_docker_client", lambda: self.client)
        monkeypatch.setattr(orchestrator, "reconcile", reconcile)
        monkeypatch.setattr(orchestrator, "doctor_lite", doctor)
        monkeypatch.setattr(orchestrator, "map_target", mapping)
        monkeypatch.setattr(orchestrator, "measure_baseline", baseline)
        monkeypatch.setattr(orchestrator, "plan_experiments", planner)
        monkeypatch.setattr(orchestrator, "execute_experiment", execute)
        monkeypatch.setattr(orchestrator, "cooldown_ready", cooldown)
        monkeypatch.setattr(
            orchestrator, "replay_journals", lambda *a: self.order.append("rollback")
        )

    def run(self, answers: list[str] | None = None, **kwargs: object) -> Path:
        replies = iter(answers or [])

        def ask(prompt: str) -> str:
            self.order.append("approval")
            return next(replies)

        return runner.run_pipeline(
            self.settings,
            Approver(
                yes=answers is None,
                is_tty=answers is not None,
                ask=ask,
                simulation=False,
            ),
            **kwargs,
        )


@pytest.fixture
def stages(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Stages:
    return Stages(settings, monkeypatch)


def test_order_rank_approval_cleanup_cooldown_and_full_plan(stages: Stages) -> None:
    directory = stages.run(["y"] * 4)
    assert stages.order == ["reconcile", "doctor", "map", "baseline", "plan"] + (
        ["approval", "runner", "reconcile", "cooldown"] * 4
    ) + ["rollback"]
    assert stages.calls == ["exp-1", "exp-2", "exp-3", "exp-4"]
    meta = read_json(directory / "run.json", RunMeta)
    assert (
        meta.planner_sources == ["mock"] and meta.target_project == stages.settings.compose_project
    )
    assert [a.decision for a in meta.approvals] == ["yes"] * 4
    assert meta.baseline_source == "measured" and meta.stop_reason is None
    assert len(read_json(directory / "plan.json", Plan).root) == 4
    assert len(read_json(directory / "findings.json", list)) == 4
    assert len(read_json(directory / "scores.json", list)) == 4
    assert meta.data_source == "measured" and meta.analyzer_source == "mock"
    stages.client.close.assert_called_once()


@pytest.mark.parametrize(
    "answers,calls,approvals",
    [
        (["n", "y", "q"], ["exp-2"], ["skip", "yes", "quit"]),
        (["a"], ["exp-1", "exp-2", "exp-3", "exp-4"], ["all", "yes", "yes", "yes"]),
        (["q"], [], ["quit"]),
    ],
)
def test_live_y_n_a_q(
    stages: Stages, answers: list[str], calls: list[str], approvals: list[str]
) -> None:
    directory = stages.run(answers)
    assert stages.calls == calls
    assert [a.decision for a in read_json(directory / "run.json", RunMeta).approvals] == approvals


def test_max_experiments_bounds_execution_preserves_trace(stages: Stages) -> None:
    directory = stages.run(max_experiments=2)
    assert stages.calls == ["exp-1", "exp-2"]
    assert len(read_json(directory / "plan.json", Plan).root) == 4
    assert len(read_json(directory / "decisions.json", list)) == 4


@pytest.mark.parametrize("maximum", [0, 9, -1])
def test_maximum_cannot_bypass_policy(stages: Stages, maximum: int) -> None:
    with pytest.raises(ValueError):
        stages.run(max_experiments=maximum)
    assert not stages.order and not stages.settings.runs_dir.exists()


def test_live_dry_run_never_approves_runs_or_cools_down(stages: Stages) -> None:
    directory = stages.run([], dry_run=True)
    assert stages.order == ["reconcile", "doctor", "map", "baseline", "plan"]
    assert not stages.calls
    assert not read_json(directory / "run.json", RunMeta).approvals
    assert not list((directory / "experiments").iterdir())


def test_non_tty_refuses_before_docker_or_artifacts(stages: Stages) -> None:
    with pytest.raises(ApprovalRequired):
        runner.run_pipeline(stages.settings, Approver(yes=False, is_tty=False, ask=input))
    assert not stages.order and not stages.settings.runs_dir.exists()


@pytest.mark.parametrize(
    "stable,age,expected",
    [
        (True, 0, True),
        (True, 3599.9, True),
        (True, 3600, False),
        (True, 3601, False),
        (False, 5, False),
        (True, -1, False),
    ],
)
def test_baseline_freshness_stability_boundaries(stable: bool, age: float, expected: bool) -> None:
    baseline = fixture("baseline", Baseline)
    baseline.stable = stable
    now = datetime.now(UTC)
    baseline.created_at = now - timedelta(seconds=age)
    assert orchestrator.baseline_reusable(baseline, now) is expected


@pytest.mark.parametrize(
    "stable,age,measured", [(True, 30, False), (False, 30, True), (True, 3600, True)]
)
def test_reuse_flag_never_bypasses_unsuitable_baseline(
    stages: Stages,
    stable: bool,
    age: float,
    measured: bool,
) -> None:
    baseline = stages.baseline.model_copy(deep=True)
    baseline.created_at = datetime.now(UTC) - timedelta(seconds=age)
    baseline.stable = stable
    write_json(stages.settings.safety_dir / "baseline.json", baseline)
    directory = stages.run(dry_run=True, reuse_baseline=True)
    assert ("baseline" in stages.order) is measured
    assert read_json(directory / "run.json", RunMeta).baseline_source == (
        "measured" if measured else "reused"
    )
    assert read_json(stages.settings.safety_dir / "baseline.json", Baseline) == baseline


def test_exception_recorded_rollback_then_readiness_before_continuation(stages: Stages) -> None:
    stages.failure = RuntimeError("password=not persisted")
    directory = stages.run(max_experiments=2)
    assert stages.calls == ["exp-1", "exp-2"]
    first = read_json(directory / "experiments" / "exp-1" / "result.json", ExperimentResult)
    assert first.aborted and first.rollback_ok and not first.recovered
    assert "not persisted" not in first.model_dump_json()
    assert stages.order[5:10] == ["runner", "rollback", "reconcile", "cooldown", "runner"]


@pytest.mark.parametrize(
    "changes",
    [
        {"rollback_ok": False},
        {"cleanup_ok": False},
        {"interrupted": True},
    ],
)
def test_failed_rollback_cleanup_and_signal_stop(
    stages: Stages, changes: dict[str, object]
) -> None:
    stages.result_changes = changes
    directory = stages.run()
    assert stages.calls == ["exp-1"]
    assert "cooldown" not in stages.order
    assert read_json(directory / "run.json", RunMeta).stop_reason


@pytest.mark.parametrize("failure", [False, True])
def test_unproven_recovery_blocks_continuation(stages: Stages, failure: bool) -> None:
    stages.ready = False
    stages.result_changes = {"recovered": False}
    if failure:
        stages.failure = RuntimeError("failed apply")
    directory = stages.run()
    assert stages.calls == ["exp-1"]
    assert "readiness/recovery failed" in read_json(directory / "run.json", RunMeta).stop_reason


def test_kill_between_experiments_remains_set(stages: Stages) -> None:
    stages.kill_after = True
    with pytest.raises(SafetyError, match="Kill switch"):
        stages.run()
    assert stages.calls == ["exp-1"] and "cooldown" not in stages.order
    assert stages.settings.kill_path.exists()


def test_unresolved_undo_stops_before_doctor(stages: Stages) -> None:
    stages.unresolved = True
    with pytest.raises(SafetyError):
        stages.run()
    assert stages.order == ["reconcile", "rollback"]
    assert not stages.calls


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signals_during_planning_stop_and_restore_handlers(
    stages: Stages,
    monkeypatch: pytest.MonkeyPatch,
    signum: int,
) -> None:
    previous = signal.getsignal(signum)
    monkeypatch.setattr(
        orchestrator, "plan_experiments", lambda *a, **kw: signal.raise_signal(signum)
    )
    # Stop before any plan is produced: metadata remains readable, no fabricated report.
    directory = stages.run()
    assert read_json(directory / "run.json", RunMeta).stop_reason == "Interrupted by signal."
    assert not stages.calls and signal.getsignal(signum) == previous


def test_live_cli_dry_run_prints_decisions_and_selected_limit(
    stages: Stages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda **kw: stages.settings)
    result = CliRunner().invoke(cli.app, ["run", "--dry-run", "--max-experiments", "2"])
    assert result.exit_code == 0, result.output
    assert "would run" in result.output and "outside selected limit" in result.output
    assert "source=mock" in result.output and "Reason:" in result.output
    assert not stages.calls


def test_reconciliation_never_repairs_unjournaled_stopped_container(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = FakeContainer("unhealthy-target", settings.compose_project)
    container.status = "exited"
    state = SimpleNamespace(state="exited", paused=False, service="redis")
    monkeypatch.setattr(orchestrator, "replay_journals", Mock(return_value=0))
    monkeypatch.setattr(orchestrator, "list_target_containers", lambda *a: [container])
    monkeypatch.setattr(orchestrator, "container_status", lambda *a: state)
    with pytest.raises(SafetyError, match="unjournaled repair"):
        orchestrator.reconcile(SimpleNamespace(), settings)
    assert not container.calls


def test_measured_cooldown_requires_twenty_seconds_and_real_windows(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.expected_services = ()
    now = [1000.0]
    load = []

    class Load:
        def __init__(self, settings, client, samples) -> None:
            self.samples = samples
            self.failed = threading.Event()
            self.stopped = False
            load.append(self)

        def prepare(self, users: int) -> None:
            pass

        def set_workers(self, count: int) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

    class Thread:
        def __init__(self, *, target, daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

        def join(self) -> None:
            pass

    class Abort:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> None:
            for name in ("browse", "login"):
                load[0].samples.append(sample(now[0] + 0.1, name))
            now[0] += seconds

    monkeypatch.setattr(
        orchestrator, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0])
    )
    monkeypatch.setattr(
        orchestrator, "threading", SimpleNamespace(Thread=Thread, Event=threading.Event)
    )
    monkeypatch.setattr(orchestrator, "LoadGenerator", Load)
    monkeypatch.setattr(orchestrator, "list_target_containers", lambda *a: [object()])
    monkeypatch.setattr(
        orchestrator,
        "container_status",
        lambda *a: SimpleNamespace(state="running", paused=False, service="redis"),
    )
    baseline = baseline_for()
    decision = policy_gate(spec_for())
    assert orchestrator.cooldown_ready(
        SimpleNamespace(),
        settings,
        fixture("graph", Graph),
        baseline,
        decision,
        settings.runs_dir / "test",
        Abort(),
    )
    assert now[0] >= 1020 and load[0].stopped
    saved = read_json(
        settings.runs_dir / "test" / "cooldown" / decision.experiment_id / "readiness.json",
        CheckResult,
    )
    assert saved.passed


def test_dry_reconciliation_retains_pending_without_mutation(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaosai.models import JournalData, LoadUndoRecord

    path = settings.runs_dir / "pending" / "journal.json"
    data = JournalData(
        records=[
            LoadUndoRecord(
                id="load",
                project=settings.compose_project,
                original_workers=10,
                owner_pid=1,
            )
        ]
    )
    write_json(path, data)
    replay = Mock(side_effect=AssertionError("dry-run mutation"))
    monkeypatch.setattr(orchestrator, "replay_journals", replay)
    with pytest.raises(SafetyError, match="Pending undo"):
        orchestrator.reconcile(SimpleNamespace(), settings, dry_run=True)
    assert read_json(path, JournalData) == data
    replay.assert_not_called()


def test_missing_scoped_service_is_not_readiness(settings: Settings) -> None:
    from chaosai.models import ContainerStatus

    states = [
        ContainerStatus(
            name="redis",
            service="redis",
            state="running",
            paused=False,
            health="healthy",
            ports=[],
        )
    ]
    assert not orchestrator.scoped_running(states, settings)


def test_doctor_lite_failure_prevents_mapping_and_planning(
    stages: Stages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "doctor_lite",
        lambda *a: [
            CheckResult(
                name="HTTP products",
                passed=False,
                detail="unavailable",
            )
        ],
    )
    with pytest.raises(SafetyError, match="Doctor-lite"):
        stages.run()
    assert "map" not in stages.order and not stages.calls


def test_runner_exception_with_rollback_failure_never_continues(
    stages: Stages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages.failure = RuntimeError("failed execution")
    # First reconciliation is mocked; both explicit and final rollback fail closed.
    monkeypatch.setattr(orchestrator, "replay_journals", Mock(side_effect=SafetyError("pending")))
    with pytest.raises(SafetyError):
        stages.run()
    assert stages.calls == ["exp-1"] and "cooldown" not in stages.order
    result = read_json(
        next(stages.settings.runs_dir.glob("*/experiments/exp-1/result.json")), ExperimentResult
    )
    assert not result.rollback_ok and not result.recovered


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signals_during_cooldown_never_start_next_experiment(
    stages: Stages,
    monkeypatch: pytest.MonkeyPatch,
    signum: int,
) -> None:
    previous = signal.getsignal(signum)
    monkeypatch.setattr(orchestrator, "cooldown_ready", lambda *a: signal.raise_signal(signum))
    directory = stages.run()
    assert stages.calls == ["exp-1"]
    assert read_json(directory / "run.json", RunMeta).stop_reason == "Interrupted by signal."
    assert signal.getsignal(signum) == previous


def test_outer_client_close_failure_is_recorded(stages: Stages) -> None:
    stages.client.close.side_effect = RuntimeError("untrusted error text")
    directory = stages.run(dry_run=True)
    meta = read_json(directory / "run.json", RunMeta)
    assert meta.stop_reason == "Run client cleanup failed."
    assert "untrusted error text" not in meta.model_dump_json()


@pytest.mark.parametrize("reuse", [False, True])
def test_corrupt_unused_or_reuse_baseline_measures_fresh(stages: Stages, reuse: bool) -> None:
    latest = stages.settings.safety_dir / "baseline.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("{invalid")
    directory = stages.run(max_experiments=1, reuse_baseline=reuse)
    assert "baseline" in stages.order
    assert read_json(directory / "run.json", RunMeta).baseline_source == "measured"
    assert read_json(directory / "baseline.json", Baseline) == stages.baseline
