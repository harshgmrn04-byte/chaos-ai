"""Pure sample statistics and read-only, scoped telemetry threads."""

import logging
import math
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any

import docker
import httpx

from chaosai.config import Settings
from chaosai.models import (
    ContainerObservation,
    ContainerStats,
    Graph,
    JourneyStats,
    QueueObservation,
    Sample,
)
from chaosai.rabbitmq import queue_snapshot
from chaosai.runs import append_jsonl
from chaosai.target import (
    TargetError,
    container_status,
    http_ready,
    list_target_containers,
    require_scope,
    tcp_ready,
)

logger = logging.getLogger(__name__)
MIN_BASELINE_SAMPLES = 20


def latency_limit_ms(baseline_p95: float, multiple: float, floor_ms: float) -> float:
    """A breach is strictly above this limit, satisfying both latency guards."""
    if any(not math.isfinite(v) or v < 0 for v in (baseline_p95, multiple, floor_ms)):
        raise ValueError("Latency inputs must be finite and nonnegative")
    return max(multiple * baseline_p95, baseline_p95 + floor_ms)


def percentile(values: Iterable[float], p: float) -> float:
    """Linear interpolation on the sorted n-1 index; empty input yields zero."""
    if not 0 <= p <= 100:
        raise ValueError("Percentile must be between 0 and 100")
    ordered = sorted(values)
    if not all(math.isfinite(v) for v in ordered):
        raise ValueError("Values must be finite")
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def window_stats(samples: Iterable[Sample], t0: float, t1: float) -> JourneyStats:
    """Count requests started in [t0,t1); timeouts are ordinary error samples."""
    if t1 <= t0:
        raise ValueError("Window must have positive duration")
    window = [s for s in samples if t0 <= s.t < t1]
    latencies = [s.latency_ms for s in window]
    n = len(window)
    return JourneyStats(
        n=n,
        low_sample=n < MIN_BASELINE_SAMPLES,
        error_rate=sum(not s.ok for s in window) / n if n else 0,
        p50_ms=percentile(latencies, 50),
        p95_ms=percentile(latencies, 95),
        p99_ms=percentile(latencies, 99),
        rps=n / (t1 - t0),
    )


def stats_by_journey(
    samples: Iterable[Sample],
    t0: float,
    t1: float,
    journeys: Iterable[str] = (),
) -> dict[str, JourneyStats]:
    values = list(samples)
    names = sorted(set(journeys) | {s.journey for s in values if t0 <= s.t < t1})
    return {name: window_stats((s for s in values if s.journey == name), t0, t1) for name in names}


# BUILD_PLAN.md uses both names for this contract.
by_journey = stats_by_journey


def docker_cpu_pct(stats: dict[str, Any]) -> float:
    current, previous = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    usage, old_usage = current.get("cpu_usage", {}), previous.get("cpu_usage", {})
    cpu_delta = usage.get("total_usage", 0) - old_usage.get("total_usage", 0)
    system_delta = current.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
    cpus = current.get("online_cpus") or len(usage.get("percpu_usage", []))
    return cpu_delta / system_delta * cpus * 100 if cpu_delta >= 0 and system_delta > 0 else 0


class ContainerStatsPoller(threading.Thread):
    def __init__(
        self,
        client: docker.DockerClient,
        settings: Settings,
        path: Path,
    ) -> None:
        super().__init__(daemon=True)
        self.client, self.settings, self.path = client, settings, path
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._observations: list[ContainerObservation] = []
        self._previous_cpu: dict[str, dict[str, Any]] = {}

    def poll_once(self) -> None:
        for container in list_target_containers(self.client, self.settings):
            try:
                container.reload()
                require_scope(container, self.settings.compose_project)
                state = container.attrs.get("State", {})
                status = "paused" if state.get("Paused", False) else container.status
                if state.get("Health", {}).get("Status") == "unhealthy":
                    status = "unhealthy"
                raw = (
                    container.stats(stream=False, one_shot=True)
                    if container.status == "running" and not state.get("Paused", False)
                    else {}
                )
                raw["precpu_stats"] = self._previous_cpu.get(container.id, raw.get("cpu_stats", {}))
                self._previous_cpu[container.id] = raw.get("cpu_stats", {})
                stats = ContainerStats(
                    status=status,
                    restarts=container.attrs.get("RestartCount", 0),
                    cpu_pct=docker_cpu_pct(raw),
                    mem_mb=raw.get("memory_stats", {}).get("usage", 0) / (1024 * 1024),
                )
                observation = ContainerObservation(t=time.time(), name=container.name, stats=stats)
                with self._lock:
                    append_jsonl(self.path, [observation])
                    self._observations.append(observation)
            except (TargetError, docker.errors.DockerException, OSError, ValueError):
                logger.warning("Scoped container telemetry unavailable for a target container.")

    def snapshot(self) -> list[ContainerObservation]:
        with self._lock:
            return list(self._observations)

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
            except (TargetError, docker.errors.DockerException, OSError):
                logger.warning("Scoped Docker telemetry unavailable.")
            self.stop_event.wait(
                max(0, self.settings.metrics_interval_s - (time.monotonic() - started))
            )

    def stop(self) -> None:
        self.stop_event.set()
        if self.ident is not None:
            self.join()


