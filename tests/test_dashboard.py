"""Dashboard boundaries use mock transport and real private artifact parsing."""

import errno
import io
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from chaosai import dashboard
from chaosai.approval import Approver
from chaosai.dashboard import ArtifactStore, JobManager, bucket_samples, command_for
from chaosai.models import (
    DashboardJob,
    DashboardRequest,
    Event,
    Sample,
)
from chaosai.runner import run_pipeline


@pytest.fixture
def fixture_run(settings):
    return run_pipeline(
        settings, Approver(yes=True, is_tty=False, ask=lambda _: "y", simulation=True), offline=True
    )


def test_snapshot_fixture_and_partial_live_records(settings, fixture_run):
    store = ArtifactStore(settings)
    result = store.snapshot(fixture_run.name)
    assert result.meta.offline and result.findings and result.summary
    assert len(result.experiments) == 3
    assert all(not e.result.log_excerpts for e in result.experiments)
    assert result.experiments[0].series
    live = fixture_run / "experiments" / "live"
    live.mkdir()
    (live / "events.jsonl").write_text(
        Event(t=1, phase="OBSERVE", note="Applied").model_dump_json() + '\n{"t":',
    )
    assert (
        next(e for e in store.snapshot(fixture_run.name).experiments if e.id == "live")
        .events[0]
        .phase
        == "OBSERVE"
    )
    assert store.runs()[0].source == "fixture"


def test_findings_are_ranked_by_severity_without_changing_saved_evidence(settings, fixture_run):
    path = fixture_run / "findings.json"
    values = json.loads(path.read_text())
    for item, level in zip(values, ("low", "critical", "high"), strict=True):
        item["severity"] = level
    path.write_text(json.dumps(values))
    original = path.read_bytes()
    result = ArtifactStore(settings).snapshot(fixture_run.name)
    assert [f.severity for f in result.findings] == ["critical", "high", "low"]
    assert path.read_bytes() == original


def test_dependency_overlay_handles_cycles_and_keeps_inferred_and_observed_separate(
    settings, fixture_run
):
    from chaosai.models import Edge

    snapshot = ArtifactStore(settings).snapshot(fixture_run.name)
    experiment = snapshot.experiments[0]
    experiment.spec.target = "postgres"
    experiment.result.impacted_journeys = ["login"]
    experiment.events = [Event(t=1, phase="OBSERVE", note="Applied")]
    graph = snapshot.graph
    graph.edges = [
        Edge(source="auth-service", target="postgres", kind="sql", evidence="source"),
        Edge(source="frontend", target="auth-service", kind="http", evidence="source"),
        Edge(source="postgres", target="frontend", kind="unknown", evidence="cycle"),
        Edge(source="stripe", target="postgres", kind="unknown", evidence="external"),
    ]
    impact = dashboard.dependency_impact(graph, experiment)
    assert impact.potential_dependents == ["auth-service", "frontend"]
    assert impact.observed_journeys == ["login"] and impact.injected
    experiment.events = []
    experiment.result = None
    preview = dashboard.dependency_impact(graph, experiment)
    assert not preview.injected and preview.observed_journeys == []


@pytest.mark.parametrize("name", ["..", "../.env", "/tmp", "a/b", ""])
def test_store_path_traversal(settings, name):
    with pytest.raises(ValueError):
        ArtifactStore(settings).directory(name)


def test_store_refuses_symlink_escape(settings, tmp_path):
    settings.runs_dir.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (settings.runs_dir / "escape").symlink_to(outside)
    assert ArtifactStore(settings).runs() == []
    with pytest.raises(ValueError):
        ArtifactStore(settings).directory("escape")


def test_bucket_errors_latency_and_bounded_payload():
    values = [
        Sample(t=i, journey="browse", ok=i % 2 == 0, status=200, latency_ms=10 + i % 3, error=None)
        for i in range(10000)
    ]
    series = bucket_samples(values)["browse"]
    assert len(series) <= 241
    assert all(p.n > 0 and 0 <= p.error_rate <= 1 for p in series)
    assert sum(p.n for p in series) == len(values)
    assert bucket_samples([]) == {}


@pytest.mark.parametrize("action", ["run", "inject", "sandbox", "seed", "resume"])
def test_mutation_requires_explicit_browser_approval(action):
    with pytest.raises(ValueError, match="approval"):
        command_for(DashboardRequest(action=action))
    assert command_for(DashboardRequest(action=action, approved=True))


def test_commands_are_argv_and_fixture_is_explicitly_offline():
    assert command_for(DashboardRequest(action="fixture")) == ["run", "--offline", "--yes"]
    assert command_for(DashboardRequest(action="run", approved=True, max_experiments=4)) == [
        "run",
        "--yes",
        "--max-experiments",
        "4",
    ]
    with pytest.raises(ValueError):
        DashboardRequest(action="inject", approved=True, target="redis; curl evil")
    with pytest.raises(ValueError):
        DashboardRequest(action="inject", approved=True, duration_s=121)


def test_job_serialization(settings):
    manager = JobManager(settings)
    manager.job = DashboardJob(action="run", state="running", started_at=1)
    with pytest.raises(ValueError, match="already running"):
        manager.start(DashboardRequest(action="baseline"))
    with pytest.raises(ValueError, match="active job"):
        manager.control(DashboardRequest(action="resume", approved=True))


@pytest.mark.parametrize("action", ["run", "inject", "sandbox", "baseline", "seed"])
def test_dashboard_refuses_live_job_while_kill_is_set(settings, action):
    settings.kill_path.parent.mkdir(parents=True)
    settings.kill_path.touch()
    manager = JobManager(settings)
    with pytest.raises(ValueError, match="KILL is set"):
        manager.start(DashboardRequest(action=action, approved=True))
    assert manager.status() is None


