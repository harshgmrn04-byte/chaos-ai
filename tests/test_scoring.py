"""Formula boundaries, evidence exclusion and model-independent ranking."""

import pytest

from chaosai.fixture_data import fixture
from chaosai.models import Baseline, ExperimentResult, Finding
from chaosai.scoring import band, held_up, ranked_summary, score_result, score_results, severity


def healthy() -> tuple[ExperimentResult, Baseline]:
    baseline = fixture("baseline", Baseline)
    result = fixture("results", list[ExperimentResult])[2]
    result.during = {n: s.model_copy(deep=True) for n, s in baseline.journeys.items()}
    result.recovered, result.recovery_time_s = True, 5
    result.impacted_journeys, result.aborted = [], False
    return result, baseline


@pytest.mark.parametrize("error,penalty", [(0, 0), (0.1, 8), (0.5, 40), (1, 40)])
def test_error_penalty(error: float, penalty: float) -> None:
    r, b = healthy()
    r.during["browse"].error_rate = error
    s = score_result(r, b)
    assert s.error_pen == pytest.approx(penalty)
    assert s.latency_pen == s.blast_pen == s.recovery_pen == s.abort_pen == 0


def test_error_delta_and_improvement() -> None:
    r, b = healthy()
    b.journeys["browse"].error_rate = 0.4
    r.during["browse"].error_rate = 0.5
    assert score_result(r, b).error_pen == pytest.approx(8)
    r.during["browse"].error_rate = 0.1
    assert score_result(r, b).error_pen == 0


@pytest.mark.parametrize("ratio,penalty", [(0, 0), (1, 0), (2, 5), (5, 20), (10, 20)])
def test_latency_penalty(ratio: float, penalty: float) -> None:
    r, b = healthy()
    r.during["browse"].p95_ms *= ratio
    assert score_result(r, b).latency_pen == penalty


@pytest.mark.parametrize("during,penalty", [(0, 0), (1, 20)])
def test_zero_baseline_p95(during: float, penalty: float) -> None:
    r, b = healthy()
    b.journeys["browse"].p95_ms = 0
    r.during["browse"].p95_ms = during
    assert score_result(r, b).latency_pen == penalty


@pytest.mark.parametrize(
    "recovered,seconds,penalty",
    [
        (True, 0, 0),
        (True, 10, 0),
        (True, 12, 5),
        (True, 60, 25),
        (True, 120, 25),
        (False, None, 25),
        (False, 2, 25),
        (True, None, 25),
    ],
)
def test_recovery_penalty(recovered: bool, seconds: float | None, penalty: float) -> None:
    r, b = healthy()
    r.recovered, r.recovery_time_s = recovered, seconds
    assert score_result(r, b).recovery_pen == penalty


def test_blast_deduplicates_and_excludes_all_unknown() -> None:
    r, b = healthy()
    r.impacted_journeys = ["browse", "browse", "all", "unknown"]
    assert score_result(r, b).blast_pen == pytest.approx(10 / 3)


@pytest.mark.parametrize("watchdog,expected", [(True, 5), (False, 0)])
def test_watchdog_distinct_from_operator_abort(watchdog: bool, expected: float) -> None:
    r, b = healthy()
    r.aborted, r.watchdog_aborted = True, watchdog
    assert score_result(r, b).abort_pen == expected


@pytest.mark.parametrize(
    "reason,penalty",
    [
        ("all: error rate exceeds 0.5.", 5),
        ("browse: p95 exceeds multiple and absolute floor.", 5),
        ("Blast radius: 4 non-target containers left running.", 5),
        ("Kill switch requested.", 0),
        ("Interrupted by signal.", 0),
        ("Watchdog evaluation unavailable.", 0),
    ],
)
def test_legacy_abort_classification(reason: str, penalty: float) -> None:
    r, b = healthy()
    r.aborted, r.abort_reason, r.watchdog_aborted = True, reason, None
    assert score_result(r, b).abort_pen == penalty


