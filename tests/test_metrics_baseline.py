from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from chaosai import baseline, metrics
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.load import JOURNEYS, SampleBuffer
from chaosai.metrics import (
    ContainerStatsPoller,
    QueueDepthPoller,
    container_baseline,
    docker_cpu_pct,
    percentile,
    queue_baseline,
    stats_by_journey,
    window_stats,
)
from chaosai.models import Baseline, ContainerObservation, ContainerStats, Graph, Sample
from chaosai.runs import read_json, read_jsonl
from chaosai.target import PROJECT_LABEL, SERVICE_LABEL


def sample(t: float, latency: float = 100, journey: str = "browse", ok: bool = True) -> Sample:
    return Sample(
        t=t,
        journey=journey,
        ok=ok,
        status=200 if ok else None,
        latency_ms=latency,
        error=None if ok else "timeout",
    )


@pytest.mark.parametrize(
    "values,p,expected",
    [
        ([], 95, 0),
        ([42], 99, 42),
        ([40, 10, 30, 20], 50, 25),
        ([0, 100], 95, 95),
        ([1, 2, 3], 0, 1),
        ([1, 2, 3], 100, 3),
    ],
)
def test_percentiles(values: list[float], p: float, expected: float) -> None:
    assert percentile(values, p) == expected


def test_percentile_invalid() -> None:
    with pytest.raises(ValueError):
        percentile([1], 101)
    with pytest.raises(ValueError):
        percentile([float("nan")], 50)


def test_window_half_open_and_errors() -> None:
    values = [sample(-1), sample(0, 10), sample(1, 5000, ok=False), sample(2)]
    stats = window_stats(values, 0, 2)
    assert stats.n == 2 and stats.error_rate == 0.5 and stats.rps == 1
    assert stats.p50_ms == 2505 and stats.p95_ms == 4750.5
    assert window_stats([], 0, 2).n == 0
    with pytest.raises(ValueError):
        window_stats(values, 1, 1)
    grouped = stats_by_journey([sample(1), sample(1, journey="checkout")], 0, 2, JOURNEYS)
    assert grouped["browse"].n == grouped["checkout"].n == 1
    assert grouped["login"].n == 0 and grouped["homepage"].rps == 0
    assert metrics.by_journey is stats_by_journey


@pytest.mark.parametrize(
    "second,stable", [(100, True), (130, True), (70, True), (131, False), (69, False)]
)
def test_stability(second: float, stable: bool) -> None:
    values = [sample(1, 100, j) for j in JOURNEYS] + [sample(8, second, j) for j in JOURNEYS]
    assert baseline.stability_check(values, 0, 10) is stable


def test_stability_missing_and_zero() -> None:
    assert not baseline.stability_check([], 0, 10)
    assert not baseline.stability_check([sample(1), sample(8)], 0, 10)
    assert baseline.stability_check([sample(1, 0), sample(8, 0)], 0, 10, ("browse",))
    assert not baseline.stability_check([sample(1, 0), sample(8, 1)], 0, 10, ("browse",))


def test_thresholds_weighted_errors_without_double_count() -> None:
    stats = window_stats([sample(1), sample(2, ok=False)], 0, 10)
    thresholds = baseline.derive_thresholds(stats)
    assert thresholds.hypothesis.max_error_rate == 0.52
    assert thresholds.hypothesis.p95_multiple == 2
    assert thresholds.abort.model_dump() == {
        "max_error_rate": 0.5,
        "p95_multiple": 10,
        "max_unhealthy_containers": 3,
        "min_samples": 10,
        "consecutive_breaches": 2,
    }
    value = baseline.build_baseline([sample(1), sample(2, ok=False)], 0, 10)
    assert baseline.derive_thresholds(value) == thresholds
    value.journeys.pop("all")
    assert baseline.derive_thresholds(value) == thresholds
    assert baseline.derive_thresholds(window_stats([], 0, 10)).hypothesis.max_error_rate == 0.02


def test_warmup_discarded_in_memory_and_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = SampleBuffer(tmp_path / "samples.jsonl")
    generator = Mock(samples=buffer)
    generator.set_workers.side_effect = lambda n: buffer.append(sample(0))
    monkeypatch.setattr(baseline.time, "time", lambda: 30)

    def wait(seconds: float) -> None:
        if seconds == 30:
            buffer.append(sample(29))
        else:
            buffer.append(sample(29.9))  # warm-up request finishes in measured window
            buffer.append(sample(31))
            buffer.append(sample(150))  # end boundary is also excluded

    t0, t1 = baseline.collect_window(generator, 10, 30, 120, wait=wait)
    assert (t0, t1) == (30, 150)
    assert buffer.snapshot() == [sample(31)]
    assert read_jsonl(buffer.path, Sample) == [sample(31)]


