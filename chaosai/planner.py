"""Fixture planning with a deterministic unavailable-provider fallback."""

from pathlib import Path

from chaosai.fixture_data import fixture
from chaosai.llm import LLMUnavailable, complete_json
from chaosai.models import Baseline, Graph, Plan


def plan_experiments(
    graph: Graph, baseline: Baseline, *, provider: str, cache_dir: Path
) -> tuple[Plan, str]:
    try:
        plan = complete_json(
            "planner",
            "Return fixture experiment specifications.",
            graph.model_dump_json() + "\n" + baseline.model_dump_json(),
            Plan,
            provider=provider,
            cache_dir=cache_dir,
        )
        return plan, "mock"
    except LLMUnavailable:
        return fixture("plan", Plan), "heuristic"
