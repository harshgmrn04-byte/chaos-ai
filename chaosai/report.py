"""Self-contained fixture report regenerated from stored run artifacts."""

from pathlib import Path

from jinja2 import Environment

from chaosai.models import Baseline, ExperimentResult, Finding, Graph, Plan, RunMeta, Score, Summary
from chaosai.runs import read_json

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>ChaosPilot — {{ meta.run_id }}</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px;
color:#172c3b;background:#f3f6f8}section{background:white;padding:20px;margin:20px 0;
border:1px solid #d6dfe4;border-radius:8px}h1,h2{line-height:1.2}table{width:100%;
border-collapse:collapse}th,td{padding:8px;text-align:left;border-bottom:1px solid #ddd;
vertical-align:top;overflow-wrap:anywhere}.notice{background:#fff1cd;padding:16px}
.score{font-size:32px;font-weight:bold}code{overflow-wrap:anywhere}
</style></head><body>
<h1>ChaosPilot resilience report</h1>
<p class="notice"><strong>Phase 1 simulation — fixture data only.</strong>
No containers were inspected or changed. Metrics, verdicts, policy decisions, recovery and findings
are synthetic examples, not measured target behavior.
Safety controls and real scoring are pending.</p>
<p>Run {{ meta.run_id }} · project {{ meta.target_project }}<br>
Planner: {{ meta.planner_source }} · Analyzer: {{ meta.analyzer_source }}</p>
<section><h2>Summary</h2>
{% if scores %}<p class="score">{{ "%.1f"|format(summary.overall_score) }}/100 ·
{{ summary.band }}</p>
{% else %}<p>No experiments executed. No resilience score assessed.</p>{% endif %}
<p>{{ summary.narrative }}</p></section>
<section><h2>Application map</h2><p>{{ graph.app }} · {{ graph.edges|length }} dependency edges</p>
<table><tr><th>Node</th><th>Kind</th><th>Container</th><th>Host ports</th><th>Injectable</th></tr>
{% for n in graph.nodes %}<tr><td>{{ n.id }}</td><td>{{ n.kind }}</td>
<td>{{ n.container or '—' }}</td><td>{{ n.host_ports|join(', ') }}</td>
<td>{{ n.injectable }}</td></tr>{% endfor %}</table>
{% for f in graph.flows %}<p>{{ f.name }}: {{ f.path|join(' → ') }}</p>{% endfor %}</section>
<section><h2>Baseline (synthetic)</h2><table>
<tr><th>Journey</th><th>Samples</th><th>Errors</th><th>p95 ms</th><th>RPS</th></tr>
{% for name,s in baseline.journeys.items() %}<tr><td>{{ name }}</td><td>{{ s.n }}</td>
<td>{{ s.error_rate }}</td><td>{{ s.p95_ms }}</td><td>{{ "%.2f"|format(s.rps) }}</td></tr>
{% endfor %}</table></section>
<section><h2>Experiment plan and approvals</h2><table>
<tr><th>Experiment</th><th>Fault / target</th><th>Reason</th><th>Approval</th></tr>
{% for s in plan.root %}<tr><td>{{ s.id }}</td><td>{{ s.fault }} / {{ s.target }}</td>
<td>{{ s.reason }}</td><td>{{ approvals.get(s.id, 'not executed') }}</td></tr>{% endfor %}</table>
</section><section><h2>Results (synthetic)</h2>
{% for r in results %}<h3>{{ r.spec.id }} · {{ r.verdict }}</h3>
<p>Recovered: {{ r.recovered }} · Recovery seconds: {{ r.recovery_time_s }} ·
Rollback OK (simulated): {{ r.rollback_ok }}</p>
<p>Impacted journeys: {{ r.impacted_journeys|join(', ') or 'none' }}</p>
<p>Sample artifact: <code>{{ r.samples_path }}</code></p>
{% else %}<p>No fixture experiments executed (dry run, skip or quit).</p>{% endfor %}</section>
<section><h2>Scores (fixture penalties)</h2><table>
<tr><th>Experiment</th><th>Score</th><th>Error</th><th>Latency</th><th>Recovery</th>
<th>Blast</th><th>Abort</th></tr>
{% for s in scores %}<tr><td>{{ s.experiment_id }}</td><td>{{ s.score }}</td>
<td>{{ "%.2f"|format(s.error_pen) }}</td><td>{{ "%.2f"|format(s.latency_pen) }}</td>
<td>{{ "%.2f"|format(s.recovery_pen) }}</td><td>{{ "%.2f"|format(s.blast_pen) }}</td>
<td>{{ s.abort_pen }}</td></tr>{% endfor %}</table></section>
<section><h2>Findings and recommended fixes (examples)</h2>
{% for f in findings %}<h3>{{ f.severity }} · {{ f.summary }}</h3><p>{{ f.root_cause }}</p>
<ul>{% for e in f.evidence %}<li>{{ e }}</li>{% endfor %}</ul>
<ul>{% for fix in f.fixes %}<li>{{ fix }}</li>{% endfor %}</ul>{% endfor %}
<h3>Ranked weaknesses</h3><ol>{% for w in summary.ranked_weaknesses %}
<li>{{ w.severity }} · {{ w.title }} — {{ w.fix }}</li>{% endfor %}</ol>
<h3>Top fixes</h3><ul>{% for fix in summary.top_fixes %}<li>{{ fix }}</li>{% endfor %}</ul>
</section></body></html>
"""


def render_report(run_dir: Path) -> Path:
    meta = read_json(run_dir / "run.json", RunMeta)
    summary = read_json(run_dir / "summary.json", Summary)
    results = [
        read_json(path, ExperimentResult)
        for path in sorted((run_dir / "experiments").glob("*/result.json"))
    ]
    rendered = (
        Environment(autoescape=True)
        .from_string(HTML)
        .render(
            meta=meta,
            graph=read_json(run_dir / "graph.json", Graph),
            baseline=read_json(run_dir / "baseline.json", Baseline),
            plan=read_json(run_dir / "plan.json", Plan),
            results=results,
            findings=read_json(run_dir / "findings.json", list[Finding]),
            scores=read_json(run_dir / "scores.json", list[Score]),
            summary=summary,
            approvals={a.experiment_id: a.decision for a in meta.approvals},
        )
    )
    html_path = run_dir / "report.html"
    html_path.write_text(rendered, encoding="utf-8")
    markdown = (
        f"# ChaosPilot — {meta.run_id}\n\nPhase 1 simulation: fixture data only.\n\n"
        f"Band: {summary.band}.\n\n{summary.narrative}\n"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    return html_path
