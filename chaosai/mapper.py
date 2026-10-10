"""Fixture mapper, replaced in Phase 3."""

from chaosai.fixture_data import fixture
from chaosai.models import Graph


def map_target() -> Graph:
    return fixture("graph", Graph)