def test_container_cpu_formula() -> None:
    raw = {
        "cpu_stats": {
            "system_cpu_usage": 2000,
            "online_cpus": 4,
            "cpu_usage": {"total_usage": 300},
        },
        "precpu_stats": {"system_cpu_usage": 1000, "cpu_usage": {"total_usage": 100}},
    }
    assert docker_cpu_pct(raw) == 80
    raw["cpu_stats"]["system_cpu_usage"] = 1000
    assert docker_cpu_pct(raw) == 0
    assert docker_cpu_pct({}) == 0


def test_container_poller_scope_status_and_previous_cpu(settings: Settings, tmp_path: Path) -> None:
    scoped = Mock(
        id="one",
        labels={PROJECT_LABEL: settings.compose_project, SERVICE_LABEL: "product-service"},
        status="running",
    )
    scoped.name = "ecommerce-products"
    scoped.attrs = {"RestartCount": 2, "State": {}}
    scoped.stats.side_effect = [
        {
            "cpu_stats": {
                "system_cpu_usage": 1000,
                "online_cpus": 2,
                "cpu_usage": {"total_usage": 100},
            },
            "memory_stats": {"usage": 1048576},
        },
        {
            "cpu_stats": {
                "system_cpu_usage": 2000,
                "online_cpus": 2,
                "cpu_usage": {"total_usage": 300},
            },
            "memory_stats": {"usage": 2097152},
        },
    ]
    foreign = Mock(labels={PROJECT_LABEL: "unrelated"})
    client = Mock()
    client.containers.list.return_value = [scoped, foreign]
    poller = ContainerStatsPoller(client, settings, tmp_path / "containers.jsonl")
    poller.poll_once()
    poller.poll_once()
    scoped.status = "exited"
    poller.poll_once()
    readings = poller.snapshot()
    assert len(readings) == 3
    assert readings[0].stats.cpu_pct == 0
    assert readings[1].stats.cpu_pct == 40 and readings[1].stats.mem_mb == 2
    assert readings[-1].stats.status == "exited" and readings[-1].stats.restarts == 2
    foreign.reload.assert_not_called()
    client.containers.list.assert_called_with(
        all=True, filters={"label": f"{PROJECT_LABEL}={settings.compose_project}"}
    )
    assert len(read_jsonl(tmp_path / "containers.jsonl", ContainerObservation)) == 3
    stats = container_baseline(readings, readings[0].t, readings[-1].t + 1)[scoped.name]
    assert stats.status == "exited" and stats.mem_mb == 1


def test_queue_contract_and_unavailable_not_zero(settings: Settings, tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "name": "notifications",
                "vhost": "/",
                "consumers": 1,
                "messages_ready": 3,
                "messages": 5,
            },
        )

    poller = QueueDepthPoller(settings, tmp_path / "queues.jsonl")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        poller.poll_once(client)
    assert seen[0].url.raw_path == b"/api/queues/%2F/notifications"
    assert seen[0].headers["Authorization"].startswith("Basic ")
    observations = poller.snapshot()
    assert observations[0].depth == 5
    assert queue_baseline(observations, observations[0].t, observations[0].t + 1) == {
        "notifications": 5
    }
    assert queue_baseline([], 0, 1) == {}


def test_offline_baseline_never_opens_docker(settings: Settings, tmp_path: Path) -> None:
    value = baseline.measure_baseline(fixture("graph", Graph), settings, tmp_path, offline=True)
    assert value == fixture("baseline", Baseline)
    assert read_json(tmp_path / "baseline.json", Baseline) == value
    assert not (settings.project_root / ".chaosai" / "baseline.json").exists()


