"""Bounded, scope-checked evidence without raw secret-bearing diagnostics."""

import os
import re

import docker

from chaosai.config import Settings
from chaosai.models import Baseline, ContainerObservation, Graph
from chaosai.target import require_scope

_SECRET_LINE = re.compile(
    r"password|passwd|secret|token|authorization|api[_ -]?key|cookie|bearer|"
    r"://[^\s/]+:[^\s/]+@|\beyJ[A-Za-z0-9_-]+\.",
    re.IGNORECASE,
)


def redact_line(line: str, settings: Settings) -> str:
    """Suppress credential-bearing lines before truncation, including known values."""
    secrets = [
        settings.seed_password.get_secret_value(),
        settings.rabbitmq_password.get_secret_value(),
    ]
    if settings.admin_password:
        secrets.append(settings.admin_password.get_secret_value())
    secrets.extend(value for key, value in os.environ.items() if value and _SECRET_LINE.search(key))
    if _SECRET_LINE.search(line) or any(secret and secret in line for secret in secrets):
        return "[redacted secret-bearing log line]"[: settings.experiment_log_line_chars]
    # Strip terminal escape/control sequences from untrusted logs.
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    line = "".join(char for char in line if char.isprintable())
    return line[: settings.experiment_log_line_chars]


def collect_logs(
    client: docker.DockerClient,
    graph: Graph,
    target: str,
    settings: Settings,
    since: float,
) -> dict[str, list[str]]:
    names = {target} | {edge.source for edge in graph.edges if edge.target == target}
    excerpts: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.id not in names or node.kind == "external" or not node.container:
            continue
        try:
            container = client.containers.get(node.container)
            container.reload()
            require_scope(container, settings.compose_project)
            lines = (
                container.logs(tail=40, since=int(since))
                .decode("utf-8", errors="replace")
                .splitlines()[-40:]
            )
            excerpts[node.container] = [redact_line(line, settings) for line in lines]
        except Exception:
            # SDK exceptions may contain credentials/log text; never persist their text.
            excerpts[node.container] = ["Scoped log evidence unavailable."]
    return excerpts


def container_events(
    observations: list[ContainerObservation],
    baseline: Baseline,
) -> list[str]:
    previous = {name: (stats.status, stats.restarts) for name, stats in baseline.containers.items()}
    events: list[str] = []
    for item in sorted(observations, key=lambda observation: observation.t):
        current = (item.stats.status, item.stats.restarts)
        if previous.get(item.name) != current:
            old = previous.get(item.name)
            events.append(f"{item.t:.3f} {item.name}: {old} -> {current}")
            previous[item.name] = current
    return events
