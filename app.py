"""Local dashboard: edit assumptions and inspect deterministic allocation evidence."""

import io
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from main import analyze, build_payload
from portfolio import DEFAULT_FACTS_PATH, DEFAULT_POLICY_PATH, ClientPolicy, load_facts, load_policy, run_validation
from reporting import STATUS_LABELS, check_rows, comparison_table, focus_candidate, save_outputs
from yfinance_source import DEFAULT_DATA_DIR, prepare_market_data


DATA_DIR = Path(os.environ.get("MSHACK_DATA_DIR", DEFAULT_DATA_DIR))
FACTS_PATH = Path(os.environ.get("MSHACK_FACTS_PATH", DEFAULT_FACTS_PATH))
POLICY_PATH = Path(os.environ.get("MSHACK_POLICY_PATH", DEFAULT_POLICY_PATH))
st.set_page_config(page_title="Claims first · Allocation lab", page_icon="◈", layout="wide")
st.markdown("""<style>
.stApp {background:#f5f7fb} [data-testid="stMetric"] {background:white;padding:18px;border:1px solid #e2e8f0;border-radius:12px}
[data-testid="stSidebar"] {background:white} .block-container {padding-top:2.2rem} h1 {letter-spacing:-.04em}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def calculate(policy_json, prices_csv, metadata_json, facts_json, analysis_date):
    return run_validation(pd.read_csv(io.StringIO(prices_csv)), json.loads(metadata_json),
                          ClientPolicy(**json.loads(policy_json)), json.loads(facts_json),
                          as_of=datetime.fromisoformat(analysis_date).date())


def show_metric(column, label, value, unit="USD", delta=None):
    text = "Unverified" if value is None else (f"${value:,.0f}" if unit == "USD" else f"{value:.2f}%")
    column.metric(label, text, delta=delta)


def dashboard():
    defaults = load_policy(POLICY_PATH)
    st.sidebar.title("Client assumptions")
    st.sidebar.caption("Demo policy for a short-term USD claims reserve. These are team assumptions, not insurance rules.")
    with st.sidebar.form("policy_form"):
        budget = st.number_input("Budget (USD)", min_value=1_000, value=int(defaults.budget_usd), step=5_000, key="budget")
        reserve = st.number_input("Immediately accessible cash (USD)", min_value=0, value=int(defaults.minimum_cash_usd), step=5_000, key="reserve")
        horizon = st.number_input("Planning horizon (months)", min_value=1, max_value=120, value=defaults.horizon_months, key="horizon")
        st.markdown("**Risk limits**")
        volatility = st.number_input("Maximum historical volatility (%)", min_value=0.0, max_value=50.0, value=defaults.maximum_volatility_pct, step=0.25, key="volatility")
        drawdown = st.number_input("Maximum historical drawdown (%)", min_value=0.0, max_value=100.0, value=defaults.maximum_drawdown_pct, step=0.5, key="drawdown")
        rate_loss = st.number_input("Maximum rate-shock loss (USD)", min_value=0, value=int(defaults.maximum_rate_loss_usd), step=250, key="rate_loss")
        shock = st.slider("Interest-rate shock (basis points)", 0, 300, int(defaults.rate_shock_bps), 25, key="shock")
        st.markdown("**Claims scenario**")
        claim = st.number_input("Total stressed claim (USD)", min_value=0, value=int(defaults.stressed_claim_usd), step=5_000, key="claim")
        due = st.number_input("Claim due in (calendar days)", min_value=0, max_value=365, value=defaults.claim_due_days, key="claim_due")
        with st.expander("Evidence and operating assumptions"):
            delay = st.number_input("Assumed ETF sale access (calendar days)", min_value=1, max_value=30, value=defaults.assumed_sale_delay_days)
            cash_rate = st.number_input("Assumed cash interest rate (%)", min_value=0.0, max_value=20.0, value=defaults.cash_annual_rate_pct, step=0.25)
            spread = st.number_input("Maximum quoted spread (%)", min_value=0.0, max_value=10.0, value=defaults.maximum_spread_pct, step=0.01)
            price_age = st.number_input("Maximum price age (calendar days)", min_value=0, max_value=30, value=defaults.maximum_price_age_days)
            fact_age = st.number_input("Maximum issuer fact age (days)", min_value=0, max_value=365, value=defaults.maximum_fact_age_days)
            grid_choices = sorted({5, 10, 20, 25, 50, defaults.grid_step_pct})
            grid_step = st.selectbox("Allocation step (%)", grid_choices, index=grid_choices.index(defaults.grid_step_pct))
        submitted = st.form_submit_button("Recalculate allocation", type="primary", width="stretch")
    proposed = replace(defaults, budget_usd=float(budget), minimum_cash_usd=float(reserve), horizon_months=horizon,
                       maximum_volatility_pct=volatility, maximum_drawdown_pct=drawdown,
                       maximum_rate_loss_usd=float(rate_loss), rate_shock_bps=float(shock),
                       stressed_claim_usd=float(claim), claim_due_days=due, assumed_sale_delay_days=delay,
                       cash_annual_rate_pct=cash_rate, maximum_spread_pct=spread,
                       maximum_price_age_days=price_age, maximum_fact_age_days=fact_age, grid_step_pct=grid_step)
    if submitted or "applied_policy" not in st.session_state:
        st.session_state.applied_policy = asdict(proposed)
    policy = ClientPolicy(**st.session_state.applied_policy)

    st.caption("INSURANCE INVESTMENT RESEARCH · PROTOTYPE 1")
    st.title("Claims first.")
    st.write("Allocate the reserve, check the evidence, and see what changes when the client’s needs change.")
    left, right = st.columns([4, 1])
    left.caption(f"${policy.budget_usd:,.0f} USD · {policy.horizon_months} months · {policy.grid_step_pct}% allocation steps")
    refresh = right.button("Refresh price history", width="stretch")
    with st.spinner("Checking prices and candidate allocations..."):
        summary = prepare_market_data(data_dir=DATA_DIR, refresh=refresh)
        prices_csv = (DATA_DIR / "prices.csv").read_text(encoding="utf-8")
        prices = pd.read_csv(io.StringIO(prices_csv))
        facts = load_facts(FACTS_PATH)
        today = datetime.now(timezone.utc).date().isoformat()
        policy_json = json.dumps(asdict(policy), sort_keys=True)
        result = calculate(policy_json, prices_csv, json.dumps(summary["source"]), json.dumps(facts), today)
        payload = build_payload(summary, policy.budget_usd, result)
        prices_hash = hashlib.sha256(prices_csv.encode("utf-8")).hexdigest()
        fingerprint = json.dumps([policy_json, summary["source"], facts, prices_hash, today], sort_keys=True)
        if st.session_state.get("result_fingerprint") != fingerprint:
            figures = save_outputs(result, prices, DATA_DIR)
            (DATA_DIR / "model_input.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
            st.session_state.figures = figures
            st.session_state.result_fingerprint = fingerprint
            st.session_state.pop("ai_explanation", None)
        figures = st.session_state.figures

    banner = {"feasible": st.success, "unverified": st.warning, "infeasible": st.error}[result["status"]]
    banner(STATUS_LABELS[result["status"]])
    s = result["search"]
    st.caption(f"{s['total']:,} allocations checked · {s['passing']:,} pass · {s['failed']:,} fail · {s['unverified']:,} unverified")
    focus = focus_candidate(result)
    if result["provisional"]:
        st.warning("Charts show an unverified illustration. It must not be presented as a validated allocation.")
    if focus:
        m = focus["metrics"]
        cols = st.columns(4)
        show_metric(cols[0], "Accessible cash now", focus["amounts_usd"]["CASH"])
        show_metric(cols[1], "Annual income run-rate", m["income_run_rate_usd"])
        show_metric(cols[2], "Historical volatility", m["annualized_volatility_pct"], "%")
        show_metric(cols[3], "Estimated rate-shock loss", m["rate_up_loss_usd"])
        st.caption("Income run-rate uses dated SEC yields and assumed cash interest; it is not a forecast. Rate-shock loss is an approximation.")

    allocation_tab, history_tab, evidence_tab, audit_tab = st.tabs(["Allocation & checks", "History & scenarios", "Evidence & sources", "Alternatives & export"])
    with allocation_tab:
        st.plotly_chart(figures["allocation"], width="stretch")
        if focus:
            allocation_rows = [{"Holding": asset, "USD": amount, "Weight (%)": round(focus["weights"][asset] * 100, 2)}
                               for asset, amount in focus["amounts_usd"].items() if amount]
            st.dataframe(pd.DataFrame(allocation_rows), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(check_rows(focus["checks"])), hide_index=True, width="stretch")
        else:
            st.write("No passing allocation. The engine has kept your limits unchanged.")
        st.markdown("**Baseline comparison**")
        st.caption(f"Required cash reserve plus the rest in {policy.baseline_ticker}; the baseline is checked against the same policy.")
        portfolios = [(label, candidate) for label, candidate in [("Selected" if result["selected"] else "Unverified illustration", focus), ("Baseline", result["baseline"])] if candidate]
        st.dataframe(comparison_table(portfolios), hide_index=True, width="stretch")
        advantage = result["income_run_rate_advantage_over_baseline_usd"]
        if advantage is not None:
            st.write(f"Selected minus baseline current annual income run-rate: **${advantage:,.2f}**. This is not a predicted performance improvement.")

    with history_tab:
        st.plotly_chart(figures["history"], width="stretch")
        st.caption("Retrospective buy-and-hold with drifting weights and adjusted prices. No claims withdrawals or transaction costs in this replay.")
        st.plotly_chart(figures["scenarios"], width="stretch")
        if focus:
            st.markdown("**Stressed claims access**")
            available = focus["metrics"]["stressed_available_usd"]
            if available is None:
                st.warning("Claims coverage is unverified because required duration or spread evidence is missing.")
            else:
                st.write(f"By day **{policy.claim_due_days}**, modelled accessible funds after the upward rate shock and sale haircut: **${available:,.2f}**, against a **${policy.stressed_claim_usd:,.2f}** total claim.")
            st.caption(f"Cash is accessible immediately. ETF proceeds are assumed accessible after {policy.assumed_sale_delay_days} calendar days. This assumption does not guarantee settlement or trading liquidity.")
            periods = pd.DataFrame(focus["historical_periods"]).rename(columns={"label": "Observed window", "start": "Start", "end": "End", "return_pct": "Return (%)", "drawdown_pct": "Maximum drawdown (%)"})
            st.dataframe(periods, hide_index=True, width="stretch")

    with evidence_tab:
        st.caption(f"Common price window: {result['common_history']['start']} to {result['common_history']['end']}. Price data fetched: {summary['source']['fetched_at_utc']}.")
        st.info("Refreshing prices does not refresh issuer facts. Update config/fund_facts.json from the cited issuer pages; stale or missing fields become UNVERIFIED.")
        for fund in result["fund_evidence"]:
            with st.expander(f"{fund['ticker']} · {fund['name']} · {fund['status'].upper()}", expanded=fund["status"] != "pass"):
                st.dataframe(pd.DataFrame(check_rows(fund["checks"])), hide_index=True, width="stretch")
                if fund["source_url"]:
                    st.link_button("Issuer source", fund["source_url"])
                st.json(facts["funds"].get(fund["ticker"], {}))
        st.markdown("**Assumptions and limits**")
        for note in [*result["methodology"].values(), *result["limitations"]]:
            st.write("• " + note)

    with audit_tab:
        st.plotly_chart(figures["frontier"], width="stretch")
        if result["alternatives"]:
            st.markdown("**Next passing alternatives**")
            st.dataframe(comparison_table([(f"Alternative {i + 1}", c) for i, c in enumerate(result["alternatives"])]), hide_index=True, width="stretch")
            st.caption("Each alternative passes the same checks; exact weights and dollar amounts are in the validation export.")
        reasons = [{"Check": key, "Failed allocations": value} for key, value in s["failure_counts"].items()]
        if reasons:
            st.dataframe(pd.DataFrame(reasons), hide_index=True, width="stretch")
        if s["unverified_counts"]:
            st.write("Unverified checks across the search:", s["unverified_counts"])
        for label, name, mime in [("Download offline chart report", "report.html", "text/html"),
                                  ("Download all candidate allocations", "candidate_allocations.csv", "text/csv"),
                                  ("Download validation evidence", "validation.json", "application/json")]:
            st.download_button(label, (DATA_DIR / name).read_bytes(), name, mime=mime)

    st.divider()
    st.subheader("Explain this result")
    st.caption("Optional AI explanation. The model receives the computed allocation and checks; it is instructed to preserve those results.")
    session_key = st.text_input(
        "Azure API key (current session)", type="password", key="azure_api_key",
        help="Optional override for the server's environment key. Used only for Azure requests; not written to reports or project files.",
    )
    credential = (session_key or os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
    if credential:
        st.caption("API key configured. You can generate an explanation below.")
    if st.button("Generate AI explanation", disabled=not bool(credential)):
        try:
            with st.spinner("Explaining the evidence..."):
                report = analyze(payload, api_key=credential)
                st.session_state.ai_explanation = report
                (DATA_DIR / "analysis.md").write_text(report, encoding="utf-8")
        except Exception as exc:
            st.error(f"AI explanation failed ({type(exc).__name__}). The deterministic report remains available.")
    if not credential:
        st.caption("Paste your key above, or set AZURE_OPENAI_API_KEY before starting the dashboard.")
    if "ai_explanation" in st.session_state:
        st.markdown(st.session_state.ai_explanation)


if __name__ == "__main__":
    try:
        dashboard()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"Unable to evaluate this request: {exc}")
        st.info("Check the policy, issuer facts and saved price files. No constraints have been relaxed.")