class QueueDepthPoller(threading.Thread):
    def __init__(self, settings: Settings, path: Path) -> None:
        super().__init__(daemon=True)
        self.settings, self.path = settings, path
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._observations: list[QueueObservation] = []
        self._warned_unavailable = False

    def poll_once(self, client: httpx.Client) -> None:
        snapshot = queue_snapshot(client, self.settings)
        # Total includes unacknowledged messages; ready-only counts hide in-flight work.
        observation = QueueObservation(
            t=time.time(),
            name=snapshot.name,
            depth=snapshot.messages,
        )
        with self._lock:
            append_jsonl(self.path, [observation])
            self._observations.append(observation)

    def snapshot(self) -> list[QueueObservation]:
        with self._lock:
            return list(self._observations)

    def run(self) -> None:
        with httpx.Client(timeout=self.settings.request_timeout_s, trust_env=False) as client:
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    self.poll_once(client)
                except (TargetError, OSError) as exc:
                    if not self._warned_unavailable:
                        reason = str(exc) if isinstance(exc, TargetError) else type(exc).__name__
                        logger.warning(
                            "RabbitMQ queue depth unavailable: %s. "
                            "No zero substituted; continuing polls.",
                            reason,
                        )
                        self._warned_unavailable = True
                self.stop_event.wait(
                    max(0, self.settings.metrics_interval_s - (time.monotonic() - started))
                )

    def stop(self) -> None:
        self.stop_event.set()
        if self.ident is not None:
            self.join()


def container_baseline(
    observations: Iterable[ContainerObservation],
    t0: float,
    t1: float,
) -> dict[str, ContainerStats]:
    groups: dict[str, list[ContainerStats]] = {}
    for item in observations:
        if t0 <= item.t < t1:
            groups.setdefault(item.name, []).append(item.stats)
    return {
        name: ContainerStats(
            cpu_pct=mean(s.cpu_pct for s in values),
            mem_mb=mean(s.mem_mb for s in values),
            restarts=values[-1].restarts,
            status=values[-1].status,
        )
        for name, values in groups.items()
    }


def queue_baseline(
    observations: Iterable[QueueObservation],
    t0: float,
    t1: float,
) -> dict[str, float]:
    groups: dict[str, list[float]] = {}
    for item in observations:
        if t0 <= item.t < t1:
            groups.setdefault(item.name, []).append(item.depth)
    return {name: mean(values) for name, values in groups.items()}


def probes(graph: Graph, settings: Settings, client: docker.DockerClient) -> dict[str, bool]:
    """Published infra TCP plus documented API routes; never fabricate /health."""
    result: dict[str, bool] = {}
    infra = {n.id for n in graph.nodes if n.kind in ("database", "broker", "cache")}
    result.update({name: False for name in infra})
    for container in list_target_containers(client, settings):
        container.reload()
        state = container_status(container, settings)
        if state.service in infra:
            result[state.service] = (
                bool(state.ports)
                and state.state == "running"
                and not state.paused
                and state.health != "unhealthy"
            )
            for host, port in state.ports:
                result[f"{state.service}:{port}"] = tcp_ready(host, port)
    for name, url in (
        ("frontend", settings.frontend_url.rstrip("/") + "/"),
        ("product-service", settings.product_url + "/products?limit=1"),
    ):
        result[name] = http_ready(url, timeout=settings.load_timeout_s)
    # Auth/order expose no public read-only health route. A 401 from protected GETs
    # proves middleware is answering without sending unknown credentials/tokens.
    with httpx.Client(timeout=settings.load_timeout_s, trust_env=False) as http:
        for name, url in (
            ("auth-service", settings.auth_url + "/me"),
            ("order-service", settings.order_url + "/orders"),
        ):
            try:
                result[name] = http.get(url).status_code == 401
            except httpx.HTTPError:
                result[name] = False
    return result
