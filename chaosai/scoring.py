"""Fixture scores; real per-result penalties arrive in Phase 9."""

from statistics import mean

from chaosai.fixture_data import fixture
from chaosai.models import ExperimentResult, Score, Summary


def score_results(results: list[ExperimentResult]) -> tuple[list[Score], Summary]:
    completed = {result.spec.id for result in results}
    scores = [s for s in fixture("scores", list[Score]) if s.experiment_id in completed]
    summary = fixture("summary", Summary)
    summary.ranked_weaknesses = [
        w for w in summary.ranked_weaknesses if set(w.experiments) <= completed
    ]
    summary.top_fixes = [w.fix for w in summary.ranked_weaknesses]
    if scores:
        summary.overall_score = mean(s.score for s in scores)
        overall = summary.overall_score
        summary.band = (
            "resilient"
            if overall >= 85
            else "acceptable"
            if overall >= 70
            else "weak"
            if overall >= 50
            else "fragile"
        )
    else:
        summary.overall_score = 0
        summary.band = "not assessed"
        summary.narrative = "No fixture experiments executed; no resilience assessment available."
    return scores, summary
