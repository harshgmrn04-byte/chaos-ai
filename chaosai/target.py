"""Scoped Docker access and bounded readiness checks."""

import json
import os
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import docker
import httpx
from docker.models.containers import Container

from chaosai.config import Settings
from chaosai.models import ContainerStatus

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"


class TargetError(RuntimeError):
    """An actionable, secret-free target failure."""


def docker_endpoints() -> Iterator[tuple[str, bool]]:
    """Yield endpoints in precedence order; inspect context only when needed."""
    if host := os.getenv("DOCKER_HOST"):
        yield host, True
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            host = json.loads(result.stdout.strip())
            if isinstance(host, str) and host:
                yield host, False
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    yield f"unix://{Path.home() / '.docker/run/docker.sock'}", False
    yield "unix:///var/run/docker.sock", False


def get_docker_client() -> docker.DockerClient:
    """Return the first reachable client, including Docker Desktop's user socket."""
    tried: set[str] = set()
    for endpoint, from_env in docker_endpoints():
        if endpoint in tried:
            continue
        tried.add(endpoint)
        client: docker.DockerClient | None = None
        try:
            client = (
                docker.from_env(timeout=5)
                if from_env
                else docker.DockerClient(base_url=endpoint, timeout=5)
            )
            if client.ping():
                return client
        except (docker.errors.DockerException, OSError, ValueError):
            pass
        if client is not None:
            client.close()
    raise TargetError(
        "Docker is unreachable via DOCKER_HOST, the active context, or local sockets. "
        "Start Docker Desktop; check docker context show/inspect. If DOCKER_HOST is stale, "
        "unset it or set DOCKER_HOST=unix://$HOME/.docker/run/docker.sock."
    )


def require_scope(container: Container, project: str) -> None:
    if container.labels.get(PROJECT_LABEL) != project:
        raise TargetError("Refusing access to a container outside COMPOSE_PROJECT.")


def list_target_containers(client: docker.DockerClient, settings: Settings) -> list[Container]:
    containers = client.containers.list(
        all=True, filters={"label": f"{PROJECT_LABEL}={settings.compose_project}"}
    )
    return [c for c in containers if c.labels.get(PROJECT_LABEL) == settings.compose_project]


def service_container(client: docker.DockerClient, settings: Settings, service: str) -> Container:
    matches = [
        c
        for c in list_target_containers(client, settings)
        if c.labels.get(SERVICE_LABEL) == service
    ]
    if len(matches) != 1:
        raise TargetError(f"Expected one scoped {service} container; found {len(matches)}.")
    container = matches[0]
    container.reload()
    require_scope(container, settings.compose_project)
    if container.status != "running" or container.attrs.get("State", {}).get("Paused", False):
        raise TargetError(f"Target {service} is {container.status} or paused; run chaosai up.")
    return container


def container_status(container: Container, settings: Settings) -> ContainerStatus:
    require_scope(container, settings.compose_project)
    state = container.attrs.get("State", {})
    ports: list[tuple[str, int]] = []
    for bindings in (container.attrs.get("NetworkSettings", {}).get("Ports") or {}).values():
        for binding in bindings or []:
            host = binding["HostIp"]
            host = "127.0.0.1" if host in ("", "0.0.0.0") else "::1" if host == "::" else host
            ports.append((host, int(binding["HostPort"])))
    return ContainerStatus(
        name=container.name,
        service=container.labels.get(SERVICE_LABEL, "unknown"),
        state=container.status,
        paused=state.get("Paused", False),
        health=state.get("Health", {}).get("Status", "not configured"),
        ports=ports,
    )


def tcp_ready(host: str, port: int, timeout: float = 2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ready(url: str, timeout: float = 2) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            return client.get(url).status_code == 200
    except httpx.HTTPError:
        return False


def compose_up(settings: Settings) -> None:
    """Bring up only the explicitly named Compose project."""
    target_dir = settings.resolved_target_dir
    if not (target_dir / "docker-compose.yml").is_file():
        raise TargetError("TARGET_DIR has no docker-compose.yml; correct TARGET_DIR.")
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                settings.compose_project,
                "--file",
                str(target_dir / "docker-compose.yml"),
                "up",
                "-d",
                "--build",
            ],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=settings.build_timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetError("Compose could not finish; check Docker Desktop and Compose.") from exc
    if result.returncode:
        raise TargetError(
            f"Compose up failed (exit {result.returncode}); inspect the target build locally. "
            "Build output is withheld because it can contain credentials."
        )
