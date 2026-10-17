# Trading Research OS (prototype)

A read-only Python CLI that gathers crypto market data, a public-company snapshot,
annual SEC revenue facts, a company overview, and recent headlines, then runs five
analyst departments and writes a Markdown research report. It does not place trades
or provide personalized investment advice.

## Run

Requires Python 3.10 or newer; no third-party packages are needed.

```powershell
python trading_os.py --asset bitcoin --company AAPL
python trading_os.py --asset ethereum --company MSFT --output reports\ethereum-msft.md
python -m unittest -v
```

`--asset` is a CoinGecko **coin ID** (for example, `bitcoin`, not `BTC`).
`--company` is a public-company ticker. Crypto market data and company quotes are
required; unavailable optional company overview, SEC revenue, and news sources are
reported as data notes in the output.

## Departments

1. **Technical Analysis** calculates daily-price moving averages, RSI, returns, and
   the observed 90-day range.
2. **Fundamental Analysis** summarizes crypto market capitalization/volume, company
   overview and quote, and latest annual SEC revenue when available.
3. **Sentiment Analysis** reviews recent Google News headlines with transparent,
   deliberately simple keyword counts.
4. **Risk Assessment** checks for fading 7-day momentum versus the 30-day trend,
   estimates historical daily volatility, and gives a range-based reference ratio.
5. **Thesis Synthesis** combines available directional evidence and reports an
   evidence-alignment score, reference support/resistance, and invalidation context.

The four analysis departments are scheduled concurrently; risk consumes the
technical output, and synthesis combines all four. The score is a heuristic—not an
AI-model confidence, probability of profit, or buy/sell recommendation. The
prototype currently uses transparent rule-based agents and public web data; it does
not connect to an LLM, broker, wallet, or trading venue.

## Data sources and caveats

- CoinGecko public API for USD crypto market and 90-day history.
- Yahoo Finance chart endpoint for company quote.
- U.S. SEC company ticker list and XBRL company facts for annual revenue.
- English Wikipedia for a short company overview and Google News RSS for headlines.

Public endpoints can rate-limit, change, or return incomplete/revised data. Company
SEC revenue may not be available for non-U.S. issuers or for companies whose revenue
uses other XBRL tags. Confirm source figures before relying on them. This prototype
is for research workflow exploration only, not financial advice.
