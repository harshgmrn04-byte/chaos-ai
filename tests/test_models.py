import inspect
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from chaosai import models
from chaosai.fixture_data import fixture
from chaosai.models import (
    Approval,
    Baseline,
    Clamp,
    ExperimentResult,
    Findings,
    Graph,
    Plan,
    RunMeta,
    Score,
    Summary,
)


def instances(value: object) -> list[BaseModel]:
    result: list[BaseModel] = []
    if isinstance(value, BaseModel):
        result.append(value)
        for name in type(value).model_fields:
            result.extend(instances(getattr(value, name)))
    elif isinstance(value, list):
        for item in value:
            result.extend(instances(item))
    elif isinstance(value, dict):
        for item in value.values():
            result.extend(instances(item))
    return result


def test_every_contract_round_trips() -> None:
    values = [
        models.ContainerObservation(
            t=1, name="test", stats=models.ContainerStats(cpu_pct=1, mem_mb=2, restarts=0)
        ),
        models.QueueObservation(t=1, name="notifications", depth=0),
        models.GraphOverrides(
            edges=models.EdgeOverrides(
                remove=[models.EdgeSelector(source="frontend", target="auth-service")],
                criticality=[
                    models.CriticalityOverride(
                        source="frontend", target="auth-service", criticality="hard"
                    )
                ],
            ),
        ),
        fixture("graph", Graph),
        fixture("baseline", Baseline),
        fixture("plan", Plan),
        fixture("results", list[ExperimentResult]),
        fixture("findings", Findings),
        fixture("scores", list[Score]),
        fixture("summary", Summary),
        Clamp(field="duration_s", requested=200, final=120, reason="cap"),
        fixture("samples-redis-stop", list[models.Sample]),
        RunMeta(
            run_id="20261011-080000",
            started_at=datetime.now(UTC),
            planner_source="mock",
            analyzer_source="heuristic",
            target_project="test",
            approvals=[Approval(experiment_id="redis-stop", decision="yes")],
        ),
    ]
    product = models.Product(
        id="p", name="Test", slug="test", description="d", price=12.5, stock=100000, categoryId="c"
    )
    values.extend(
        [
            models.APIModel(),
            models.AuthResponse(
                user=models.TargetUser(id="u", name="Test", email="user@test.com", role="USER"),
                accessToken="mock-token",
                refreshToken="mock-refresh",
            ),
            models.Category(id="c", name="Test", slug="test"),
            models.ProductPage(
                products=[product],
                pagination=models.Pagination(page=1, limit=100, total=1, totalPages=1),
            ),
            models.OrderResponse(
                order=models.TargetOrder(
                    id="o",
                    userId="u",
                    status="pending",
                    subtotal=12.5,
                    shipping=10,
                    total=22.5,
                    items=[models.OrderItem(productId="p", name="Test", price=12.5, quantity=1)],
                )
            ),
            models.QueueSnapshot(
                name="notifications", vhost="/", consumers=1, messages_ready=0, messages=0
            ),
            models.ContainerStatus(
                name="test",
                service="postgres",
                state="running",
                paused=False,
                health="healthy",
                ports=[("localhost", 5433)],
            ),
            models.CheckResult(name="test", passed=True, detail="test"),
            models.DatabaseUser(id="u", email="user@test.com", role="USER"),
            models.CredentialCheck(exists=True, password_matches=True),
            models.ProductCounts(total=4, seed=4, high_stock_seed=4),
            models.SeedResult(),
            models.SmokeResult(order_id="o"),
        ]
    )
    covered: set[type[BaseModel]] = set()
    for value in values:
        for model in instances(value):
            assert type(model).model_validate_json(model.model_dump_json()) == model
            covered.add(type(model))
    contracts = {
        cls
        for _, cls in inspect.getmembers(models, inspect.isclass)
        if issubclass(cls, BaseModel) and cls.__module__ == models.__name__ and cls != models.Model
    }
    assert covered == contracts


@pytest.mark.parametrize("bad_score", [-1, 101])
def test_score_bounds(bad_score: int) -> None:
    score = fixture("scores", list[Score])[0].model_dump()
    score["score"] = bad_score
    with pytest.raises(ValidationError):
        Score.model_validate(score)


def test_unknown_fault_and_extra_fields_rejected() -> None:
    spec = fixture("plan", Plan).root[0].model_dump()
    spec["fault"] = "full_disk"
    with pytest.raises(ValidationError):
        models.ExperimentSpec.model_validate(spec)
    spec["fault"] = "stop"
    spec["shell_command"] = "anything"
    with pytest.raises(ValidationError):
        models.ExperimentSpec.model_validate(spec)


def test_llm_models_have_no_free_form_dicts() -> None:
    for model in (Plan, Findings):
        schema = model.model_json_schema()
        assert schema["type"] == "array"
        assert all(
            part.get("additionalProperties") is not True for part in schema["$defs"].values()
        )
