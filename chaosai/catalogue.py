"""Planning metadata only; injector availability is explicitly separate."""

from chaosai.models import FaultDefinition, Graph

CATALOGUE = (
    FaultDefinition(fault="kill", node_kinds=["app"], implemented=True),
    FaultDefinition(
        fault="stop", node_kinds=["app", "database", "cache", "broker"], implemented=True
    ),
    FaultDefinition(
        fault="pause", node_kinds=["app", "database", "cache", "broker", "tool"], implemented=True
    ),
    FaultDefinition(
        fault="cpu_limit",
        node_kinds=["app", "database"],
        min_intensity=0.1,
        max_intensity=0.5,
        default_intensity=0.5,
    ),
    FaultDefinition(
        fault="traffic_spike",
        node_kinds=[],
        target_kind="journey",
        min_intensity=2,
        max_intensity=8,
        default_intensity=2,
    ),
    FaultDefinition(fault="netem_delay", node_kinds=["app", "database"], plannable=False),
)


def definition(fault: str) -> FaultDefinition | None:
    return next((entry for entry in CATALOGUE if entry.fault == fault), None)


def applicable(fault: str, target: str, graph: Graph, journeys: set[str]) -> bool:
    entry = definition(fault)
    if entry is None or not entry.plannable:
        return False
    if entry.target_kind == "journey":
        # Journey load is bounded by policy, and only traverses the mapped application.
        flows = [f for f in graph.flows if target == "all" or f.name == target]
        nodes = {n.id: n for n in graph.nodes}
        return (
            target in journeys | {"all"}
            and bool(flows)
            and any(
                any(nodes.get(name) and nodes[name].injectable for name in f.path) for f in flows
            )
        )
    nodes = [node for node in graph.nodes if node.id == target]
    return (
        len(nodes) == 1
        and nodes[0].injectable
        and bool(nodes[0].container)
        and (nodes[0].kind != "external" and nodes[0].kind in entry.node_kinds)
    )
