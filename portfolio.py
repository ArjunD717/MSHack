"""Deterministic portfolio checks. No LLM participates in selection or arithmetic."""

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = ROOT / "config" / "client_policy.json"
DEFAULT_FACTS_PATH = ROOT / "config" / "fund_facts.json"
EPSILON = 1e-8


@dataclass(frozen=True)
class ClientPolicy:
    budget_usd: float = 100_000
    currency: str = "USD"
    horizon_months: int = 12
    minimum_cash_usd: float = 25_000
    maximum_volatility_pct: float = 2.0
    maximum_drawdown_pct: float = 5.0
    maximum_rate_loss_usd: float = 2_000
    rate_shock_bps: float = 100
    stressed_claim_usd: float = 40_000
    claim_due_days: int = 30
    assumed_sale_delay_days: int = 3
    maximum_spread_pct: float = 0.1
    maximum_price_age_days: int = 7
    maximum_fact_age_days: int = 30
    minimum_history_sessions: int = 252
    grid_step_pct: int = 5
    cash_annual_rate_pct: float = 0.0
    baseline_ticker: str = "BIL"
    minimum_holdings: int = 3
    minimum_holding_pct: float = 10.0
    maximum_holding_pct: float = 35.0

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key in ("currency", "baseline_ticker"):
                if not isinstance(value, str) or not value:
                    raise ValueError(f"{key} must be a nonempty string.")
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be a finite, nonnegative number.")
        if self.budget_usd <= 0 or round(self.budget_usd, 2) != self.budget_usd:
            raise ValueError("Budget must be positive and expressed to whole cents.")
        if self.currency != "USD":
            raise ValueError("This prototype supports USD client obligations only.")
        for key in ("minimum_holdings", "horizon_months", "claim_due_days", "assumed_sale_delay_days", "maximum_price_age_days", "maximum_fact_age_days", "minimum_history_sessions", "grid_step_pct"):
            if not isinstance(getattr(self, key), int):
                raise ValueError(f"{key} must be a whole number.")
        if self.horizon_months < 1 or self.minimum_history_sessions < 252:
            raise ValueError("Use a positive horizon and at least 252 historical return observations.")
        if self.assumed_sale_delay_days < 1:
            raise ValueError("ETF sale access must be assumed to take at least one calendar day; only CASH is immediately accessible.")
        if self.grid_step_pct < 1 or self.grid_step_pct > 100 or 100 % self.grid_step_pct:
            raise ValueError("grid_step_pct must be a positive whole-number divisor of 100.")
        if self.rate_shock_bps > 300:
            raise ValueError("The linear duration approximation is limited to shocks of at most 300 bps.")
        if self.cash_annual_rate_pct > 20:
            raise ValueError("The assumed cash rate must be between 0% and 20%.")
        if not 0 < self.minimum_holding_pct <= self.maximum_holding_pct <= 100:
            raise ValueError("Holding limits must satisfy 0 < minimum_holding_pct <= maximum_holding_pct <= 100.")


def load_policy(path=DEFAULT_POLICY_PATH):
    return ClientPolicy(**json.loads(Path(path).read_text(encoding="utf-8-sig")))


def load_facts(path=DEFAULT_FACTS_PATH):
    facts = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if facts.get("schema_version") != 1 or not isinstance(facts.get("funds"), dict):
        raise ValueError("Fund facts must have schema_version 1 and a funds object.")
    return facts


def check(name, measured, limit, passed, detail):
    return {"name": name, "status": "unverified" if passed is None else ("pass" if passed else "fail"),
            "measured": measured, "limit": limit, "detail": detail}


def _fresh(as_of, today, max_age):
    try:
        age = (today - date.fromisoformat(as_of)).days
        return 0 <= age <= max_age
    except (ValueError, TypeError):
        return False


