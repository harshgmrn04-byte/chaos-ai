import json
import re
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
def api(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> tuple[httpx.Client, MagicMock, dict]:
    state = {"accounts": {}, "categories": [], "products": [], "requests": [], "orders": {}}
    state.update({"passwords": {}, "db_reads": [], "credential_checks": [], "promote": True})
    postgres = container("postgres")
    auth_container = container("auth-service")
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [postgres, auth_container]

    def sql(command: list[str]) -> SimpleNamespace:
        assert command[:4] == ["psql", "-U", "ecommerce", "-d"]
        assert command[5:7] == ["-v", "ON_ERROR_STOP=1"]
        query = command[-1]
        if command[4] == "ecommerce_products":
            selected = re.search(r"WHERE slug IN \((.*?)\)", query).group(1)
            slugs = re.findall(r"'([^']*)'", selected)
            products = [p for p in state["products"] if p["slug"] in slugs]
            payload = {
                "total": len(state["products"]),
                "seed": len(products),
                "high_stock_seed": sum(p["stock"] >= 100000 for p in products),
            }
            if state.get("db_product_counts") is not None:
                payload = state["db_product_counts"]
            state["db_reads"].append((command[4], payload))
            output = json.dumps(payload).encode()
        elif query.startswith("SELECT"):
            selected = re.search(r"WHERE email IN \((.*?)\)", query).group(1)
            emails = re.findall(r"'([^']*)'", selected)
            payload = [
                {k: user[k] for k in ("id", "email", "role")}
                for email, user in state["accounts"].items()
                if email in emails
            ]
            if state.get("hide_final_user") and len(emails) > 1:
                payload = payload[:-1]
            if state.get("bad_final_role") and len(emails) > 1:
                for user in payload:
                    if user["email"] == "admin@test.com":
                        user["role"] = "USER"
            state["db_reads"].append((command[4], payload))
            output = json.dumps(payload).encode()
        else:
            assert query == "UPDATE \"User\" SET role='ADMIN' WHERE email='admin@test.com';"
            if state["promote"]:
                state["accounts"]["admin@test.com"]["role"] = "ADMIN"
            output = b"UPDATE 1"
        return SimpleNamespace(exit_code=0, output=output)

    postgres.exec_run.side_effect = sql

    def credentials(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command[0:2] == ["node", "-e"]
        assert kwargs["workdir"] == settings.auth_workdir
        environment = kwargs["environment"]
        email = environment["CHAOSAI_AUTH_EMAIL"]
        password = environment["CHAOSAI_AUTH_PASSWORD"]
        assert email not in command[2] and password not in command[2]
        state["credential_checks"].append(email)
        payload = {
            "exists": email in state["accounts"],
            "password_matches": state["passwords"].get(email) == password,
        }
        return SimpleNamespace(exit_code=0, output=json.dumps(payload).encode())

    auth_container.exec_run.side_effect = credentials
    monkeypatch.setattr(smoke, "get_docker_client", lambda: docker_client)

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
            assert request.extensions["timeout"]["read"] == settings.auth_timeout_s
            if callback := state.get("before_register"):
                callback(body)
            if body["email"] in state["accounts"]:
                raise httpx.RemoteProtocolError("Duplicate register disconnected")
            if state.get("register_failure") == "before_commit":
                raise httpx.ReadTimeout("Registration timed out before commit")
            state["passwords"][body["email"]] = body["password"]
            state["accounts"][body["email"]] = {
                "id": body["email"],
                "name": body["name"],
                "email": body["email"],
                "role": "USER",
            }
            if state.get("register_failure") == "after_commit":
                raise httpx.RemoteProtocolError("Registration response disconnected after commit")
            return httpx.Response(201, json=auth(body["email"]))
        if path == "/login":
            assert set(body) == {"email", "password"}
            assert request.extensions["timeout"]["read"] == settings.auth_timeout_s
            assert body["email"] in state["accounts"], "Missing user must never reach login"
            assert state["passwords"][body["email"]] == body["password"], "Invalid login forbidden"
            if callback := state.get("before_login_failure"):
                callback(body)
            if state.get("login_failure") == "disconnect":
                raise httpx.RemoteProtocolError("Server disconnected without sending a response")
            if state.get("login_failure") == "timeout":
                raise httpx.ReadTimeout("Login timed out")
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
    client, docker_client, state = api
    state["promote"] = False
    with pytest.raises(TargetError, match="ADMIN token"):
        seed.seed_target(settings, docker_client, client, users=0)


def test_sql_failure_is_secret_free(settings: Settings) -> None:
    postgres = container("postgres")
    postgres.exec_run.return_value = SimpleNamespace(exit_code=1, output=b"secret password")
    with pytest.raises(TargetError, match="PostgreSQL query failed") as exc:
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
    docker_client.containers.list.return_value.append(notification)
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
        monkeypatch.setattr(smoke, "get_docker_client", lambda: docker_client)
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
    docker_client.containers.list.return_value.append(notification)
    monkeypatch.setattr(smoke, "get_docker_client", lambda: docker_client)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    result = smoke.smoke_target(settings, client)
    assert any("No notification receipt" in message for message in result.warnings)
    assert result.order_id == "new-order"


def existing_account(state: dict, email: str, password: str, role: str = "USER") -> None:
    state["accounts"][email] = {"id": email, "name": "Existing", "email": email, "role": role}
    state["passwords"][email] = password


def test_existing_admin_wrong_password_sends_no_http_auth(settings: Settings, api: tuple) -> None:
    client, docker_client, state = api
    existing_account(state, "admin@test.com", "different-existing-password", "ADMIN")
    with pytest.raises(TargetError, match="exists but the password does not match") as error:
        seed.seed_target(settings, docker_client, client)
    assert "ADMIN_PASSWORD" in str(error.value)
    assert "different-existing-password" not in str(error.value)
    assert not state["requests"]
    assert len(state["accounts"]) == 1


def test_existing_load_user_is_not_reregistered_with_different_password(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    existing_account(state, "admin@test.com", settings.seed_password.get_secret_value(), "ADMIN")
    existing_account(state, "loaduser001@test.com", "other-password")
    with pytest.raises(TargetError, match="SEED_PASSWORD"):
        seed.seed_target(settings, docker_client, client, users=1)
    assert not state["requests"]


def test_separate_admin_password_preserves_load_credentials(settings: Settings, api: tuple) -> None:
    from pydantic import SecretStr

    client, docker_client, state = api
    settings.admin_password = SecretStr("existing-admin-password")
    existing_account(state, "admin@test.com", "existing-admin-password", "ADMIN")
    first = seed.seed_target(settings, docker_client, client, users=2)
    assert first.users_created == 2 and first.users_existing == 1
    assert state["passwords"]["loaduser001@test.com"] == settings.seed_password.get_secret_value()
    assert first.verified_users == 3 and first.verified_admin
    assert first.verified_products == first.product_count == 4
    before = len(state["requests"])
    second = seed.seed_target(settings, docker_client, client, users=2)
    assert second.users_created == 0 and second.users_existing == 3
    assert not any(path == "/register" for _, path, *_ in state["requests"][before:])


def test_registered_account_disconnected_after_commit_is_reconciled(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    state["register_failure"] = "after_commit"
    result = seed.seed_target(settings, docker_client, client, users=0)
    assert result.users_created == result.verified_users == 1
    assert result.verified_admin and result.warnings
    assert "database recheck confirms account exists" in result.warnings[0]
    assert sum(path == "/register" for _, path, *_ in state["requests"]) == 1
    assert sum(path == "/login" for _, path, *_ in state["requests"]) == 1


def test_registration_timeout_before_commit_checks_db_and_does_not_login(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    state["register_failure"] = "before_commit"
    with pytest.raises(TargetError, match="database recheck confirms user is absent"):
        seed.seed_target(settings, docker_client, client, users=0)
    assert len(state["db_reads"]) == 2
    assert len(state["requests"]) == 1 and state["requests"][0][1] == "/register"
    assert not state["accounts"]


def test_existing_accounts_never_reach_dropped_duplicate_register_transport(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    seed.seed_target(settings, docker_client, client, users=2)
    state["requests"].clear()
    # The transport disconnects whenever /register receives an existing email.
    result = seed.seed_target(settings, docker_client, client, users=2)
    assert result.users_existing == 3
    assert not any(path == "/register" for _, path, *_ in state["requests"])


def test_concurrent_duplicate_register_disconnect_is_reconciled(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    state["before_register"] = lambda body: existing_account(state, body["email"], body["password"])
    result = seed.seed_target(settings, docker_client, client, users=0)
    assert result.verified_users == 1 and result.warnings
    assert "No registration retry" in result.warnings[0]
    assert sum(path == "/register" for _, path, *_ in state["requests"]) == 1
    assert state["credential_checks"].count("admin@test.com") >= 2


def test_duplicate_registration_race_with_other_password_refuses_login(
    settings: Settings, api: tuple
) -> None:
    client, docker_client, state = api
    state["before_register"] = lambda body: existing_account(state, body["email"], "other-password")
    with pytest.raises(TargetError, match="ADMIN_PASSWORD"):
        seed.seed_target(settings, docker_client, client, users=0)
    assert sum(path == "/register" for _, path, *_ in state["requests"]) == 1
    assert not any(path == "/login" for _, path, *_ in state["requests"])


@pytest.mark.parametrize("failure", ["disconnect", "timeout"])
def test_login_transport_failure_rechecks_existing_valid_credentials(
    settings: Settings, api: tuple, failure: str
) -> None:
    from chaosai.target_auth import safe_admin_login

    client, docker_client, state = api
    existing_account(state, "admin@test.com", settings.seed_password.get_secret_value(), "ADMIN")
    state["login_failure"] = failure
    postgres = docker_client.containers.list.return_value[0]
    with pytest.raises(TargetError, match="database recheck confirms.*credentials match"):
        safe_admin_login(docker_client, postgres, settings, client)
    assert len(state["requests"]) == 1 and state["requests"][0][1] == "/login"
    assert len(state["db_reads"]) == len(state["credential_checks"]) == 2


def test_login_disconnect_recheck_reports_password_change(settings: Settings, api: tuple) -> None:
    from chaosai.target_auth import safe_admin_login

    client, docker_client, state = api
    existing_account(state, "admin@test.com", settings.seed_password.get_secret_value(), "ADMIN")
    state["login_failure"] = "disconnect"
    state["before_login_failure"] = lambda body: state["passwords"].update(
        {body["email"]: "concurrently-changed-password"}
    )
    postgres = docker_client.containers.list.return_value[0]
    with pytest.raises(TargetError, match="ADMIN_PASSWORD") as error:
        safe_admin_login(docker_client, postgres, settings, client)
    assert "concurrently-changed-password" not in str(error.value)
    assert len(state["requests"]) == 1 and len(state["db_reads"]) == 2


def test_smoke_missing_or_wrong_password_never_sends_login(settings: Settings, api: tuple) -> None:
    client, docker_client, state = api
    seed.seed_target(settings, docker_client, client, users=0)
    state["requests"].clear()
    state["passwords"]["admin@test.com"] = "different-existing-password"
    with pytest.raises(TargetError, match="password does not match"):
        smoke.smoke_target(settings, client)
    assert not any(path in ("/login", "/register", "/orders") for _, path, *_ in state["requests"])
    state["accounts"].pop("admin@test.com")
    with pytest.raises(TargetError, match="absent.*run chaosai seed"):
        smoke.smoke_target(settings, client)
    assert not any(path == "/login" for _, path, *_ in state["requests"])


@pytest.mark.parametrize("failure", ["missing_users", "bad_role", "product_count"])
def test_post_seed_database_verification_is_required(
    settings: Settings, api: tuple, failure: str
) -> None:
    client, docker_client, state = api
    if failure == "missing_users":
        state["hide_final_user"] = True
    elif failure == "bad_role":
        state["bad_final_role"] = True
    else:
        state["db_product_counts"] = {"total": 3, "seed": 3, "high_stock_seed": 3}
    with pytest.raises(TargetError, match="Seed database verification failed"):
        seed.seed_target(settings, docker_client, client, users=1)
    # All documented HTTP creation responses succeeded; DB evidence still denies success.
    assert len(state["products"]) == 4


def test_credential_helper_failure_does_not_login_or_disclose_output(
    settings: Settings, api: tuple
) -> None:
    from chaosai.target_auth import safe_admin_login

    client, docker_client, state = api
    existing_account(state, "admin@test.com", settings.seed_password.get_secret_value(), "ADMIN")
    auth = docker_client.containers.list.return_value[1]
    auth.exec_run.side_effect = None
    auth.exec_run.return_value = SimpleNamespace(exit_code=2, output=b"sensitive hash/password")
    with pytest.raises(TargetError, match="No further auth request") as error:
        safe_admin_login(
            docker_client, docker_client.containers.list.return_value[0], settings, client
        )
    assert "sensitive" not in str(error.value) and not state["requests"]


def test_foreign_auth_container_is_never_used(settings: Settings, api: tuple) -> None:
    from chaosai.target_auth import safe_admin_login

    client, docker_client, state = api
    existing_account(state, "admin@test.com", settings.seed_password.get_secret_value(), "ADMIN")
    foreign = container("auth-service", "outside")
    docker_client.containers.list.return_value = [
        docker_client.containers.list.return_value[0],
        foreign,
    ]
    with pytest.raises(TargetError, match="Expected one scoped auth-service"):
        safe_admin_login(
            docker_client, docker_client.containers.list.return_value[0], settings, client
        )
    foreign.exec_run.assert_not_called()
    assert not state["requests"]


def test_new_admin_short_password_is_rejected_before_register(
    settings: Settings, api: tuple
) -> None:
    from pydantic import SecretStr

    client, docker_client, state = api
    settings.admin_password = SecretStr("short")
    with pytest.raises(TargetError, match="at least six characters"):
        seed.seed_target(settings, docker_client, client, users=0)
    assert not state["requests"]
