"""Pure resilience penalties and deterministic ranking; no model-assigned scores."""

from statistics import mean

from chaosai.models import Baseline, ExperimentResult, Finding, Score, Severity, Summary, Weakness


def band(score: float) -> str:
    return (
        "resilient"
        if score >= 85
        else "acceptable"
        if score >= 70
        else "weak"
        if score >= 50
        else "fragile"
    )


def score_result(result: ExperimentResult, baseline: Baseline) -> Score | None:
    """No score for skips or absent during evidence; sparse scores are descriptive."""
    pairs = [
        (stats, baseline.journeys[name])
        for name, stats in result.during.items()
        if name in baseline.journeys and stats.n > 0 and baseline.journeys[name].n > 0
    ]
    if result.skipped or not pairs:
        return None
    error_delta = max(0, max(s.error_rate - b.error_rate for s, b in pairs))
    ratios = [s.p95_ms / b.p95_ms if b.p95_ms > 0 else (5 if s.p95_ms > 0 else 1) for s, b in pairs]
    error_pen = 40 * min(1, error_delta / 0.5)
    latency_pen = 20 * min(1, max(0, max(ratios) - 1) / 4)
    recovery = result.recovery_time_s
    recovery_pen = (
        25.0
        if not result.recovered or recovery is None or recovery < 0
        else 0.0
        if recovery <= 10
        else 25 * min(1, recovery / 60)
    )
    names = {name for name in baseline.journeys if name != "all"}
    blast_pen = 10 * len(names.intersection(result.impacted_journeys)) / len(names) if names else 0
    # A records metric Watchdog aborts, not q, KILL, signals or transport failures.
    reason = result.abort_reason or ""
    watchdog_abort = result.aborted and (
        result.watchdog_aborted is True
        or (
            result.watchdog_aborted is None
            and (
                reason.startswith(("Blast radius:", "Synthetic checkout error rate"))
                or ": error rate exceeds " in reason
                or reason.endswith(": p95 exceeds multiple and absolute floor.")
            )
        )
    )
    abort_pen = 5.0 if watchdog_abort else 0.0
    value = max(0, min(100, 100 - error_pen - latency_pen - recovery_pen - blast_pen - abort_pen))
    return Score(
        experiment_id=result.spec.id,
        score=round(value),
        error_pen=error_pen,
        latency_pen=latency_pen,
        recovery_pen=recovery_pen,
        blast_pen=blast_pen,
        abort_pen=abort_pen,
    )


def severity(score: Score, result: ExperimentResult) -> Severity:
    if score.score < 30 or (result.aborted and len(set(result.impacted_journeys)) >= 2):
        return "critical"
    return "high" if score.score < 60 else "medium" if score.score < 80 else "low"


def held_up(score: Score, result: ExperimentResult) -> bool:
    return result.verdict == "confirmed" and score.score >= 80


def score_results(
    results: list[ExperimentResult], baseline: Baseline
) -> tuple[list[Score], Summary]:
    scores = [
        s
        for r in sorted(results, key=lambda r: r.spec.rank)
        if (s := score_result(r, baseline)) is not None
    ]
    overall = mean(s.score for s in scores) if scores else 0
    return scores, Summary(
        overall_score=overall,
        band=band(overall) if scores else "not assessed",
        ranked_weaknesses=[],
        top_fixes=[],
        narrative="Scores describe the recorded experiments; untested dependencies remain unknown."
        if scores
        else "No resilience score assessed: no injected-fault measurement available.",
    )


def ranked_summary(
    results: list[ExperimentResult], scores: list[Score], findings: list[Finding], summary: Summary
) -> Summary:
    by_result = {r.spec.id: r for r in results}
    by_finding = {f.experiment_id: f for f in findings}
    levels = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    weak = sorted(
        [
            s
            for s in scores
            if s.experiment_id in by_finding and not held_up(s, by_result[s.experiment_id])
        ],
        key=lambda s: (
            levels[severity(s, by_result[s.experiment_id])],
            s.score,
            by_result[s.experiment_id].spec.rank,
        ),
    )
    summary = summary.model_copy(deep=True)
    summary.overall_score = mean(s.score for s in scores) if scores else 0
    summary.band = band(summary.overall_score) if scores else "not assessed"
    summary.ranked_weaknesses = [
        Weakness(
            title=by_finding[s.experiment_id].summary,
            severity=severity(s, by_result[s.experiment_id]),
            experiments=[s.experiment_id],
            fix=next(iter(by_finding[s.experiment_id].fixes), "Collect more evidence."),
        )
        for s in weak
    ]
    summary.top_fixes = list(dict.fromkeys(w.fix for w in summary.ranked_weaknesses))
    return summary