def test_max_penalties_zero_score_and_sparse_observations() -> None:
    r, b = healthy()
    for name, stats in r.during.items():
        stats.error_rate, stats.p95_ms, stats.n = 1, b.journeys[name].p95_ms * 10, 1
    r.recovered, r.aborted, r.watchdog_aborted = False, True, True
    r.impacted_journeys = list(b.journeys)
    assert score_result(r, b).score == 0
    r, b = healthy()
    assert score_result(r, b).score == 100


@pytest.mark.parametrize("kind", ["skipped", "empty", "zero_samples", "unknown"])
def test_unmeasured_never_scores(kind: str) -> None:
    r, b = healthy()
    if kind == "skipped":
        r.skipped = True
    elif kind == "empty":
        r.during = {}
    elif kind == "zero_samples":
        for stats in r.during.values():
            stats.n = 0
    else:
        r.during = {"unknown": next(iter(r.during.values()))}
    assert score_result(r, b) is None
    scores, summary = score_results([r], b)
    assert scores == [] and summary.band == "not assessed"


@pytest.mark.parametrize(
    "value,expected",
    [
        (100, "resilient"),
        (85, "resilient"),
        (84.99, "acceptable"),
        (70, "acceptable"),
        (69.99, "weak"),
        (50, "weak"),
        (49.99, "fragile"),
    ],
)
def test_bands(value: float, expected: str) -> None:
    assert band(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(29, "critical"), (30, "high"), (59, "high"), (60, "medium"), (79, "medium"), (80, "low")],
)
def test_severity(value: int, expected: str) -> None:
    r, b = healthy()
    score = score_result(r, b).model_copy(update={"score": value})
    assert severity(score, r) == expected
    r.aborted, r.impacted_journeys = True, ["browse", "login"]
    assert severity(score, r) == "critical"


def test_formula_on_original_fixtures_and_held_split() -> None:
    results, baseline = fixture("results", list[ExperimentResult]), fixture("baseline", Baseline)
    scores, summary = score_results(results, baseline)
    assert [s.score for s in scores] == [53, 17, 95]
    assert summary.overall_score == 55
    findings = fixture("findings", list[Finding])
    summary = ranked_summary(results, scores, findings, summary)
    assert [w.experiments[0] for w in summary.ranked_weaknesses] == ["postgres-pause", "redis-stop"]
    assert [w.severity for w in summary.ranked_weaknesses] == ["critical", "high"]
    assert held_up(scores[2], results[2])
    assert not held_up(scores[0], results[0])
    assert len(summary.top_fixes) == 2


def test_overall_mean_excludes_skips_and_sorts_rank() -> None:
    r, b = healthy()
    r.spec.rank = 1
    second = r.model_copy(deep=True)
    second.spec.id, second.spec.rank, second.recovered = "second", 2, False
    skipped = r.model_copy(deep=True)
    skipped.skipped = True
    scores, summary = score_results([second, skipped, r], b)
    assert [s.score for s in scores] == [100, 75]
    assert summary.overall_score == 87.5


@pytest.mark.parametrize(
    "value,verdict,expected",
    [(79, "confirmed", False), (80, "confirmed", True), (100, "inconclusive", False)],
)
def test_held_split_boundaries(value: int, verdict: str, expected: bool) -> None:
    result, baseline = healthy()
    result.verdict = verdict
    score = score_result(result, baseline).model_copy(update={"score": value})
    assert held_up(score, result) == expected


def test_same_severity_ranks_lowest_score_first() -> None:
    first, baseline = healthy()
    second = first.model_copy(deep=True)
    second.spec.id, second.spec.rank = "second", 10
    results = [first, second]
    scores, summary = score_results(results, baseline)
    scores[0].score, scores[1].score = 59, 31
    findings = [
        Finding(
            experiment_id=r.spec.id,
            summary=r.spec.id,
            root_cause="Unproven",
            severity="low",
            evidence=["Recorded"],
            fixes=["Test recovery"],
        )
        for r in results
    ]
    ranked = ranked_summary(results, scores, findings, summary)
    assert [w.experiments[0] for w in ranked.ranked_weaknesses] == ["second", first.spec.id]
