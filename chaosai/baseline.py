"""Fixture baseline, replaced in Phase 4."""

from chaosai.fixture_data import fixture
from chaosai.models import Baseline, Graph


def measure_baseline(graph: Graph) -> Baseline:
    return fixture("baseline", Baseline)
