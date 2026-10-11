"""Scope-checked log bounds, redaction and container transition evidence."""

from types import SimpleNamespace
from unittest.mock import Mock

from chaosai.config import Settings
from chaosai.evidence import collect_logs, container_events, redact_line
from chaosai.fixture_data import fixture
from chaosai.models import ContainerObservation, ContainerStats, Graph
from tests.test_evaluate import baseline_for
from tests.test_safety import FakeContainer


def test_logs_target_direct_dependents_only_and_redaction(settings: Settings) -> None:
    graph = fixture("graph", Graph)
    containers = {
        node.container: FakeContainer(node.container, settings.compose_project)
        for node in graph.nodes
        if node.container
    }
    for container in containers.values():
        container.logs = Mock(
            return_value=(
                "\n".join(["x" * 400] * 50) + "\n" + "Authorization: Bearer abc\n"
            ).encode()
        )
    client = SimpleNamespace(containers=SimpleNamespace(get=Mock(side_effect=containers.get)))
    excerpts = collect_logs(client, graph, "redis", settings, 100)
    expected = {
        node.container
        for node in graph.nodes
        if node.id == "redis"
        or node.id in {edge.source for edge in graph.edges if edge.target == "redis"}
    }
    assert set(excerpts) == expected
    assert {call.args[0] for call in client.containers.get.call_args_list} == expected
    assert all(len(lines) == 40 for lines in excerpts.values())
    assert all(len(line) <= 300 for lines in excerpts.values() for line in lines)
    assert all(lines[-1] == "[redacted secret-bearing log line]" for lines in excerpts.values())
    foreign = containers[
        next(node.container for node in graph.nodes if node.id == "product-service")
    ]
    foreign.labels = {}
    foreign.logs.reset_mock()
    excerpts = collect_logs(client, graph, "redis", settings, 100)
    foreign.logs.assert_not_called()
    assert excerpts[foreign.name] == ["Scoped log evidence unavailable."]


def test_known_secret_values_and_controls(settings: Settings, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "value-with-no-keywords")
    for line in (
        "value-with-no-keywords",
        settings.seed_password.get_secret_value(),
        "redis://user:pass@redis:6379",
        "eyJhbGc.abcd.signature",
    ):
        assert redact_line(line, settings) == "[redacted secret-bearing log line]"
    assert redact_line("\x1b[31mnormal\x1b[0m\x07", settings) == "normal"


def test_restart_changes_even_when_state_is_running() -> None:
    baseline = baseline_for()
    baseline.containers = {"app": ContainerStats(cpu_pct=0, mem_mb=0, restarts=0, status="running")}
    observations = [
        ContainerObservation(
            t=i,
            name="app",
            stats=ContainerStats(cpu_pct=0, mem_mb=0, restarts=restarts, status=status),
        )
        for i, (status, restarts) in enumerate(
            [("running", 0), ("exited", 0), ("running", 1), ("running", 1)]
        )
    ]
    events = container_events(observations, baseline)
    assert len(events) == 2 and "exited" in events[0] and "('running', 1)" in events[1]
