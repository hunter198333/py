"""Read-only, evidence-based crypto and company research pipeline."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


USER_AGENT = "TradingResearchOS/0.1 (research prototype)"
TIMEOUT_SECONDS = 20


class DataSourceError(RuntimeError):
    """Raised when a required public data source cannot provide valid data."""


@dataclass
class NewsItem:
    title: str
    published: str
    url: str


@dataclass
class Discovery:
    asset_id: str
    asset_name: str
    asset_symbol: str
    price_usd: float
    market_cap_usd: float
    volume_24h_usd: float
    change_24h_pct: float | None
    price_history: list[float]
    company_ticker: str
    company_name: str
    company_description: str | None
    company_price: float | None
    revenue_latest_usd: float | None
    revenue_prior_usd: float | None
    news: list[NewsItem]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    department: str
    findings: list[str]
    metrics: dict[str, float | str | None] = field(default_factory=dict)


def fetch_json(url: str, *, user_agent: str = USER_AGENT) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Could not retrieve data from {url}: {exc}") from exc


def fetch_text(url: str, *, user_agent: str = USER_AGENT) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/rss+xml, text/xml"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise DataSourceError(f"Could not retrieve data from {url}: {exc}") from exc


def discover_crypto(asset_id: str) -> tuple[dict[str, Any], list[float]]:
    encoded_id = urllib.parse.quote(asset_id.strip().lower(), safe="")
    market_url = (
        "https://api.coingecko.com/api/v3/coins/markets?"
        + urllib.parse.urlencode(
            {"vs_currency": "usd", "ids": asset_id.strip().lower(), "price_change_percentage": "24h"}
        )
    )
    market_data = fetch_json(market_url)
    if not isinstance(market_data, list) or not market_data:
        raise DataSourceError(f"CoinGecko did not find a crypto asset with ID '{asset_id}'.")
    coin = market_data[0]
    history_url = (
        f"https://api.coingecko.com/api/v3/coins/{encoded_id}/market_chart?"
        + urllib.parse.urlencode({"vs_currency": "usd", "days": "90"})
    )
    history_data = fetch_json(history_url)
    prices = [
        float(point[1])
        for point in history_data.get("prices", [])
        if isinstance(point, list) and len(point) > 1 and isinstance(point[1], (int, float))
    ]
    if len(prices) < 14:
        raise DataSourceError(f"CoinGecko returned too little price history for '{asset_id}'.")
    return coin, prices


def discover_company(
    ticker: str,
) -> tuple[str, str | None, float | None, float | None, float | None, list[str]]:
    encoded_ticker = urllib.parse.quote(ticker.strip().upper(), safe="")
    chart_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_ticker}?range=1y&interval=1d"
    chart_payload = fetch_json(chart_url)
    chart_results = chart_payload.get("chart", {}).get("result") or []
    chart_error = chart_payload.get("chart", {}).get("error")
    if not chart_results:
        message = chart_error.get("description") if isinstance(chart_error, dict) else "No quote found."
        raise DataSourceError(f"Yahoo Finance could not retrieve '{ticker}': {message}")
    meta = chart_results[0].get("meta", {})
    company_name = meta.get("longName") or meta.get("shortName") or ticker.strip().upper()
    current_price = meta.get("regularMarketPrice")
    description, description_warning = discover_company_description(company_name)
    revenue_latest, revenue_prior, sec_warning = discover_sec_revenue(ticker)
    warnings = [warning for warning in (description_warning, sec_warning) if warning]
    return company_name, description, _number(current_price), revenue_latest, revenue_prior, warnings


def discover_company_description(company_name: str) -> tuple[str | None, str | None]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": "true",
            "explaintext": "true",
            "titles": company_name,
        }
    )
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    try:
        payload = fetch_json(url, user_agent=f"{USER_AGENT}; contact: research-prototype")
    except DataSourceError as exc:
        return None, f"Wikipedia company overview unavailable ({exc})."
    pages = payload.get("query", {}).get("pages", {})
    extract = next(
        (
            page.get("extract", "").strip()
            for page in pages.values()
            if page.get("extract", "").strip()
        ),
        None,
    )
    if not extract:
        return None, f"No Wikipedia business overview found for {company_name}."
    return extract[:1200], None


def discover_sec_revenue(ticker: str) -> tuple[float | None, float | None, str | None]:
    headers_agent = "TradingResearchOS/0.1 research contact research-prototype@example.com"
    ticker_payload = fetch_json(
        "https://www.sec.gov/files/company_tickers.json", user_agent=headers_agent
    )
    matches = [
        record
        for record in ticker_payload.values()
        if str(record.get("ticker", "")).upper() == ticker.strip().upper()
    ]
    if not matches:
        return None, None, f"No SEC company facts available for ticker {ticker}."
    cik = int(matches[0]["cik_str"])
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    try:
        facts = fetch_json(facts_url, user_agent=headers_agent)
    except DataSourceError as exc:
        return None, None, f"SEC revenue data unavailable ({exc})."

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    revenue_tags = (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    )
    for tag in revenue_tags:
        units = us_gaap.get(tag, {}).get("units", {})
        annual_values = []
        for unit_records in units.values():
            annual_values.extend(
                entry
                for entry in unit_records
                if entry.get("form") == "10-K"
                and entry.get("fp") == "FY"
                and entry.get("start")
                and entry.get("end")
                and (datetime.fromisoformat(entry["end"]) - datetime.fromisoformat(entry["start"])).days >= 300
            )
        by_year_end: dict[str, dict[str, Any]] = {}
        for entry in annual_values:
            previous = by_year_end.get(entry["end"])
            if previous is None or entry.get("filed", "") > previous.get("filed", ""):
                by_year_end[entry["end"]] = entry
        latest = sorted(by_year_end.values(), key=lambda entry: entry["end"])
        if latest:
            prior = latest[-2]["val"] if len(latest) > 1 else None
            return float(latest[-1]["val"]), float(prior) if prior is not None else None, None
    return None, None, f"No annual SEC revenue fact found for ticker {ticker}."


def discover_news(query: str, limit: int = 10) -> list[NewsItem]:
    params = urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    try:
        feed = ET.fromstring(fetch_text(f"https://news.google.com/rss/search?{params}"))
    except ET.ParseError as exc:
        raise DataSourceError(f"Google News returned an invalid RSS feed: {exc}") from exc
    items = []
    for item in feed.findall("./channel/item")[:limit]:
        items.append(
            NewsItem(
                title=item.findtext("title", default="").strip(),
                published=item.findtext("pubDate", default="").strip(),
                url=item.findtext("link", default="").strip(),
            )
        )
    return items


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def discover(asset_id: str, ticker: str) -> Discovery:
    coin, prices = discover_crypto(asset_id)
    company_name, description, company_price, revenue, prior_revenue, company_warnings = discover_company(ticker)
    news_query = f"{company_name} OR {coin.get('name', asset_id)}"
    warnings = list(company_warnings)
    try:
        news = discover_news(news_query)
    except DataSourceError as exc:
        warnings.append(f"Google News unavailable ({exc}).")
        news = []
    return Discovery(
        asset_id=str(coin.get("id", asset_id)),
        asset_name=str(coin.get("name", asset_id)),
        asset_symbol=str(coin.get("symbol", "")).upper(),
        price_usd=float(coin["current_price"]),
        market_cap_usd=float(coin.get("market_cap") or 0),
        volume_24h_usd=float(coin.get("total_volume") or 0),
        change_24h_pct=_number(coin.get("price_change_percentage_24h")),
        price_history=prices,
        company_ticker=ticker.upper(),
        company_name=company_name,
        company_description=description,
        company_price=company_price,
        revenue_latest_usd=revenue,
        revenue_prior_usd=prior_revenue,
        news=news,
        warnings=warnings,
    )


def percent_change(start: float, end: float) -> float | None:
    return ((end / start) - 1) * 100 if start else None


def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    changes = [current - previous for previous, current in zip(prices[-period - 1 :], prices[-period:])]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100 - (100 / (1 + gains / losses))


def technical_agent(data: Discovery) -> Analysis:
    prices = data.price_history
    rsi = calculate_rsi(prices)
    sma_7 = sum(prices[-7:]) / min(7, len(prices))
    sma_30 = sum(prices[-30:]) / min(30, len(prices))
    change_7d = percent_change(prices[-8], prices[-1]) if len(prices) >= 8 else None
    change_30d = percent_change(prices[-31], prices[-1]) if len(prices) >= 31 else None
    trend = "above" if sma_7 > sma_30 else "below"
    findings = [
        f"Crypto spot is ${data.price_usd:,.4f}; 7-day SMA is ${sma_7:,.4f}, {trend} the 30-day SMA (${sma_30:,.4f}).",
        f"90-day observed range: ${min(prices):,.4f}–${max(prices):,.4f}.",
        f"14-period RSI is {rsi:.1f}." if rsi is not None else "Insufficient observations to calculate RSI.",
    ]
    return Analysis(
        "Technical Analysis",
        findings,
        {"rsi_14": rsi, "sma_7_usd": sma_7, "sma_30_usd": sma_30,
         "return_7d_pct": change_7d, "return_30d_pct": change_30d,
         "support_90d_usd": min(prices), "resistance_90d_usd": max(prices)},
    )


def fundamental_agent(data: Discovery) -> Analysis:
    growth = None
    if data.revenue_latest_usd is not None and data.revenue_prior_usd:
        growth = percent_change(data.revenue_prior_usd, data.revenue_latest_usd)
    findings = [
        f"{data.asset_name} ({data.asset_symbol}) market capitalization: ${data.market_cap_usd:,.0f}; "
        f"24-hour trading volume: ${data.volume_24h_usd:,.0f}.",
        f"{data.company_name} ({data.company_ticker}) last quoted price: "
        f"${data.company_price:,.2f}." if data.company_price is not None
        else f"No current quote was available for {data.company_name}.",
    ]
    if data.company_description:
        findings.append(data.company_description)
    if data.revenue_latest_usd is not None:
        findings.append(
            f"Latest annual SEC revenue: ${data.revenue_latest_usd:,.0f}."
            + (f" Year-over-year change: {growth:+.1f}%." if growth is not None else "")
        )
    else:
        findings.append("Annual SEC revenue was unavailable; do not infer business growth.")
    return Analysis(
        "Fundamental Analysis", findings,
        {"crypto_market_cap_usd": data.market_cap_usd, "crypto_volume_24h_usd": data.volume_24h_usd,
         "company_price_usd": data.company_price, "revenue_latest_usd": data.revenue_latest_usd,
         "revenue_growth_pct": growth},
    )


def sentiment_agent(data: Discovery) -> Analysis:
    positive_terms = re.compile(r"\b(gain|growth|surge|approval|adoption|beat|upgrade|launch)\b", re.I)
    negative_terms = re.compile(r"\b(loss|drop|fall|lawsuit|hack|ban|probe|miss|downgrade)\b", re.I)
    positive = sum(bool(positive_terms.search(item.title)) for item in data.news)
    negative = sum(bool(negative_terms.search(item.title)) for item in data.news)
    score = positive - negative
    tone = "positive" if score > 0 else "negative" if score < 0 else "mixed or inconclusive"
    findings = [
        f"Headline tone is {tone} based on simple keyword counts ({positive} positive, {negative} negative).",
        f"Reviewed {len(data.news)} recent headlines; keyword scoring is a weak proxy, not a model of market sentiment.",
    ]
    findings.extend(f"Headline: {item.title} ({item.published})" for item in data.news[:5])
    return Analysis("Sentiment Analysis", findings, {"positive_headlines": positive,
                                                      "negative_headlines": negative,
                                                      "sentiment_score": score})


def risk_agent(data: Discovery, technical: Analysis) -> Analysis:
    prices = data.price_history
    short_return = technical.metrics["return_7d_pct"]
    medium_return = technical.metrics["return_30d_pct"]
    rsi = technical.metrics["rsi_14"]
    momentum_fading = (
        isinstance(short_return, float) and isinstance(medium_return, float)
        and medium_return > 0 and short_return < medium_return / 4
    )
    declining_momentum = (
        isinstance(short_return, float) and isinstance(medium_return, float)
        and medium_return < 0 and short_return > medium_return / 4
    )
    returns = [
        current / previous - 1 for previous, current in zip(prices, prices[1:]) if previous
    ]
    daily_volatility = (
        math.sqrt(sum((item - sum(returns) / len(returns)) ** 2 for item in returns) / (len(returns) - 1)) * 100
        if len(returns) > 1 else None
    )
    findings = [
        "Possible positive-trend momentum fade: 7-day return has slowed materially versus 30 days."
        if momentum_fading else
        "Possible bearish-trend momentum fade: recent 7-day return is less negative than the 30-day pace."
        if declining_momentum else
        "No clear short-term momentum fade detected by the 7-day versus 30-day return rule.",
        f"RSI at {rsi:.1f} is above 70 (stretched)" if isinstance(rsi, float) and rsi > 70 else
        f"RSI at {rsi:.1f} is below 30 (stretched)" if isinstance(rsi, float) and rsi < 30 else
        f"RSI at {rsi:.1f} is not at a conventional extreme." if isinstance(rsi, float) else
        "RSI is unavailable.",
        f"Historical daily return volatility: {daily_volatility:.2f}% (descriptive, not a forecast)."
        if daily_volatility is not None else "Insufficient data to estimate return volatility.",
    ]
    support_metric = technical.metrics["support_90d_usd"]
    resistance_metric = technical.metrics["resistance_90d_usd"]
    support = float(support_metric) if isinstance(support_metric, (int, float)) else None
    resistance = float(resistance_metric) if isinstance(resistance_metric, (int, float)) else None
    current = data.price_usd
    reward_risk = (
        (resistance - current) / (current - support)
        if support is not None and resistance is not None
        and current > support and resistance > current
        else None
    )
    findings.append(
        f"90-day range-based reference reward/risk is {reward_risk:.2f}:1 from spot to range extremes; "
        "this is not a proposed trade setup."
        if reward_risk is not None else
        "A range-based reward/risk ratio is not meaningful at the current position in the 90-day range."
    )
    return Analysis(
        "Risk Assessment", findings,
        {"momentum_fading": "yes" if momentum_fading or declining_momentum else "no",
         "daily_volatility_pct": daily_volatility, "range_reward_risk": reward_risk},
    )


def synthesis_agent(
    data: Discovery, analyst_futures: dict[str, concurrent.futures.Future[Analysis]]
) -> Analysis:
    analyses = {name: future.result() for name, future in analyst_futures.items()}
    technical = analyses["technical"]
    fundamentals = analyses["fundamental"]
    sentiment = analyses["sentiment"]
    risk = analyses["risk"]
    signals = [
        technical.metrics.get("return_30d_pct"),
        fundamentals.metrics.get("revenue_growth_pct"),
        sentiment.metrics.get("sentiment_score"),
    ]
    positive = sum(isinstance(signal, (int, float)) and signal > 0 for signal in signals)
    negative = sum(isinstance(signal, (int, float)) and signal < 0 for signal in signals)
    confidence = min(90, 35 + 15 * abs(positive - negative))
    stance = "constructive" if positive > negative else "cautious" if negative > positive else "mixed / insufficiently aligned"
    support = technical.metrics["support_90d_usd"]
    resistance = technical.metrics["resistance_90d_usd"]
    findings = [
        f"Overall evidence is {stance}; this is a research summary, not personalized financial advice.",
        f"Directional evidence count: {positive} positive, {negative} negative, across available 30-day crypto return, "
        "annual company revenue growth, and headline keyword balance.",
        f"Evidence-alignment score: {confidence}/90. This is a transparent heuristic, not a probability of profit.",
        f"Scenario references: monitor support near ${support:,.4f} and resistance near ${resistance:,.4f}; "
        "a sustained break or rejection may invalidate a range-bound thesis.",
        "No entry, exit, position size, stop, or order is prescribed. Define a personal risk budget and reassess "
        "the thesis when its evidence changes.",
        risk.findings[0],
    ]
    return Analysis(
        "Thesis Synthesis", findings,
        {"stance": stance, "evidence_alignment_score_90": confidence,
         "positive_evidence_count": positive, "negative_evidence_count": negative,
         "reference_support_usd": support, "reference_resistance_usd": resistance},
    )


Agent = Callable[[Discovery], Analysis]


def run_agents(data: Discovery) -> list[Analysis]:
    def assess_risk(
        discovery: Discovery, technical_future: concurrent.futures.Future[Analysis]
    ) -> Analysis:
        return risk_agent(discovery, technical_future.result())

    with concurrent.futures.ThreadPoolExecutor(max_workers=5, thread_name_prefix="analyst") as pool:
        analyst_futures: dict[str, concurrent.futures.Future[Analysis]] = {
            "technical": pool.submit(technical_agent, data),
            "fundamental": pool.submit(fundamental_agent, data),
            "sentiment": pool.submit(sentiment_agent, data),
        }
        analyst_futures["risk"] = pool.submit(
            assess_risk, data, analyst_futures["technical"]
        )
        synthesis = pool.submit(synthesis_agent, data, analyst_futures)
        return [
            analyst_futures["technical"].result(),
            analyst_futures["fundamental"].result(),
            analyst_futures["sentiment"].result(),
            analyst_futures["risk"].result(),
            synthesis.result(),
        ]


def render_report(data: Discovery, analyses: list[Analysis]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Research report: {data.asset_name} + {data.company_name}",
        "",
        f"Generated {generated}. **Research only — not financial advice; no trades are placed.**",
        "",
        "## Discovery snapshot",
        f"- Crypto asset: {data.asset_name} ({data.asset_symbol}), ${data.price_usd:,.4f} USD",
        f"- Market capitalization: ${data.market_cap_usd:,.0f} USD",
        f"- 24-hour volume: ${data.volume_24h_usd:,.0f} USD",
        f"- 24-hour price change: {data.change_24h_pct:+.2f}%"
        if data.change_24h_pct is not None else "- 24-hour price change: unavailable",
        f"- Company: {data.company_name} ({data.company_ticker})"
        + (f", last quoted ${data.company_price:,.2f}" if data.company_price is not None else ""),
    ]
    if data.revenue_latest_usd is not None:
        lines.append(f"- Latest annual SEC revenue: ${data.revenue_latest_usd:,.0f} USD")
    if data.company_description:
        lines.extend(["", f"> {data.company_description}"])
    for warning in data.warnings:
        lines.append(f"- **Data note:** {warning}")
    lines.extend(["", "## Analyst departments"])
    for analysis in analyses:
        lines.extend(["", f"### {analysis.department}"])
        lines.extend(f"- {finding}" for finding in analysis.findings)
    lines.extend(
        [
            "",
            "## Sources and limitations",
            "- Crypto price, market capitalization, volume, and history: CoinGecko public API.",
            "- Company quote and annual revenue facts: Yahoo Finance chart endpoint and U.S. SEC company facts.",
            "- Company overview: English Wikipedia; news headlines: Google News RSS.",
            "- Indicators use daily crypto price observations. Headlines use simple keyword counts; neither establishes causation.",
            "- Verify all figures at source. Data may be delayed, unavailable, revised, or unsuitable for trading.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only, five-department crypto and company research pipeline."
    )
    parser.add_argument("--asset", required=True, help="CoinGecko asset ID, e.g. bitcoin or ethereum.")
    parser.add_argument("--company", required=True, help="Public company ticker, e.g. AAPL.")
    parser.add_argument("--output", type=Path, help="Optional Markdown report path; defaults to stdout.")
    args = parser.parse_args(argv)
    try:
        data = discover(args.asset, args.company)
        analyses = run_agents(data)
        report = render_report(data, analyses)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
            print(f"Research report saved to {args.output}")
        else:
            print(report)
        return 0
    except DataSourceError as exc:
        print(f"Research pipeline failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not write report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