def _fact_value(fact, value_key, date_key, today, max_age):
    value = fact.get(value_key)
    source = fact.get("source_url", "")
    if not source.startswith("https://") or not _fresh(fact.get(date_key), today, max_age):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def instrument_checks(ticker, fact, last_price, policy, today):
    """Check evidence before evaluating exposures; no missing field becomes zero."""
    identity_current = bool(fact.get("source_url", "").startswith("https://")) and _fresh(
        fact.get("identity_as_of"), today, policy.maximum_fact_age_days
    )
    currency = fact.get("quote_currency") if identity_current else None
    asset_class = fact.get("asset_class") if identity_current else None
    duration = _fact_value(fact, "duration_years", "duration_as_of", today, policy.maximum_fact_age_days)
    sec_yield = _fact_value(fact, "sec_yield_pct", "yield_as_of", today, policy.maximum_fact_age_days)
    spread = _fact_value(fact, "spread_pct", "spread_as_of", today, policy.maximum_fact_age_days)
    checks = [
        check("Price freshness", last_price, f"<= {policy.maximum_price_age_days} calendar days old",
              True if _fresh(last_price, today, policy.maximum_price_age_days) else None,
              "Price dates must be recent and cannot be in the future."),
        check("Currency match", currency, policy.currency, None if currency is None else currency == policy.currency,
              "Reviewed quote currency must match the USD obligations."),
        check("Permitted exposure", asset_class, "us_treasury_etf",
              None if asset_class is None else asset_class == "us_treasury_etf",
              "Demo policy permits Treasury ETFs. This is not a credit-rating or solvency assessment."),
        check("Duration evidence", duration, "Current effective duration in years", None if duration is None else True,
              "A dated issuer duration is required for the rate stress."),
        check("Income evidence", sec_yield, "Current 30-day SEC yield (%)", None if sec_yield is None else True,
              "Yield is after fund expenses. It is not a promised or forecast return."),
        check("Trading spread", spread, policy.maximum_spread_pct,
              None if spread is None else spread <= policy.maximum_spread_pct + EPSILON,
              "Dated 30-day median spread (%); a limited liquidity proxy, not proof of execution capacity."),
    ]
    return {"ticker": ticker, "name": fact.get("name", ticker), "source_url": fact.get("source_url"),
            "checks": checks, "duration_years": duration, "sec_yield_pct": sec_yield, "spread_pct": spread,
            "status": _status(checks)}


def _status(checks):
    if any(item["status"] == "fail" for item in checks):
        return "fail"
    return "unverified" if any(item["status"] == "unverified" for item in checks) else "pass"


def align_prices(prices, tickers):
    if not {"date", "ticker", "adjusted_close"}.issubset(prices.columns):
        raise ValueError("prices.csv needs date, ticker and adjusted_close columns.")
    rows = prices.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="raise")
    rows["adjusted_close"] = pd.to_numeric(rows["adjusted_close"], errors="raise")
    if rows["date"].isna().any() or rows.duplicated(["date", "ticker"]).any():
        raise ValueError("Price dates must be present and unique for each ticker.")
    if set(rows["ticker"].unique()) != set(tickers):
        raise ValueError("Price symbols and metadata differ.")
    table = rows.pivot(index="date", columns="ticker", values="adjusted_close").sort_index()[tickers]
    if any(table[t].dropna().empty for t in tickers):
        raise ValueError("Every requested ticker needs prices.")
    first = max(table[t].first_valid_index() for t in tickers)
    last = min(table[t].last_valid_index() for t in tickers)
    table = table.loc[first:last]
    if len(table) < 2:
        raise ValueError("The instruments need at least two overlapping price dates.")
    values = table.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("The common price window contains gaps or invalid prices; no forward filling is used.")
    return table


def allocation_grid(asset_count, step_pct):
    units = 100 // step_pct
    count = math.comb(units + asset_count - 1, asset_count - 1)
    if count > 25_000:
        raise ValueError(f"The grid would contain {count:,} allocations. Increase the step or reduce the universe (limit 25,000).")

    def compositions(remaining, parts):
        if parts == 1:
            yield [remaining]
        else:
            for value in range(remaining + 1):
                for rest in compositions(remaining - value, parts - 1):
                    yield [value, *rest]
    return np.array(list(compositions(units, asset_count)), dtype=float) / units


def normalized_assets(table, policy):
    elapsed_years = (table.index - table.index[0]).days.to_numpy() / 365.25
    cash = (1 + policy.cash_annual_rate_pct / 100) ** elapsed_years
    return np.column_stack([cash, table.to_numpy() / table.iloc[0].to_numpy()])


