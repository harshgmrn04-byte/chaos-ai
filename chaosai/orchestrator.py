"""Automatic, sequential experiments around the existing deterministic runner."""

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import docker

from chaosai.analyzer import analyze_run
from chaosai.approval import Approver, Choice
from chaosai.baseline import measure_baseline
from chaosai.catalogue import definition
from chaosai.config import Settings
from chaosai.doctor import status_checks
from chaosai.evaluate import find_recovery, near_baseline
from chaosai.experiment import execute_experiment
from chaosai.load import LoadGenerator, SampleBuffer
from chaosai.mapper import map_target, save_graph
from chaosai.models import (
    Approval,
    Baseline,
    CheckResult,
    ContainerStatus,
    Event,
    ExperimentResult,
    Graph,
    JournalData,
    PolicyDecision,
    RunMeta,
)
from chaosai.planner import plan_experiments
from chaosai.planner_facts import SOURCE_REVISION, source_facts
from chaosai.report import render_report
from chaosai.runs import append_jsonl, create_run, read_json, write_json
from chaosai.safety import (
    SafetyError,
    SafetyInterrupted,
    file_lock,
    kill_requested,
    replay_journals,
    rollback_signals,
)
from chaosai.target import container_status, get_docker_client, http_ready, list_target_containers


def baseline_reusable(baseline: Baseline, now: datetime, max_age_s: float = 3600) -> bool:
    """Future dates, unstable measurements and the one-hour boundary are unsuitable."""
    age = (now - baseline.created_at).total_seconds()
    return (
        baseline.stable
        and all(s.n > 0 and s.error_rate == 0 for s in baseline.journeys.values())
        and 0 <= age < min(max_age_s, 3600)
    )


def scoped_running(states: list[ContainerStatus], settings: Settings) -> bool:
    return (
        bool(states)
        and set(settings.expected_services) <= {s.service for s in states}
        and all(s.state == "running" and not s.paused for s in states)
    )


def reconcile(client: docker.DockerClient, settings: Settings, *, dry_run: bool = False) -> None:
    if dry_run:
        # A preview never mutates container/worker state, including pending undo.
        for path in settings.runs_dir.glob("*/journal.json"):
            raw = read_json(path, object)
            data = JournalData() if raw == [] else JournalData.model_validate(raw)
            if any(not record.done for record in data.records):
                raise SafetyError("Pending undo prevents dry-run; use chaosai rollback first.")
    else:
        replay_journals(client, settings)
    if kill_requested(settings):
        raise SafetyError("KILL flag present; explicitly clear it after reviewing rollback.")
    states = [container_status(c, settings) for c in list_target_containers(client, settings)]
    if not scoped_running(states, settings):
        raise SafetyError("Scoped target is not running/unpaused; no unjournaled repair attempted.")


def doctor_lite(client: docker.DockerClient, settings: Settings) -> list[CheckResult]:
    """Read-only scoped state/ports and documented public GETs; no LLM/key probe."""
    states = [container_status(c, settings) for c in list_target_containers(client, settings)]
    checks = status_checks(states, settings)
    for name, url in (
        ("products", settings.product_url + "/products?limit=1"),
        ("categories", settings.product_url + "/categories"),
    ):
        checks.append(CheckResult(name=f"HTTP {name}", passed=http_ready(url), detail="GET ready"))
    return checks


def check_control(settings: Settings, abort: threading.Event) -> None:
    if abort.is_set():
        raise SafetyInterrupted("Run interrupted; stopping further experiments.")
    if kill_requested(settings):
        raise SafetyError("Kill switch requested; stopping further experiments.")


def cooldown_ready(
    client: docker.DockerClient,
    settings: Settings,
    graph: Graph,
    baseline: Baseline,
    decision: PolicyDecision,
    run_dir: Path,
    abort: threading.Event,
) -> bool:
    """Fresh measured recovery/readiness plus >=20s cooldown, with bounded preparation."""
    directory = run_dir / "cooldown" / decision.experiment_id
    samples = SampleBuffer(directory / "samples.jsonl")
    generator = LoadGenerator(settings, client, samples)
    thresholds = baseline.thresholds.model_copy(deep=True)
    thresholds.hypothesis.max_error_rate = decision.final.hypothesis.max_error_rate
    thresholds.hypothesis.p95_multiple = decision.final.hypothesis.max_p95_multiple
    start = time.monotonic()
    deadline = start + settings.precheck_timeout_s
    done = threading.Event()
    errors: list[str] = []
    ready = False
    reason = "Cooldown readiness timed out; further experiments stopped."

    def prepare() -> None:
        try:
            generator.prepare(settings.load_users)
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            done.set()

    preparer = threading.Thread(target=prepare, daemon=True)
    t0 = time.time()
    try:
        preparer.start()
        while not done.is_set() and time.monotonic() < deadline:
            check_control(settings, abort)
            abort.wait(0.5)
        if done.is_set() and not errors and time.monotonic() < deadline:
            # Preparation does not consume the independent measurement budget.
            deadline = time.monotonic() + settings.precheck_timeout_s
            t0 = time.time()
            samples.begin_window(t0)
            generator.set_workers(settings.load_users)
            while time.monotonic() < deadline:
                check_control(settings, abort)
                if generator.failed.is_set():
                    reason = "Cooldown load worker failed."
                    break
                t1 = time.time() - settings.load_timeout_s
                values = samples.snapshot()
                near, reason = near_baseline(values, baseline, thresholds, t0, t1)
                recovered, _ = find_recovery(values, baseline, thresholds, t0, t_end=t1)
                if near and recovered and time.monotonic() - start >= settings.cooldown_s:
                    states = [
                        container_status(c, settings)
                        for c in list_target_containers(client, settings)
                    ]
                    ready = scoped_running(states, settings)
                    if ready:
                        reason = "Measured readiness/recovery and minimum cooldown satisfied."
                        break
                abort.wait(0.5)
    finally:
        # Joining preparation is required even when stopping workers fails.
        try:
            generator.stop()
        finally:
            preparer.join()
        write_json(
            directory / "readiness.json",
            CheckResult(
                name="Cooldown readiness",
                passed=ready,
                detail=reason,
            ),
        )
    return ready


