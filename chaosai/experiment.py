"""Live experiment lifecycle; deterministic safety components own all mutations."""

import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from chaosai.approval import Approver, Choice
from chaosai.config import Settings
from chaosai.evaluate import compute_during_after, find_recovery, impacted_journeys, near_baseline
from chaosai.evaluate import verdict as evaluate_verdict
from chaosai.evidence import collect_logs, container_events
from chaosai.faults import get_fault
from chaosai.faults.base import FaultContext
from chaosai.faults.traffic_spike import TrafficSpikeFault
from chaosai.load import LoadGenerator, SampleBuffer
from chaosai.metrics import ContainerStatsPoller, QueueDepthPoller
from chaosai.models import Baseline, Event, ExperimentResult, Graph, PolicyDecision
from chaosai.runs import append_jsonl, write_json
from chaosai.safety import (
    SafetyError,
    SafetyInterrupted,
    UndoJournal,
    file_lock,
    kill_requested,
    replay_journals,
    rollback_signals,
    scope_guard,
)
from chaosai.target import get_docker_client
from chaosai.watchdog import Watchdog


class PrecheckSkipped(RuntimeError):
    """Readiness could not be established within S9's deadline."""


def wait_tick(seconds: float) -> None:
    threading.Event().wait(seconds)


def execute_experiment(
    decision: PolicyDecision,
    run_dir: Path,
    *,
    settings: Settings,
    graph: Graph,
    baseline: Baseline,
    approver: Approver,
    approval_choice: Choice | None = None,
    lease_held: bool = False,
) -> ExperimentResult:
    """Approve, precheck, inject, observe, undo, recover and persist even on failure."""
    approver.ensure_interactive()
    if not decision.allowed:
        raise SafetyError(decision.deny_reason or "Policy denied.")
    spec = decision.final
    fault = get_fault(spec.fault)
    fault.validate(spec, graph)
    # Specs may be supplied by later planners; never permit paths escaping the run.
    directory = run_dir / "experiments" / spec.id
    if (
        not spec.id
        or spec.id in {".", ".."}
        or Path(spec.id).name != spec.id
        or directory.resolve().parent != (run_dir / "experiments").resolve()
    ):
        raise SafetyError("Experiment ID must be a single path component.")
    directory.mkdir(parents=True, exist_ok=True)
    samples_path = directory / "samples.jsonl"
    append_jsonl(samples_path, [])
    samples = SampleBuffer(samples_path)
    started = time.time()
    events: list[Event] = []

    def event(phase: str, note: str) -> None:
        item = Event(t=time.time(), phase=phase, note=note)
        events.append(item)
        append_jsonl(directory / "events.jsonl", [item])

    thresholds = baseline.thresholds.model_copy(deep=True)
    thresholds.abort.max_error_rate = spec.abort.max_error_rate
    thresholds.abort.p95_multiple = spec.abort.max_p95_multiple
    thresholds.hypothesis.max_error_rate = spec.hypothesis.max_error_rate
    thresholds.hypothesis.p95_multiple = spec.hypothesis.max_p95_multiple
    event("APPROVE", "Policy-clamped experiment awaiting approval.")
    choice = approval_choice if approval_choice is not None else approver.decide(decision)
    skipped, skip_reason = choice in {"skip", "quit"}, None
    if skipped:
        skip_reason = f"Approval: {choice}."
    aborted, reason, rollback_ok = False, None, False
    cleanup_ok, interrupted = True, False
    recovered, recovery_time = False, None
    t_inject = t_undo = None
    client = generator = poller = watchdog = timer = observer = control = preparer = ctx = None
    queue_poller = None
    abort, cap, stop_observer = threading.Event(), threading.Event(), threading.Event()
    cancel = threading.Event()
    prepare_done = threading.Event()
    prepare_errors: list[str] = []
    undo_lock = threading.Lock()
    undo_errors: list[str] = []
    observation_errors: list[str] = []
    control_reason: str | None = None
    cleaned = lease_acquired = False
    logs: dict[str, list[str]] = {}

    def undo() -> None:
        nonlocal rollback_ok, t_undo
        with undo_lock:
            try:
                if client is not None and lease_acquired:
                    if ctx is not None:
                        ctx.journal.replay_all()
                    else:
                        replay_journals(client, settings)
                    rollback_ok = True
                    if t_inject is not None and t_undo is None:
                        t_undo = time.time()
            except Exception as exc:
                rollback_ok = False
                undo_errors.append(type(exc).__name__)

    def duration_expired() -> None:
        cap.set()
        cancel.set()
        # Independent timer rolls back even if the observation/main thread is in SDK I/O.
        undo()

    def monitor_control() -> None:
        nonlocal control_reason, t_undo
        try:
            while not stop_observer.wait(0.5):
                if kill_requested(settings):
                    control_reason = "Kill switch requested."
                elif ctx and ctx.record and not cap.is_set():
                    done, done_at = ctx.journal.completion(ctx.record.id)
                    if ctx.journal.stop_requested(ctx.record.id):
                        control_reason = "Rolled back from another terminal."
                    if done:
                        control_reason = "Rolled back from another terminal."
                        t_undo = done_at or time.time()
                if control_reason:
                    cancel.set()
                    abort.set()
                    undo()
                    return
        except Exception:
            control_reason = "Safety control observation unavailable."
            cancel.set()
            abort.set()
            undo()

    def observe_fault() -> None:
        try:
            while not stop_observer.is_set():
                fault.observe(ctx)
                stop_observer.wait(0.5)
        except Exception as exc:
            observation_errors.append(type(exc).__name__)
            abort.set()

    def prepare_load() -> None:
        try:
            generator.prepare(settings.load_users)
        except Exception as exc:
            prepare_errors.append(type(exc).__name__)
        finally:
            prepare_done.set()

    def cleanup() -> None:
        nonlocal cleaned, logs, aborted, reason, cleanup_ok
        if cleaned:
            return
        cleaned = True
        stop_observer.set()
        if timer is not None:
            timer.cancel()
        # Restore before joining any thread doing bounded SDK I/O.
        undo()
        if timer is not None:
            timer.join()
        for component in (watchdog, generator, poller, queue_poller):
            if component is not None:
                try:
                    component.stop()
                except Exception as exc:
                    cleanup_ok = False
                    aborted, reason = True, f"Cleanup failed ({type(exc).__name__})."
        for thread in (observer, control, preparer):
            if thread is not None:
                thread.join()
        if client is not None:
            try:
                logs = collect_logs(client, graph, spec.target, settings, started)
            finally:
                try:
                    client.close()
                except Exception:
                    cleanup_ok = False
                    aborted, reason = True, "Docker client cleanup failed."

    try:
        if not skipped:
            client = get_docker_client()
            with (
                nullcontext()
                if lease_held
                else file_lock(settings.active_lock_path, blocking=False)
            ):
                lease_acquired = True
                # Signals stay ignored during ALL cleanup, including thread/client closure.
                with rollback_signals(cleanup, abort):
                    replay_journals(client, settings)
                    if kill_requested(settings):
                        raise SafetyError("KILL flag present; explicitly clear it after rollback.")
                    traffic = isinstance(fault, TrafficSpikeFault)
                    container = (
                        None
                        if traffic
                        else scope_guard(spec.target, graph, client, settings.compose_project)
                    )
                    ctx = FaultContext(
                        spec,
                        graph,
                        client,
                        settings,
                        UndoJournal(settings.journal_path(run_dir), client, settings),
                        cancel_event=cancel,
                    )
                    control = threading.Thread(target=monitor_control, daemon=True)
                    control.start()
                    # One-second telemetry for S5, without modifying the saved settings/baseline.
                    poller = ContainerStatsPoller(
                        client,
                        settings.model_copy(update={"metrics_interval_s": 1}),
                        directory / "containers.jsonl",
                    )
                    queue_poller = QueueDepthPoller(settings, directory / "queues.jsonl")
                    generator = LoadGenerator(settings, client, samples)
                    ctx.generator = generator
                    event("PRECHECK", "Reconciling journals; measuring S9 readiness (up to 60s).")
                    deadline = time.monotonic() + settings.precheck_timeout_s
                    preparer = threading.Thread(target=prepare_load, daemon=True)
                    preparer.start()
                    while not prepare_done.is_set() and time.monotonic() < deadline:
                        if kill_requested(settings) or abort.is_set():
                            raise SafetyError(control_reason or "Kill switch during preparation.")
                        wait_tick(min(0.5, max(0, deadline - time.monotonic())))
                    if prepare_errors:
                        raise SafetyError(f"Load preparation failed ({prepare_errors[0]}).")
                    if not prepare_done.is_set():
                        raise PrecheckSkipped("Load preparation did not complete within 60s.")
                    samples.begin_window(time.time())
                    precheck_start = time.time()
                    poller.start()
                    queue_poller.start()
                    generator.set_workers(settings.load_users)
                    healthy, precheck_reason = False, "Precheck setup exceeded deadline."
                    while time.monotonic() < deadline:
                        if kill_requested(settings) or abort.is_set():
                            raise SafetyError(
                                control_reason or "Kill switch requested during precheck."
                            )
                        if generator.failed.is_set():
                            raise SafetyError("Load worker failed during precheck.")
                        healthy, precheck_reason = near_baseline(
                            samples.snapshot(),
                            baseline,
                            thresholds,
                            precheck_start,
                            time.time() - settings.load_timeout_s,
                        )
                        if healthy:
                            container = (
                                None
                                if traffic
                                else scope_guard(
                                    spec.target, graph, client, settings.compose_project
                                )
                            )
                            if prepare_done.is_set() and (
                                traffic
                                or (
                                    container.status == "running"
                                    and not container.attrs.get("State", {}).get("Paused")
                                )
                            ):
                                break
                            healthy, precheck_reason = False, "Target is not running/unpaused."
                        wait_tick(min(0.5, max(0, deadline - time.monotonic())))
                    if not healthy:
                        if not prepare_done.is_set():
                            precheck_reason = "Load preparation did not complete within 60s."
                        skipped, skip_reason = True, f"S9 timeout: {precheck_reason}"
                        event("PRECHECK", skip_reason)
                    else:
                        event("PRECHECK", precheck_reason)
                        t_inject = time.time()
                        event("INJECT", f"Applying {spec.fault} to {spec.target}.")
                        # Starts BEFORE apply: Docker stop/apply time counts toward the hard cap.
                        timer = threading.Timer(spec.duration_s, duration_expired)
                        timer.daemon = True
                        timer.start()
                        fault.apply(ctx)
                        watchdog = Watchdog(
                            samples,
                            poller,
                            baseline,
                            thresholds,
                            abort,
                            target_containers=set() if traffic else {container.name},
                            started_at=t_inject,
                        )
                        watchdog.start()
                        observer = threading.Thread(target=observe_fault, daemon=True)
                        observer.start()
                        event("OBSERVE", "Fault applied; load and one-second watchdog active.")
                        while True:
                            if kill_requested(settings):
                                aborted, reason = True, "Kill switch requested."
                                break
                            if abort.is_set():
                                aborted, reason = (
                                    True,
                                    control_reason
                                    or watchdog.breach_reason
                                    or (
                                        "Fault observation unavailable."
                                        if observation_errors
                                        else "Abort requested."
                                    ),
                                )
                                break
                            if generator.failed.is_set():
                                aborted, reason = True, "Load worker failed."
                                break
                            if cap.is_set():
                                break
                            if ctx.record:
                                done, done_at = ctx.journal.completion(ctx.record.id)
                                if done or ctx.journal.stop_requested(ctx.record.id):
                                    aborted, reason = True, "Rolled back from another terminal."
                                    if done:
                                        t_undo = done_at or time.time()
                                    break
                            wait_tick(0.5)
                    event("UNDO", reason or "Duration complete; restoring containers.")
                    stop_observer.set()
                    undo()
                    if observer is not None:
                        observer.join()
                    if watchdog is not None:
                        watchdog.stop()
                    if timer is not None:
                        timer.cancel()
                        timer.join()
                    undo()
                    event("RECOVER", "Container restoration and HTTP recovery assessed separately.")
                    if rollback_ok and t_undo is not None:
                        recovery_deadline = time.monotonic() + max(
                            0, settings.recovery_timeout_s - (time.time() - t_undo)
                        )
                        while time.monotonic() < recovery_deadline:
                            recovered, recovery_time = find_recovery(
                                samples.snapshot(),
                                baseline,
                                thresholds,
                                t_undo,
                                t_end=time.time() - settings.load_timeout_s,
                            )
                            if kill_requested(settings):
                                aborted, reason = True, "Kill switch requested."
                                break
                            if generator.failed.is_set():
                                aborted, reason = True, "Load worker failed during recovery."
                                break
                            if recovered:
                                break
                            wait_tick(0.5)
    except PrecheckSkipped as exc:
        skipped, skip_reason = True, f"S9 timeout: {exc}"
        event("PRECHECK", skip_reason)
    except (Exception, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, (SafetyInterrupted, KeyboardInterrupt))
        aborted, reason = (
            True,
            (
                "Interrupted by signal; rollback requested."
                if isinstance(exc, (SafetyInterrupted, KeyboardInterrupt))
                else str(exc)
                if isinstance(exc, SafetyError)
                else f"Experiment failed ({type(exc).__name__}); rollback requested."
            ),
        )
        if ctx is not None and ctx.record is None:
            # Write-before-apply guarantees that no mutation began without a record.
            t_inject = None
        event("UNDO", reason)
    finally:
        cleanup()

    if t_inject is not None and not rollback_ok:
        aborted, reason = True, "Rollback incomplete; retained journals need chaosai rollback."
    ended = time.time()
    values = samples.snapshot()
    during, after = (
        ({}, {})
        if t_inject is None
        else compute_during_after(
            values, t_inject, t_undo if t_undo is not None else ended, t_end=ended
        )
    )
    if t_undo is not None and rollback_ok:
        recovered, recovery_time = find_recovery(values, baseline, thresholds, t_undo, t_end=ended)
    limits = [
        "Recovery requires every journey in three healthy windows (1–20s from baseline RPS).",
        "Sparse (<20) journey p95/error rates are descriptive; they cannot confirm a hypothesis.",
        "Container restoration does not establish application/HTTP recovery.",
    ]
    if t_inject is None:
        limits.append("No fault applied; precheck/approval evidence only.")
    if undo_errors:
        limits.append(
            "Rollback encountered failures; inspect retained journals and retry rollback."
        )
    event("RESULT", f"rollback_ok={rollback_ok}; recovered={recovered}; skipped={skipped}.")
    result = ExperimentResult(
        spec=spec,
        decision=decision,
        started_at=datetime.fromtimestamp(started, UTC),
        ended_at=datetime.fromtimestamp(ended, UTC),
        events=events,
        during=during,
        after=after,
        aborted=aborted,
        abort_reason=reason,
        watchdog_aborted=bool(
            aborted
            and watchdog is not None
            and watchdog.breach_reason is not None
            and watchdog.breach_reason == reason
            and reason != "Watchdog evaluation unavailable."
        ),
        verdict=evaluate_verdict(during, baseline, thresholds, spec.hypothesis, aborted=aborted),
        recovered=recovered,
        recovery_time_s=recovery_time,
        self_healed=ctx.self_healed if ctx else None,
        impacted_journeys=impacted_journeys(during, baseline, thresholds),
        container_events=container_events(poller.snapshot(), baseline) if poller else [],
        log_excerpts=logs,
        rollback_ok=rollback_ok,
        samples_path=str(samples_path.relative_to(run_dir)),
        skipped=skipped,
        skip_reason=skip_reason,
        evidence_limits=limits,
        approval_decision=choice,
        cleanup_ok=cleanup_ok,
        interrupted=interrupted,
    )
    write_json(directory / "result.json", result)
    return result
