"""Checkout verification plus best-effort notification evidence."""

import math
import time
from urllib.parse import quote

import docker
import httpx
from docker.models.containers import Container
from pydantic import ValidationError

from chaosai.config import Settings
from chaosai.models import (
    AuthResponse,
    OrderResponse,
    Product,
    QueueSnapshot,
    SmokeResult,
    TargetOrder,
)
from chaosai.target import TargetError, get_docker_client, require_scope, service_container
from chaosai.target_api import order_body, product_page, request_json
from chaosai.target_auth import safe_admin_login


def queue_snapshot(client: httpx.Client, settings: Settings) -> QueueSnapshot:
    path = (
        f"/api/queues/{quote(settings.rabbitmq_vhost, safe='')}/"
        f"{quote(settings.notification_queue, safe='')}"
    )
    try:
        response = client.get(
            settings.rabbitmq_url + path,
            headers={"Accept": "application/json"},
            auth=(settings.rabbitmq_user, settings.rabbitmq_password.get_secret_value()),
        )
    except httpx.HTTPError as exc:
        raise TargetError(
            "RabbitMQ management API unreachable; queue evidence unavailable."
        ) from exc
    if response.status_code == 404:
        raise TargetError("RabbitMQ notifications queue not found (HTTP 404).")
    if response.status_code != 200:
        raise TargetError(f"RabbitMQ management API returned HTTP {response.status_code}.")
    try:
        snapshot = QueueSnapshot.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise TargetError(
            "RabbitMQ queue metrics missing/invalid; check statistics collection."
        ) from exc
    if snapshot.name != settings.notification_queue or snapshot.vhost != settings.rabbitmq_vhost:
        raise TargetError("RabbitMQ returned a different queue or vhost.")
    return snapshot


def queue_warnings(before: QueueSnapshot, after: list[QueueSnapshot]) -> list[str]:
    """Compare bounded observations, without claiming delivery or email success."""
    warnings: list[str] = []
    if not after:
        return ["RabbitMQ: no post-order queue snapshots available."]
    if any(snapshot.consumers < 1 for snapshot in [before, *after]):
        warnings.append("RabbitMQ notifications queue has no consumer in at least one snapshot.")
    last = after[-1]
    if last.messages_ready > before.messages_ready or (
        len(after) > 1 and last.messages_ready > after[-2].messages_ready
    ):
        warnings.append(
            f"RabbitMQ ready messages grew or did not drain to baseline: "
            f"before={before.messages_ready}, after={[s.messages_ready for s in after]}."
        )
    return warnings


def notification_received(logs: bytes, order_id: str) -> bool:
    # Winston's simple console transport emits message and metadata on the same line.
    return any(
        "Received event: order.created" in line and order_id in line
        for line in logs.decode("utf-8", errors="replace").splitlines()
    )


def validate_order(order: TargetOrder, user_id: str, product: Product) -> None:
    subtotal = product.price
    shipping = 0 if subtotal > 100 else 10
    if (
        order.userId != user_id
        or order.status != "pending"
        or len(order.items) != 1
        or order.items[0].productId != product.id
        or order.items[0].name != product.name
        or order.items[0].quantity != 1
        or not math.isclose(order.items[0].price, subtotal)
        or not math.isclose(order.subtotal, subtotal)
        or not math.isclose(order.shipping, shipping)
        or not math.isclose(order.total, subtotal + shipping)
    ):
        raise TargetError(
            "Checkout: order owner, pending status, items or totals do not match request."
        )


def smoke_target(settings: Settings, client: httpx.Client) -> SmokeResult:
    page = product_page(client, settings)
    product = next((p for p in page.products if p.stock >= 1), None)
    if product is None:
        raise TargetError("Browse products: no in-stock products; run chaosai seed first.")
    docker_client = get_docker_client()
    try:
        postgres = service_container(docker_client, settings, "postgres")
        auth = safe_admin_login(docker_client, postgres, settings, client)
        return smoke_checkout(settings, client, docker_client, product, auth)
    finally:
        docker_client.close()


def smoke_checkout(
    settings: Settings,
    client: httpx.Client,
    docker_client: docker.DockerClient,
    product: Product,
    auth: AuthResponse,
) -> SmokeResult:
    warnings: list[str] = []
    before: QueueSnapshot | None = None
    try:
        before = queue_snapshot(client, settings)
    except TargetError as exc:
        warnings.append(str(exc))
    since = int(time.time())
    body = order_body(product)
    # Never retry checkout: the target can persist an order before returning HTTP 500.
    created = request_json(
        client,
        "POST",
        settings.order_url + "/orders",
        OrderResponse,
        stage="Checkout (an HTTP 500 may leave a stored order; inspect DB/broker before retrying)",
        expected=201,
        token=auth.accessToken,
        body=body,
    ).order
    validate_order(created, auth.user.id, product)
    stored = request_json(
        client,
        "GET",
        settings.order_url + "/orders/" + quote(created.id, safe=""),
        TargetOrder,
        stage="Retrieve created order",
        token=auth.accessToken,
    )
    if stored.id != created.id:
        raise TargetError("Checkout: retrieved order ID differs from the created order.")
    validate_order(stored, auth.user.id, product)
    result = SmokeResult(order_id=created.id, warnings=warnings)
    snapshots: list[QueueSnapshot] = []
    logs_found = False
    notification: Container | None = None
    try:
        notification = service_container(docker_client, settings, "notification-service")
    except (TargetError, docker.errors.DockerException):
        result.warnings.append("Notification logs unavailable; check scoped service/Docker.")
    for _ in range(settings.notification_samples):
        time.sleep(settings.notification_interval_s)
        if before is not None:
            try:
                snapshots.append(queue_snapshot(client, settings))
            except TargetError as exc:
                result.warnings.append(str(exc))
                before = None
        if notification is not None and not logs_found:
            try:
                notification.reload()
                require_scope(notification, settings.compose_project)
                logs_found = notification_received(
                    notification.logs(since=since, tail=settings.notification_log_tail),
                    created.id,
                )
            except (TargetError, docker.errors.DockerException):
                result.warnings.append("Notification logs became unavailable.")
                notification = None
    if before is not None:
        queue_notes = queue_warnings(before, snapshots)
        result.warnings.extend(queue_notes)
        if not queue_notes:
            last = snapshots[-1]
            result.evidence.append(
                f"notifications exists: consumers={last.consumers}; "
                f"ready={before.messages_ready} -> {[s.messages_ready for s in snapshots]}; "
                f"total={last.messages}."
            )
    if logs_found:
        result.evidence.append("notification-service received order.created for this order ID.")
    elif notification is not None:
        result.warnings.append(
            "No notification receipt for this order ID within observation window."
        )
    return result