def failed_result(
    decision: PolicyDecision,
    run_dir: Path,
    choice: Choice,
    exc: BaseException,
    *,
    rollback_ok: bool,
) -> ExperimentResult:
    """Persist safe failure evidence without inventing impact or application recovery."""
    identifier = decision.experiment_id
    if (
        identifier != decision.final.id
        or not identifier
        or identifier in {".", ".."}
        or Path(identifier).name != identifier
    ):
        raise SafetyError("Experiment ID must be a single matching path component.")
    now = datetime.now(UTC)
    interrupted = isinstance(exc, (SafetyInterrupted, KeyboardInterrupt))
    reason = (
        "Interrupted by signal; rollback requested."
        if interrupted
        else f"Experiment failed ({type(exc).__name__}); rollback requested."
    )
    path = f"experiments/{decision.experiment_id}/samples.jsonl"
    result = ExperimentResult(
        spec=decision.final,
        decision=decision,
        started_at=now,
        ended_at=now,
        events=[Event(t=now.timestamp(), phase="RESULT", note=reason)],
        during={},
        after={},
        aborted=True,
        abort_reason=reason,
        verdict="aborted",
        recovered=False,
        recovery_time_s=None,
        self_healed=None,
        impacted_journeys=[],
        container_events=[],
        log_excerpts={},
        rollback_ok=rollback_ok,
        samples_path=path,
        interrupted=interrupted,
        approval_decision=choice,
        evidence_limits=["Runner failed; no complete measurement or recovery claim available."],
    )
    append_jsonl(run_dir / path, [])
    write_json(run_dir / "experiments" / decision.experiment_id / "result.json", result)
    return result


def annotate_capabilities(graph: Graph, client: docker.DockerClient, settings: Settings) -> None:
    """Expose safe refusals as planner data before choosing fault diversity."""
    for node in graph.nodes:
        if node.injectable and node.container and node.kind != "external":
            container = client.containers.get(node.container)
            container.reload()
            if container.labels.get("com.docker.compose.project") != settings.compose_project:
                raise SafetyError("Mapped container lost its project scope.")
            host = container.attrs.get("HostConfig", {})
            if host.get("CpuQuota", 0) == 0 or host.get("NanoCpus", 0):
                node.unsupported_faults = ["cpu_limit"]


