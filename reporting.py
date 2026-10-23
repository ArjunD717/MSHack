"""Interactive charts and portable, offline reports for the validated allocation."""

import html
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portfolio import ClientPolicy, align_prices, candidate_rows, portfolio_path


COLORS = {"CASH": "#94a3b8", "BIL": "#0d9488", "SHY": "#2563eb", "IEF": "#a855f7"}
STATUS_LABELS = {"feasible": "An allocation passes the prototype checks",
                 "unverified": "Evidence is insufficient to validate an allocation",
                 "infeasible": "No feasible allocation under these assumptions"}


def focus_candidate(result):
    return result["selected"] or result["provisional"]


def format_check_value(name, value):
    if value is None:
        return "Unverified"
    if not isinstance(value, (int, float)):
        return str(value)
    if "USD" in name or "cash" in name.lower() or name == "Budget and weights":
        return f"${value:,.2f}"
    if "(%)" in name or name in ("Trading spread", "Income evidence"):
        return f"{value:.3f}%"
    if name == "Duration evidence":
        return f"{value:.2f} years"
    return f"{value:,.0f}"


def check_rows(checks):
    return [{"Check": c["name"], "Status": c["status"].upper(),
             "Measured": format_check_value(c["name"], c["measured"]),
             "Requirement": format_check_value(c["name"], c["limit"]), "Explanation": c["detail"]} for c in checks]


def comparison_table(portfolios):
    """Present measurements as readable rows, retaining full precision in JSON/CSV."""
    labels = [("Cash accessible now", "cash", "USD"),
              ("Annual income run-rate", "income_run_rate_usd", "USD"),
              ("Historical annualized volatility", "annualized_volatility_pct", "%"),
              ("Historical maximum drawdown", "max_drawdown_pct", "%"),
              ("Estimated upward rate-shock loss", "rate_up_loss_usd", "USD"),
              ("Accessible funds under claims stress", "stressed_available_usd", "USD")]
    rows = [{"Measurement": "Checks", **{label: c["status"].upper() for label, c in portfolios}}]
    for name, key, unit in labels:
        row = {"Measurement": name}
        for label, candidate in portfolios:
            value = candidate["amounts_usd"]["CASH"] if key == "cash" else candidate["metrics"][key]
            row[label] = "Unverified" if value is None else (f"${value:,.2f}" if unit == "USD" else f"{value:.3f}%")
        rows.append(row)
    return pd.DataFrame(rows)


def build_figures(result, prices):
    policy = ClientPolicy(**result["policy"])
    table = align_prices(prices, result["market_source"]["tickers"])
    focus = focus_candidate(result)
    portfolios = []
    if focus:
        portfolios.append(("Selected" if result["selected"] else "Unverified illustration", focus))
    if result["baseline"]:
        portfolios.append(("Baseline", result["baseline"]))

    allocation = go.Figure()
    for asset in ["CASH", *list(table.columns)]:
        values = [c["amounts_usd"][asset] for _, c in portfolios]
        allocation.add_bar(name=asset, y=[label for label, _ in portfolios], x=values, orientation="h",
                           marker_color=COLORS.get(asset), text=[f"${v:,.0f}" if v else "" for v in values],
                           hovertemplate=f"{asset}: $%{{x:,.2f}}<extra></extra>")
    allocation.update_layout(barmode="stack", title="Where the $100,000 goes" if policy.budget_usd == 100000 else "Where the money goes",
                             xaxis_title="USD allocated", height=260)

    history = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
                            subplot_titles=("Historical portfolio value — no claims withdrawn", "Decline from the previous portfolio peak"))
    history_rows = pd.DataFrame(index=table.index)
    for index, (label, candidate) in enumerate(portfolios):
        path = portfolio_path(table, candidate["weights"], policy)
        history_rows[f"{label} value USD"] = path * policy.budget_usd
        history_rows[f"{label} drawdown pct"] = (path / path.cummax() - 1) * 100
        color = "#0d9488" if index == 0 and label != "Baseline" else "#94a3b8"
        history.add_trace(go.Scatter(x=table.index, y=path * policy.budget_usd, mode="lines", name=label,
                                    line={"color": color}, hovertemplate="$%{y:,.2f}<extra>%{fullData.name}</extra>"), row=1, col=1)
        history.add_trace(go.Scatter(x=table.index, y=(path / path.cummax() - 1) * 100, mode="lines", name=label,
                                    showlegend=False, line={"color": color}, hovertemplate="%{y:.2f}%<extra>%{fullData.name}</extra>"), row=2, col=1)
    history.update_layout(height=620, hovermode="x unified")
    history.update_yaxes(title_text="USD", row=1, col=1)
    history.update_yaxes(title_text="Drawdown (%)", row=2, col=1)

    scenarios = go.Figure()
    for label, candidate in portfolios:
        metrics = candidate["metrics"]
        loss = metrics["rate_up_loss_usd"]
        scenarios.add_bar(name=label, x=["Rates rise", "Rates fall"],
                          y=[-loss if loss is not None else None, loss],
                          hovertemplate="%{x}: $%{y:,.2f}<extra>%{fullData.name}</extra>")
    scenarios.update_layout(title=f"Approximate market-value change for ±{policy.rate_shock_bps:g} bps",
                            yaxis_title="USD change (negative = loss)", barmode="group", height=350)
    scenarios.add_hline(y=-policy.maximum_rate_loss_usd, line_dash="dot", line_color="#e11d48",
                        annotation_text="Rate-loss limit")

    frontier = go.Figure()
    audit = pd.DataFrame(result["candidate_audit"])
    for status, color in (("fail", "#cbd5e1"), ("unverified", "#f59e0b"), ("pass", "#0d9488")):
        subset = audit.loc[audit["status"] == status]
        frontier.add_scatter(x=subset["annualized_volatility_pct"], y=subset["income_run_rate_usd"],
                             mode="markers", name=status.title(), marker={"size": 5, "color": color, "opacity": 0.65},
                             hovertemplate="Volatility: %{x:.2f}%<br>Income run-rate: $%{y:,.2f}<extra>%{fullData.name}</extra>")
    if focus:
        frontier.add_scatter(x=[focus["metrics"]["annualized_volatility_pct"]],
                             y=[focus["metrics"]["income_run_rate_usd"]], mode="markers", name="Shown allocation",
                             marker={"symbol": "star", "size": 16, "color": "#2563eb"})
    frontier.update_layout(title="Candidate allocations: income versus historical variability", height=380,
                           xaxis_title="Historical annualized volatility (%)", yaxis_title="Current annual income run-rate (USD)")
    for figure in (allocation, history, scenarios, frontier):
        figure.update_layout(template="plotly_white", font={"family": "Arial", "color": "#172033"},
                             margin={"l": 45, "r": 30, "t": 60, "b": 40}, legend={"orientation": "h"})
    history_rows.index.name = "date"
    return {"allocation": allocation, "history": history, "scenarios": scenarios, "frontier": frontier}, history_rows


