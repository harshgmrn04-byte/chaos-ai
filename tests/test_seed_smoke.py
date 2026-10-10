import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from typer.testing import CliRunner

from chaosai import cli, seed, smoke
from chaosai.config import Settings
from chaosai.models import Product, QueueSnapshot
from chaosai.target import TargetError
from chaosai.target_api import all_products, order_body, product_body, register_body, request_json

from .test_target import container


@pytest.fixture
def api(settings: Settings) -> tuple[httpx.Client, MagicMock, dict]:
    state = {"accounts": {}, "categories": [], "products": [], "requests": [], "orders": {}}
    postgres = container("postgres")
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [postgres]

    def sql(command: list[str]) -> SimpleNamespace:
        assert command[:7] == [
            "psql",
            "-U",
            "ecommerce",
            "-d",
            "ecommerce_auth",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        if command[-1].startswith("SELECT"):
            output = "\n".join(state["accounts"]).encode()
        else:
            assert command[-1] == "UPDATE \"User\" SET role='ADMIN' WHERE email='admin@test.com';"
            state["accounts"]["admin@test.com"]["role"] = "ADMIN"
            output = b"UPDATE 1"
        return SimpleNamespace(exit_code=0, output=output)

    postgres.exec_run.side_effect = sql

    def auth(email: str) -> dict:
        return {
            "user": state["accounts"][email],
            "accessToken": "mock-token",
            "refreshToken": "mock-refresh",
        }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        state["requests"].append((request.method, request.url.path, body, dict(request.headers)))
        path = request.url.path
        if path == "/register":
            assert set(body) == {"name", "email", "password"}
            assert body["email"] not in state["accounts"], "Duplicate register must never happen"
            state["accounts"][body["email"]] = {
                "id": body["email"],
                "name": body["name"],
                "email": body["email"],
                "role": "USER",
            }
            return httpx.Response(201, json=auth(body["email"]))
        if path == "/login":
            assert set(body) == {"email", "password"}
            return httpx.Response(200, json=auth(body["email"]))
        if request.method == "POST":
            assert request.headers["Authorization"] == "Bearer mock-token"
        if path == "/categories":
            if request.method == "GET":
                return httpx.Response(200, json=state["categories"])
            category = {**body, "id": str(len(state["categories"]) + 1)}
            state["categories"].append(category)
            return httpx.Response(201, json=category)
        if path == "/products":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "products": state["products"],
                        "pagination": {
                            "page": 1,
                            "limit": 100,
                            "total": len(state["products"]),
                            "totalPages": 1,
                        },
                    },
                )
            assert body["featured"] == "true"
            assert body["stock"] == "100000" and "images" not in body
            product = {
                **body,
                "id": str(len(state["products"]) + 1),
                "price": float(body["price"]),
                "stock": int(body["stock"]),
            }
            state["products"].append(product)
            return httpx.Response(201, json=product)
        if path.startswith("/api/queues/"):
            assert request.url.raw_path == b"/api/queues/%2F/notifications"
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(
                200,
                json={
                    "name": "notifications",
                    "vhost": "/",
                    "consumers": 1,
                    "messages_ready": 0,
                    "messages": 0,
                },
            )
        if path == "/orders":
            assert body["paymentMethod"] == "stripe"
            item = body["items"][0]
            order = {
                "id": "new-order",
                "userId": "admin@test.com",
                "status": "pending",
                "subtotal": item["price"],
                "shipping": 10,
                "total": item["price"] + 10,
                "items": body["items"],
            }
            state["orders"][order["id"]] = order
            return httpx.Response(201, json={"order": order})
        if path == "/orders/new-order":
            return httpx.Response(200, json=state["orders"]["new-order"])
        raise AssertionError(f"Unexpected target endpoint {path}")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        yield client, docker_client, state


def test_seed_twice_is_idempotent(settings: Settings, api: tuple) -> None:
    client, docker_client, state = api
    first = seed.seed_target(settings, docker_client, client)
    assert first.users_created == 21 and first.users_existing == 0
    assert first.categories_created == 2 and first.products_created == 4
    second = seed.seed_target(settings, docker_client, client)
    assert second.users_created == second.categories_created == second.products_created == 0
    assert second.users_existing == 21
    assert len(state["accounts"]) == 21
    assert len(state["categories"]) == 2 and len(state["products"]) == 4
    assert sum(path == "/register" for _, path, *_ in state["requests"]) == 21
    assert sum(path == "/login" for _, path, *_ in state["requests"]) == 2
    assert all(product["stock"] >= 100000 for product in state["products"])


