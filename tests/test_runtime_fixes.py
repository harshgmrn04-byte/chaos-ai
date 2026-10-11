"""Regression cases for observed laptop noise, sparse recovery and scope."""

from types import SimpleNamespace

import pytest

from chaosai.baseline import stability_check
from chaosai.evaluate import find_recovery, recovery_window_s
from chaosai.fixture_data import fixture
from chaosai.models import Graph
from chaosai.orchestrator import annotate_capabilities, baseline_reusable
from chaosai.runs import write_json
from chaosai.sandbox import assert_idle, patched_auth, sandbox_overlay
from tests.test_evaluate import baseline_for, sample


def test_small_absolute_drift_is_noise_but_large_regression_fails():
    # The supplied healthy run drifted 7.7 -> 12.3ms, not a resilience failure.
    samples = [sample(1, latency=7.7)] * 10 + [sample(8, latency=12.3)] * 10
    assert stability_check(samples, 0, 10, ("browse",))
    samples = [sample(1, latency=13.5)] * 10 + [sample(8, latency=101.3)] * 10
    assert stability_check(samples, 0, 10, ("browse",))
    samples = [sample(1, latency=13.5)] * 10 + [sample(8, latency=114)] * 10
    assert not stability_check(samples, 0, 10, ("browse",))
    samples = [sample(1, latency=1000)] * 10 + [sample(8, latency=1301)] * 10
    assert not stability_check(samples, 0, 10, ("browse",))


def test_sparse_half_rps_recovery_uses_three_observed_windows():
    baseline = baseline_for()
    baseline.journeys["login"].rps = 0.5
    values = [sample(i + 0.1) for i in range(6)]
    values += [sample(i + 0.1, "login") for i in (0, 2, 4)]
    assert recovery_window_s(baseline) == 2
    assert find_recovery(values, baseline, baseline.thresholds, 0, t_end=5.99) == (False, None)
    assert find_recovery(values, baseline, baseline.thresholds, 0, t_end=6) == (True, 6)
    assert find_recovery(values[:-1], baseline, baseline.thresholds, 0, t_end=6) == (False, None)
    values[-1].ok = False
    assert find_recovery(values, baseline, baseline.thresholds, 0, t_end=6) == (False, None)


def test_contaminated_baseline_cannot_be_reused():
    baseline = baseline_for()
    assert baseline_reusable(baseline, baseline.created_at)
    baseline.journeys["login"].error_rate = 0.1
    assert not baseline_reusable(baseline, baseline.created_at)


def test_auth_patch_preserves_default_and_has_bounded_override():
    source = "const limiter = rateLimit({\n  max: 100,\n});"
    result = patched_auth(source)
    assert "|| 100" in result and "Math.max(100," in result and "Math.min(10000," in result
    with pytest.raises(RuntimeError):
        patched_auth("Different source")


def test_sandbox_refuses_kill_and_tampered_overlay(settings):
    settings.safety_dir.mkdir()
    settings.kill_path.write_text("stop")
    with pytest.raises(RuntimeError, match="KILL"):
        assert_idle(settings)
    settings.kill_path.unlink()
    write_json(
        settings.safety_dir / "sandbox.json",
        {"project": settings.compose_project, "overlay_sha256": "wrong"},
    )
    (settings.safety_dir / "sandbox.compose.yaml").write_text("services: {}")
    with pytest.raises(RuntimeError, match="changed"):
        sandbox_overlay(settings)


def test_cpu_runtime_refusal_is_planner_data_and_scope_checked(settings):
    graph = fixture("graph", Graph)
    containers = {
        n.container: SimpleNamespace(
            reload=lambda: None,
            labels={"com.docker.compose.project": settings.compose_project},
            attrs={"HostConfig": {"CpuQuota": 0, "NanoCpus": 0}},
        )
        for n in graph.nodes
        if n.container
    }
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: containers[name]))
    annotate_capabilities(graph, client, settings)
    assert all(
        "cpu_limit" in n.unsupported_faults
        for n in graph.nodes
        if n.injectable and n.kind != "external"
    )
    node = next(n for n in graph.nodes if n.injectable and n.container)
    containers[node.container].labels = {"com.docker.compose.project": "other"}
    with pytest.raises(RuntimeError, match="scope"):
        annotate_capabilities(graph, client, settings)


def test_readiness_expands_sample_window_without_lowering_sample_minimum():
    from chaosai.evaluate import near_baseline

    baseline = baseline_for(("browse",))
    baseline.journeys["browse"].rps = 1
    # 8 samples in the estimated 10s window, 16 in a full 20s: legitimate slower cadence.
    values = [sample(i * 1.25) for i in range(16)]
    assert near_baseline(values, baseline, baseline.thresholds, 0, 20)[0]
    assert not near_baseline(values[:9], baseline, baseline.thresholds, 0, 20)[0]


@pytest.mark.parametrize("missing_image", [False, True])
def test_sandbox_preserves_original_before_scoped_recreation(settings, monkeypatch, missing_image):
    from pathlib import Path
    from unittest.mock import Mock

    import docker
    import yaml

    from chaosai import sandbox
    from chaosai.runs import read_json

    settings.target_dir = Path("target")
    source = settings.resolved_target_dir / "auth-service/src/index.ts"
    source.parent.mkdir(parents=True)
    original = "const limiter = rateLimit({\n  max: 100,\n});"
    source.write_text(original)
    client = Mock()
    client.api.commit.return_value = {"Id": "sha256:snapshot"}
    if missing_image:
        client.images.get.side_effect = docker.errors.ImageNotFound("missing")
    auth = SimpleNamespace(id="owned-auth", attrs={"Image": "sha256:original"})
    monkeypatch.setattr(sandbox, "get_docker_client", lambda: client)
    monkeypatch.setattr(sandbox, "service_container", lambda *a: auth)
    commands = []

    def execute(command, config):
        commands.append(command)
        if command[1] == "compose":
            # Restoration metadata must exist before recreation begins.
            marker = read_json(config.safety_dir / "sandbox.json", dict)
            assert marker["original_image"] == (
                "sha256:snapshot" if missing_image else "sha256:original"
            )
            assert command[-5:] == ["up", "-d", "--no-deps", "--no-build", "auth-service"]
            assert config.compose_project in command

    monkeypatch.setattr(sandbox, "_run", execute)
    sandbox.setup_sandbox(settings)
    assert source.read_text() == original
    overlay = yaml.safe_load(sandbox.sandbox_overlay(settings).read_text())
    service = overlay["services"]["auth-service"]
    assert service["environment"]["AUTH_RATE_LIMIT_MAX"] == "6000"
    assert Path(service["build"]["context"]).is_relative_to(settings.safety_dir)
    client.close.assert_called_once()
    sandbox.setup_sandbox(settings, restore=True)
    assert not (settings.safety_dir / "sandbox.json").exists()
    assert source.read_text() == original
    assert len([c for c in commands if c[1] == "compose"]) == 2


def test_sandbox_rejects_cross_project_marker_before_docker(settings, monkeypatch):
    from unittest.mock import Mock

    from chaosai import sandbox

    write_json(settings.safety_dir / "sandbox.json", {"project": "other"})
    client = Mock(side_effect=AssertionError("Must not reach Docker"))
    monkeypatch.setattr(sandbox, "get_docker_client", client)
    with pytest.raises(RuntimeError, match="another Compose project"):
        sandbox.setup_sandbox(settings)
    client.assert_not_called()