def deterministic_report(result):
    policy = result["policy"]
    search = result["search"]
    focus = focus_candidate(result)
    lines = ["# Claims reserve allocation — Prototype 1", "", STATUS_LABELS[result["status"]] + ".", "",
             f"Budget: **${policy['budget_usd']:,.2f} USD**. Horizon: **{policy['horizon_months']} months**.",
             "All thresholds below are editable demo assumptions.", "",
             f"Evaluated {search['total']:,} allocations: {search['passing']:,} pass, {search['failed']:,} fail, {search['unverified']:,} unverified.", ""]
    if focus:
        lines += ["## Allocation" if result["selected"] else "## Unverified illustration — not a validated allocation", ""]
        lines += [f"- {asset}: **${amount:,.2f}** ({focus['weights'][asset] * 100:.2f}%)" for asset, amount in focus["amounts_usd"].items() if amount]
        metrics = focus["metrics"]
        lines += ["", "## Measurements", "",
                  f"- Historical annualized volatility: {metrics['annualized_volatility_pct']:.3f}%." if metrics["annualized_volatility_pct"] is not None else "- Historical annualized volatility: unavailable.",
                  f"- Historical maximum drawdown: {metrics['max_drawdown_pct']:.3f}% decline."]
        if metrics["rate_up_loss_usd"] is not None:
            lines.append(f"- Estimated loss for +{policy['rate_shock_bps']:g} bps: ${metrics['rate_up_loss_usd']:,.2f}.")
        if metrics["income_run_rate_usd"] is not None:
            lines.append(f"- Current annual income run-rate: ${metrics['income_run_rate_usd']:,.2f}; not a forecast or guaranteed payout.")
            lines.append(f"- At an unchanged run-rate for {policy['horizon_months']} months: ${metrics['income_run_rate_usd'] * policy['horizon_months'] / 12:,.2f}, before claims withdrawals and taxes.")
        if metrics["stressed_available_usd"] is not None:
            lines.append(f"- Modelled funds accessible by day {policy['claim_due_days']} after rate/spread stress: ${metrics['stressed_available_usd']:,.2f}, against a ${policy['stressed_claim_usd']:,.2f} total claim.")
        lines += ["", "## Checks", "", "| Check | Result | Measured | Requirement |", "|---|---|---|---|"]
        for item in focus["checks"]:
            lines.append(f"| {item['name']} | {item['status'].upper()} | {format_check_value(item['name'], item['measured'])} | {format_check_value(item['name'], item['limit'])} |")
    else:
        lines += ["No allocation passes every required check. Review the binding constraints; the tool has not relaxed them.", ""]
    lines += ["", "## Baseline comparison", ""]
    baseline = result["baseline"]
    if baseline:
        lines.append(f"Baseline = required cash reserve plus the remainder in {policy['baseline_ticker']}. Baseline status: **{baseline['status']}**.")
        advantage = result["income_run_rate_advantage_over_baseline_usd"]
        if advantage is not None:
            lines.append(f"Selected minus baseline annual income run-rate: **${advantage:,.2f}**. This is not a future performance advantage.")
    else:
        lines.append("Baseline unavailable: its ticker is absent or the reserve exceeds the budget.")
    lines += ["", "## Binding constraints across the search", ""]
    lines += [f"- {name}: {count:,} allocations failed." for name, count in search["failure_counts"].items()]
    lines += [f"- {name}: {count:,} allocations unverified." for name, count in search["unverified_counts"].items()]
    lines += ["", "## Method and limitations", ""]
    lines += [f"- {description}" for description in result["methodology"].values()]
    lines += [f"- {description}" for description in result["limitations"]]
    lines += ["", "## Sources", "", f"Historical price window: {result['common_history']['start']} through {result['common_history']['end']}."]
    for fund in result["fund_evidence"]:
        if fund["source_url"]:
            lines.append(f"- [{fund['ticker']} issuer]({fund['source_url']}); evidence status: {fund['status']}.")
    return "\n".join(lines) + "\n"


