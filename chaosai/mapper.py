"""Deterministic Compose references; never read dotenv files or contact the target."""

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.models import (
    Edge,
    EdgeKind,
    EdgeSelector,
    Graph,
    GraphOverrides,
    GraphSources,
    Node,
    NodeKind,
)
from chaosai.runs import write_json

logger = logging.getLogger(__name__)
SCHEME_KINDS: dict[str, EdgeKind] = {
    "postgresql": "sql",
    "postgres": "sql",
    "mongodb": "nosql",
    "mongodb+srv": "nosql",
    "redis": "cache",
    "rediss": "cache",
    "amqp": "amqp",
    "amqps": "amqp",
    "http": "http",
    "https": "http",
    "smtp": "smtp",
    "smtps": "smtp",
}


class MapperError(ValueError):
    """A safe mapper diagnostic without source environment values."""


def read_yaml(path: Path) -> dict[str, Any]:
    # env_file is metadata only. Do not let alternate input paths bypass the .env rule.
    name = path.resolve().name
    if name == ".env" or name.startswith(".env."):
        raise MapperError("Dotenv files cannot be used as mapper input.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MapperError("Cannot read mapper YAML; check file existence and syntax.") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise MapperError("Mapper YAML must be a mapping.")
    return data


def environment_entries(value: Any) -> dict[str, str | None]:
    """Normalize list/dict syntax without resolving interpolation or reading process secrets."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): None if val is None else str(val) for key, val in value.items()}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        result: dict[str, str | None] = {}
        for item in value:
            key, separator, val = item.partition("=")
            result[key] = val if separator else None
        return result
    raise MapperError("Environment/build args must use list or mapping syntax.")


def env_file_entries(value: Any) -> list[str]:
    """Parse Compose env_file declarations only; their contents are intentionally private."""
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    paths: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            entry = entry.get("path")
        if not isinstance(entry, str) or not entry:
            raise MapperError("env_file must contain paths or mappings with a path.")
        paths.append(entry)
    return paths


def node_kind(image: str | None) -> NodeKind:
    name = (image or "").rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0].lower()
    return {
        "postgres": "database",
        "mongo": "database",
        "mongodb": "database",
        "redis": "cache",
        "rabbitmq": "broker",
        "mailhog": "tool",
    }.get(name, "app")


def port_numbers(value: Any) -> list[int]:
    text = str(value)
    if "$" in text:
        return []
    if not re.fullmatch(r"\d+(?:-\d+)?", text):
        raise MapperError("Ports must be numeric or numeric ranges.")
    parts = [int(part) for part in text.split("-")]
    start, end = parts[0], parts[-1]
    if not 1 <= start <= end <= 65535:
        raise MapperError("Port out of range.")
    return list(range(start, end + 1))


def service_ports(entries: Any) -> tuple[list[int], list[int]]:
    if not isinstance(entries, list):
        raise MapperError("Service ports must be a list.")
    host: list[int] = []
    container: list[int] = []
    for entry in entries:
        if isinstance(entry, dict):
            target = entry.get("target")
            published = entry.get("published")
        else:
            parts = str(entry).split("/", 1)[0].rsplit(":", 2)
            target = parts[-1]
            published = parts[-2] if len(parts) > 1 else None
        container.extend(port_numbers(target))
        if published is not None:
            host.extend(port_numbers(published))
    return sorted(set(host)), sorted(set(container))


def references(
    value: str, aliases: dict[str, set[str]], host_ports: dict[int, set[str]]
) -> list[tuple[str, EdgeKind, str]]:
    """Match URL authority or plain host tokens, never URL credentials/path/query text."""
    if "$" in value:
        return []
    kind: EdgeKind = "unknown"
    if "://" in value:
        try:
            url = urlsplit(value)
            kind = SCHEME_KINDS.get(url.scheme.lower(), "unknown")
            hosts = url.netloc.rsplit("@", 1)[-1].split(",")
        except ValueError:
            return []
    else:
        hosts = re.split(r"[,;\s]+", value)
    found: list[tuple[str, EdgeKind, str]] = []
    for host in hosts:
        try:
            authority = urlsplit(f"//{host}")
            name, port = authority.hostname, authority.port
        except ValueError:
            continue
        candidates = aliases.get(name or "", set())
        resolution = "service/container reference"
        if name == "localhost" and port is not None:
            candidates = host_ports.get(port, set())
            resolution = f"localhost host port {port}"
        if len(candidates) == 1:
            found.append((next(iter(candidates)), kind, resolution))
        elif len(candidates) > 1:
            logger.warning("Ambiguous Compose reference omitted; use overrides to resolve it.")
    return found


def merge_evidence(edges: list[Edge]) -> list[Edge]:
    """Combine references; typed env evidence refines unknown startup edges."""
    grouped: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for edge in edges:
        grouped[edge.source, edge.target].append(edge)
    result: list[Edge] = []
    for (source, target), group in sorted(grouped.items()):
        kinds = {edge.kind for edge in group} - {"unknown"}
        for kind in sorted(kinds or {"unknown"}):
            evidence = sorted({e.evidence for e in group if e.kind in (kind, "unknown")})
            result.append(
                Edge(source=source, target=target, kind=kind, evidence="; ".join(evidence))
            )
    return result


def parse_compose(data: dict[str, Any], *, app: str) -> Graph:
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        raise MapperError("Compose must declare a nonempty services mapping.")
    nodes: dict[str, Node] = {}
    aliases: dict[str, set[str]] = defaultdict(set)
    host_ports: dict[int, set[str]] = defaultdict(set)
    for service, spec in services.items():
        if not isinstance(service, str) or not isinstance(spec, dict):
            raise MapperError("Compose services must have names and mapping definitions.")
        host, container = service_ports(spec.get("ports", []))
        node = Node(
            id=service,
            container=spec.get("container_name"),
            image=spec.get("image"),
            kind=node_kind(spec.get("image")),
            host_ports=host,
            container_ports=container,
            injectable=True,
        )
        nodes[service] = node
        for name in (service, node.container):
            if name:
                aliases[name].add(service)
        for port in host:
            host_ports[port].add(service)
    edges: list[Edge] = []
    for service, spec in services.items():
        dependencies = spec.get("depends_on", [])
        if not isinstance(dependencies, (dict, list)):
            raise MapperError("depends_on must use list or mapping syntax.")
        for dependency in dependencies:
            if dependency not in services:
                raise MapperError("depends_on references an undeclared service.")
            condition = dependencies[dependency] if isinstance(dependencies, dict) else None
            suffix = ""
            if isinstance(condition, dict) and condition.get("condition") in (
                "service_started",
                "service_healthy",
                "service_completed_successfully",
            ):
                suffix = f" ({condition['condition']})"
            edges.append(
                Edge(
                    source=service,
                    target=dependency,
                    kind="unknown",
                    evidence=f"compose.services.{service}.depends_on.{dependency}{suffix}",
                )
            )
        if env_file_entries(spec.get("env_file")):
            logger.warning("env_file declared: contents not read; add dependencies via overrides.")
        build = spec.get("build")
        args = build.get("args") if isinstance(build, dict) else None
        for section, entries in (
            ("environment", environment_entries(spec.get("environment"))),
            ("build.args", environment_entries(args)),
        ):
            for key, value in sorted(entries.items()):
                evidence = f"compose.services.{service}.{section}.{key}"
                sensitive = bool(re.search(r"(?:^|_)(?:KEY|SECRET|PASSWORD|TOKEN)(?:_|$)", key))
                provider_kind: EdgeKind = "unknown"
                if value and not sensitive and "$" not in value:
                    try:
                        provider_kind = SCHEME_KINDS.get(urlsplit(value).scheme, "unknown")
                    except ValueError:
                        pass
                for provider in ("stripe", "cloudinary", "resend"):
                    if provider.upper() in key.upper().split("_"):
                        if provider in services:
                            raise MapperError(
                                "External provider ID conflicts with a Compose service."
                            )
                        nodes[provider] = Node(
                            id=provider,
                            container=None,
                            kind="external",
                            image=None,
                            host_ports=[],
                            container_ports=[],
                            injectable=False,
                        )
                        edges.append(
                            Edge(
                                source=service,
                                target=provider,
                                kind=provider_kind,
                                evidence=(
                                    f"{evidence} (external provider name; activation unverified)"
                                ),
                            )
                        )
                if value is None or sensitive:
                    continue
                for target, kind, resolution in references(value, aliases, host_ports):
                    if target != service:
                        edges.append(
                            Edge(
                                source=service,
                                target=target,
                                kind=kind,
                                evidence=f"{evidence} -> {target} ({resolution})",
                            )
                        )
    return Graph(
        app=app,
        nodes=sorted(nodes.values(), key=lambda node: node.id),
        edges=merge_evidence(edges),
        flows=[],
        sources=GraphSources(parsed=True, overrides=False),
    )


def matches(edge: Edge, selector: EdgeSelector) -> bool:
    return (
        edge.source == selector.source
        and edge.target == selector.target
        and (selector.kind is None or edge.kind == selector.kind)
    )


def validate_graph(graph: Graph) -> Graph:
    ids = {node.id for node in graph.nodes}
    if len(ids) != len(graph.nodes) or any(not node.id for node in graph.nodes):
        raise MapperError("Graph node IDs must be unique and nonempty.")
    for node in graph.nodes:
        if node.id in ("stripe", "cloudinary", "resend") and node.kind != "external":
            raise MapperError("Stripe, Cloudinary and Resend must remain external nodes.")
        if node.kind == "external" and (node.injectable or node.container is not None):
            raise MapperError("External nodes must be non-injectable and have no container.")
        if any(not 1 <= port <= 65535 for port in node.host_ports + node.container_ports):
            raise MapperError("Graph port out of range.")
    for edge in graph.edges:
        if edge.source not in ids or edge.target not in ids or not edge.evidence.strip():
            raise MapperError("Graph edges require existing endpoints and evidence.")
    if len({flow.name for flow in graph.flows}) != len(graph.flows):
        raise MapperError("Flow names must be unique.")
    for flow in graph.flows:
        if not flow.name or not flow.path or any(node not in ids for node in flow.path):
            raise MapperError("Flows require a name and a nonempty path of existing nodes.")
    return graph


def apply_overrides(graph: Graph, overrides: GraphOverrides) -> Graph:
    """Remove nodes/incident edges, upsert nodes/edges, then criticality and named flows."""
    result = graph.model_copy(deep=True)
    nodes = {node.id: node for node in result.nodes}
    for node_id in overrides.nodes.remove:
        if node_id not in nodes:
            raise MapperError("Node removal references an unknown ID.")
        del nodes[node_id]
    result.edges = [edge for edge in result.edges if edge.source in nodes and edge.target in nodes]
    for node in overrides.nodes.add:
        nodes[node.id] = node.model_copy(deep=True)
    result.nodes = sorted(nodes.values(), key=lambda node: node.id)
    for selector in overrides.edges.remove:
        if not any(matches(edge, selector) for edge in result.edges):
            raise MapperError("Edge removal matches no edge.")
        result.edges = [edge for edge in result.edges if not matches(edge, selector)]
    edges = {(edge.source, edge.target, edge.kind): edge for edge in result.edges}
    for edge in overrides.edges.add:
        edges[edge.source, edge.target, edge.kind] = edge.model_copy(deep=True)
    result.edges = sorted(edges.values(), key=lambda edge: (edge.source, edge.target, edge.kind))
    for selector in overrides.edges.criticality:
        selected = [edge for edge in result.edges if matches(edge, selector)]
        if not selected:
            raise MapperError("Criticality override matches no edge.")
        for edge in selected:
            edge.criticality = selector.criticality
    flows = {flow.name: flow for flow in result.flows}
    flows.update({flow.name: flow.model_copy(deep=True) for flow in overrides.flows})
    result.flows = sorted(flows.values(), key=lambda flow: flow.name)
    result.sources.overrides = True
    return validate_graph(result)


def map_target(settings: Settings, *, offline: bool = False) -> Graph:
    if offline:
        graph = fixture("graph", Graph)
    else:
        graph = parse_compose(
            read_yaml(settings.resolved_target_dir / settings.compose_file),
            app=settings.compose_project,
        )
    if settings.resolved_overrides_file.exists():
        overrides = GraphOverrides.model_validate(read_yaml(settings.resolved_overrides_file))
        graph = apply_overrides(graph, overrides)
    return validate_graph(graph)


def save_graph(graph: Graph, settings: Settings, run_dir: Path) -> None:
    for path in (run_dir / "graph.json", settings.project_root / ".chaosai" / "graph.json"):
        write_json(path, graph)


def mermaid(graph: Graph) -> str:
    """Use generated identifiers and escaped labels so node IDs remain diagram data."""
    labels = {node.id: f"n{index}" for index, node in enumerate(graph.nodes)}

    def escape(value: str) -> str:
        return "".join(
            char if char.isalnum() or char in " -_" else f"#{ord(char)};" for char in value
        )

    lines = ["flowchart LR"]
    for node in graph.nodes:
        label = escape(f"{node.id} ({node.kind})")
        lines.append(f'  {labels[node.id]}["{label}"]')
    for edge in graph.edges:
        lines.append(
            f'  {labels[edge.source]} -->|"{edge.kind} / {edge.criticality}"| {labels[edge.target]}'
        )
    return "\n".join(lines) + "\n"
