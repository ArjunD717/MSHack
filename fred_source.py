"""FRED rate history and informational bond-price scenarios.

FRED yields are market proxies, not ETF yields or cash-account rates. This module
does not change selection, income assumptions, or the policy's required stress.
"""

import argparse
import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


SERIES = {
    "DFF": "Effective federal funds rate",
    "DGS3MO": "3-month US Treasury yield",
    "DGS2": "2-year US Treasury yield",
    "DGS10": "10-year US Treasury yield",
}
FUND_RATE_PROXIES = {"BIL": "DGS3MO", "SHY": "DGS2", "IEF": "DGS10"}
ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"


def _observations(rows, start, end):
    """FRED's '.' is missing, never a zero or a forward-filled rate."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("FRED returned no observations.")
    parsed = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        if not start <= day <= end or day in parsed:
            raise ValueError("FRED observations contain duplicate or out-of-window dates.")
        raw = row["value"]
        value = None if raw in (".", None) else float(raw)
        if isinstance(raw, bool) or (value is not None and not math.isfinite(value)):
            raise ValueError("FRED observations contain invalid rates.")
        parsed[day] = value
    if all(value is None for value in parsed.values()):
        raise ValueError("FRED returned no usable rates.")
    return [{"date": day.isoformat(), "value": value} for day, value in sorted(parsed.items())]


def prepare_rate_data(start="2019-01-01", end=None, data_dir=DEFAULT_DATA_DIR, refresh=False, api_key=None):
    """Fetch four series or reuse a matching cache. End is exclusive, like prices.

    Failed downloads retain the existing cache and raise sanitized errors. A
    cached snapshot can be reused without credentials; freshness is separate.
    """
    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
    if start_day >= end_day:
        raise ValueError("FRED start must be earlier than the exclusive end date.")
    request = {"start": start, "requested_end": end, "series_ids": list(SERIES)}
    cache = Path(data_dir) / "fred_rates.json"
    if cache.exists() and not refresh:
        try:
            snapshot = json.loads(cache.read_text(encoding="utf-8"))
            if snapshot["schema_version"] != 1 or snapshot["request"] != request:
                raise ValueError("FRED cache request differs.")
            window_end = date.fromisoformat(snapshot["observation_end"])
            if not start_day <= window_end < end_day or set(snapshot["series"]) != set(SERIES):
                raise ValueError("FRED cache is incomplete or has an invalid window.")
            fetched = datetime.fromisoformat(snapshot["fetched_at_utc"])
            if fetched.tzinfo is None:
                raise ValueError("FRED fetch timestamp needs a timezone.")
            for series_id in SERIES:
                snapshot["series"][series_id]["observations"] = _observations(
                    snapshot["series"][series_id]["observations"], start_day, window_end)
        except (ValueError, KeyError, TypeError, OverflowError):
            raise ValueError("FRED cache is invalid or its request differs. Use --refresh-fred or another data directory.") from None
        return snapshot

    key = (api_key or os.environ.get("FRED_API_KEY", "")).strip()
    if not key:
        raise ValueError("Set FRED_API_KEY to download rates, or reuse an existing FRED cache.")
    window_end = end_day - timedelta(days=1)
    series = {}
    for series_id, title in SERIES.items():
        query = urlencode({"series_id": series_id, "api_key": key, "file_type": "json",
                           "observation_start": start, "observation_end": window_end.isoformat(),
                           "sort_order": "asc", "limit": 100000})
        try:
            with urlopen(ENDPOINT + "?" + query, timeout=15) as response:
                payload = json.load(response)
            rows = payload["observations"]
            if int(payload["count"]) != len(rows):
                raise ValueError("FRED response is incomplete.")
            observations = _observations(rows, start_day, window_end)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ValueError(f"FRED request failed for {series_id} (HTTP {code}). Check your key and retry.") from None
        except (URLError, TimeoutError, OSError):
            raise ValueError(f"FRED connection failed for {series_id}. Check connectivity and retry.") from None
        except (ValueError, KeyError, TypeError, OverflowError):
            raise ValueError(f"FRED response is invalid or incomplete for {series_id}; existing cache retained.") from None
        series[series_id] = {"title": title, "units": "Percent",
                             "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
                             "observations": observations}
    snapshot = {"schema_version": 1, "request": request, "observation_end": window_end.isoformat(),
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "series": series}
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(cache)
    return snapshot


def summarize_rates(snapshot, as_of=None, maximum_age_days=7):
    """Changes use available endpoints, never future observations or filled gaps.

    Windows end on each series' latest observation, not the fetch date. At each
    earlier target use an observation on/before it, no more than 7 days earlier.
    """
    today = as_of or datetime.now(timezone.utc).date()
    summary = {}
    for series_id in SERIES:
        source = snapshot["series"].get(series_id, {})
        observations = [(date.fromisoformat(row["date"]), row["value"]) for row in source.get("observations", [])
                        if row["value"] is not None and date.fromisoformat(row["date"]) <= today]
        observations.sort()
        latest_day, latest = observations[-1] if observations else (None, None)
        fresh = latest_day is not None and (today - latest_day).days <= maximum_age_days
        changes = {}
        for label, days in (("30d", 30), ("365d", 365)):
            target = latest_day - timedelta(days=days) if latest_day else None
            prior = [(day, value) for day, value in observations if target and day <= target]
            earlier_day, earlier = prior[-1] if prior else (None, None)
            valid = fresh and earlier_day is not None and (target - earlier_day).days <= 7
            changes[label] = {"status": "available" if valid else "unverified",
                              "target_date": target.isoformat() if target else None,
                              "start": earlier_day.isoformat() if earlier_day else None,
                              "end": latest_day.isoformat() if latest_day else None,
                              "change_bps": round((latest - earlier) * 100, 6) if valid else None}
        summary[series_id] = {"title": SERIES[series_id], "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
                              "units": "Percent", "latest_date": latest_day.isoformat() if latest_day else None,
                              "latest_rate_pct": latest, "status": "available" if fresh else "unverified",
                              "age_days": (today - latest_day).days if latest_day else None, "changes": changes}
    return {"analysis_date": today.isoformat(), "fetched_at_utc": snapshot["fetched_at_utc"],
            "maximum_age_days": maximum_age_days, "series": summary}


def attach_rate_analysis(result, snapshot, prices=None):
    """Enrich a validation result in place; selection and required checks stay intact."""
    context = summarize_rates(snapshot, date.fromisoformat(result["analysis_date"]),
                              result["policy"]["maximum_price_age_days"])
    result["interest_rates"] = context
    evidence = {fund["ticker"]: fund for fund in result["fund_evidence"]}
    candidates = [result.get(name) for name in ("selected", "provisional", "baseline")]
    candidates += result.get("alternatives", [])
    for candidate in candidates:
        if candidate is None:
            continue
        scenarios = []
        for window in ("30d", "365d"):
            impacts = []
            for ticker, amount in candidate["amounts_usd"].items():
                if ticker == "CASH" or amount <= 0:
                    continue
                proxy = FUND_RATE_PROXIES.get(ticker)
                change = context["series"].get(proxy, {}).get("changes", {}).get(window, {})
                delta = change.get("change_bps")
                duration = evidence.get(ticker, {}).get("duration_years")
                usable = delta is not None and abs(delta) <= 300 and duration is not None
                reason = None
                if not usable:
                    reason = ("No maturity proxy configured" if proxy is None else
                              "Current duration evidence missing" if duration is None else
                              "Rate change unavailable or stale" if delta is None else
                              "Rate change exceeds the 300 bps approximation limit")
                impacts.append({"ticker": ticker, "proxy_series": proxy,
                                "start": change.get("start"), "end": change.get("end"),
                                "change_bps": delta, "duration_years": duration,
                                "estimated_value_change_usd": round(-amount * duration * delta / 10000, 2) if usable else None,
                                "status": "available" if usable else "unverified", "reason": reason})
            complete = all(item["status"] == "available" for item in impacts)
            scenarios.append({"window": window, "status": "available" if complete else "unverified",
                              "estimated_value_change_usd": round(sum(item["estimated_value_change_usd"] for item in impacts), 2) if complete else None,
                              "holdings": impacts})
        candidate["observed_rate_scenarios"] = scenarios
    context["method"] = (
        "Illustration: estimated price change = minus today's dollar holdings × current reviewed effective duration × observed proxy yield change (bps / 10,000). "
        "A rate rise implies a price fall. BIL uses DGS3MO, SHY DGS2, IEF DGS10; these are maturity proxies, not exact fund holdings. "
        "DFF provides context only. Each proxy uses its own dated endpoints. Missing or stale evidence and moves over 300 bps are unverified. "
        "This applies historical yield moves to today's holdings, not an attribution of actual historical returns or a forecast. "
        "It omits convexity, income and other price drivers. It does not alter allocation checks, cash interest or SEC yields, "
        "and must not be added to historical price returns (which already reflect rate movements). "
        "Data reflects the downloaded FRED vintage, not a point-in-time backtest."
    )
    if prices is not None:
        from rate_relationships import attach_price_rate_relationships
        attach_price_rate_relationships(result, prices, snapshot, FUND_RATE_PROXIES)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", help="Exclusive YYYY-MM-DD")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        snapshot = prepare_rate_data(**vars(args))
        print(json.dumps(summarize_rates(snapshot), indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Rate data error: {exc}\n")


if __name__ == "__main__":
    main()