def _history_metrics(paths):
    returns = paths[1:] / paths[:-1] - 1
    volatility = returns[-252:].std(axis=0, ddof=1) * np.sqrt(252) * 100 if len(returns) >= 252 else np.full(paths.shape[1], np.nan)
    drawdown = (1 - paths / np.maximum.accumulate(paths, axis=0)).max(axis=0) * 100
    return volatility, drawdown


def _money(weights, assets, budget):
    amounts = {asset: round(float(weight) * budget, 2) for asset, weight in zip(assets, weights)}
    # Keep a cent-exact budget even when grid fractions produce rounding residuals.
    amounts["CASH"] = round(budget - sum(value for key, value in amounts.items() if key != "CASH"), 2)
    return amounts


def evaluate_allocation(weights, assets, metrics, policy, evidence, common_history):
    amounts = _money(weights, assets, policy.budget_usd)
    actual_weights = {asset: amount / policy.budget_usd for asset, amount in amounts.items()}
    held = [evidence[a] for a in assets[1:] if amounts[a] > 0]
    holding_pcts = [actual_weights[e["ticker"]] * 100 for e in held]
    cash = amounts["CASH"]
    duration_known = all(e["duration_years"] is not None for e in held)
    spread_known = all(e["spread_pct"] is not None for e in held)
    yield_known = all(e["sec_yield_pct"] is not None for e in held)
    duration = sum(actual_weights[e["ticker"]] * e["duration_years"] for e in held) if duration_known else None
    rate_loss = policy.budget_usd * duration * policy.rate_shock_bps / 10_000 if duration is not None else None
    run_rate = cash * policy.cash_annual_rate_pct / 100
    if yield_known:
        run_rate += sum(amounts[e["ticker"]] * e["sec_yield_pct"] / 100 for e in held)
    else:
        run_rate = None

    can_sell = policy.claim_due_days >= policy.assumed_sale_delay_days
    available = cash
    if can_sell and held:
        available = None
        if duration_known and spread_known:
            available = cash + sum(amounts[e["ticker"]] * max(0, 1 - e["duration_years"] * policy.rate_shock_bps / 10_000 - e["spread_pct"] / 100) for e in held)
    shortfall = max(0, policy.stressed_claim_usd - available) if available is not None else None
    history_ok = common_history["return_observations"] >= policy.minimum_history_sessions
    vol = metrics["annualized_volatility_pct"]
    drawdown = metrics["max_drawdown_pct"]
    checks = [
        check("Minimum investment holdings", len(held), policy.minimum_holdings,
              len(held) >= policy.minimum_holdings,
              "Count separate fund holdings. Cash does not count. Every held fund must also meet the minimum size below."),
        check("Minimum holding size (%)", min(holding_pcts) if held else "No investments", policy.minimum_holding_pct,
              all(weight + EPSILON >= policy.minimum_holding_pct for weight in holding_pcts),
              "Each nonzero fund holding must be at least this percentage of the full budget. Tiny token holdings fail."),
        check("Maximum single holding (%)", max(holding_pcts, default=0), policy.maximum_holding_pct,
              all(weight <= policy.maximum_holding_pct + EPSILON for weight in holding_pcts),
              "No fund may exceed this percentage of the full budget. Immediately accessible cash is excluded."),
        check("Budget and weights", round(sum(amounts.values()), 2), policy.budget_usd,
              min(amounts.values()) >= 0 and abs(sum(amounts.values()) - policy.budget_usd) < 0.005,
              "Nonnegative dollar allocations must sum to the full budget."),
        check("Immediately accessible cash", cash, policy.minimum_cash_usd, cash + EPSILON >= policy.minimum_cash_usd,
              "Only the CASH bucket is immediately accessible. ETF shares are not cash."),
        check("Common history", common_history["return_observations"], policy.minimum_history_sessions,
              True if history_ok else None, "All investments use the same dates; minimum required return observations."),
        check("Common window freshness", common_history["end"], f"<= {policy.maximum_price_age_days} calendar days old",
              True if common_history["fresh"] else None,
              "The aligned comparison window must be recent even when a fund is not held."),
        check("Historical volatility (%)", vol, policy.maximum_volatility_pct,
              None if vol is None else vol <= policy.maximum_volatility_pct + EPSILON,
              "Last 252 returns of the combined buy-and-hold portfolio; annualized."),
        check("Historical maximum drawdown (%)", drawdown, policy.maximum_drawdown_pct,
              drawdown <= policy.maximum_drawdown_pct + EPSILON,
              "Loss magnitude from a prior portfolio peak over the common history; not an individual-drawdown average."),
        check("Rate shock loss (USD)", round(rate_loss, 2) if rate_loss is not None else None, policy.maximum_rate_loss_usd,
              None if rate_loss is None else rate_loss <= policy.maximum_rate_loss_usd + EPSILON,
              f"First-order duration estimate for +{policy.rate_shock_bps:g} bps; excludes convexity and default."),
        check("Stressed claims coverage (USD)", round(available, 2) if available is not None else None, policy.stressed_claim_usd,
              None if shortfall is None else shortfall <= EPSILON,
              f"Claim due in {policy.claim_due_days} days, after the rate shock and a full-spread sale haircut. ETF sale access after {policy.assumed_sale_delay_days} calendar days is an operational assumption, not guaranteed liquidity."),
    ]
    for name in ("Price freshness", "Currency match", "Permitted exposure", "Duration evidence", "Income evidence", "Trading spread"):
        relevant = [(e["ticker"], next(c for c in e["checks"] if c["name"] == name)) for e in held]
        status = _status([c for _, c in relevant])
        checks.append(check(name, "; ".join(f"{t}: {c['status']}" for t, c in relevant) or "Cash only",
                            "All held funds pass", None if status == "unverified" else status == "pass",
                            "See the individual fund evidence and source dates for this check."))
    return {
        "weights": actual_weights, "amounts_usd": amounts, "status": _status(checks), "checks": checks,
        "metrics": {**metrics, "duration_years": duration,
                    "investment_holdings": len(held), "largest_holding_pct": max(holding_pcts, default=0),
                    "rate_up_loss_usd": round(rate_loss, 2) if rate_loss is not None else None,
                    "income_run_rate_usd": round(run_rate, 2) if run_rate is not None else None,
                    "income_run_rate_pct": round(run_rate / policy.budget_usd * 100, 4) if run_rate is not None else None,
                    "stressed_available_usd": round(available, 2) if available is not None else None,
                    "stressed_claim_shortfall_usd": round(shortfall, 2) if shortfall is not None else None},
    }


