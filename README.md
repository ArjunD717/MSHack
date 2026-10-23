# MSHack — Prototype 1: Claims first

A local investment research tool for an insurer with **USD 100,000**. It downloads
prices, searches allocations, checks explicit risk policies, compares a baseline,
and exports interactive evidence. The optional LLM explains the computed result.

## Run the dashboard

From the project folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local address shown by Streamlit (normally http://localhost:8501).
The first run downloads prices if there is no cache; later runs reuse local data.
The dashboard works without an AI key. Change assumptions in the sidebar and click
**Recalculate allocation**. Download the report and evidence from **Alternatives & export**.

For a fresh checkout, create the environment first with `py -m venv .venv`.
Use Python 3.10 or newer.

## Command-line workflow

```powershell
# Generate the allocation, checks, CSV exports and offline charts; no AI call.
.\.venv\Scripts\python.exe main.py --data-only

# Download fresh Yahoo prices before running the same checks.
.\.venv\Scripts\python.exe main.py --data-only --refresh

# Use another policy/fact snapshot, or override the policy budget.
.\.venv\Scripts\python.exe main.py --data-only --policy config/client_policy.json --facts config/fund_facts.json --budget 100000
```

`--no-ai` is an alias for `--data-only`. All reports are generated before an optional
AI request. The price source also runs independently with `yfinance_source.py`.

## FRED interest-rate integration

FRED is optional in the command-line pipeline. It adds dated rate context and
illustrations of bond-price sensitivity without changing the allocation rules,
income assumptions or required hypothetical rate-shock check. The dashboard has
not been changed; its UI can use the backend functions below.

Register a [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html), then
set it in the same PowerShell session used to run the pipeline:

```powershell
$env:FRED_API_KEY = "your-fred-api-key"
.\.venv\Scripts\python.exe main.py --data-only --fred

# Refresh only FRED (also enables the integration).
.\.venv\Scripts\python.exe main.py --data-only --refresh-fred

# Refresh both price history and FRED.
.\.venv\Scripts\python.exe main.py --data-only --refresh --refresh-fred

# Fetch or inspect rate data independently of portfolio validation.
.\.venv\Scripts\python.exe fred_source.py --refresh
```

No additional dependencies are required. Keys are used for API requests only;
the generated files and error messages exclude the credential and request URL.
The cache is `data/fred_rates.json` and can be reused without a key. The source
uses the official [FRED observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html).

| Series | Role |
| --- | --- |
| [DFF](https://fred.stlouisfed.org/series/DFF) | Effective federal funds rate, for economic context |
| [DGS3MO](https://fred.stlouisfed.org/series/DGS3MO) | 3-month Treasury yield, a maturity proxy for BIL |
| [DGS2](https://fred.stlouisfed.org/series/DGS2) | 2-year Treasury yield, a maturity proxy for SHY |
| [DGS10](https://fred.stlouisfed.org/series/DGS10) | 10-year Treasury yield, a maturity proxy for IEF |

All rates are annual percentages. Changes are expressed in basis points: a move
from 4.00% to 4.25% is +25 bps. Each series includes its latest observation,
freshness, and changes over 30 and 365 calendar days. Each window ends on that
series' latest available date; the earlier endpoint must be on/before the target
date and within seven calendar days of it. Both actual endpoints are retained.
Missing observations (`.`) stay missing; rates are never forward-filled or
replaced with zero. Negative yields are supported.

For selected, provisional, baseline and alternative portfolios, the backend adds:

```text
estimated price change = -holding dollars × reviewed effective duration × (yield change in bps / 10,000)
```

For example, $75,000 in a fund with duration 1.8 has an estimated $337.50 price
loss for a +25 bps move in its proxy yield. A negative yield move gives a gain.
These scenarios apply observed moves to today's holdings. They do **not** explain
actual past performance, forecast future prices or change portfolio selection.
Do not add them to the historical replay: the price data already reflects rate
movements. DFF does not replace the assumed cash-account interest rate, and
Treasury yields do not replace fund SEC yields. Curve proxies are approximate;
the estimates omit income, convexity and other price drivers.

Latest observations older than the policy's `maximum_price_age_days` remain
visible with an `unverified` status; their changes and portfolio impacts become
unavailable. Missing earlier endpoints, missing reviewed duration, unsupported
tickers, and moves beyond the existing model's 300 bps limit also produce
unverified estimates rather than zero. A missing unused proxy does not prevent
calculations for other holdings. CASH has no bond-price effect in these scenarios.
The base allocation's pass/fail status is independent of this optional evidence.

`--start` and `--end` also control FRED's requested history; `--end` is exclusive.
A different request requires `--refresh-fred` or another data directory. Cache
reuse does not fetch updates automatically. Failed or incomplete downloads raise
an error and leave the previous cache intact; stale data is never substituted
silently. An explicitly requested FRED run with no key and no cache fails with
setup guidance. Downloaded observations reflect the FRED vintage at retrieval,
not the information available at each historical investment date.

Successful FRED runs include `interest_rates` in `validation.json` and in the AI
payload's `validation_result`. Individual portfolios contain
`observed_rate_scenarios`. Summaries appear in `report.md`, `report.html` and CLI
output. The full observation history remains in `fred_rates.json`, outside the
AI payload. Running without either FRED flag preserves the original pipeline.

The pipeline also measures the relationship between actual Yahoo adjusted-price
returns and FRED yield changes. For each fund it matches both sources on exact
dates, keeps the last matched date per Friday-ending week, and compares
non-overlapping intervals of 4–10 calendar days. It excludes longer gaps and
short partial weeks rather than filling missing data. At least 26 recent-ended
intervals with varying yields and returns are required; this is a prototype
evidence threshold, not a statistical significance test.

`rate_price_relationships` contains the sample dates/count, correlation, fitted
return sensitivity per +100 bps, R-squared, and average returns when yields rose
or fell. It measures changes rather than correlating price and rate levels.
This is a descriptive fit on the same historical sample, not a causal claim or
an out-of-sample forecast. Adjusted-price returns include distributions; these
are distinct from the duration-based price estimates. Sources can differ in
within-day timestamps, and relationships can change over time.

The offline report adds a scatter chart and fitted lines. Exact paired intervals
are exported to `rate_price_observations.csv` and retained in validation evidence;
they are excluded from the AI payload, which receives only summary statistics.
On a run without FRED the paired CSV is written with headers only, clearing prior
observations from that output. The allocation objective and required policy
checks still operate as before. The Streamlit UI remains unchanged.

For teammates integrating the new UI:

```python
from fred_source import prepare_rate_data, attach_rate_analysis

snapshot = prepare_rate_data(start=start, end=end, data_dir=data_dir, refresh=False)
attach_rate_analysis(result, snapshot, prices=prices)  # result is from run_validation()
# Then call save_outputs() and build_payload() with the enriched result.
```

`summarize_rates(snapshot, as_of=..., maximum_age_days=7)` exposes the rate context
alone. Only observations on/before `as_of` are used, but this does not recreate a
historical vintage. Use a separate FRED refresh control and include the snapshot
in the UI's result-cache fingerprint so refreshed data invalidates old results.

To enable the AI explanation in the dashboard, paste your key into the masked
**Azure API key (current session)** field near the bottom. It is kept in the
current dashboard session and is not saved to project files or reports.

Alternatively, set the key before starting the dashboard or CLI. A key set in a
different PowerShell window will not update an already-running server:

```powershell
$env:AZURE_OPENAI_API_KEY = "your-current-hackathon-key"
.\.venv\Scripts\python.exe main.py
```

The original endpoint, API version and model deployment remain defaults. Optional
overrides are `AZURE_OPENAI_ENDPOINT`, `OPENAI_API_VERSION` and `AZURE_OPENAI_MODEL`.
This prototype does not automatically load `.env` files. No credential is stored in
source or sent to the dashboard browser. `analysis.md` is only updated after a
successful AI call; an existing explanation can belong to an earlier run. The
interactive dashboard clears its displayed explanation whenever inputs change.

## Editable demo assumptions

`config/client_policy.json` is the default policy. These are team-selected examples,
not agreed client requirements or universal definitions of low risk.

| Setting | Default |
| --- | --- |
| Budget and liability currency | $100,000 USD |
| Planning horizon | 12 months |
| Immediately accessible cash | At least $25,000 |
| Historical annualized volatility | At most 2% |
| Historical maximum drawdown | At most 5% |
| Rate shock | +100 basis points (1 percentage point) |
| Loss allowed under that rate shock | At most $2,000 |
| One total stressed claim | $40,000 due in 30 calendar days |
| Assumed access to ETF sale proceeds | After 3 calendar days |
| Maximum quoted 30-day median spread | 0.10% |
| Maximum price / issuer-fact age | 7 / 30 calendar days |
| Required common history | At least 252 return observations |
| Allocation increments | 5 percentage points |
| Assumed cash interest | 0% |
| Baseline | Required cash reserve plus the rest in BIL |

The claim is a single total payment, not an additional payment on top of the
reserve. Only CASH counts as immediately accessible; an ETF is not cash. Sale
access is an explicit operating assumption, not a guarantee of settlement or
liquidity. CASH assumes an accessible USD account; bank credit and deposit
protection are not independently checked. Its chosen interest rate is also used
as a constant assumption in the historical replay.

The planning horizon scales an illustrative income run-rate; it does not turn
current yield into a forecast or model a schedule of future claims. The one stress
claim is evaluated separately from the historical replay.

## How allocation selection works

1. Read cached adjusted prices and the separately dated issuer-fact snapshot.
2. Align all funds to the same overlapping window, rejecting internal missing
   rows and invalid or duplicate prices. No forward filling or guessed returns.
3. Generate all nonnegative grid allocations across CASH and the funds that sum
   to 100%. Three funds plus CASH at 5% increments gives 1,771 candidates.
4. Simulate each portfolio's combined buy-and-hold path, with an initial allocation
   and drifting weights. Calculate trailing-252-return annualized volatility and
   maximum drawdown across the full common window, including the initial peak.
5. Check cash needs, price freshness, common history, permitted exposure, currency,
   duration/yield evidence, quoted spread, rate stress and stressed claim coverage.
6. Rank only allocations that pass every check by the highest **current annual
   income run-rate**. Ties favour smaller historical drawdown, then more cash.
   There is no rule requiring the tool to invest in every fund.
7. Compare with a cash-plus-BIL baseline, checked against the same constraints.

Each check is **PASS**, **FAIL** or **UNVERIFIED**. Missing, stale or future-dated
facts are never silently turned into zero. Unknown evidence blocks a passing
allocation containing that fund. If only cash passes, the tool can select cash;
that does not establish that an unverified investment would be inferior.

If no candidate passes, the report says **no feasible allocation** or **insufficient
evidence**. It never relaxes limits. An unverified illustration may be shown with
an explicit label; it is not a validated selection. The full grid, including
failed and unverified candidates, is exported for inspection.

## Income, stress and historical validation

Income run-rate is allocation dollars multiplied by source-dated **30-day SEC
yields**, plus assumed cash interest. SEC yields are after fund expenses, so the
expense ratio is not deducted again. Waiver treatment can differ between funds.
This comparison assumes unchanged yields and invested balances; it is not promised
income, expected total return or a forecast of value at the horizon.

The rate stress uses `price change ≈ -effective duration × yield change`. It is a
first-order, parallel-shift approximation; no convexity, default, curve-shape or
market-closure model is included. Stress claims access combines the upward rate
shock and a full quoted spread haircut, then applies the assumed sale delay.
It does not add future income to cover the claim.

Historical replay uses provider-adjusted prices as a reinvested-distribution
proxy. It has no claims withdrawals, rebalancing, commissions, sale costs or taxes.
Historical drawdown is calculated from the **combined portfolio path**, never by
averaging individual drawdowns. Observed 2020 and 2022 windows are labelled with
the actual coverage available. The tool uses already-known history: this is
retrospective validation, not an untouched out-of-sample predictive test.

The default funds are BIL (1–3 month Treasury bills), SHY (1–3 year Treasuries), and
IEF (7–10 year Treasuries). They share US Treasury exposure; adding fund tickers
does not remove this common risk. Eligibility here means a sourced Treasury ETF
classification, not a credit-rating, solvency or regulatory capital assessment.

## Issuer evidence: update separately from prices

`config/fund_facts.json` contains manually reviewed issuer values with individual
observation dates for currency/classification, effective duration, SEC yield and
30-day median spread. The bundled snapshot was reviewed on **23 October 2026**.
Its fields will become UNVERIFIED once the policy's age limit is exceeded.

To update it:

1. Follow each fund's `source_url` to the issuer page.
2. Copy the issuer's value and its **as-of date** for each field. Use `null` or omit
   a field when unavailable; do not guess a duration, yield, currency or spread.
3. Retain the source URL and update the review date. Do not substitute trailing
   distribution yield or yield to maturity for 30-day SEC yield.
4. Recalculate. Changes to facts are part of the report and dashboard cache key.

Refreshing Yahoo prices does **not** refresh these issuer snapshots. Current fund
facts are never applied as if they were historical predictors in the price replay.

## Price source and custom universes

```powershell
.\.venv\Scripts\python.exe main.py --data-only --refresh --tickers BIL SHY --start 2020-01-01 --end 2026-10-23
```

Add reviewed entries for custom tickers to the facts file, and set the policy's
baseline ticker if BIL is absent. Without required facts those exposures remain
unverified. The dashboard uses the default three-fund universe; the CLI supports
custom universes. The grid is capped at 25,000 candidates to keep the demo responsive.

Dates use YYYY-MM-DD: start is inclusive, end is exclusive. By default today's UTC
bar is excluded to avoid an incomplete trading day. Cache settings must match the
requested symbols and dates; otherwise use `--refresh` or a different `--data-dir`.
Adjusted prices are not executable quotes. Basic gap checks do not independently
verify the complete exchange calendar or every omitted trading day.

## Outputs (default: data/)

| File | Contents |
| --- | --- |
| `prices.csv`, `metadata.json`, `summary.json` | Price cache, provenance and individual fund measurements |
| `validation.json` | Policy, evidence, selected allocation, baseline, checks and full search audit |
| `candidate_allocations.csv` | Every allocation, measurements, status and rejection reasons |
| `portfolio_comparison.csv` | Selected/provisional allocation and baseline |
| `portfolio_history.csv` | Common-date values and drawdowns of the portfolios shown |
| `report.md` | Deterministic written assessment, available without AI |
| `report.html` | Self-contained interactive charts; opens offline with no CDN |
| `model_input.json` | Exact compact evidence packet sent to the LLM (full search excluded) |
| `analysis.md` | Latest successful optional AI explanation |

Data is ignored by Git. `portfolio.py` owns calculations and selection;
`reporting.py` owns charts/exports; `app.py` provides the local dashboard;
`main.py` connects the workflow and optional LLM. `embeddings.py` remains a separate
example and is not part of this structured-data pipeline.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover known financial calculations, cent-exact budgets, incomplete evidence,
currency mismatches, stale/future dates, claim timing, impossible constraints,
combined-portfolio drawdown, baseline checks, offline reports and mocked AI input.
External APIs are mocked in tests. A full cached-data check is `main.py --data-only`.

References: [BIL issuer](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-bloomberg-1-3-month-t-bill-etf-bil),
[SHY issuer](https://www.ishares.com/us/products/239452/ishares-13-year-treasury-bond-etf),
[IEF issuer](https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf),
[yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html),
[OpenAI Python client](https://developers.openai.com/api/reference/python),
[Streamlit](https://docs.streamlit.io/develop/api-reference),
[Plotly offline HTML](https://plotly.com/python/interactive-html-export/).
