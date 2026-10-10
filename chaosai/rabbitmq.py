"""One management request contract for smoke evidence and baseline telemetry."""

from urllib.parse import quote

import httpx
from pydantic import ValidationError

from chaosai.config import Settings
from chaosai.models import QueueSnapshot
from chaosai.target import TargetError


def queue_snapshot(client: httpx.Client, settings: Settings) -> QueueSnapshot:
    """Read the configured queue; report only safe status/type diagnostics."""
    path = (
        f"/api/queues/{quote(settings.rabbitmq_vhost, safe='')}/"
        f"{quote(settings.notification_queue, safe='')}"
    )
    try:
        response = client.get(
            settings.rabbitmq_url.rstrip("/") + path,
            headers={"Accept": "application/json"},
            auth=(settings.rabbitmq_user, settings.rabbitmq_password.get_secret_value()),
            # Override the caller's load timeout so smoke/poller behave identically.
            timeout=settings.request_timeout_s,
        )
    except httpx.HTTPError as exc:
        raise TargetError(f"RabbitMQ management request failed ({type(exc).__name__}).") from exc
    if response.status_code == 404:
        raise TargetError("RabbitMQ notifications queue not found (HTTP 404).")
    if response.status_code != 200:
        raise TargetError(f"RabbitMQ management API returned HTTP {response.status_code}.")
    try:
        snapshot = QueueSnapshot.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise TargetError(
            f"RabbitMQ queue metrics missing/invalid (HTTP 200, {type(exc).__name__})."
        ) from exc
    if snapshot.name != settings.notification_queue or snapshot.vhost != settings.rabbitmq_vhost:
        raise TargetError("RabbitMQ returned a different queue or vhost (HTTP 200).")
    return snapshot