def run_automatic(
    settings: Settings,
    approver: Approver,
    *,
    dry_run: bool = False,
    max_experiments: int = 8,
    reuse_baseline: bool = False,
) -> Path:
    if not 1 <= max_experiments <= 8:
        raise SafetyError("max-experiments must be between 1 and 8.")
    if not dry_run:
        approver.ensure_interactive()
    run_dir = create_run(settings.runs_dir)
    meta = RunMeta(
        run_id=run_dir.name,
        started_at=datetime.now(UTC),
        planner_source="heuristic",
        analyzer_source="heuristic",
        target_project=settings.compose_project,
        approvals=[],
        offline=False,
        data_source="measured",
        target_profile="owned-auth"
        if (settings.safety_dir / "sandbox.json").exists()
        else "original",
        credential_cache_s=settings.credential_cache_s,
    )
    write_json(run_dir / "run.json", meta)
    write_json(run_dir / "journal.json", [])
    abort = threading.Event()
    client = None
    leased = False

    def rollback() -> None:
        if leased and not dry_run:
            replay_journals(client, settings)

    try:
        client = get_docker_client()
        # Hold the lease through planning, approval, experiment cleanup and cooldown.
        with file_lock(settings.active_lock_path, blocking=False):
            leased = True
            with rollback_signals(rollback, abort):
                reconcile(client, settings, dry_run=dry_run)
                checks = doctor_lite(client, settings)
                write_json(run_dir / "doctor.json", checks)
                if any(not c.passed for c in checks):
                    raise SafetyError("Doctor-lite failed; inspect doctor.json before injecting.")
                check_control(settings, abort)
                graph = map_target(settings)
                annotate_capabilities(graph, client, settings)
                save_graph(graph, settings, run_dir)
                facts = source_facts(graph)
                write_json(
                    run_dir / "source-facts.json",
                    {
                        "revision": SOURCE_REVISION if facts else None,
                        "facts": [fact.model_dump() for fact in facts],
                    },
                )
                meta.phase = "BASELINE"
                write_json(run_dir / "run.json", meta)
                latest = settings.safety_dir / "baseline.json"
                baseline = None
                if reuse_baseline and latest.exists():
                    try:
                        baseline = read_json(latest, Baseline)
                    except (OSError, ValueError):
                        # An unreadable cache cannot justify reuse; measure fresh evidence.
                        pass
                # Reuse is opt-in; the flag never overrides freshness/stability.
                if (
                    reuse_baseline
                    and baseline is not None
                    and baseline_reusable(baseline, datetime.now(UTC), settings.baseline_max_age_s)
                ):
                    meta.baseline_source = "reused"
                else:
                    baseline = measure_baseline(graph, settings, run_dir)
                    meta.baseline_source = "measured"
                write_json(run_dir / "baseline.json", baseline)
                check_control(settings, abort)
                if not baseline_reusable(baseline, datetime.now(UTC), settings.baseline_max_age_s):
                    raise SafetyError(
                        "Fresh baseline is unstable or error-contaminated; no faults applied."
                    )
                meta.phase = "PLANNING"
                write_json(run_dir / "run.json", meta)
                plan, meta.planner_source = plan_experiments(
                    graph,
                    baseline,
                    provider=settings.llm_provider,
                    cache_dir=run_dir / "llm",
                    settings=settings,
                    max_experiments=max_experiments,
                )
                decisions = read_json(run_dir / "decisions.json", list[PolicyDecision])
                meta.planner_sources = list(dict.fromkeys(d.source for d in decisions))
                write_json(run_dir / "plan.json", plan)
                write_json(run_dir / "run.json", meta)
                if not dry_run:
                    for decision in sorted(decisions, key=lambda d: d.final.rank)[:max_experiments]:
                        check_control(settings, abort)
                        entry = definition(decision.final.fault)
                        if not decision.allowed or entry is None or not entry.implemented:
                            continue
                        meta.phase = "EXPERIMENTS"
                        write_json(run_dir / "run.json", meta)
                        choice = approver.decide(decision)
                        meta.approvals.append(
                            Approval(experiment_id=decision.experiment_id, decision=choice)
                        )
                        write_json(run_dir / "run.json", meta)
                        if choice == "quit":
                            meta.stop_reason = "Approval: quit."
                            break
                        if choice == "skip":
                            continue
                        check_control(settings, abort)
                        try:
                            result = execute_experiment(
                                decision,
                                run_dir,
                                settings=settings,
                                graph=graph,
                                baseline=baseline,
                                approver=approver,
                                approval_choice=choice,
                                lease_held=True,
                            )
                        except (Exception, KeyboardInterrupt) as exc:
                            ok = True
                            try:
                                rollback()
                            except Exception:
                                ok = False
                            result = failed_result(decision, run_dir, choice, exc, rollback_ok=ok)
                        if result.interrupted:
                            meta.stop_reason = "Interrupted by signal."
                            break
                        if not result.rollback_ok or not result.cleanup_ok:
                            meta.stop_reason = (
                                "Experiment cleanup/rollback failed; no continuation."
                            )
                            break
                        check_control(settings, abort)
                        # Reconcile again to catch unresolved undo and verify scoped states.
                        reconcile(client, settings)
                        if not cooldown_ready(
                            client, settings, graph, baseline, decision, run_dir, abort
                        ):
                            meta.stop_reason = "Measured cooldown readiness/recovery failed."
                            break
    except (SafetyInterrupted, KeyboardInterrupt):
        meta.stop_reason = "Interrupted by signal."
    except Exception as exc:
        meta.stop_reason = (
            str(exc) if isinstance(exc, SafetyError) else f"Run failed ({type(exc).__name__})."
        )
        meta.phase = "FAILED"
        meta.finished_at = datetime.now(UTC)
        raise
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            meta.stop_reason = "Run client cleanup failed."
        finally:
            write_json(run_dir / "run.json", meta)
    meta.phase = "ANALYZING"
    write_json(run_dir / "run.json", meta)
    analyze_run(
        run_dir,
        settings.model_copy(update={"llm_provider": "none"}) if meta.stop_reason else settings,
    )
    if all((run_dir / name).exists() for name in ("graph.json", "baseline.json", "plan.json")):
        render_report(run_dir)
    meta = read_json(run_dir / "run.json", RunMeta)
    meta.phase = "STOPPED" if meta.stop_reason else "COMPLETE"
    meta.finished_at = datetime.now(UTC)
    write_json(run_dir / "run.json", meta)
    return run_dir