def test_partial_seed_existing_accounts_and_stock_guard(settings: Settings, api: tuple) -> None:
    client, docker_client, state = api
    seed.seed_target(settings, docker_client, client, users=2)
    result = seed.seed_target(settings, docker_client, client, users=4)
    assert result.users_created == 2 and result.users_existing == 3
    state["products"][0]["stock"] = 999
    with pytest.raises(TargetError, match="stock below 100000"):
        seed.seed_target(settings, docker_client, client)


def test_seed_rejects_foreign_postgres_before_sql(settings: Settings, api: tuple) -> None:
    client, docker_client, _ = api
    foreign = container("postgres", "outside")
    docker_client.containers.list.return_value = [foreign]
    with pytest.raises(TargetError, match="Expected one"):
        seed.seed_target(settings, docker_client, client)
    foreign.exec_run.assert_not_called()


def test_promotion_requires_new_admin_token(settings: Settings, api: tuple) -> None:
    client, docker_client, _ = api
    postgres = docker_client.containers.list.return_value[0]
    postgres.exec_run.side_effect = None
    postgres.exec_run.return_value = SimpleNamespace(exit_code=0, output=b"")
    with pytest.raises(TargetError, match="ADMIN token"):
        seed.seed_target(settings, docker_client, client, users=0)


def test_sql_failure_is_secret_free(settings: Settings) -> None:
    postgres = container("postgres")
    postgres.exec_run.return_value = SimpleNamespace(exit_code=1, output=b"secret password")
    with pytest.raises(TargetError, match="database SQL failed") as exc:
        seed.auth_sql(postgres, settings, 'SELECT email FROM "User";')
    assert "secret password" not in str(exc.value)


def test_request_bodies_match_notes() -> None:
    assert register_body("User", "user@test.com", "fake-password") == {
        "name": "User",
        "email": "user@test.com",
        "password": "fake-password",
    }
    product = Product(
        id="p", name="Test", slug="test", description="d", price=100, stock=100000, categoryId="c"
    )
    body = order_body(product)
    assert set(body) == {"items", "shippingAddress", "paymentMethod"}
    assert body["items"] == [{"productId": "p", "name": "Test", "price": 100, "quantity": 1}]
    assert set(body["shippingAddress"]) == {"street", "city", "state", "zip", "country"}
    assert product_body("Test", "test", "c")["featured"] == "true"


@pytest.mark.parametrize(
    "response", [httpx.Response(500, text="secret"), httpx.Response(200, json={"secret": "value"})]
)
def test_bad_api_response_does_not_disclose_body(response: httpx.Response) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(TargetError) as exc:
            request_json(client, "GET", "http://local/products", Product, stage="Browse")
        assert "secret" not in str(exc.value)