def render_html(result, figures):
    title = STATUS_LABELS[result["status"]]
    focus = focus_candidate(result)
    p = result["policy"]
    checks_html = pd.DataFrame(check_rows(focus["checks"])).to_html(index=False, escape=True) if focus else "<p>No allocation passed. Review the constraints below.</p>"
    portfolios = [(label, candidate) for label, candidate in [("Selected" if result["selected"] else "Unverified illustration", focus), ("Baseline", result["baseline"])] if candidate]
    comparison_html = comparison_table(portfolios).to_html(index=False, escape=True)
    fund_rows = [{"Fund": fund["ticker"], **item} for fund in result["fund_evidence"] for item in check_rows(fund["checks"])]
    fund_html = pd.DataFrame(fund_rows).to_html(index=False, escape=True)
    plots = "\n".join(figure.to_html(full_html=False, include_plotlyjs=(index == 0), config={"responsive": True, "displaylogo": False})
                      for index, figure in enumerate(figures.values()))
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in [*result["methodology"].values(), *result["limitations"]])
    sources = "".join(f'<li>{html.escape(t)}: <a href="{html.escape(f.get("source_url", ""), quote=True)}">issuer snapshot</a>; yield {html.escape(str(f.get("yield_as_of")))}, duration {html.escape(str(f.get("duration_as_of")))}, spread {html.escape(str(f.get("spread_as_of")))}</li>' for t, f in result["fund_facts"]["funds"].items())
    failures = html.escape(json.dumps(result["search"], indent=2))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claims reserve allocation</title><style>
body{{margin:0;background:#f4f7fb;color:#172033;font:15px/1.6 Arial,sans-serif}}main{{max-width:1180px;margin:40px auto;padding:32px;background:white;border-radius:18px}}h1{{font-size:32px;line-height:1.2}}h2{{margin-top:36px}}.tag{{color:#0d9488;font-weight:bold}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #e2e8f0}}th{{background:#eff6ff}}pre{{white-space:pre-wrap;background:#f8fafc;padding:16px}}a{{color:#2563eb}}
</style></head><body><main><p class="tag">CLAIMS FIRST · PROTOTYPE 1</p><h1>{html.escape(title)}</h1>
<p>${p['budget_usd']:,.0f} USD · {p['horizon_months']} months · ${p['minimum_cash_usd']:,.0f} immediately accessible reserve.</p>
<p>Editable demo assumptions. Current income run-rate is not a forecast. Historical replay has no claims withdrawals.</p>
{plots}<h2>Allocation checks</h2><div class="scroll">{checks_html}</div><h2>Baseline comparison</h2><div class="scroll">{comparison_html}</div>
<h2>Fund evidence</h2><div class="scroll">{fund_html}</div><h2>Search audit</h2><pre>{failures}</pre>
<h2>How to read this report</h2><ul>{notes}</ul><h2>Issuer sources and dates</h2><ul>{sources}</ul>
<p>Analysis date: {result['analysis_date']}. Price window: {result['common_history']['start']}–{result['common_history']['end']}.</p></main></body></html>'''


def save_outputs(result, prices, data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    figures, history = build_figures(result, prices)
    (data_dir / "validation.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    (data_dir / "report.md").write_text(deterministic_report(result), encoding="utf-8")
    (data_dir / "report.html").write_text(render_html(result, figures), encoding="utf-8")
    pd.DataFrame(result["candidate_audit"]).to_csv(data_dir / "candidate_allocations.csv", index=False)
    pd.DataFrame(candidate_rows(result)).to_csv(data_dir / "portfolio_comparison.csv", index=False)
    history.to_csv(data_dir / "portfolio_history.csv")
    return figures
