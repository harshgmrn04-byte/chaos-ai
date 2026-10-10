"""Read-only checks with actionable fix hints."""

import sys

import docker

from chaosai.config import Settings
from chaosai.models import CheckResult, ContainerStatus
from chaosai.target import (
    TargetError,
    container_status,
    get_docker_client,
    http_ready,
    list_target_containers,
    tcp_ready,
)


def target_status(settings: Settings) -> list[ContainerStatus]:
    client = get_docker_client()
    try:
        return [container_status(c, settings) for c in list_target_containers(client, settings)]
    finally:
        client.close()


def status_checks(states: list[ContainerStatus], settings: Settings) -> list[CheckResult]:
    services = {s.service for s in states}
    missing = sorted(set(settings.expected_services) - services)
    stopped = [s.service for s in states if s.state != "running" or s.paused]
    unhealthy = [s.service for s in states if s.health not in ("healthy", "not configured")]
    missing_ports = sorted(
        set(settings.published_services) - {s.service for s in states if s.ports}
    )
    checks = [
        CheckResult(
            name="Target containers found",
            passed=not missing,
            detail=f"Found {len(states)}; missing: {', '.join(missing) or 'none'}",
            hint="Run chaosai up; verify COMPOSE_PROJECT matches the target labels.",
        ),
        CheckResult(
            name="Target containers running",
            passed=bool(states) and not stopped,
            detail=", ".join(stopped) or "All discovered containers running and unpaused",
            hint="Run chaosai up; inspect target startup failures.",
        ),
        CheckResult(
            name="Container health",
            passed=bool(states) and not unhealthy,
            detail=", ".join(unhealthy) or "Declared healthchecks healthy",
            hint="Wait for startup and inspect the target service logs.",
        ),
        CheckResult(
            name="Published host ports",
            passed=not missing_ports,
            detail=", ".join(missing_ports) or "All expected services publish host ports",
            hint="Check target Compose port mappings and COMPOSE_PROJECT.",
        ),
    ]
    for state in states:
        for host, port in state.ports:
            checks.append(
                CheckResult(
                    name=f"TCP {state.service} {host}:{port}",
                    passed=tcp_ready(host, port),
                    detail="Host port probe",
                    hint="Check published ports, startup readiness and port conflicts.",
                )
            )
    return checks


def doctor_checks(settings: Settings) -> list[CheckResult]:
    checks = [
        CheckResult(
            name="Python >= 3.11",
            passed=sys.version_info >= (3, 11),
            detail=sys.version.split()[0],
            hint="Activate .venv with Python >=3.11.",
        ),
        CheckResult(
            name="LLM key present",
            passed=settings.llm_key_present,
            detail="Presence only; no LLM call",
            hint="Set GEMINI_API_KEY or GOOGLE_API_KEY.",
        ),
        CheckResult(
            name=".env present",
            passed=(settings.project_root / ".env").is_file(),
            detail="Existence only",
            hint="Create chaos-ai/.env from .env.example locally.",
        ),
    ]
    try:
        states = target_status(settings)
    except (TargetError, docker.errors.DockerException) as exc:
        detail = (
            str(exc) if isinstance(exc, TargetError) else "Cannot list scoped target containers."
        )
        checks.append(
            CheckResult(
                name="Docker reachable",
                passed=False,
                detail=detail,
                hint="Start Docker Desktop and correct the active context/socket.",
            )
        )
        checks.extend(status_checks([], settings))
        return checks
    checks.append(CheckResult(name="Docker reachable", passed=True, detail="Docker ping succeeded"))
    checks.extend(status_checks(states, settings))
    # No speculative /health endpoint: only documented public GET routes are probed.
    for service, url in (
        ("products", settings.product_url + "/products?limit=1"),
        ("categories", settings.product_url + "/categories"),
    ):
        checks.append(
            CheckResult(
                name=f"HTTP {service}",
                passed=http_ready(url),
                detail="Documented GET endpoint returns HTTP 200",
                hint="Wait for product-service and its DB/cache dependencies.",
            )
        )
    return checks
