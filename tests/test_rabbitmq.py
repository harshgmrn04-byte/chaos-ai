from pathlib import Path

import httpx
import pytest

from chaosai import metrics, rabbitmq, smoke
from chaosai.config import Settings
from chaosai.metrics import QueueDepthPoller
from chaosai.target import TargetError

QUEUE = {"name": "notifications", "vhost": "/", "consumers": 1, "messages_ready": 3, "messages": 5}


def test_smoke_and_poller_share_request_timeout_url_and_credentials(
    settings: Settings,
    tmp_path: Path,
) -> None:
    assert smoke.queue_snapshot is metrics.queue_snapshot is rabbitmq.queue_snapshot
    settings.rabbitmq_url += "/"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=QUEUE)

    poller = QueueDepthPoller(settings, tmp_path / "queue.jsonl")
    with httpx.Client(timeout=10, transport=httpx.MockTransport(handler)) as client:
        smoke.queue_snapshot(client, settings)
    with httpx.Client(timeout=5, transport=httpx.MockTransport(handler)) as client:
        poller.poll_once(client)
    a, b = seen
    assert a.url == b.url and a.url.raw_path == b"/api/queues/%2F/notifications"
    assert a.headers["Authorization"] == b.headers["Authorization"]
    assert a.extensions["timeout"] == b.extensions["timeout"]
    assert b.extensions["timeout"]["read"] == settings.request_timeout_s
    assert poller.snapshot()[0].depth == 5


@pytest.mark.parametrize("failure,reason", [("status", "HTTP 503"), ("transport", "ReadTimeout")])
def test_poller_warns_once_keeps_retrying_and_reports_safe_reason(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
    reason: str,
) -> None:
    poller = QueueDepthPoller(settings, tmp_path / "queue.jsonl")
    # Run the real loop without sleeping or networking. Recover on its third poll.
    monkeypatch.setattr(poller.stop_event, "wait", lambda seconds: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 4:
            poller.stop_event.set()
        if calls == 3:
            return httpx.Response(200, json=QUEUE)
        if failure == "transport":
            raise httpx.ReadTimeout("secret request contents", request=request)
        return httpx.Response(503, text="secret HTTP body")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(metrics.httpx, "Client", lambda **kwargs: client)
    poller.run()
    assert calls == 4 and client.is_closed
    assert len(poller.snapshot()) == 1
    warnings = [r.getMessage() for r in caplog.records if "queue depth unavailable" in r.message]
    assert len(warnings) == 1 and reason in warnings[0]
    assert "secret" not in caplog.text
    assert settings.rabbitmq_password.get_secret_value() not in caplog.text


@pytest.mark.parametrize(
    "body,reason", [({"name": "notifications"}, "ValidationError"), (None, "JSONDecodeError")]
)
def test_shared_reader_diagnostics_omit_response_body(
    settings: Settings,
    body: dict[str, str] | None,
    reason: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body) if body else httpx.Response(200, text="secret")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TargetError) as error:
            rabbitmq.queue_snapshot(client, settings)
    assert reason in str(error.value) and "HTTP 200" in str(error.value)
    assert "secret" not in str(error.value)
