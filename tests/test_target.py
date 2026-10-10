import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docker
import httpx
import pytest

from chaosai import target
from chaosai.config import Settings
from chaosai.target import TargetError


def container(service: str, project: str = "ecommerce-microservices") -> MagicMock:
    result = MagicMock()
    result.name = f"test-{service}"
    result.status = "running"
    result.labels = {target.PROJECT_LABEL: project, target.SERVICE_LABEL: service}
    result.attrs = {"State": {"Paused": False}, "NetworkSettings": {"Ports": {}}}
    return result


def test_env_precedes_context_and_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://localhost:2375")
    client = MagicMock()
    from_env = MagicMock(return_value=client)
    monkeypatch.setattr(target.docker, "from_env", from_env)
    monkeypatch.setattr(
        target.subprocess, "run", MagicMock(side_effect=AssertionError("No context"))
    )
    assert target.get_docker_client() is client
    from_env.assert_called_once_with(timeout=5)
    client.ping.assert_called_once()


def test_context_before_socket_and_failed_client_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///stale.sock")
    stale = MagicMock()
    stale.ping.side_effect = docker.errors.DockerException("secret-test-value")
    monkeypatch.setattr(target.docker, "from_env", lambda **_: stale)
    inspect = MagicMock(
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps("unix:///context.sock"))
    )
    monkeypatch.setattr(target.subprocess, "run", inspect)
    good = MagicMock()
    factory = MagicMock(return_value=good)
    monkeypatch.setattr(target.docker, "DockerClient", factory)
    assert target.get_docker_client() is good
    stale.close.assert_called_once()
    factory.assert_called_once_with(base_url="unix:///context.sock", timeout=5)
    assert inspect.call_args.args[0][:3] == ["docker", "context", "inspect"]


@pytest.mark.parametrize("context", ["unavailable", "malformed", "duplicate"])
def test_socket_fallback_order(monkeypatch: pytest.MonkeyPatch, context: str) -> None:
    monkeypatch.setattr(Path, "home", lambda: Path("/fakehome"))
    if context == "unavailable":
        inspect = MagicMock(side_effect=FileNotFoundError())
    else:
        stdout = (
            "invalid" if context == "malformed" else '"unix:///fakehome/.docker/run/docker.sock"'
        )
        inspect = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=stdout))
    monkeypatch.setattr(target.subprocess, "run", inspect)
    failed, success = MagicMock(), MagicMock()
    failed.ping.return_value = False
    factory = MagicMock(side_effect=[failed, success])
    monkeypatch.setattr(target.docker, "DockerClient", factory)
    assert target.get_docker_client() is success
    assert [call.kwargs["base_url"] for call in factory.call_args_list] == [
        "unix:///fakehome/.docker/run/docker.sock",
        "unix:///var/run/docker.sock",
    ]
    failed.close.assert_called_once()


def test_all_socket_failures_have_safe_fix_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://user:secret-value@bad-host:2375")
    monkeypatch.setattr(
        target.docker, "from_env", MagicMock(side_effect=ValueError("secret-value"))
    )
    monkeypatch.setattr(
        target.subprocess, "run", MagicMock(side_effect=subprocess.TimeoutExpired([], 5))
    )
    monkeypatch.setattr(
        target.docker, "DockerClient", MagicMock(side_effect=OSError("secret-value"))
    )
    with pytest.raises(TargetError, match="Start Docker Desktop") as error:
        target.get_docker_client()
    assert "secret-value" not in str(error.value)


def test_scope_filters_and_rechecks(settings: Settings) -> None:
    client = MagicMock()
    good, foreign = container("postgres"), container("postgres", "foreign")
    client.containers.list.return_value = [good, foreign]
    assert target.list_target_containers(client, settings) == [good]
    client.containers.list.assert_called_with(
        all=True, filters={"label": "com.docker.compose.project=ecommerce-microservices"}
    )
    assert target.service_container(client, settings, "postgres") is good
    with pytest.raises(TargetError, match="outside"):
        target.require_scope(foreign, settings.compose_project)
    foreign.exec_run.assert_not_called()


def test_service_refuses_missing_duplicate_stopped_and_relabelled(settings: Settings) -> None:
    client = MagicMock()
    good = container("postgres")
    for entries in ([], [good, good]):
        client.containers.list.return_value = entries
        with pytest.raises(TargetError, match="Expected one"):
            target.service_container(client, settings, "postgres")
    client.containers.list.return_value = [good]
    good.status = "exited"
    with pytest.raises(TargetError, match="exited"):
        target.service_container(client, settings, "postgres")
    good.status = "running"
    good.reload.side_effect = lambda: good.labels.update({target.PROJECT_LABEL: "foreign"})
    with pytest.raises(TargetError, match="outside"):
        target.service_container(client, settings, "postgres")


def test_ports_health_and_readiness(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    service = container("auth-service")
    service.attrs = {
        "State": {"Health": {"Status": "healthy"}, "Paused": True},
        "NetworkSettings": {
            "Ports": {
                "3001/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "3004"},
                    {"HostIp": "::", "HostPort": "3004"},
                ],
                "other": None,
            }
        },
    }
    state = target.container_status(service, settings)
    assert state.ports == [("127.0.0.1", 3004), ("::1", 3004)]
    assert state.paused and state.health == "healthy"
    connection = MagicMock()
    monkeypatch.setattr(socket, "create_connection", MagicMock(return_value=connection))
    assert target.tcp_ready("localhost", 3004)
    connection.__exit__.assert_called_once()
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError()))
    assert not target.tcp_ready("localhost", 3004)
    http_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    monkeypatch.setattr(target.httpx, "Client", lambda **_: http_client)
    assert target.http_ready("http://localhost/products")


def test_http_unready(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    http_client = httpx.Client(transport=httpx.MockTransport(fail))
    monkeypatch.setattr(target.httpx, "Client", lambda **_: http_client)
    assert not target.http_ready("http://localhost/products")


def test_compose_command_is_scoped_and_bounded(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    target_dir = settings.project_root / "target"
    target_dir.mkdir()
    (target_dir / "docker-compose.yml").write_text("services: {}")
    settings.target_dir = Path("target")
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(target.subprocess, "run", run)
    target.compose_up(settings)
    assert run.call_args.args[0] == [
        "docker",
        "compose",
        "--project-name",
        settings.compose_project,
        "--file",
        str(target_dir / "docker-compose.yml"),
        "up",
        "-d",
        "--build",
    ]
    assert run.call_args.kwargs["cwd"] == target_dir
    assert run.call_args.kwargs["timeout"] == settings.build_timeout_s
    run.return_value.returncode = 1
    with pytest.raises(TargetError, match="exit 1"):
        target.compose_up(settings)


def test_compose_missing_directory_does_not_run(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    run = MagicMock()
    monkeypatch.setattr(target.subprocess, "run", run)
    with pytest.raises(TargetError, match="TARGET_DIR"):
        target.compose_up(settings)
    run.assert_not_called()


def test_paused_postgres_is_not_ready(settings: Settings) -> None:
    client = MagicMock()
    postgres = container("postgres")
    postgres.attrs["State"]["Paused"] = True
    client.containers.list.return_value = [postgres]
    with pytest.raises(TargetError, match="paused"):
        target.service_container(client, settings, "postgres")
    postgres.exec_run.assert_not_called()
