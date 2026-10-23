"""Prototype 0: download daily adjusted prices, cache them, and build evidence."""

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = ["BIL", "SHY", "IEF"]  # Comparison examples, not recommendations.
DEFAULT_START = "2019-01-01"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


def add_data_arguments(parser):
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--start", default=DEFAULT_START, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", help="Exclusive YYYY-MM-DD; defaults to today")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--refresh", action="store_true", help="Download fresh prices")


def normalize_prices(download, tickers):
    """Turn yfinance's single- or multi-ticker result into a flat price table."""
    if download is None or download.empty:
        raise ValueError("Yahoo Finance returned no prices. Check connectivity, symbols and dates.")
    if isinstance(download.columns, pd.MultiIndex):
        close = download["Close"]
    elif len(tickers) == 1 and "Close" in download:
        close = download[["Close"]].rename(columns={"Close": tickers[0]})
    else:
        raise ValueError("The download did not contain the expected adjusted Close columns.")

    frames = []
    for ticker in tickers:
        if ticker not in close:
            raise ValueError(f"No prices returned for {ticker}; no partial dataset will be used.")
        series = pd.to_numeric(close[ticker], errors="raise")
        valid = series.dropna()
        if len(valid) < 2:
            raise ValueError(f"At least two prices are required for {ticker}.")
        # Leading blanks can reflect a later inception; internal blanks need investigation.
        if series.loc[valid.index[0]:valid.index[-1]].isna().any():
            raise ValueError(f"{ticker} has gaps inside its price history. Check the source data.")
        if not valid.map(math.isfinite).all() or (valid <= 0).any():
            raise ValueError(f"{ticker} contains invalid or nonpositive prices.")
        frames.append(pd.DataFrame({
            "date": pd.DatetimeIndex(valid.index).strftime("%Y-%m-%d"),
            "ticker": ticker,
            "adjusted_close": valid.to_numpy(),
        }))
    return pd.concat(frames, ignore_index=True)


def summarize_prices(prices, metadata):
    """Calculate measured history. All *_pct values use percentage units."""
    required = {"date", "ticker", "adjusted_close"}
    if not required.issubset(prices.columns):
        raise ValueError("The price cache is missing required columns; run with --refresh.")
    if set(prices["ticker"].unique()) != set(metadata["tickers"]):
        raise ValueError("The price cache and metadata have different tickers; run with --refresh.")

    instruments = []
    for ticker in metadata["tickers"]:
        rows = prices.loc[prices["ticker"] == ticker].copy()
        rows["date"] = pd.to_datetime(rows["date"], errors="raise")
        rows = rows.sort_values("date")
        close = pd.to_numeric(rows["adjusted_close"], errors="raise")
        if len(close) < 2 or rows["date"].duplicated().any():
            raise ValueError(f"{ticker} needs at least two unique price dates.")
        if not close.map(math.isfinite).all() or (close <= 0).any():
            raise ValueError(f"{ticker} has missing, invalid or nonpositive cached prices.")
        returns = close.pct_change(fill_method=None).dropna()

        def trailing_return(sessions):
            if len(close) <= sessions:
                return None
            return round(float((close.iloc[-1] / close.iloc[-sessions - 1] - 1) * 100), 4)

        volatility = None
        if len(returns) >= 252:
            volatility = round(float(returns.tail(252).std(ddof=1) * math.sqrt(252) * 100), 4)
        instruments.append({
            "ticker": ticker,
            "source_url": f"https://finance.yahoo.com/quote/{ticker}/history/",
            "first_price_date": rows["date"].iloc[0].date().isoformat(),
            "last_price_date": rows["date"].iloc[-1].date().isoformat(),
            "price_observations": len(close),
            "last_adjusted_close": round(float(close.iloc[-1]), 6),
            "return_63_sessions_pct": trailing_return(63),
            "return_252_sessions_pct": trailing_return(252),
            "annualized_volatility_last_252_sessions_pct": volatility,
            "max_drawdown_full_history_pct": round(float((close / close.cummax() - 1).min() * 100), 4),
        })
    return {
        "schema_version": 1,
        "source": metadata,
        "methodology": {
            "prices": "Yahoo Finance Close with auto_adjust=True; adjusted for splits and distributions by the provider. Not a live executable quote.",
            "returns": "Changes in adjusted Close; a historical performance proxy, not a yield or forecast.",
            "windows": "63 and 252 trading observations approximate 3 and 12 months. Short histories produce null metrics.",
            "volatility": "Sample standard deviation of the last 252 daily returns times sqrt(252).",
            "drawdown": "Minimum adjusted Close / running peak - 1 over each instrument's full available history; reported as a negative percentage.",
        },
        "limitations": [
            "No credit ratings, duration, current yield, fees, spread or liquidity assessment is included.",
            "Currency is not verified by this price-only feed. The client budget is USD; confirm quote currencies before comparing custom tickers.",
            "History windows may differ. Compare first and last dates before ranking instruments.",
            "Cached data stays unchanged until --refresh. Check fetched_at_utc and last_price_date.",
            "Historical volatility and drawdown do not bound future losses or establish suitability.",
        ],
        "instruments": instruments,
    }


def prepare_market_data(tickers=DEFAULT_TICKERS, start=DEFAULT_START, end=None,
                        data_dir=DEFAULT_DATA_DIR, refresh=False):
    tickers = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
    if not tickers:
        raise ValueError("Supply at least one ticker.")
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
    if start_date >= end_date:
        raise ValueError("--start must be earlier than --end (which is exclusive).")
    data_dir = Path(data_dir)
    prices_path = data_dir / "prices.csv"
    metadata_path = data_dir / "metadata.json"
    request = {"tickers": tickers, "start": start, "requested_end": end}

    if not refresh and prices_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(metadata.get(key) != value for key, value in request.items()):
            raise ValueError("Cached symbols or dates differ from this request. Use --refresh or another --data-dir.")
        prices = pd.read_csv(prices_path)
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(data_dir / ".yfinance_cache"))
        download = yf.download(
            tickers, start=start, end=end_date.isoformat(), interval="1d",
            auto_adjust=True, group_by="column", multi_level_index=True,
            threads=False, progress=False, timeout=20,
        )
        prices = normalize_prices(download, tickers)
        metadata = {
            **request,
            "download_end_exclusive": end_date.isoformat(),
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider": "Yahoo Finance via yfinance",
            "yfinance_version": yf.__version__,
            "interval": "1d",
            "auto_adjust": True,
        }
        # Validate before replacing an existing good cache.
        summarize_prices(prices, metadata)
        prices.to_csv(prices_path, index=False)
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    summary = summarize_prices(prices, metadata)
    (data_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_data_arguments(parser)
    args = parser.parse_args()
    try:
        summary = prepare_market_data(**vars(args))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Data error: {exc}\n")
    print(json.dumps(summary, indent=2, allow_nan=False))
    print(f"\nSaved prices.csv, metadata.json and summary.json in {args.data_dir.resolve()}")


if __name__ == "__main__":
    main()