def test_catalog_pagination_finds_later_seed_product(settings: Settings) -> None:
    pages = []

    def handle(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "products": [
                    {
                        "id": str(page),
                        "name": "Test",
                        "slug": str(page),
                        "description": "d",
                        "price": 1,
                        "stock": 100000,
                        "categoryId": "c",
                    }
                ],
                "pagination": {"page": page, "limit": 1, "total": 2, "totalPages": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert [p.id for p in all_products(client, settings)] == ["1", "2"]
    assert pages == [1, 2]


def test_queue_request_encoding_auth_and_fields(settings: Settings, api: tuple) -> None:
    client, _, _ = api
    snapshot = smoke.queue_snapshot(client, settings)
    assert snapshot.name == "notifications" and snapshot.consumers == 1
    assert snapshot.messages_ready == snapshot.messages == 0


def snapshot(ready: int, consumers: int = 1) -> QueueSnapshot:
    return QueueSnapshot(
        name="notifications", vhost="/", messages_ready=ready, consumers=consumers, messages=ready
    )


@pytest.mark.parametrize(
    "ready,warn", [([1, 0, 0], False), ([0, 1, 2], True), ([1, 1, 1], True), ([2, 1, 0], False)]
)
def test_ready_count_trend(ready: list[int], warn: bool) -> None:
    assert bool(smoke.queue_warnings(snapshot(0), [snapshot(n) for n in ready])) == warn


def test_queue_no_consumer_warns() -> None:
    assert "no consumer" in smoke.queue_warnings(snapshot(0), [snapshot(0, 0)])[0]


def test_smoke_success_correlates_order_and_logs(
    settings: Settings, api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, docker_client, state = api
    seed.seed_target(settings, docker_client, client)
    notification = container("notification-service")
    notification.logs.side_effect = [
        b"Received event: order.created {orderId: old-order}",
        b"Received event: order.created {orderId: new-order}",
    ]
    docker_client.containers.list.return_value = [notification]
    monkeypatch.setattr(smoke, "get_docker_client", lambda: docker_client)
    sleep = MagicMock()
    monkeypatch.setattr(smoke.time, "sleep", sleep)
    result = smoke.smoke_target(settings, client)
    assert result.order_id == "new-order" and not result.warnings
    assert len(result.evidence) == 2
    assert len(notification.logs.call_args_list) == 2
    assert sleep.call_count == 3
    assert notification.logs.call_args.kwargs["tail"] == settings.notification_log_tail
    docker_client.close.assert_called_once()
    assert sum(path == "/orders" for _, path, *_ in state["requests"]) == 1


@pytest.mark.parametrize("failure", ["unreachable", "missing", "unauthorized", "bad_metrics"])
def test_notification_management_failure_only_warns(
    settings: Settings, api: tuple, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    client, docker_client, _ = api
    seed.seed_target(settings, docker_client, client)
    if failure == "unreachable":

        def unavailable(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("test-secret")

        management = httpx.MockTransport(unavailable)
    else:
        status = {"missing": 404, "unauthorized": 401, "bad_metrics": 200}[failure]
        management = httpx.MockTransport(lambda _: httpx.Response(status, json={}))
    original_get = client.get
    with httpx.Client(transport=management) as rabbit:
        monkeypatch.setattr(
            client,
            "get",
            lambda url, **kw: (
                rabbit.get(url, **kw) if "/api/queues/" in url else original_get(url, **kw)
            ),
        )
        monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
        monkeypatch.setattr(
            smoke, "get_docker_client", MagicMock(side_effect=TargetError("offline"))
        )
        result = smoke.smoke_target(settings, client)
        assert result.order_id == "new-order" and result.warnings
        assert "test-secret" not in str(result)


def test_smoke_checkout_failure_no_retry_or_payment_guess(
    settings: Settings, api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, docker_client, _ = api
    seed.seed_target(settings, docker_client, client)
    original = client.request
    attempts = []

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        if method == "POST" and url.endswith("/orders"):
            attempts.append(url)
            return httpx.Response(500)
        return original(method, url, **kwargs)

    monkeypatch.setattr(client, "request", request)
    with pytest.raises(TargetError, match="stored order"):
        smoke.smoke_target(settings, client)
    assert len(attempts) == 1


def test_smoke_no_catalog_fails_precisely(settings: Settings, api: tuple) -> None:
    client, _, _ = api
    with pytest.raises(TargetError, match="no in-stock products"):
        smoke.smoke_target(settings, client)


def test_log_receipt_requires_routing_key_and_matching_order() -> None:
    assert not smoke.notification_received(b"Received event: order.created old-order", "new-order")
    assert not smoke.notification_received(b"Received event: order.shipped new-order", "new-order")
    assert smoke.notification_received(b"Received event: order.created new-order", "new-order")


def test_smoke_cli_warnings_exit_zero_and_failure_nonzero(
    cli_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaosai.models import SmokeResult

    monkeypatch.setattr(
        cli,
        "smoke_target",
        lambda *_: SmokeResult(order_id="o", warnings=["management API unreachable"]),
    )
    result = CliRunner().invoke(cli.app, ["smoke"])
    assert result.exit_code == 0 and "WARNING" in result.output
    monkeypatch.setattr(
        cli, "smoke_target", MagicMock(side_effect=TargetError("Checkout HTTP 500"))
    )
    result = CliRunner().invoke(cli.app, ["smoke"])
    assert result.exit_code == 1 and "Checkout HTTP 500" in result.output


@pytest.mark.parametrize("price,shipping", [(100, 10), (100.01, 0)])
def test_smoke_validates_shipping_boundary(price: float, shipping: float) -> None:
    from chaosai.models import OrderItem, TargetOrder

    product = Product(
        id="p", name="Test", slug="test", description="d", price=price, stock=100000, categoryId="c"
    )
    order = TargetOrder(
        id="o",
        userId="u",
        status="pending",
        subtotal=price,
        shipping=shipping,
        total=price + shipping,
        items=[OrderItem(productId="p", name="Test", price=price, quantity=1)],
    )
    smoke.validate_order(order, "u", product)
    order.total += 1
    with pytest.raises(TargetError, match="totals do not match"):
        smoke.validate_order(order, "u", product)


def test_request_timeout_has_precise_secret_free_error() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("password-must-not-leak")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(TargetError, match="timed out") as error:
            request_json(client, "POST", "http://local/register", dict, stage="Register")
    assert "password-must-not-leak" not in str(error.value)


def test_best_effort_log_timeout_warns(
    settings: Settings, api: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, docker_client, _ = api
    seed.seed_target(settings, docker_client, client)
    notification = container("notification-service")
    notification.logs.return_value = b"Received event: order.created old-order"
    docker_client.containers.list.return_value = [notification]
    monkeypatch.setattr(smoke, "get_docker_client", lambda: docker_client)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    result = smoke.smoke_target(settings, client)
    assert any("No notification receipt" in message for message in result.warnings)
    assert result.order_id == "new-order"
