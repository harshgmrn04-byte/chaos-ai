"""Phase 1 data contracts; only LLM-facing lists have root wrappers."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

NodeKind = Literal["app", "database", "cache", "broker", "external", "tool"]
EdgeKind = Literal["http", "sql", "nosql", "cache", "amqp", "smtp", "unknown"]
Criticality = Literal["hard", "soft", "unknown"]
FaultType = Literal["kill", "stop", "pause", "cpu_limit", "traffic_spike", "netem_delay"]
Verdict = Literal["confirmed", "refuted", "aborted", "inconclusive"]
Severity = Literal["critical", "high", "medium", "low"]
LLMSource = Annotated[str, Field(pattern=r"^(gemini:\S+|heuristic|mock)$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Node(Model):
    id: str
    container: str | None
    kind: NodeKind
    image: str | None
    host_ports: list[int]
    container_ports: list[int]
    injectable: bool
    injectable_reason: str | None = None


class Edge(Model):
    source: str
    target: str
    kind: EdgeKind
    evidence: str = Field(min_length=1)
    criticality: Criticality = "unknown"


class Flow(Model):
    """Components touched, ordered from user entry point to deepest dependencies."""

    name: str
    path: list[str]


class GraphSources(Model):
    parsed: bool
    overrides: bool
    ignored_config: list[str] = Field(default_factory=list)


class Graph(Model):
    app: str
    nodes: list[Node]
    edges: list[Edge]
    flows: list[Flow]
    sources: GraphSources


class NodeOverrides(Model):
    add: list[Node] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class EdgeSelector(Model):
    source: str
    target: str
    kind: EdgeKind | None = None


class CriticalityOverride(EdgeSelector):
    criticality: Criticality


class EdgeOverrides(Model):
    add: list[Edge] = Field(default_factory=list)
    remove: list[EdgeSelector] = Field(default_factory=list)
    criticality: list[CriticalityOverride] = Field(default_factory=list)


class GraphOverrides(Model):
    nodes: NodeOverrides = Field(default_factory=NodeOverrides)
    edges: EdgeOverrides = Field(default_factory=EdgeOverrides)
    flows: list[Flow] = Field(default_factory=list)


class JourneyStats(Model):
    n: int
    error_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rps: float
    low_sample: bool = False


class Hypothesis(Model):
    journey: str
    max_error_rate: float
    max_p95_multiple: float
    statement: str


class AbortRule(Model):
    max_error_rate: float
    max_p95_multiple: float


class HypothesisThresholds(Model):
    max_error_rate: float
    p95_multiple: float
    p95_floor_ms: float = Field(default=100, ge=0)


class AbortThresholds(Model):
    max_error_rate: float
    p95_multiple: float
    p95_floor_ms: float = Field(default=750, ge=0)
    max_unhealthy_containers: int = Field(ge=0)
    min_samples: int = Field(ge=1)
    consecutive_breaches: int = Field(ge=1)


class Thresholds(Model):
    hypothesis: HypothesisThresholds
    abort: AbortThresholds


class ContainerStats(Model):
    cpu_pct: float
    mem_mb: float
    restarts: int
    status: str = "unknown"


class ContainerObservation(Model):
    t: float
    name: str
    stats: ContainerStats


class QueueObservation(Model):
    t: float
    name: str
    depth: float = Field(ge=0)


class Baseline(Model):
    created_at: datetime
    window_s: float
    stable: bool
    journeys: dict[str, JourneyStats]
    containers: dict[str, ContainerStats]
    queues: dict[str, float]
    thresholds: Thresholds


class ExperimentSpec(Model):
    id: str
    fault: FaultType
    target: str
    duration_s: int
    intensity: float | None
    hypothesis: Hypothesis
    abort: AbortRule
    reason: str
    rank: int


class ExperimentProposal(Model):
    """Flat LLM contract; semantic validation drops unsupported individual proposals."""

    fault: str
    target: str
    duration_s: int
    intensity: float | None
    journey: str
    max_error_rate: float
    max_p95_multiple: float
    statement: str
    abort_max_error_rate: float
    abort_max_p95_multiple: float
    reason: str
    rank: int


class PlannerOutput(Model):
    experiments: list[ExperimentProposal]


class SourceFact(Model):
    node_id: str
    statement: str
    evidence: list[str]


class LLMProbe(Model):
    ok: bool


class ResolvedModel(Model):
    model: str = Field(min_length=1, pattern=r"^\S+$")


class FaultDefinition(Model):
    fault: FaultType
    node_kinds: list[NodeKind]
    target_kind: Literal["node", "journey"] = "node"
    min_duration_s: int = 5
    max_duration_s: int = 120
    default_duration_s: int = 30
    min_intensity: float | None = None
    max_intensity: float | None = None
    default_intensity: float | None = None
    implemented: bool = False
    plannable: bool = True


class Clamp(Model):
    field: str
    requested: str | int | float | bool | None
    final: str | int | float | bool | None
    reason: str


class PolicyDecision(Model):
    experiment_id: str
    requested: ExperimentSpec
    final: ExperimentSpec
    clamps: list[Clamp]
    allowed: bool
    deny_reason: str | None
    source: LLMSource = "heuristic"


class OriginalContainerState(Model):
    running: bool
    paused: bool
    cpu_period: int = Field(ge=0)
    cpu_quota: int = Field(ge=-1)
    nano_cpus: int = Field(default=0, ge=0)


class UndoRecord(Model):
    id: str
    container: str
    container_id: str
    project: str
    fault: FaultType
    original: OriginalContainerState
    done: bool = False
    done_at: float | None = Field(default=None, ge=0)


class JournalData(Model):
    version: Literal[1] = 1
    records: list[UndoRecord] = Field(default_factory=list)


class WatchdogWindow(Model):
    elapsed_s: float = Field(ge=0)
    journeys: dict[str, JourneyStats]
    container_states: dict[str, str] = Field(default_factory=dict)


class WatchdogState(Model):
    target_containers: set[str] = Field(default_factory=set)
    consecutive: dict[str, int] = Field(default_factory=dict)
    abort_reason: str | None = None


class Sample(Model):
    t: float
    journey: str
    ok: bool
    status: int | None
    latency_ms: float
    error: str | None


class Event(Model):
    t: float
    phase: str
    note: str


class ExperimentResult(Model):
    spec: ExperimentSpec
    decision: PolicyDecision
    started_at: datetime
    ended_at: datetime
    events: list[Event]
    during: dict[str, JourneyStats]
    after: dict[str, JourneyStats]
    aborted: bool
    abort_reason: str | None
    verdict: Verdict
    recovered: bool
    recovery_time_s: float | None
    self_healed: bool | None
    impacted_journeys: list[str]
    container_events: list[str]
    log_excerpts: dict[str, list[str]]
    rollback_ok: bool
    samples_path: str
    skipped: bool = False
    skip_reason: str | None = None
    evidence_limits: list[str] = Field(default_factory=list)
    approval_decision: Literal["yes", "skip", "all", "quit"] = "yes"


class Finding(Model):
    experiment_id: str
    summary: str
    root_cause: str
    severity: Severity
    evidence: list[str]
    fixes: list[str]


class Score(Model):
    experiment_id: str
    score: int = Field(ge=0, le=100)
    error_pen: float
    latency_pen: float
    recovery_pen: float
    blast_pen: float
    abort_pen: float


class Weakness(Model):
    title: str
    severity: Severity
    experiments: list[str]
    fix: str


class Summary(Model):
    overall_score: float
    band: str
    ranked_weaknesses: list[Weakness]
    top_fixes: list[str]
    narrative: str


class Approval(Model):
    experiment_id: str
    decision: Literal["yes", "skip", "all", "quit"]


class RunMeta(Model):
    run_id: str
    started_at: datetime
    planner_source: LLMSource
    analyzer_source: LLMSource
    target_project: str
    approvals: list[Approval]
    offline: bool = True


class Plan(RootModel[list[ExperimentSpec]]):
    pass


class Findings(RootModel[list[Finding]]):
    pass


# Phase 2 target contracts. API models ignore additional documented upstream fields.
class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class TargetUser(APIModel):
    id: str
    name: str
    email: str
    role: Literal["USER", "ADMIN"]


class AuthResponse(APIModel):
    user: TargetUser
    accessToken: str = Field(repr=False, min_length=1)
    refreshToken: str = Field(repr=False, min_length=1)


class Category(APIModel):
    id: str = Field(min_length=1)
    name: str
    slug: str


class Product(APIModel):
    id: str = Field(min_length=1)
    name: str
    slug: str
    description: str
    price: float = Field(ge=0)
    stock: int = Field(ge=0)
    categoryId: str


class Pagination(APIModel):
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total: int = Field(ge=0)
    totalPages: int = Field(ge=0)


class ProductPage(APIModel):
    products: list[Product]
    pagination: Pagination


class OrderItem(APIModel):
    productId: str
    name: str
    price: float
    quantity: int


class TargetOrder(APIModel):
    id: str = Field(min_length=1)
    userId: str
    status: str
    subtotal: float
    shipping: float
    total: float
    items: list[OrderItem]


class OrderResponse(APIModel):
    order: TargetOrder


class QueueSnapshot(APIModel):
    name: str
    vhost: str
    consumers: int = Field(ge=0, strict=True)
    messages_ready: int = Field(ge=0, strict=True)
    messages: int = Field(ge=0, strict=True)


class ContainerStatus(Model):
    name: str
    service: str
    state: str
    paused: bool
    health: str
    ports: list[tuple[str, int]]


class CheckResult(Model):
    name: str
    passed: bool
    detail: str
    hint: str = ""


class SeedResult(Model):
    users_created: int = 0
    users_existing: int = 0
    categories_created: int = 0
    products_created: int = 0
    verified_users: int = 0
    verified_admin: bool = False
    verified_products: int = 0
    product_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class SmokeResult(Model):
    order_id: str
    warnings: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class DatabaseUser(Model):
    id: str
    email: str
    role: Literal["USER", "ADMIN"]


class CredentialCheck(Model):
    exists: bool = Field(strict=True)
    password_matches: bool = Field(strict=True)


class ProductCounts(Model):
    total: int = Field(ge=0)
    seed: int = Field(ge=0)
    high_stock_seed: int = Field(ge=0)
