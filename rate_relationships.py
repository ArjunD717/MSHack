"""Descriptive relationships between matched price returns and FRED yield moves."""

import numpy as np
import pandas as pd


MINIMUM_INTERVALS = 26  # A prototype evidence threshold, not a significance test.
METHOD = (
    "Match adjusted prices and Treasury yields on the same calendar dates, then keep the last matched date "
    "in each Friday-ending week. Compare non-overlapping intervals of 4–10 calendar days; longer gaps and "
    "short partial weeks are excluded. No forward fill. Regress interval adjusted-price returns (%) on "
    "yield changes (basis points), with an intercept; correlation uses changes, not price/rate levels. "
    "At least 26 intervals and nonconstant rates and returns are required. The minimum is a demo evidence "
    "threshold, not a statistical significance test. Adjusted returns include the provider's distribution "
    "and split adjustments; they are not pure bond-price changes. Yield and closing-price timestamps may "
    "differ within the day. Association is descriptive and does not establish causation or forecast returns. "
    "The regression is fitted and assessed on the same sample; no out-of-sample validation is claimed. "
    "Different instruments can have different matched samples. FRED data uses the downloaded vintage. "
    "No selection rule, SEC yield, cash assumption or duration stress is changed."
)


def price_rate_relationship(prices, observations, ticker, proxy_series, as_of, maximum_age_days=7):
    """Return summary plus auditable weekly pairs. Null statistics never mean zero."""
    today = pd.Timestamp(as_of)
    rows = prices.loc[prices["ticker"] == ticker, ["date", "adjusted_close"]].copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="raise")
    rows["adjusted_close"] = pd.to_numeric(rows["adjusted_close"], errors="raise")
    if rows["date"].isna().any() or rows["date"].duplicated().any():
        raise ValueError(f"{ticker} price dates must be present and unique.")
    values = rows["adjusted_close"].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError(f"{ticker} prices must be finite and positive.")
    rates = pd.DataFrame(observations, columns=["date", "value"])
    rates["date"] = pd.to_datetime(rates["date"], errors="raise")
    rates["value"] = pd.to_numeric(rates["value"].replace(".", None), errors="raise")
    if rates["date"].isna().any() or rates["date"].duplicated().any():
        raise ValueError("FRED dates must be present and unique.")
    if not np.isfinite(rates["value"].dropna().to_numpy(dtype=float)).all():
        raise ValueError("FRED yields must be finite where present.")
    joined = rows.merge(rates.dropna(subset=["value"]), on="date", how="inner", validate="one_to_one")
    joined = joined.loc[joined["date"] <= today].sort_values("date")
    matched_end = joined["date"].iloc[-1] if len(joined) else None
    weekly = joined.groupby(joined["date"].dt.to_period("W-FRI"), sort=True).tail(1)
    elapsed = weekly["date"].diff().dt.days
    paired = pd.DataFrame({"start": weekly["date"].shift(), "end": weekly["date"],
                           "yield_change_bps": weekly["value"].diff() * 100,
                           "adjusted_return_pct": weekly["adjusted_close"].pct_change(fill_method=None) * 100})
    paired = paired.loc[elapsed.between(4, 10)].dropna()
    samples = [{"ticker": ticker, "proxy_series": proxy_series,
                "start": row.start.date().isoformat(), "end": row.end.date().isoformat(),
                "yield_change_bps": float(row.yield_change_bps),
                "adjusted_return_pct": float(row.adjusted_return_pct)} for row in paired.itertuples()]
    result = {"ticker": ticker, "proxy_series": proxy_series, "status": "unverified", "reason": None,
              "intervals": len(samples), "minimum_intervals": MINIMUM_INTERVALS,
              "start": samples[0]["start"] if samples else None,
              "end": samples[-1]["end"] if samples else None,
              "latest_matched_date": matched_end.date().isoformat() if matched_end is not None else None,
              "correlation": None, "slope_return_pct_per_100bps": None,
              "intercept_return_pct": None, "r_squared": None,
              "mean_return_when_yields_rise_pct": None, "rising_intervals": 0,
              "mean_return_when_yields_fall_pct": None, "falling_intervals": 0}
    if not samples or (today - pd.Timestamp(samples[-1]["end"])).days > maximum_age_days:
        result["reason"] = "No recent matched interval"
    elif len(samples) < MINIMUM_INTERVALS:
        result["reason"] = f"Fewer than {MINIMUM_INTERVALS} matched weekly intervals"
    else:
        x = paired["yield_change_bps"].to_numpy(dtype=float)
        y = paired["adjusted_return_pct"].to_numpy(dtype=float)
        dx, dy = x - x.mean(), y - y.mean()
        if np.max(np.abs(dx)) < 1e-10 or np.max(np.abs(dy)) < 1e-10:
            result["reason"] = "Rates or adjusted returns do not vary enough to measure a relationship"
        else:
            slope = float(np.dot(dx, dy) / np.dot(dx, dx))
            correlation = float(np.corrcoef(x, y)[0, 1])
            result.update(status="available", correlation=correlation,
                          slope_return_pct_per_100bps=slope * 100,
                          intercept_return_pct=float(y.mean() - slope * x.mean()), r_squared=correlation ** 2,
                          rising_intervals=int((x > 1e-10).sum()), falling_intervals=int((x < -1e-10).sum()))
            result["mean_return_when_yields_rise_pct"] = float(y[x > 1e-10].mean()) if result["rising_intervals"] else None
            result["mean_return_when_yields_fall_pct"] = float(y[x < -1e-10].mean()) if result["falling_intervals"] else None
    return result, samples


def attach_price_rate_relationships(result, prices, snapshot, proxies):
    """Keep summaries separate from raw pairs so the AI receives compact evidence."""
    summaries, samples = [], []
    for ticker in result["market_source"]["tickers"]:
        proxy = proxies.get(ticker)
        if proxy is None:
            summaries.append({"ticker": ticker, "proxy_series": None, "status": "unverified",
                              "reason": "No maturity proxy configured", "intervals": 0})
            continue
        summary, pairs = price_rate_relationship(
            prices, snapshot["series"].get(proxy, {}).get("observations", []), ticker, proxy,
            result["analysis_date"], result["policy"]["maximum_price_age_days"])
        summaries.append(summary)
        samples.extend(pairs)
    result["rate_price_relationships"] = {"instruments": summaries, "method": METHOD}
    result["rate_price_observations"] = samples
    return result
