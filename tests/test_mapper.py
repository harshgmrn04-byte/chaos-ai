"""Source-only mapping from a checked-in COPY, with no target/Docker/network access."""

from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chaosai import cli, mapper
from chaosai.config import PROJECT_ROOT, Settings
from chaosai.models import Edge, Graph, GraphOverrides
from chaosai.runs import read_json

COMPOSE = Path(__file__).parent / "fixtures" / "docker-compose.yml"


@pytest.fixture
def graph() -> Graph:
    return mapper.parse_compose(mapper.read_yaml(COMPOSE), app="ecommerce-microservices")


def test_target_nodes_ports_kinds_and_externals(graph: Graph) -> None:
    nodes = {node.id: node for node in graph.nodes}
    assert len(nodes) == 13
    expected = {
        "postgres": ("database", "ecommerce-postgres", [5433], [5432]),
        "mongodb": ("database", "ecommerce-mongodb", [27017], [27017]),
        "redis": ("cache", "ecommerce-redis", [6380], [6379]),
        "rabbitmq": ("broker", "ecommerce-rabbitmq", [5672, 15672], [5672, 15672]),
        "mailhog": ("tool", "ecommerce-mailhog", [1025, 8025], [1025, 8025]),
        "auth-service": ("app", "ecommerce-auth", [3004], [3001]),
        "product-service": ("app", "ecommerce-products", [3002], [3002]),
        "order-service": ("app", "ecommerce-orders", [3003], [3003]),
        "notification-service": ("app", "ecommerce-notifications", [], []),
        "frontend": ("app", "ecommerce-frontend", [3000], [3000]),
    }
    for name, values in expected.items():
        node = nodes[name]
        assert (node.kind, node.container, node.host_ports, node.container_ports) == values
        assert node.injectable == (name != "mailhog")
    for provider in ("stripe", "cloudinary", "resend"):
        node = nodes[provider]
        assert node.kind == "external" and not node.injectable
        assert node.container is node.image is None
        assert node.host_ports == node.container_ports == []


def test_target_exact_edges_evidence_and_no_mailhog(graph: Graph) -> None:
    expected = {
        ("auth-service", "postgres", "sql"),
        ("product-service", "postgres", "sql"),
        ("product-service", "mongodb", "nosql"),
        ("product-service", "redis", "cache"),
        ("order-service", "postgres", "sql"),
        ("order-service", "rabbitmq", "amqp"),
        ("notification-service", "rabbitmq", "amqp"),
        ("notification-service", "mongodb", "nosql"),
        ("product-service", "cloudinary", "unknown"),
        ("order-service", "stripe", "unknown"),
        ("notification-service", "resend", "unknown"),
        ("frontend", "auth-service", "http"),
        ("frontend", "product-service", "http"),
        ("frontend", "order-service", "http"),
    }
    assert {(e.source, e.target, e.kind) for e in graph.edges} == expected
    for edge in graph.edges:
        assert edge.evidence.startswith(f"compose.services.{edge.source}.")
        assert edge.criticality == "unknown"
    db = next(e for e in graph.edges if e.source == "auth-service" and e.target == "postgres")
    assert "depends_on.postgres (service_healthy)" in db.evidence
    assert "environment.DATABASE_URL" in db.evidence
    frontend = next(e for e in graph.edges if e.source == "frontend" and e.target == "auth-service")
    assert "depends_on.auth-service" in frontend.evidence
    assert "build.args.NEXT_PUBLIC_API_URL" in frontend.evidence
    assert "localhost host port 3004" in frontend.evidence
    assert not any("mailhog" in (e.source, e.target) for e in graph.edges)
    assert not any(
        e.source == "order-service"
        and e.target in ("product-service", "auth-service", "notification-service")
        for e in graph.edges
    )
    # Credentials, URLs and interpolation values are not evidence/artifact fields.
    payload = graph.model_dump_json()
    for private in (
        "ecommerce123",
        "super-secret",
        "sk_test_placeholder",
        "your-key",
        "re_placeholder",
    ):
        assert private not in payload


