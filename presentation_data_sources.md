# Presentation data sources

Compiled from the project code, source snapshots and saved outputs on 23 October 2026. Descriptions below document what the prototype used; issuer values have not been independently rechecked for this summary.

| Data source | Data supplied and outcomes generated (2 sentences per source) |
| --- | --- |
| Yahoo Finance, accessed through yfinance — BIL, SHY and IEF | Supplied daily prices adjusted for distributions and splits for BIL, SHY and IEF, covering 2 January 2019–22 October 2026 in the saved run. These generated individual-fund return, volatility and drawdown measurements, plus portfolio historical value paths and risk checks; the selected portfolio recorded 1.073% annualized volatility and a 4.347% maximum drawdown. |
| State Street — BIL issuer fact snapshot | Supplied manually recorded USD currency and US Treasury ETF classification, effective duration, 30-day SEC yield, median trading spread and expense ratio, with separate observation dates. The recorded 3.76% SEC yield and 0.10-year duration supported eligibility, income and stress calculations, including a cash-plus-BIL baseline with a $2,820 annual income run-rate and a $75 estimated loss under a +100-basis-point shock. |
| BlackRock / iShares — SHY issuer fact snapshot | Supplied manually recorded currency, Treasury ETF classification, effective duration, 30-day SEC yield, median trading spread and expense ratio. The recorded 4.68% SEC yield and 1.79-year duration supported selection of $75,000 SHY alongside $25,000 cash, producing a $3,510 annual income run-rate and a $1,342.50 estimated loss under a +100-basis-point shock. |
| BlackRock / iShares — IEF issuer fact snapshot | Supplied manually recorded currency, Treasury ETF classification, effective duration, 30-day SEC yield, median trading spread and expense ratio. Its recorded 5.04% SEC yield and 6.83-year duration fed candidate income and rate-stress calculations; IEF received no allocation in the saved selection after the combined policy and historical-risk checks. |
| Federal Reserve Bank of St. Louis — FRED | Supplied effective federal funds rates (DFF) and 3-month, 2-year and 10-year Treasury yields (DGS3MO, DGS2 and DGS10), generating dated rate summaries and 30-day/365-day changes. Applying the observed 2-year yield increases of 5 and 136 basis points to the selected SHY holding generated illustrative price changes of −$67.12 and −$1,825.80; these scenarios did not affect allocation selection. |
| Team-defined client policy — internal modelling input | Supplied the $100,000 budget, $25,000 minimum cash reserve, risk limits, $40,000 stressed claim, timing assumptions and 5-percentage-point allocation increments. Combined with the market and issuer evidence, these inputs generated 1,771 candidate allocations, of which 399 passed and 1,372 failed, and selected the passing allocation with the highest current income run-rate. |

## Source references

- Yahoo Finance histories: https://finance.yahoo.com/quote/BIL/history/ ; https://finance.yahoo.com/quote/SHY/history/ ; https://finance.yahoo.com/quote/IEF/history/
- BIL issuer: https://www.ssga.com/us/en/individual/etfs/state-street-spdr-bloomberg-1-3-month-t-bill-etf-bil
- SHY issuer: https://www.ishares.com/us/products/239452/ishares-13-year-treasury-bond-etf
- IEF issuer: https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf
- FRED: https://fred.stlouisfed.org/series/DFF ; https://fred.stlouisfed.org/series/DGS3MO ; https://fred.stlouisfed.org/series/DGS2 ; https://fred.stlouisfed.org/series/DGS10
- Internal snapshots: config/fund_facts.json and config/client_policy.json.
- Saved results: data/report.md, data/portfolio_comparison.csv, data/summary.json and data/metadata.json.

## Presentation notes

- Income run-rates assume unchanged yields and balances; they are not forecasts or guaranteed payments. Historical risk measurements and duration stress estimates describe different calculations.
- The policy is a team-selected demonstration, not a documented real client's agreed requirements.
- The code also implements matched weekly price/rate correlations, regression slopes and R-squared using Yahoo and FRED together. These are not reported as already-generated findings here because the saved report inspected does not contain those results.
- Azure OpenAI is an optional explanation service using the calculated evidence, not an additional financial data source. Streamlit and Plotly present the results; they do not supply market data.