def test_measured_artifacts_and_cleanup(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = SampleBuffer(tmp_path / "samples.jsonl")
    for j in JOURNEYS:
        buffer.append(sample(11, journey=j))
        buffer.append(sample(19, journey=j))
    generator = Mock(samples=buffer, failed=SimpleNamespace(is_set=lambda: False))
    docker_client, containers, queues = Mock(), Mock(), Mock()
    containers.snapshot.return_value = [
        ContainerObservation(
            t=15,
            name="ecommerce-products",
            stats=ContainerStats(cpu_pct=20, mem_mb=30, restarts=1, status="running"),
        )
    ]
    queues.snapshot.return_value = []
    monkeypatch.setattr(baseline, "get_docker_client", lambda: docker_client)
    monkeypatch.setattr(baseline, "LoadGenerator", lambda *a: generator)
    monkeypatch.setattr(baseline, "ContainerStatsPoller", lambda *a: containers)
    monkeypatch.setattr(baseline, "QueueDepthPoller", lambda *a: queues)
    monkeypatch.setattr(baseline, "probes", lambda *a: {"frontend": True})
    measured: list[tuple[float, float]] = []

    def collect(g: object, workers: int, warmup: float, duration: float) -> tuple[float, float]:
        measured.append((warmup, duration))
        return 10, 20

    monkeypatch.setattr(baseline, "collect_window", collect)
    value = baseline.measure_baseline(
        fixture("graph", Graph), settings, tmp_path, duration_s=99, quick=True
    )
    assert measured == [(10, 40)] and value.stable
    assert value.containers["ecommerce-products"].cpu_pct == 20 and value.queues == {}
    assert read_json(tmp_path / "baseline.json", Baseline) == value
    assert read_json(settings.project_root / ".chaosai" / "baseline.json", Baseline) == value
    for obj in (generator, containers, queues):
        obj.stop.assert_called_once()
    docker_client.close.assert_called_once()


def test_probes_use_published_tcp_and_safe_http_contracts(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    requests: list[httpx.Request] = []
    graph = fixture("graph", Graph)
    containers = [
        Mock(
            labels={PROJECT_LABEL: settings.compose_project, SERVICE_LABEL: "postgres"},
            status="running",
        )
    ]
    containers[0].name = "ecommerce-postgres"
    containers[0].attrs = {
        "State": {},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5433"}]}},
    }
    client = Mock()
    client.containers.list.return_value = containers
    monkeypatch.setattr(
        metrics, "tcp_ready", lambda host, port: host == "127.0.0.1" and port == 5433
    )

    def ready(url: str, timeout: float) -> bool:
        seen.append(url)
        return True

    monkeypatch.setattr(metrics, "http_ready", ready)

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "Authorization" not in request.headers
        return httpx.Response(401)

    original = httpx.Client
    monkeypatch.setattr(
        metrics.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(response), **kw),
    )
    result = metrics.probes(graph, settings, client)
    assert result["postgres"] and result["postgres:5433"]
    assert not result["mongodb"] and not result["redis"] and not result["rabbitmq"]
    assert seen == [settings.frontend_url + "/", settings.product_url + "/products?limit=1"]
    assert [r.url.path for r in requests] == ["/me", "/orders"]
    assert result["auth-service"] and result["order-service"]


@pytest.mark.parametrize(
    "status,body",
    [(404, {}), (200, {"name": "wrong"}), (200, {"name": "notifications", "vhost": "/"})],
)
def test_queue_failure_creates_no_zero_observation(
    settings: Settings,
    tmp_path: Path,
    status: int,
    body: dict[str, str],
) -> None:
    from chaosai.target import TargetError

    poller = QueueDepthPoller(settings, tmp_path / "queue.jsonl")
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    ) as c:
        with pytest.raises(TargetError):
            poller.poll_once(c)
    assert poller.snapshot() == [] and not poller.path.exists()


def test_baseline_prepare_failure_cleans_up_and_does_not_save(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaosai.target import TargetError

    client, generator, containers, queues = Mock(), Mock(), Mock(), Mock()
    generator.prepare.side_effect = TargetError("No products")
    monkeypatch.setattr(baseline, "get_docker_client", lambda: client)
    monkeypatch.setattr(baseline, "LoadGenerator", lambda *a: generator)
    monkeypatch.setattr(baseline, "ContainerStatsPoller", lambda *a: containers)
    monkeypatch.setattr(baseline, "QueueDepthPoller", lambda *a: queues)
    with pytest.raises(TargetError, match="No products"):
        baseline.measure_baseline(fixture("graph", Graph), settings, tmp_path)
    for obj in (generator, containers, queues):
        obj.stop.assert_called_once()
    client.close.assert_called_once()
    assert not (tmp_path / "baseline.json").exists()