def test_list_environment_container_name_and_host_ports() -> None:
    graph = mapper.parse_compose(
        {
            "services": {
                "api": {
                    "environment": [
                        "CACHE=redis://cache-box:6379",
                        "AUTH=http://localhost:3004/login",
                        "DB=postgresql://person:private@db:5432/data",
                        "BARE=cache",
                        "PASSTHROUGH",
                        "NOT_A_HOST=https://remote.invalid/cache?db=auth",
                        "UNRESOLVED=${URL}",
                        "SECRET_TOKEN=http://auth:3001",
                    ],
                    "depends_on": ["db"],
                },
                "cache": {"container_name": "cache-box", "image": "redis:7"},
                "auth": {"ports": ["3004:3001"]},
                "db": {"image": "postgres:16"},
            }
        },
        app="test",
    )
    assert {(e.target, e.kind) for e in graph.edges} == {
        ("cache", "cache"),
        ("auth", "http"),
        ("db", "sql"),
    }
    assert "private" not in graph.model_dump_json()
    assert "PASSTHROUGH" not in graph.model_dump_json()
    assert "SECRET_TOKEN" not in graph.model_dump_json()


def test_port_forms_ranges_and_unpublished_ports() -> None:
    assert mapper.service_ports(
        [
            "127.0.0.1:8080:80/tcp",
            "[::1]:8081:81",
            "8082-8083:82-83/udp",
            84,
            {"target": 85, "published": "8085", "protocol": "tcp"},
            {"target": 86},
        ]
    ) == ([8080, 8081, 8082, 8083, 8085], [80, 81, 82, 83, 84, 85, 86])
    assert mapper.service_ports(["${PORT}:80"]) == ([], [80])


def test_only_published_host_port_resolves_and_ambiguity_is_not_guessed(caplog) -> None:
    graph = mapper.parse_compose(
        {
            "services": {
                "api": {
                    "environment": {
                        "RIGHT": "http://localhost:3004",
                        "WRONG": "http://localhost:3001",
                        "AMBIGUOUS": "http://localhost:9000",
                        "NO_SUBSTRING": "not-auth",
                    }
                },
                "auth": {"ports": ["3004:3001"]},
                "one": {"ports": ["9000:80"]},
                "two": {"ports": ["9000:81"]},
            }
        },
        app="test",
    )
    assert len(graph.edges) == 1 and graph.edges[0].target == "auth"
    assert "Ambiguous" in caplog.text


