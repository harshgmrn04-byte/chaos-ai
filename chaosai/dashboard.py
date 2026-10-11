"""Loopback dashboard over typed artifacts and the existing CLI safety boundary."""

import hmac
import json
import math
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from chaosai.config import Settings
from chaosai.evidence import redact_line
from chaosai.metrics import window_stats
from chaosai.models import (
    Baseline,
    ContainerObservation,
    DashboardExperiment,
    DashboardJob,
    DashboardPoint,
    DashboardRequest,
    DashboardRun,
    DashboardSnapshot,
    DependencyImpact,
    Event,
    ExperimentResult,
    Finding,
    Graph,
    Model,
    PolicyDecision,
    QueueObservation,
    RunMeta,
    Sample,
    Summary,
)
from chaosai.runs import read_json
from chaosai.safety import file_lock


class ArtifactStore:
    """Never serve arbitrary files, dotenv, caches, raw logs, or request bodies."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.runs_dir.resolve()

    def directory(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("Invalid run ID")
        path = (self.root / run_id).resolve()
        if path.parent != self.root or not path.is_dir():
            raise ValueError("Unknown run")
        return path

    def read(self, directory: Path, name: str, model: type[Model]) -> Model | None:
        path = directory / name
        if path.resolve().parent != directory.resolve() or not path.is_file():
            return None
        try:
            return read_json(path, model)
        except (OSError, ValueError):
            return None

    def lines(self, path: Path, model: type[Model], limit: int = 30000) -> list[Model]:
        # Skip an incomplete last JSONL record during append; retry next refresh.
        if not path.is_file() or not path.resolve().is_relative_to(self.root):
            return []
        values: list[Model] = []
        try:
            with path.open() as stream:
                for line in stream:
                    try:
                        values.append(model.model_validate_json(line))
                    except ValueError:
                        continue
                    if len(values) > limit:
                        values = values[-limit:]
        except OSError:
            return []
        return values

    def runs(self) -> list[DashboardRun]:
        if not self.root.is_dir():
            return []
        values = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if not directory.is_dir() or directory.resolve().parent != self.root:
                continue
            meta = self.read(directory, "run.json", RunMeta)
            baseline = self.read(directory, "baseline.json", Baseline)
            results = list((directory / "experiments").glob("*/result.json"))
            if not meta and not baseline and not (directory / "graph.json").exists():
                continue
            values.append(
                DashboardRun(
                    id=directory.name,
                    source="fixture" if meta and meta.offline else "measured",
                    phase=meta.phase if meta else "BASELINE" if baseline else "MAPPING",
                    created_at=meta.started_at.isoformat()
                    if meta
                    else baseline.created_at.isoformat()
                    if baseline
                    else "",
                    experiments=len(results),
                    report_available=(directory / "report.html").is_file(),
                )
            )
        return values[:100]

    def snapshot(self, run_id: str) -> DashboardSnapshot:
        directory = self.directory(run_id)
        snapshot = DashboardSnapshot(run_id=run_id)
        for field, filename, model in (
            ("meta", "run.json", RunMeta),
            ("baseline", "baseline.json", Baseline),
            ("graph", "graph.json", Graph),
            ("summary", "summary.json", Summary),
        ):
            setattr(snapshot, field, self.read(directory, filename, model))
        if snapshot.meta and snapshot.meta.stop_reason:
            snapshot.warnings.append(
                f"Run stopped: {redact_line(snapshot.meta.stop_reason, self.settings)}"
            )
        decisions = directory / "decisions.json"
        if decisions.is_file() and decisions.resolve().parent == directory.resolve():
            try:
                values = read_json(decisions, list[PolicyDecision])
                reasons = dict.fromkeys(d.fallback_reason for d in values if d.fallback_reason)
                snapshot.warnings.extend(f"Planner fallback: {reason}" for reason in reasons)
            except (OSError, ValueError):
                pass
        for field, filename, model in (
            ("plan", "plan.json", ExperimentResult),
            ("findings", "findings.json", Finding),
        ):
            path = directory / filename
            if not path.is_file() or path.resolve().parent != directory.resolve():
                continue
            try:
                raw = json.loads(path.read_text())
                if field == "plan":
                    from chaosai.models import ExperimentSpec

                    model = ExperimentSpec
                setattr(snapshot, field, [model.model_validate(item) for item in raw])
            except (OSError, ValueError, TypeError):
                snapshot.warnings.append(f"{field.capitalize()} not available yet.")
        snapshot.series = bucket_samples(self.lines(directory / "samples.jsonl", Sample))
        roots = [directory]
        for child in sorted((directory / "experiments").glob("*")):
            if not child.is_dir() or not child.resolve().is_relative_to(directory):
                continue
            result = self.read(child, "result.json", ExperimentResult)
            if result:
                # Raw container text is intentionally excluded from the live API.
                result = result.model_copy(update={"log_excerpts": {}})
            spec = (
                result.spec
                if result
                else next((s for s in snapshot.plan if s.id == child.name), None)
            )
            snapshot.experiments.append(
                DashboardExperiment(
                    id=child.name,
                    spec=spec,
                    result=result,
                    events=self.lines(child / "events.jsonl", Event)
                    or (result.events if result else []),
                    series=bucket_samples(self.lines(child / "samples.jsonl", Sample)),
                )
            )
            roots.append(child)
        latest: dict[str, tuple[float, object]] = {}
        queues = []
        for root in roots:
            queues.extend(self.lines(root / "queues.jsonl", QueueObservation))
            for observation in self.lines(root / "containers.jsonl", ContainerObservation):
                if observation.t >= latest.get(observation.name, (-1, None))[0]:
                    latest[observation.name] = (observation.t, observation.stats)
        snapshot.containers = {name: pair[1] for name, pair in latest.items()}
        snapshot.queues = sorted(queues, key=lambda q: q.t)[-100:]
        levels = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        snapshot.findings.sort(key=lambda f: (levels[f.severity], f.experiment_id))
        if snapshot.graph:
            snapshot.dependency_impacts = [
                dependency_impact(snapshot.graph, experiment)
                for experiment in snapshot.experiments
                if experiment.spec and experiment.spec.fault != "traffic_spike"
            ]
        return snapshot


def dependency_impact(graph: Graph, experiment: DashboardExperiment) -> DependencyImpact:
    """Reverse dependencies are possible propagation paths, not measured service failures."""
    target = experiment.spec.target
    local = {node.id for node in graph.nodes if node.kind != "external"}
    reached = {target}
    frontier = {target}
    while frontier:
        next_nodes = {
            edge.source
            for edge in graph.edges
            if edge.target in frontier and edge.source in local and edge.source not in reached
        }
        reached.update(next_nodes)
        frontier = next_nodes
    return DependencyImpact(
        experiment_id=experiment.id,
        target=target,
        injected=any(event.phase == "OBSERVE" for event in experiment.events),
        potential_dependents=sorted(reached - {target}),
        observed_journeys=experiment.result.impacted_journeys if experiment.result else [],
    )


def bucket_samples(samples: list[Sample]) -> dict[str, list[DashboardPoint]]:
    groups: dict[tuple[str, int], list[Sample]] = defaultdict(list)
    if not samples:
        return {}
    # At most ~240 buckets across the supplied sample span.
    width = max(1, math.ceil((max(s.t for s in samples) - min(s.t for s in samples)) / 240))
    for sample in samples:
        groups[(sample.journey, int(sample.t // width) * width)].append(sample)
    series: dict[str, list[DashboardPoint]] = defaultdict(list)
    for (journey, start), values in sorted(groups.items()):
        stats = window_stats(values, start, start + width)
        series[journey].append(
            DashboardPoint(
                t=start,
                n=stats.n,
                p95_ms=stats.p95_ms,
                error_rate=stats.error_rate,
            )
        )
    return dict(series)


def command_for(request: DashboardRequest) -> list[str]:
    if request.action in {"run", "inject", "sandbox", "seed", "resume"} and not request.approved:
        raise ValueError("Explicit approval is required for this action.")
    if request.action == "fixture":
        return ["run", "--offline", "--yes"]
    if request.action == "run":
        return ["run", "--yes", "--max-experiments", str(request.max_experiments)]
    if request.action == "inject":
        return [
            "inject",
            request.fault,
            request.target,
            "--duration",
            str(request.duration_s),
            "--yes",
        ]
    if request.action == "resume":
        return ["rollback", "--clear-kill"]
    return [request.action]


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.Lock()
        self.job: DashboardJob | None = None
        self.process: subprocess.Popen | None = None
        self.closed = threading.Event()

    def status(self) -> DashboardJob | None:
        with self.lock:
            return self.job.model_copy(deep=True) if self.job else None

    def start(self, request: DashboardRequest) -> DashboardJob:
        command = command_for(request)
        if request.action in {"kill", "rollback", "resume"}:
            raise ValueError("Safety controls use the control endpoint.")
        if self.settings.kill_path.exists() and request.action in {
            "run", "inject", "sandbox", "baseline", "seed",
        }:
            raise ValueError("KILL is set. Review rollback and explicitly resume first.")
        with self.lock:
            if self.job and self.job.state == "running":
                raise ValueError("A job is already running; use KILL for an active fault.")
            # External CLI injectors use the same lease. The CLI rechecks before mutation.
            with file_lock(self.settings.active_lock_path, blocking=False):
                pass
            job = DashboardJob(action=request.action, state="running", started_at=time.time())
            self.job = job
            thread = threading.Thread(target=self._execute, args=(command, job), daemon=True)
            thread.start()
            return job.model_copy(deep=True)

    def _execute(self, command: list[str], job: DashboardJob) -> None:
        try:
            if self.closed.is_set():
                raise ValueError("Dashboard is closing.")
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", "chaosai", *command],
                cwd=self.settings.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with self.lock:
                self.process = process
                if self.closed.is_set():
                    process.send_signal(signal.SIGINT)
            for line in process.stdout:
                with self.lock:
                    job.output = (job.output + [redact_line(line.rstrip(), self.settings)])[-100:]
            code = process.wait()
        except Exception as exc:
            code = 1
            with self.lock:
                job.output.append(f"Job failed ({type(exc).__name__}).")
        with self.lock:
            job.exit_code = code
            job.ended_at = time.time()
            job.state = "complete" if code == 0 else "failed"
            self.process = None

    def shutdown(self) -> None:
        """Ask only this dashboard's child to interrupt and perform its normal rollback."""
        with self.lock:
            self.closed.set()
            process = self.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # The independent duration timer remains active; never SIGKILL an injector.
                pass

    def control(self, request: DashboardRequest) -> dict[str, object]:
        if request.action not in {"kill", "rollback", "resume"}:
            raise ValueError("Unknown safety control")
        if request.action == "resume":
            if self.status() and self.status().state == "running":
                raise ValueError("Wait for the active job to exit before resuming.")
            with file_lock(self.settings.active_lock_path, blocking=False):
                pass
        result = subprocess.run(
            [sys.executable, "-m", "chaosai", *command_for(request)],
            cwd=self.settings.project_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "exit_code": result.returncode,
            "output": [
                redact_line(line, self.settings)
                for line in (result.stdout + result.stderr).splitlines()
            ],
        }