def portfolio_path(table, weights, policy):
    assets = ["CASH", *list(table.columns)]
    return pd.Series(normalized_assets(table, policy) @ np.array([weights.get(a, 0) for a in assets]), index=table.index)


def _detail(candidate, table, policy):
    if candidate is None:
        return None
    path = portfolio_path(table, candidate["weights"], policy)
    candidate["historical_periods"] = []
    for year in (2020, 2022):
        mask = np.flatnonzero(path.index.year == year)
        if not len(mask):
            continue
        window = path.iloc[max(0, mask[0] - 1):mask[-1] + 1]
        candidate["historical_periods"].append({
            "label": f"{year} observed window", "start": window.index[0].date().isoformat(),
            "end": window.index[-1].date().isoformat(),
            "return_pct": round(float((window.iloc[-1] / window.iloc[0] - 1) * 100), 4),
            "drawdown_pct": round(float((1 - window / window.cummax()).max() * 100), 4),
        })
    return candidate


def run_validation(prices, metadata, policy, facts, as_of=None):
    """Search all grid allocations. Rank only passing portfolios by income run-rate."""
    today = as_of or datetime.now(timezone.utc).date()
    tickers = metadata["tickers"]
    if not tickers or "CASH" in tickers or len(set(tickers)) != len(tickers):
        raise ValueError("Use unique fund tickers, excluding the reserved CASH symbol.")
    table = align_prices(prices, tickers)
    common_history = {"start": table.index[0].date().isoformat(), "end": table.index[-1].date().isoformat(),
                      "return_observations": len(table) - 1,
                      "fresh": _fresh(table.index[-1].date().isoformat(), today, policy.maximum_price_age_days)}
    evidence = {t: instrument_checks(t, facts.get("funds", {}).get(t, {}),
                                    str(prices.loc[prices["ticker"] == t, "date"].max())[:10], policy, today) for t in tickers}
    assets = ["CASH", *tickers]
    grid = allocation_grid(len(assets), policy.grid_step_pct)
    base = normalized_assets(table, policy)
    candidates = []
    for offset in range(0, len(grid), 256):
        chunk = grid[offset:offset + 256]
        # Calculate paths from cent-exact allocations, matching the displayed dollars.
        actual = np.array([[v / policy.budget_usd for v in _money(w, assets, policy.budget_usd).values()] for w in chunk])
        paths = base @ actual.T
        volatility, drawdown = _history_metrics(paths)
        for i, weights in enumerate(actual):
            metrics = {"annualized_volatility_pct": float(volatility[i]) if np.isfinite(volatility[i]) else None,
                       "max_drawdown_pct": float(drawdown[i]),
                       "historical_return_pct": float((paths[-1, i] - 1) * 100)}
            candidates.append(evaluate_allocation(weights, assets, metrics, policy, evidence, common_history))

    def ranking(candidate):
        income = candidate["metrics"]["income_run_rate_usd"]
        return (-(income if income is not None else -1), candidate["metrics"]["max_drawdown_pct"], -candidate["amounts_usd"]["CASH"])

    passing = sorted([c for c in candidates if c["status"] == "pass"], key=ranking)
    unverified = sorted([c for c in candidates if c["status"] == "unverified"], key=ranking)
    selected = _detail(passing[0], table, policy) if passing else None
    provisional = _detail(unverified[0], table, policy) if not selected and unverified else None
    baseline = None
    if policy.baseline_ticker in tickers and policy.minimum_cash_usd <= policy.budget_usd:
        w = np.zeros(len(assets))
        w[0] = policy.minimum_cash_usd / policy.budget_usd
        w[assets.index(policy.baseline_ticker)] = 1 - w[0]
        amounts = _money(w, assets, policy.budget_usd)
        w = np.array([amounts[a] / policy.budget_usd for a in assets])
        path = (base @ w).reshape(-1, 1)
        vol, dd = _history_metrics(path)
        baseline = _detail(evaluate_allocation(w, assets,
            {"annualized_volatility_pct": float(vol[0]) if np.isfinite(vol[0]) else None,
             "max_drawdown_pct": float(dd[0]), "historical_return_pct": float((path[-1, 0] - 1) * 100)},
            policy, evidence, common_history), table, policy)
    failures = Counter(c["name"] for candidate in candidates for c in candidate["checks"] if c["status"] == "fail")
    unknowns = Counter(c["name"] for candidate in candidates for c in candidate["checks"] if c["status"] == "unverified")
    status = "feasible" if selected else ("unverified" if provisional else "infeasible")
    comparison = None
    if selected and baseline and selected["metrics"]["income_run_rate_usd"] is not None and baseline["metrics"]["income_run_rate_usd"] is not None:
        comparison = round(selected["metrics"]["income_run_rate_usd"] - baseline["metrics"]["income_run_rate_usd"], 2)
    # Compare against the original unconstrained concentration rules to make the
    # diversification cost visible. This is a reference, never an eligible fallback.
    diversification_checks = {"Minimum investment holdings", "Minimum holding size (%)", "Maximum single holding (%)"}
    reference_candidates = [c for c in candidates if all(item["status"] == "pass" for item in c["checks"]
                                                        if item["name"] not in diversification_checks)]
    reference = _detail(min(reference_candidates, key=ranking), table, policy) if reference_candidates else None
    income_change = None
    if selected and reference:
        income_change = round(selected["metrics"]["income_run_rate_usd"] - reference["metrics"]["income_run_rate_usd"], 2)
    return {
        "prototype": 1, "status": status, "analysis_date": today.isoformat(),
        "policy": asdict(policy), "common_history": common_history, "market_source": metadata,
        "fund_facts": facts, "fund_evidence": list(evidence.values()),
        "search": {"total": len(candidates), "passing": len(passing), "unverified": len(unverified),
                   "failed": sum(c["status"] == "fail" for c in candidates),
                   "failure_counts": dict(failures), "unverified_counts": dict(unknowns)},
        "selected": selected, "provisional": provisional, "baseline": baseline,
        "income_run_rate_advantage_over_baseline_usd": comparison,
        "diversification": {
            "scope": "Holdings across Treasury funds; all still share US government debt exposure.",
            "comparison_without_holding_limits": reference,
            "income_change_vs_without_holding_limits_usd": income_change,
            "comparison_note": "Same grid, evidence and risk limits, ignoring only the three holding rules. The reference retains its actual check status and is not an eligible fallback.",
        },
        "alternatives": [_detail(c, table, policy) for c in passing[1:4]],
        "candidate_audit": [
            {"status": c["status"], **c["amounts_usd"], **c["metrics"],
             "failed_checks": "; ".join(k["name"] for k in c["checks"] if k["status"] == "fail"),
             "unverified_checks": "; ".join(k["name"] for k in c["checks"] if k["status"] == "unverified")}
            for c in candidates
        ],
        "methodology": {
            "objective": "Highest current SEC-yield income run-rate among portfolios passing every prototype check. Ties favour smaller historical drawdown, then more cash. No historical-return maximization.",
            "replay": "Retrospective buy-and-hold with an initial allocation, provider-adjusted prices as a reinvested-distribution proxy, and drifting weights. No rebalancing, withdrawals, tax, commissions or entry/exit costs in the historical replay.",
            "claims": "The stressed claim is one total payment (not additional to the reserve). Combine a positive parallel rate shock and a full quoted spread haircut; cash is accessible now, ETF sale proceeds only after the assumed calendar-day delay. No future income offsets the claim.",
            "rate_stress": "Approximate price change = -effective duration × change in yield. Instantaneous parallel shock; omits convexity, curve shape, changing spreads, default and market closure.",
            "income": "Budget × allocation weights × dated 30-day SEC yields plus assumed cash interest. Already after fund expenses; not a future return or guaranteed distribution. The horizon scales the illustrative run-rate only.",
            "cash": "CASH is an operational modelling assumption of immediately accessible USD. The same assumed fixed interest rate is used throughout the historical replay; bank credit, account access and deposit protection are not verified.",
            "diversification": f"Require at least {policy.minimum_holdings} non-cash fund holdings, each between {policy.minimum_holding_pct:g}% and {policy.maximum_holding_pct:g}% of the full budget when held. Cash is excluded. All other evidence, cash, historical-risk and rate-stress checks still apply. If no allocation passes, no constraints are silently relaxed. Only initial allocation weights are constrained; buy-and-hold weights may later drift.",
        },
        "limitations": [
            "All policy thresholds are editable hackathon assumptions, not universal definitions of low risk or regulatory approval.",
            "Selection uses known history and current facts. Historical results are retrospective validation, not an untouched out-of-sample forecast test.",
            "The three default funds share US Treasury exposure. More fund tickers do not remove that common exposure.",
            "A passing portfolio passes only these prototype checks. Settlement delays, market capacity and claims timing remain assumptions.",
            "Fresh prices and fresh manually reviewed issuer facts are separate requirements. --refresh does not update issuer facts.",
            "If some candidate funds have unverified evidence, the optimum is only among portfolios with verified required inputs.",
        ],
    }


def candidate_rows(result):
    """Compact shortlist for exports; the search audit is retained separately."""
    rows = []
    for label, candidate in [("Selected", result["selected"]), ("Unverified illustration", result["provisional"]), ("Baseline", result["baseline"])]:
        if candidate:
            rows.append({"portfolio": label, "status": candidate["status"], **candidate["amounts_usd"], **candidate["metrics"]})
    reference = result.get("diversification", {}).get("comparison_without_holding_limits")
    if reference:
        rows.append({"portfolio": "Comparison without holding limits (not an eligible fallback)",
                     "status": reference["status"], **reference["amounts_usd"], **reference["metrics"]})
    return rows