@pytest.mark.parametrize(
    "value,expected",
    [
        (".env", [".env"]),
        ([".env", "extra.env"], [".env", "extra.env"]),
        ([{"path": ".env", "required": False, "format": "raw"}], [".env"]),
    ],
)
def test_env_file_declarations_never_open_files(
    value: object, expected: list[str], monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("No files may be read by the Compose parser")

    monkeypatch.setattr(Path, "read_text", denied)
    assert mapper.env_file_entries(value) == expected
    graph = mapper.parse_compose({"services": {"api": {"env_file": value}}}, app="test")
    assert len(graph.nodes) == 1 and graph.edges == []
    assert "contents not read" in caplog.text


def test_explicit_mailhog_reference_is_mapped() -> None:
    graph = mapper.parse_compose(
        {
            "services": {
                "api": {"environment": {"SMTP_URL": "smtp://mailhog:1025"}},
                "mailhog": {"image": "mailhog/mailhog"},
            }
        },
        app="test",
    )
    assert graph.edges[0].target == "mailhog" and graph.edges[0].kind == "smtp"
    assert next(node for node in graph.nodes if node.id == "mailhog").injectable


def test_override_merge_add_remove_criticality_flow_and_no_mutation(graph: Graph) -> None:
    before = graph.model_dump_json()
    overrides = GraphOverrides.model_validate(
        {
            "nodes": {
                "remove": ["mailhog"],
                "add": [
                    {
                        "id": "extra",
                        "container": None,
                        "kind": "tool",
                        "image": None,
                        "host_ports": [],
                        "container_ports": [],
                        "injectable": False,
                    }
                ],
            },
            "edges": {
                "remove": [{"source": "frontend", "target": "auth-service"}],
                "add": [
                    {
                        "source": "frontend",
                        "target": "extra",
                        "kind": "http",
                        "evidence": "manual: source inspected",
                    }
                ],
                "criticality": [
                    {
                        "source": "product-service",
                        "target": "redis",
                        "kind": "cache",
                        "criticality": "hard",
                    }
                ],
            },
            "flows": [{"name": "custom", "path": ["frontend", "extra"]}],
        }
    )
    result = mapper.apply_overrides(graph, overrides)
    assert graph.model_dump_json() == before
    assert "mailhog" not in {n.id for n in result.nodes}
    assert "extra" in {n.id for n in result.nodes}
    assert not any(e.source == "frontend" and e.target == "auth-service" for e in result.edges)
    assert next(e for e in result.edges if e.target == "redis").criticality == "hard"
    assert result.flows[0].path == ["frontend", "extra"]
    assert result.sources.parsed and result.sources.overrides
    assert next(e for e in result.edges if e.target == "extra").criticality == "unknown"


def test_node_removal_removes_incident_edges_and_flow_requires_correction(graph: Graph) -> None:
    result = mapper.apply_overrides(
        graph, GraphOverrides.model_validate({"nodes": {"remove": ["redis"]}})
    )
    assert not any(e.target == "redis" or e.source == "redis" for e in result.edges)
    graph.flows = mapper.apply_overrides(
        graph,
        GraphOverrides.model_validate(
            {"flows": [{"name": "browse", "path": ["product-service", "redis"]}]}
        ),
    ).flows
    with pytest.raises(mapper.MapperError, match="Flows"):
        mapper.apply_overrides(
            graph, GraphOverrides.model_validate({"nodes": {"remove": ["redis"]}})
        )


@pytest.mark.parametrize(
    "data",
    [
        {
            "edges": {
                "add": [
                    {
                        "source": "frontend",
                        "target": "missing",
                        "kind": "http",
                        "evidence": "manual",
                    }
                ]
            }
        },
        {
            "edges": {
                "criticality": [{"source": "mailhog", "target": "frontend", "criticality": "hard"}]
            }
        },
        {"edges": {"remove": [{"source": "mailhog", "target": "frontend"}]}},
        {"nodes": {"remove": ["missing"]}},
        {"flows": [{"name": "bad", "path": ["missing"]}]},
    ],
)
def test_invalid_overrides_fail(graph: Graph, data: dict) -> None:
    with pytest.raises(mapper.MapperError):
        mapper.apply_overrides(graph, GraphOverrides.model_validate(data))


@pytest.mark.parametrize("kind,injectable", [("external", True), ("app", True)])
def test_overrides_cannot_make_provider_injectable(
    graph: Graph, kind: str, injectable: bool
) -> None:
    node = next(n for n in graph.nodes if n.id == "stripe").model_dump()
    node.update(kind=kind, injectable=injectable)
    with pytest.raises(mapper.MapperError):
        mapper.apply_overrides(graph, GraphOverrides.model_validate({"nodes": {"add": [node]}}))


def test_upsert_edge_and_flow(graph: Graph) -> None:
    edge = next(e for e in graph.edges if e.target == "redis").model_dump()
    edge.update(evidence="manual: reviewed cache utility", criticality="hard")
    overrides = GraphOverrides.model_validate(
        {
            "edges": {"add": [edge]},
            "flows": [{"name": "browse", "path": ["frontend", "product-service"]}],
        }
    )
    once = mapper.apply_overrides(graph, overrides)
    twice = mapper.apply_overrides(once, overrides)
    assert once == twice
    assert len(once.edges) == len(graph.edges)
    assert next(e for e in once.edges if e.target == "redis").evidence.startswith("manual:")


def test_shipped_overrides_flows(settings: Settings) -> None:
    settings.target_dir = COMPOSE.parent
    settings.overrides_file = PROJECT_ROOT / "overrides.yaml"
    graph = mapper.map_target(settings)
    assert graph.sources.parsed and graph.sources.overrides
    flows = {f.name: f.path for f in graph.flows}
    assert set(flows) == {"browse", "login", "checkout"}
    assert flows["login"] == ["frontend", "auth-service", "postgres"]
    assert flows["browse"] == ["frontend", "product-service", "redis", "postgres", "mongodb"]
    assert flows["checkout"] == [
        "frontend",
        "order-service",
        "postgres",
        "rabbitmq",
        "notification-service",
        "resend",
        "mongodb",
    ]
    assert "mailhog" not in flows["checkout"]
    assert all(e.criticality == "unknown" for e in graph.edges)


def test_mapping_is_deterministic_independent_of_service_order() -> None:
    data = mapper.read_yaml(COMPOSE)
    original = mapper.parse_compose(data, app="test")
    changed = deepcopy(data)
    changed["services"] = dict(reversed(list(changed["services"].items())))
    assert mapper.parse_compose(changed, app="test") == original


def test_offline_does_not_read_compose(settings: Settings) -> None:
    settings.target_dir = settings.project_root / "missing-target"
    graph = mapper.map_target(settings, offline=True)
    assert not graph.sources.parsed and len(graph.nodes) == 13


def test_cli_outputs_both_graphs_mermaid_and_reuses_run(cli_workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["map", "--mermaid"])
    assert result.exit_code == 0, result.output
    assert "13 nodes, 14 edges" in result.output and "EVIDENCE" in result.output
    assert "ecommerce-auth | 3004 | 3001" in result.output
    run_dir = next((cli_workspace / "runs").iterdir())
    assert read_json(run_dir / "graph.json", Graph) == read_json(
        cli_workspace / ".chaosai" / "graph.json", Graph
    )
    diagram = (cli_workspace / "docs" / "graph.mmd").read_text()
    assert diagram.startswith("flowchart LR\n") and "mailhog" in diagram
    assert "Ignored config (CORS/allowed origins)" in result.output
    graph = read_json(run_dir / "graph.json", Graph)
    assert graph.sources.ignored_config == [
        f"compose.services.{service}.environment.FRONTEND_URL"
        for service in ("auth-service", "order-service", "product-service")
    ]
    assert next(n for n in graph.nodes if n.id == "mailhog").injectable_reason
    result = runner.invoke(cli.app, ["map", "--run-dir", str(run_dir)])
    assert result.exit_code == 0 and len(list((cli_workspace / "runs").iterdir())) == 1
    result = runner.invoke(cli.app, ["map", "--run-dir", str(cli_workspace)])
    assert result.exit_code == 2 and "inside chaos-ai/runs" in result.output


def test_mermaid_arbitrary_ids_are_escaped(graph: Graph) -> None:
    graph.nodes[0].id = 'strange"]; evil["'
    graph.edges = []
    diagram = mapper.mermaid(graph)
    assert 'strange"]; evil["' not in diagram
    assert "#34;" in diagram


def test_compose_origin_config_is_ignored_and_recorded(graph: Graph) -> None:
    assert graph.sources.ignored_config == [
        f"compose.services.{service}.environment.FRONTEND_URL"
        for service in ("auth-service", "order-service", "product-service")
    ]
    assert not any(edge.target == "frontend" for edge in graph.edges)
    assert all("FRONTEND_URL" not in edge.evidence for edge in graph.edges)
    serialized = graph.model_dump(mode="json")
    assert serialized["sources"]["ignored_config"] == graph.sources.ignored_config


@pytest.mark.parametrize("section", ["environment", "build.args"])
def test_origin_patterns_are_ignored_before_any_provider_or_reference_inference(
    section: str,
) -> None:
    entries = [
        "FRONTEND_URL=http://web:3000",
        "AUTH_ORIGIN=http://db:5432",
        "ALLOWED_ORIGINS=http://web:3000,http://db:5432",
        "CORS_STRIPE_URL=http://db:5432",
        "CORS_RESEND_ORIGIN",
        "cors_allowed_hosts=http://db:5432",
        "API_URL=http://web:3000",
    ]
    spec = {"environment": entries} if section == "environment" else {"build": {"args": entries}}
    graph = mapper.parse_compose(
        {"services": {"api": spec, "web": {}, "db": {"image": "postgres"}}}, app="test"
    )
    assert graph.sources.ignored_config == sorted(
        f"compose.services.api.{section}.{entry.partition('=')[0]}" for entry in entries[:-1]
    )
    assert len(graph.nodes) == 3  # CORS_* must not create external-provider nodes either.
    assert [(e.source, e.target, e.kind) for e in graph.edges] == [("api", "web", "http")]


def test_isolated_tool_default_can_be_flipped_with_an_override(graph: Graph) -> None:
    mailhog = next(node for node in graph.nodes if node.id == "mailhog")
    assert not mailhog.injectable
    assert mailhog.injectable_reason == "Tool has no inbound dependency edges."
    changed = mailhog.model_dump()
    changed["injectable"] = True
    result = mapper.apply_overrides(
        graph, GraphOverrides.model_validate({"nodes": {"add": [changed]}})
    )
    overridden = next(node for node in result.nodes if node.id == "mailhog")
    assert overridden.injectable
    assert overridden.injectable_reason == "Set by nodes.add override."
    assert not mailhog.injectable  # applying overrides leaves the input graph untouched


def test_tool_default_changes_with_inbound_edges_but_outbound_does_not_count(graph: Graph) -> None:
    inbound = {"source": "frontend", "target": "mailhog", "kind": "smtp", "evidence": "manual"}
    result = mapper.apply_overrides(
        graph, GraphOverrides.model_validate({"edges": {"add": [inbound]}})
    )
    node = next(node for node in result.nodes if node.id == "mailhog")
    assert node.injectable and node.injectable_reason == "Tool has inbound dependency edges."
    result = mapper.apply_overrides(
        result,
        GraphOverrides.model_validate(
            {"edges": {"remove": [{"source": "frontend", "target": "mailhog"}]}}
        ),
    )
    assert not next(node for node in result.nodes if node.id == "mailhog").injectable
    outbound = dict(inbound, source="mailhog", target="frontend")
    result = mapper.apply_overrides(
        graph, GraphOverrides.model_validate({"edges": {"add": [outbound]}})
    )
    assert not next(node for node in result.nodes if node.id == "mailhog").injectable


def test_cli_and_mermaid_display_flow_touch_sets(cli_workspace: Path) -> None:
    result = CliRunner().invoke(cli.app, ["map", "--offline", "--mermaid"])
    assert result.exit_code == 0, result.output
    graph = read_json(cli_workspace / ".chaosai" / "graph.json", Graph)
    diagram = (cli_workspace / "docs" / "graph.mmd").read_text()
    for flow in graph.flows:
        label = f"Flow {flow.name} touches: {', '.join(flow.path)}"
        assert label in result.output and label in diagram
        assert f"Flow {flow.name}: " not in result.output
    assert "Flow checkout touches: frontend ->" not in result.output + diagram
    assert "f0 -->" not in diagram and "f1 -->" not in diagram and "f2 -->" not in diagram


def test_offline_snapshot_matches_mapper_nodes_edges_flows_and_ignored_config(
    settings: Settings,
) -> None:
    settings.target_dir = COMPOSE.parent
    settings.overrides_file = PROJECT_ROOT / "overrides.yaml"
    parsed = mapper.map_target(settings)
    # Use a separate workspace without overrides so the snapshot's flows are verified too.
    snapshot = mapper.map_target(Settings(project_root=settings.project_root), offline=True)
    assert not snapshot.sources.parsed and not snapshot.sources.overrides
    assert snapshot.nodes == parsed.nodes
    assert snapshot.edges == parsed.edges
    assert snapshot.flows == parsed.flows
    assert snapshot.sources.ignored_config == parsed.sources.ignored_config


def test_cli_mapper_error_does_not_echo_yaml_values(cli_workspace: Path) -> None:
    (cli_workspace / "overrides.yaml").write_text("edges: {add: [{kind: secret-value}]}\n")
    result = CliRunner().invoke(cli.app, ["map"])
    assert result.exit_code == 2 and "secret-value" not in result.output


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"services": []},
        {"services": {"api": {"depends_on": ["missing"]}}},
        {"services": {"api": {"environment": "wrong"}}},
        {"services": {"api": {"ports": ["70000:80"]}}},
        {"services": {"api": {"env_file": [42]}}},
    ],
)
def test_invalid_compose_reports_safe_errors(data: dict) -> None:
    with pytest.raises(mapper.MapperError):
        mapper.parse_compose(data, app="test")


def test_dotenv_input_is_refused_without_reading(settings: Settings) -> None:
    with pytest.raises(mapper.MapperError, match="Dotenv"):
        mapper.read_yaml(settings.project_root / ".env")


def test_edge_evidence_is_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Edge(source="a", target="b", kind="http", evidence="")
