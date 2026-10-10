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


@pytest.mark.integration
def test_live_quick_baseline() -> None:
    """Human-run: ~50s plus preflight; creates capped orders and notifications."""
    from chaosai.baseline import measure_baseline
    from chaosai.mapper import map_target
    from chaosai.models import Baseline
    from chaosai.runs import create_run, read_json

    settings = load_config()
    run_dir = create_run(settings.runs_dir)
    result = measure_baseline(map_target(settings), settings, run_dir, quick=True)
    assert result.window_s == 40
    assert all(result.journeys[name].n > 0 for name in ("homepage", "browse", "login", "checkout"))
    assert result.containers
    assert "notifications" in result.queues
    assert read_json(run_dir / "baseline.json", Baseline) == result
    assert read_json(settings.project_root / ".chaosai" / "baseline.json", Baseline) == result
    # Stability is a measured property; inspect/repeat instead of asserting a live outcome.
