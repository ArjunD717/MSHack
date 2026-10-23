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