def make_server(settings: Settings, host: str, port: int) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Dashboard must bind to loopback.")
    store, jobs, token = ArtifactStore(settings), JobManager(settings), secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def send(self, status: int, data: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def json(self, status: int, value: object) -> None:
            self.send(status, json.dumps(value, allow_nan=False).encode())

        def trusted_host(self) -> bool:
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def do_GET(self) -> None:
            if not self.trusted_host():
                self.json(403, {"error": "Loopback host required"})
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/":
                    html = (Path(__file__).parent / "templates/dashboard.html").read_text()
                    self.send(
                        200,
                        html.replace("__CSRF_TOKEN__", token).encode(),
                        "text/html; charset=utf-8",
                    )
                elif path == "/api/runs":
                    self.json(200, [r.model_dump(mode="json") for r in store.runs()])
                elif path == "/api/state":
                    job = jobs.status()
                    try:
                        with file_lock(settings.active_lock_path, blocking=False):
                            external_active = False
                    except RuntimeError:
                        external_active = True
                    self.json(
                        200,
                        {
                            "external_active": external_active,
                            "job": job.model_dump(mode="json") if job else None,
                            "kill": settings.kill_path.exists(),
                            "project": settings.compose_project,
                            "sandbox": (settings.safety_dir / "sandbox.json").exists(),
                        },
                    )
                elif path.startswith("/api/runs/"):
                    self.json(
                        200, store.snapshot(path.removeprefix("/api/runs/")).model_dump(mode="json")
                    )
                elif path.startswith("/reports/"):
                    directory = store.directory(path.removeprefix("/reports/"))
                    report = directory / "report.html"
                    if report.resolve().parent != directory or not report.is_file():
                        raise ValueError("Report not available")
                    self.send(200, report.read_bytes(), "text/html; charset=utf-8")
                else:
                    self.json(404, {"error": "Not found"})
            except (ValueError, OSError):
                self.json(404, {"error": "Run or report not available"})

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            allowed = {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }
            if (
                not self.trusted_host()
                or origin not in allowed
                or not hmac.compare_digest(self.headers.get("X-ChaosPilot-Token", ""), token)
            ):
                self.json(403, {"error": "Same-origin dashboard approval required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid request size")
                request = DashboardRequest.model_validate_json(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path == "/api/jobs":
                    self.json(202, jobs.start(request).model_dump(mode="json"))
                elif path == "/api/control":
                    self.json(200, jobs.control(request))
                else:
                    self.json(404, {"error": "Not found"})
            except (ValueError, OSError, RuntimeError) as exc:
                self.json(
                    409,
                    {
                        "error": str(exc)
                        if type(exc) is ValueError
                        else "Job refused; check active job and retained journals."
                    },
                )
            except Exception:
                self.json(500, {"error": "Control failed; inspect retained journal locally."})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.jobs = jobs
    return server
