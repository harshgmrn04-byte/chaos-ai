import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from chaosai import load
from chaosai.config import Settings
from chaosai.load import JourneyClient, LoadGenerator, RateCap, SampleBuffer, Session
from chaosai.models import AuthResponse, Product, Sample
from chaosai.runs import read_jsonl
from chaosai.target import TargetError

PRODUCT = {
    "id": "p/1",
    "name": "Seed product",
    "slug": "seed",
    "description": "seed",
    "price": 12.5,
    "stock": 100000,
    "categoryId": "c",
}
AUTH = {
    "user": {"id": "u", "name": "Load User", "email": "loaduser001@test.com", "role": "USER"},
    "accessToken": "mock-access",
    "refreshToken": "mock-refresh",
}
ORDER = {
    "order": {
        "id": "o",
        "userId": "u",
        "status": "pending",
        "subtotal": 12.5,
        "shipping": 10,
        "total": 22.5,
        "items": [{"productId": "p/1", "name": "Seed product", "price": 12.5, "quantity": 1}],
    }
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login":
        return httpx.Response(200, json=AUTH)
    if request.url.path == "/products":
        return httpx.Response(
            200,
            json={
                "products": [PRODUCT],
                "pagination": {"page": 1, "limit": 12, "total": 1, "totalPages": 1},
            },
        )
    if request.url.path.startswith("/products/"):
        return httpx.Response(200, json=PRODUCT)
    if request.url.path == "/categories":
        return httpx.Response(200, json=[{"id": "c", "name": "Seed", "slug": "seed"}])
    if request.url.path == "/orders":
        return httpx.Response(201, json=ORDER)
    if request.url.path == "/":
        return httpx.Response(200, text="frontend")
    raise AssertionError("Undocumented endpoint")


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(load, "service_container", lambda *a: Mock())
    check = Mock()
    monkeypatch.setattr(load, "require_credentials", check)
    return check


def generator(settings: Settings, tmp_path: Path) -> LoadGenerator:
    gen = LoadGenerator(
        settings,
        Mock(),
        SampleBuffer(tmp_path / "samples.jsonl"),
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return gen


def test_shared_rate_cap_no_burst_under_threads() -> None:
    now = [0.0]
    cap = RateCap(1, lambda: now[0])
    for instant in (0.0, 0.5, 1.0, 1.9, 2.0):
        now[0] = instant
        with ThreadPoolExecutor(max_workers=20) as pool:
            admitted = list(pool.map(lambda _: cap.admit(), range(100)))
        assert sum(admitted) == (1 if instant in (0, 1, 2) else 0)
    cancelled = threading.Event()
    cancelled.set()
    assert not cap.acquire(cancelled)


def test_buffer_concurrent_appends_and_append_only(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    samples = [
        Sample(t=i, journey="browse", ok=True, status=200, latency_ms=i, error=None)
        for i in range(100)
    ]
    buffer = SampleBuffer(path)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(buffer.append, samples))
    assert len(buffer.snapshot()) == len(read_jsonl(path, Sample)) == 100
    prefix = path.read_bytes()
    buffer.append(samples[0])
    assert path.read_bytes().startswith(prefix)
    snapshot = buffer.snapshot()
    snapshot.clear()
    assert len(buffer.snapshot()) == 101


@pytest.mark.parametrize("reason", ["missing user", "wrong password", "helper unavailable"])
def test_invalid_credentials_never_reach_http(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    verified.side_effect = TargetError(reason)
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    assert not gen.sessions
    with gen.client_factory() as client:
        http = JourneyClient(client, gen.samples, settings)
        gen.journey("login", http)
        gen.journey("checkout", http)
        assert gen.login(Session(AUTH["user"]["email"]), http) is None
    assert gen.samples.snapshot() == []
    assert "No login sent" in caplog.text
    assert settings.seed_password.get_secret_value() not in caplog.text


def test_unavailable_password_never_queries_or_sends_login(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
) -> None:
    settings.seed_password = load.Settings(seed_password="").seed_password
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    assert not gen.sessions
    verified.assert_not_called()


def test_credential_changed_after_prepare_skips_without_login(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
) -> None:
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    verified.side_effect = TargetError("password changed")
    with gen.client_factory() as client:
        assert gen.login(gen.sessions[0], JourneyClient(client, gen.samples, settings)) is None
    assert gen.sessions[0].disabled and gen.samples.snapshot() == []


def test_exact_journeys_timeout_and_global_checkout_cap(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
) -> None:
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    with httpx.Client(transport=httpx.MockTransport(record)) as client:
        http = JourneyClient(client, gen.samples, settings)
        gen.journey("homepage", http)
        gen.journey("browse", http)
        gen.journey("login", http)
        for _ in range(100):
            gen.journey("checkout", http)
    assert [r.url.path for r in seen] == [
        "/",
        "/products",
        "/products/p/1",
        "/categories",
        "/login",
        "/orders",
    ]
    assert seen[0].url == settings.frontend_url + "/"
    assert seen[2].url.raw_path == b"/products/p%2F1"
    assert all(
        r.extensions["timeout"]["read"] == (4 if r.url.path == "/login" else 5) for r in seen
    )
    body = json.loads(seen[-1].content)
    assert body == load.order_body(Product.model_validate(PRODUCT))
    assert seen[-1].headers["Authorization"] == "Bearer mock-access"
    assert all(s.ok for s in gen.samples.snapshot())
    assert len(gen.samples.snapshot()) == 6
    text = gen.samples.path.read_text()
    assert "mock-access" not in text and settings.seed_password.get_secret_value() not in text


def test_cache_proactive_renewal_and_login_cap_does_not_clear_cache(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(load.time, "monotonic", lambda: now[0])
    gen = generator(settings, tmp_path)
    gen.auth_cap = RateCap(settings.login_rate_cap, lambda: now[0])
    gen.prepare(1)
    with gen.client_factory() as client:
        http, session = JourneyClient(client, gen.samples, settings), gen.sessions[0]
        assert gen.login(session, http) == "mock-access"
        assert gen.login(session, http, force=True) is None
        assert session.auth is not None  # rate cap must preserve a usable token
        now[0] = 699.0
        assert gen.login(session, http) == "mock-access"
        assert len(gen.samples.snapshot()) == 1
        now[0] = 700.0
        assert gen.login(session, http) == "mock-access"
        assert len(gen.samples.snapshot()) == 2
        assert verified.call_count == 3  # prepare + each actual login


@pytest.mark.parametrize("status", [500, 401])
def test_checkout_retry_only_401(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
    status: int,
) -> None:
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    session = gen.sessions[0]
    session.auth = AuthResponse.model_validate(AUTH)
    session.issued_at = load.time.monotonic()
    gen.checkout_cap = Mock(admit=lambda: True, acquire=lambda stop: True)
    count = 0

    def fail_first(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if request.url.path == "/orders":
            count += 1
            if count == 1:
                return httpx.Response(status)
        return handler(request)

    with httpx.Client(transport=httpx.MockTransport(fail_first)) as client:
        gen.journey("checkout", JourneyClient(client, gen.samples, settings))
    assert count == (2 if status == 401 else 1)
    assert not gen.samples.snapshot()[0].ok
    if status == 401:
        assert [s.journey for s in gen.samples.snapshot()] == ["checkout", "login", "checkout"]
        verified.assert_called()


def test_401_with_auth_cap_exhausted_invalidates_token(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
) -> None:
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    session = gen.sessions[0]
    session.auth = AuthResponse.model_validate(AUTH)
    session.issued_at = load.time.monotonic()
    gen.auth_cap.admit()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        gen.journey("checkout", JourneyClient(client, gen.samples, settings))
    assert session.auth is None
    assert len(gen.samples.snapshot()) == 1


def test_request_timeout_and_invalid_json_are_secret_free_errors(
    settings: Settings,
    tmp_path: Path,
) -> None:
    buffer = SampleBuffer(tmp_path / "samples.jsonl")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive body", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        JourneyClient(client, buffer, settings).request("checkout", "POST", settings.order_url)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="secret"))
    ) as c:
        JourneyClient(c, buffer, settings).request("login", "POST", settings.auth_url, AuthResponse)
    assert [s.error for s in buffer.snapshot()] == ["timeout", "invalid response contract"]
    assert not any(s.ok for s in buffer.snapshot())
    assert "sensitive" not in buffer.path.read_text()


def test_runtime_worker_add_remove_and_cleanup(settings: Settings, tmp_path: Path) -> None:
    gen = generator(settings, tmp_path)
    started = threading.Event()

    def worker(stop: threading.Event) -> None:
        started.set()
        stop.wait()

    gen._worker = worker
    gen.set_workers(2)
    assert started.wait(1) and gen.worker_count == 2
    gen.add_workers(3)
    assert gen.worker_count == 5
    gen.remove_workers(4)
    assert gen.worker_count == 1
    threads = [t for t, _ in gen._workers + gen._retiring]
    gen.stop()
    assert gen.worker_count == 0 and all(not t.is_alive() for t in threads)
    with pytest.raises(ValueError):
        gen.set_workers(151)


def test_load_weights() -> None:
    assert load.JOURNEYS == ("homepage", "browse", "login", "checkout")
    assert load.WEIGHTS == (10, 60, 10, 20)


def test_failed_login_rechecks_and_never_blindly_retries(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
) -> None:
    gen = generator(settings, tmp_path)
    gen.prepare(1)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        http = JourneyClient(client, gen.samples, settings)
        assert gen.login(gen.sessions[0], http) is None
        assert gen.login(gen.sessions[0], http) is None
    assert len(gen.samples.snapshot()) == 1
    assert verified.call_count == 3  # prepare, immediately before login, after failure
    assert gen.sessions[0].auth is None


def test_real_workers_use_private_clients_and_flush_on_stop(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = threading.Event()
    clients: list[httpx.Client] = []

    def respond(request: httpx.Request) -> httpx.Response:
        event.set()
        return httpx.Response(200, text="frontend")

    def factory() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    gen = LoadGenerator(
        settings, Mock(), SampleBuffer(tmp_path / "samples.jsonl"), client_factory=factory
    )
    monkeypatch.setattr(load.random, "choices", lambda *a, **kw: ["homepage"])
    gen.set_workers(2)
    try:
        assert event.wait(2)
    finally:
        gen.stop()
    assert len(clients) == 2 and all(c.is_closed for c in clients)
    samples = read_jsonl(gen.samples.path, Sample)
    assert samples and all(s.ok and s.journey == "homepage" for s in samples)


def test_worker_cancellation_skips_next_requests(settings: Settings, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    gen = generator(settings, tmp_path)
    with gen.client_factory() as client:
        gen.journey("homepage", JourneyClient(client, gen.samples, settings), cancel=cancel)
    assert gen.samples.snapshot() == []


@pytest.mark.parametrize("rps", [0.5, 1.0])
def test_verified_login_reaches_configured_cap_with_auth_timeout(
    settings: Settings,
    tmp_path: Path,
    verified: Mock,
    monkeypatch: pytest.MonkeyPatch,
    rps: float,
) -> None:
    settings.login_rate_cap = rps
    now = [0.0]
    monkeypatch.setattr(load.time, "monotonic", lambda: now[0])
    gen = generator(settings, tmp_path)
    gen.auth_cap = RateCap(settings.login_rate_cap, lambda: now[0])
    gen.prepare(1)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=AUTH)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        http = JourneyClient(client, gen.samples, settings)
        for tick in range(20):
            now[0] = tick / 2
            gen.journey("login", http)
    assert len(seen) == (5 if rps == 0.5 else 10)
    assert all(r.extensions["timeout"]["read"] == settings.auth_timeout_s == 4 for r in seen)
    assert verified.call_count == len(seen) + 1  # preflight before each admitted login


def test_prepare_cancellation_skips_future_verification_and_http(settings, tmp_path) -> None:
    from unittest.mock import Mock

    from chaosai.load import LoadGenerator, SampleBuffer

    factory = Mock(side_effect=AssertionError("Cancelled preparation must not open HTTP"))
    generator = LoadGenerator(
        settings, Mock(), SampleBuffer(tmp_path / "samples.jsonl"), client_factory=factory
    )
    calls = []

    def verify(email: str) -> bool:
        calls.append(email)
        generator._shutdown.set()
        return True

    generator._verify = verify
    generator.prepare(10)
    assert len(calls) == 1
    factory.assert_not_called()
    generator.prepare(10)
    assert len(calls) == 1
