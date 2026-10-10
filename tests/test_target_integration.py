"""Human-run live checks; smoke creates one pending order and can trigger Resend."""

import httpx
import pytest

from chaosai.config import load_config
from chaosai.doctor import status_checks, target_status
from chaosai.smoke import smoke_target


@pytest.mark.integration
def test_live_target_ready() -> None:
    settings = load_config()
    checks = status_checks(target_status(settings), settings)
    assert all(check.passed for check in checks), [c.name for c in checks if not c.passed]


@pytest.mark.integration
def test_live_checkout() -> None:
    settings = load_config()
    with httpx.Client(timeout=settings.request_timeout_s, trust_env=False) as client:
        result = smoke_target(settings, client)
    assert result.order_id
    # Notifications are best-effort and do not turn successful checkout into test failure.
