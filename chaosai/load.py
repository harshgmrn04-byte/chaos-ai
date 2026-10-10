"""Threaded HTTP journeys with verified credentials and bounded write traffic."""

import logging
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

import docker
import httpx
from pydantic import TypeAdapter, ValidationError

from chaosai.config import Settings
from chaosai.models import AuthResponse, Category, OrderResponse, Product, ProductPage, Sample
from chaosai.runs import append_jsonl
from chaosai.target import TargetError, service_container
from chaosai.target_api import order_body, product_page
from chaosai.target_auth import require_credentials

logger = logging.getLogger(__name__)
T = TypeVar("T")
JOURNEYS = ("homepage", "browse", "login", "checkout")
WEIGHTS = (10, 60, 10, 20)


class SampleBuffer:
    """Serialize both memory snapshots and append-only disk writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._samples: list[Sample] = []
        self._since = float("-inf")
        self._until = float("inf")

    def begin_window(self, t0: float, t1: float = float("inf")) -> None:
        with self._lock:
            self._since, self._until = t0, t1
            self._samples.clear()

    def append(self, sample: Sample) -> None:
        with self._lock:
            if self._since <= sample.t < self._until:
                append_jsonl(self.path, [sample])
                self._samples.append(sample)

    def snapshot(self) -> list[Sample]:
        with self._lock:
            return list(self._samples)


class RateCap:
    """Nonblocking admission: no burst, shared by every worker and retry."""

    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic) -> None:
        if rps <= 0:
            raise ValueError("Rate must be positive")
        self._interval = 1 / rps
        self._clock = clock
        self._next = float("-inf")
        self._lock = threading.Lock()

    def acquire(self, cancel: threading.Event) -> bool:
        while not cancel.is_set():
            if self.admit():
                return True
            cancel.wait(min(self._interval, 0.1))
        return False

    def admit(self) -> bool:
        with self._lock:
            now = self._clock()
            if now < self._next:
                return False
            self._next = now + self._interval
            return True


class Session:
    """One locked token cache per account, including across added workers."""

    def __init__(self, email: str) -> None:
        self.email = email
        self.auth: AuthResponse | None = None
        self.issued_at = 0.0
        self.disabled = False
        self.lock = threading.Lock()


class JourneyClient:
    def __init__(self, client: httpx.Client, samples: SampleBuffer, settings: Settings) -> None:
        self.client = client
        self.samples = samples
        self.settings = settings

    def request(
        self,
        journey: str,
        method: str,
        url: str,
        shape: type[T] | None = None,
        *,
        expected: int = 200,
        token: str | None = None,
        body: dict[str, object] | None = None,
        params: dict[str, int] | None = None,
    ) -> tuple[int | None, T | None]:
        t0, started = time.time(), time.monotonic()
        status, error, value = None, None, None
        try:
            response = self.client.request(
                method,
                url,
                json=body,
                params=params,
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=self.settings.load_timeout_s,
            )
            status = response.status_code
            if status != expected:
                error = f"HTTP {status}"
            elif shape is not None:
                value = TypeAdapter(shape).validate_python(response.json())
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.HTTPError:
            error = "transport error"
        except (ValueError, ValidationError):
            error = "invalid response contract"
        self.samples.append(
            Sample(
                t=t0,
                journey=journey,
                ok=error is None,
                status=status,
                latency_ms=(time.monotonic() - started) * 1000,
                error=error,
            )
        )
        return status, value


class LoadGenerator:
    """Caller owns Docker; each worker owns and closes its HTTP client."""

    def __init__(
        self,
        settings: Settings,
        docker_client: docker.DockerClient,
        samples: SampleBuffer,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        if settings.think_max_s < settings.think_min_s:
            raise TargetError("Think-time maximum must be at least its minimum.")
        self.settings = settings
        self.docker_client = docker_client
        self.samples = samples
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=settings.load_timeout_s, trust_env=False)
        )
        self.checkout_cap = RateCap(settings.checkout_rps)
        self.auth_cap = RateCap(settings.auth_rps)
        self.sessions: list[Session] = []
        self.products: list[Product] = []
        self._workers: list[tuple[threading.Thread, threading.Event]] = []
        self._retiring: list[tuple[threading.Thread, threading.Event]] = []
        self._lock = threading.Lock()
        self.failed = threading.Event()
        self._shutdown = threading.Event()

    def _verify(self, email: str) -> bool:
        if not self.settings.seed_password.get_secret_value():
            logger.warning("Skipping %s: SEED_PASSWORD is unavailable; no login sent.", email)
            return False
        try:
            postgres = service_container(self.docker_client, self.settings, "postgres")
            require_credentials(
                self.docker_client, postgres, self.settings, email, self.settings.seed_password
            )
            return True
        except (TargetError, docker.errors.DockerException, OSError):
            logger.warning(
                "Skipping %s: account/password could not be verified; check seed and "
                "SEED_PASSWORD. No login sent.",
                email,
            )
            return False

    def prepare(self, users: int) -> None:
        if not 1 <= users <= 150:
            raise TargetError("Load workers must be between 1 and 150.")
        for index in range(1, users + 1):
            email = self.settings.load_email_template.format(index=index)
            if self._verify(email):
                self.sessions.append(Session(email))
        if not self.sessions:
            logger.warning("No verified load users; login and checkout will be skipped.")
        with self.client_factory() as client:
            page = product_page(client, self.settings)
        self.products = [p for p in page.products if p.stock >= 1]
        if not self.products:
            raise TargetError("No in-stock catalog products; run chaosai seed before load.")

    def login(self, session: Session, http: JourneyClient, *, force: bool = False) -> str | None:
        with session.lock:
            if session.disabled:
                return None
            if (
                not force
                and session.auth is not None
                and time.monotonic() - session.issued_at < self.settings.token_refresh_s
            ):
                return session.auth.accessToken
            # Re-login mints a fresh 15-minute JWT. Avoid refresh-token calls: another
            # login replaces the stored refresh token and invalid refresh errors can hang.
            if not self.auth_cap.admit():
                return None
            session.auth = None
            if not self._verify(session.email):
                session.disabled = True
                return None
            _, auth = http.request(
                "login",
                "POST",
                self.settings.auth_url + "/login",
                AuthResponse,
                body={
                    "email": session.email,
                    "password": self.settings.seed_password.get_secret_value(),
                },
            )
            if auth is None or auth.user.email != session.email:
                # Never retry a failed login blindly, even when transport dropped.
                if not self._verify(session.email):
                    session.disabled = True
                return None
            session.auth = auth
            session.issued_at = time.monotonic()
            return auth.accessToken

    def journey(
        self,
        name: str,
        http: JourneyClient,
        *,
        cancel: threading.Event | None = None,
    ) -> None:
        cancel = cancel or self._shutdown
        if cancel.is_set():
            return
        if name == "homepage":
            http.request(name, "GET", self.settings.frontend_url.rstrip("/") + "/")
        elif name == "browse":
            _, page = http.request(
                name,
                "GET",
                self.settings.product_url + "/products",
                ProductPage,
                params={"page": 1, "limit": 12},
            )
            if page is not None and page.products and not cancel.is_set():
                product = random.choice(page.products)
                http.request(
                    name,
                    "GET",
                    self.settings.product_url + "/products/" + quote(product.id, safe=""),
                    Product,
                )
            if not cancel.is_set():
                http.request(name, "GET", self.settings.product_url + "/categories", list[Category])
        elif name in ("login", "checkout"):
            active = [s for s in self.sessions if not s.disabled]
            if not active:
                return
            # Prefer a usable session for checkout; login still samples all seed users.
            cached = [s for s in active if s.auth is not None]
            session = random.choice(cached if name == "checkout" and cached else active)
            token = self.login(session, http, force=name == "login")
            if (
                name != "checkout"
                or token is None
                or cancel.is_set()
                or not self.checkout_cap.admit()
            ):
                return
            product = random.choice(self.products)
            status, _ = http.request(
                name,
                "POST",
                self.settings.order_url + "/orders",
                OrderResponse,
                expected=201,
                token=token,
                body=order_body(product),
            )
            if status == 401 and not cancel.is_set():
                with session.lock:
                    session.auth = None
                # Middleware rejects before order persistence; only this status is retryable.
                token = self.login(session, http, force=True)
                if token is not None and self.checkout_cap.acquire(cancel):
                    http.request(
                        name,
                        "POST",
                        self.settings.order_url + "/orders",
                        OrderResponse,
                        expected=201,
                        token=token,
                        body=order_body(product),
                    )

    def _worker(self, stop: threading.Event) -> None:
        try:
            with self.client_factory() as client:
                http = JourneyClient(client, self.samples, self.settings)
                while not stop.is_set():
                    self.journey(
                        random.choices(JOURNEYS, weights=WEIGHTS, k=1)[0], http, cancel=stop
                    )
                    stop.wait(random.uniform(self.settings.think_min_s, self.settings.think_max_s))
        except Exception:
            # Never expose HTTP bodies, credential helper output or exception payloads.
            logger.error("Load worker stopped unexpectedly; samples may be incomplete.")
            self.failed.set()

    @property
    def worker_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def set_workers(self, count: int) -> None:
        if not 0 <= count <= 150:
            raise ValueError("Worker count must be between 0 and 150")
        with self._lock:
            while len(self._workers) > count:
                worker = self._workers.pop()
                worker[1].set()
                self._retiring.append(worker)
            # Count retiring threads against the hard limit until they actually exit.
            self._retiring = [w for w in self._retiring if w[0].is_alive()]
            if count + len(self._retiring) > 150:
                raise ValueError("Wait for retiring workers before adding more (limit 150)")
            while len(self._workers) < count:
                stop = threading.Event()
                thread = threading.Thread(target=self._worker, args=(stop,), daemon=True)
                self._workers.append((thread, stop))
                thread.start()

    def add_workers(self, count: int) -> None:
        if count < 0:
            raise ValueError("Worker increment must be nonnegative")
        self.set_workers(self.worker_count + count)

    def remove_workers(self, count: int) -> None:
        if count < 0:
            raise ValueError("Worker decrement must be nonnegative")
        self.set_workers(max(0, self.worker_count - count))

    def stop(self) -> None:
        self._shutdown.set()
        self.set_workers(0)
        for thread, _ in self._retiring:
            thread.join()
        self._retiring.clear()