def test_dashboard_retains_saved_stop_reason(settings, fixture_run):
    path = fixture_run / "run.json"
    meta = json.loads(path.read_text())
    meta["stop_reason"] = "KILL flag present; explicitly clear it after reviewing rollback."
    path.write_text(json.dumps(meta))
    snapshot = ArtifactStore(settings).snapshot(fixture_run.name)
    assert any("Run stopped: KILL flag present" in warning for warning in snapshot.warnings)


def test_dashboard_uses_result_events_when_stream_is_missing(settings, fixture_run):
    snapshot = ArtifactStore(settings).snapshot(fixture_run.name)
    experiment = snapshot.experiments[0]
    stream = fixture_run / "experiments" / experiment.id / "events.jsonl"
    stream.unlink(missing_ok=True)
    assert ArtifactStore(settings).snapshot(fixture_run.name).experiments[0].events == (
        experiment.result.events
    )


def test_dashboard_prefers_live_stream_over_saved_result(settings, fixture_run):
    experiment = ArtifactStore(settings).snapshot(fixture_run.name).experiments[0]
    event = Event(t=1, phase="RECOVER", note="Recorded event")
    stream = fixture_run / "experiments" / experiment.id / "events.jsonl"
    stream.write_text(event.model_dump_json() + "\n")
    assert ArtifactStore(settings).snapshot(fixture_run.name).experiments[0].events == [event]


def test_job_output_redacts_known_secrets(settings, monkeypatch):
    secret = settings.seed_password.get_secret_value()
    fake = SimpleNamespace(
        stdout=iter([f"password={secret}\n", "Measured result saved\n"]), wait=lambda: 0
    )
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *a, **k: fake)
    manager = JobManager(settings)
    job = DashboardJob(action="smoke", state="running", started_at=1)
    manager.job = job
    manager._execute(["smoke"], job)
    assert job.state == "complete" and job.exit_code == 0
    assert secret not in str(job.output) and "redacted" in job.output[0]


@pytest.fixture
def handler(settings, monkeypatch):
    captured = []

    def server_factory(address, cls):
        captured.append(cls)
        return SimpleNamespace(server_port=8765, daemon_threads=True)

    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", server_factory)
    dashboard.make_server(settings, "127.0.0.1", 8765)
    value = object.__new__(captured[0])
    value.server = SimpleNamespace(server_port=8765)
    value.headers = {"Host": "127.0.0.1:8765"}
    value.responses = []
    value.send = lambda status, data, kind="application/json": value.responses.append(
        (status, data, kind)
    )
    value.path = "/"
    value.do_GET()
    html = value.responses[-1][1].decode()
    token = html.split("const csrf='")[1].split("'")[0]
    value.responses.clear()
    value.token = token
    return value


def test_host_origin_token_and_private_file_boundaries(handler):
    handler.path = "/.env"
    handler.do_GET()
    assert handler.responses[-1][0] == 404
    handler.headers = {"Host": "evil.example"}
    handler.path = "/api/runs"
    handler.do_GET()
    assert handler.responses[-1][0] == 403
    handler.headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "https://evil.example",
        "X-ChaosPilot-Token": handler.token,
    }
    handler.path = "/api/jobs"
    handler.do_POST()
    assert handler.responses[-1][0] == 403
    handler.headers["Origin"] = "http://127.0.0.1:8765"
    handler.headers["X-ChaosPilot-Token"] = "wrong"
    handler.do_POST()
    assert handler.responses[-1][0] == 403


def test_browser_unapproved_fault_refused(handler):
    body = json.dumps({"action": "run", "approved": False}).encode()
    handler.headers.update(
        {
            "Origin": "http://127.0.0.1:8765",
            "X-ChaosPilot-Token": handler.token,
            "Content-Length": str(len(body)),
        }
    )
    handler.path = "/api/jobs"
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    assert handler.responses[-1][0] == 409
    assert b"approval" in handler.responses[-1][1]


def test_no_public_bind(settings):
    with pytest.raises(ValueError, match="loopback"):
        dashboard.make_server(settings, "0.0.0.0", 8765)


@pytest.mark.parametrize(
    ("code", "hint"),
    [
        (errno.EADDRINUSE, "already in use"),
        (errno.EPERM, "regular terminal"),
        (errno.EACCES, "regular terminal"),
        (errno.EADDRNOTAVAIL, "Check the host and port"),
    ],
)
def test_dashboard_startup_explains_os_failure(cli_workspace, monkeypatch, code, hint):
    def fail(*args):
        raise OSError(code, "unsafe detail must not appear")

    monkeypatch.setattr(dashboard, "make_server", fail)
    from chaosai.cli import app

    result = CliRunner().invoke(app, ["dashboard", "--port", "9876"])
    assert result.exit_code == 1 and hint in result.output
    assert "unsafe detail" not in result.output
    if code == errno.EADDRINUSE:
        assert "http://127.0.0.1:9876" in result.output
        assert "--port 8766" in result.output


@pytest.mark.parametrize("error", [ValueError("secret setting"), OSError("secret setting")])
def test_dashboard_invalid_config_has_safe_hint(cli_workspace, monkeypatch, error):
    from chaosai import cli

    def fail():
        raise error

    monkeypatch.setattr(cli, "load_config", fail)
    result = CliRunner().invoke(cli.app, ["dashboard"])
    assert result.exit_code == 1
    assert "DASHBOARD_HOST" in result.output and "DASHBOARD_PORT" in result.output
    assert "secret setting" not in result.output
