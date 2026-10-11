"""Curated source observations; never a claim about measured runtime behavior."""

from chaosai.models import ExperimentSpec, Graph, SourceFact

SOURCE_REVISION = "bae94402773bc7fd7d05dadd4062dffe2d1c2057"
SOURCE_APP = "ecommerce-microservices"
SOURCE_FACTS = (
    SourceFact(
        node_id="redis",
        statement=(
            "Product requests await Redis reads/writes. A cache miss can reach the database, "
            "but an unavailable Redis has no application fallback or command deadline. "
            "Failure timing, reconnection and application recovery remain unmeasured."
        ),
        evidence=[
            "docs/target-notes.md#4-redis-usage-and-unavailability",
            "ecommerce-microservices/product-service/src/utils/cache.ts:5-25",
            "ecommerce-microservices/product-service/src/controllers/productController.ts:50-84",
        ],
    ),
    SourceFact(
        node_id="rabbitmq",
        statement=(
            "Order creation is persisted before order.created publication. A final publication "
            "exception can leave a stored order despite an HTTP error. The publisher has no "
            "application reconnect handler or outbox; actual post-disconnection behavior is "
            "unmeasured."
        ),
        evidence=[
            "docs/target-notes.md#3-order-creation-deep-dive",
            "ecommerce-microservices/order-service/src/controllers/orderController.ts:25-83",
            "ecommerce-microservices/order-service/src/utils/rabbitmq.ts:4-29",
        ],
    ),
    SourceFact(
        node_id="postgres",
        statement=(
            "One PostgreSQL container hosts separate auth, product and order databases. "
            "A container fault tests their shared dependency; measured blast radius is unknown."
        ),
        evidence=[
            "docs/target-notes.md#1-services-and-deployment",
            "ecommerce-microservices/docker-compose.yml:3-18",
            "ecommerce-microservices/docker-compose.yml:80",
            "ecommerce-microservices/docker-compose.yml:98",
            "ecommerce-microservices/docker-compose.yml:122",
        ],
    ),
)


def source_facts(graph: Graph) -> list[SourceFact]:
    """Use this checkout's observations only for its named app and present nodes."""
    if graph.app != SOURCE_APP:
        return []
    nodes = {node.id for node in graph.nodes}
    return [fact.model_copy(deep=True) for fact in SOURCE_FACTS if fact.node_id in nodes]


def grounded_reason(spec: ExperimentSpec, graph: Graph) -> tuple[str, str] | None:
    """Normalize known rationales; retain original AI text in the decision trace."""
    if spec.fault == "traffic_spike":
        return (
            f"Test whether {spec.target} stays within baseline-derived limits under bounded "
            "extra load; overload capacity is unverified.",
            "Baseline latency/error statistics do not establish overload capacity.",
        )
    fact = next((fact for fact in source_facts(graph) if fact.node_id == spec.target), None)
    if fact is None:
        return None
    observations = {
        "redis": "Source cache calls have no unavailable-Redis fallback",
        "rabbitmq": "Source order creation persists before broker publication",
        "postgres": "Source PostgreSQL container serves auth, product and order databases",
    }
    return (
        f"{observations[spec.target]}; test {spec.fault} impact and recovery without assuming "
        "a measured outcome.",
        "Grounded in inspected source, not live verification: " + "; ".join(fact.evidence),
    )
