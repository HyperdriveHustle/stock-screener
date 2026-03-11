#!/usr/bin/env python3
"""Translate screener picks into Polymarket paper trades."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests


BASE_BET = 100.0
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
DISCOVERY_ORDERS = ("createdAt", "volume")
DEFAULT_SCAN_PAGES = 8
PAGE_SIZE = 100
TICKER_ALIASES = {
    "AAPL": ["Apple"],
    "JPM": ["JPMorgan", "JP Morgan", "Chase"],
    "LIN": ["Linde"],
    "MSFT": ["Microsoft"],
    "NEE": ["NextEra", "NextEra Energy"],
    "NVDA": ["NVIDIA", "Nvidia"],
    "UNH": ["UnitedHealth", "UnitedHealth Group"],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match screener picks to Polymarket markets")
    parser.add_argument("--session-dir", required=True, help="Path to output/runs/<session_id>")
    parser.add_argument("--bot", required=True, help="Poly Arena bot name")
    parser.add_argument("--poly-arena-url", default="http://localhost:8046", help="Poly Arena base URL")
    parser.add_argument("--min-volume", type=float, default=100.0, help="Minimum 24h volume filter")
    parser.add_argument("--max-spread", type=float, default=0.15, help="Maximum spread filter")
    parser.add_argument("--scan-pages", type=int, default=DEFAULT_SCAN_PAGES, help="How many 100-market pages to scan per discovery order")
    parser.add_argument("--dry-run", action="store_true", help="Do not post trades")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    session_dir = Path(args.session_dir).resolve()
    picks = load_screener_picks(session_dir)
    active_markets = discover_active_markets(args.scan_pages)
    results = []
    for pick in picks:
        market, diagnostics = match_pick_to_market(pick, active_markets, args.min_volume, args.max_spread)
        if not market:
            results.append({"ticker": pick["ticker"], "status": "no_market", "diagnostics": diagnostics})
            continue
        trade_body = build_trade_request(args.bot, pick, market)
        if args.dry_run:
            results.append(
                {
                    "ticker": pick["ticker"],
                    "status": "matched",
                    "trade": trade_body,
                    "market": summarize_market(market),
                    "diagnostics": diagnostics,
                }
            )
            continue
        response = requests.post(
            f"{args.poly_arena_url.rstrip('/')}/api/trade",
            json=trade_body,
            timeout=30,
        )
        response.raise_for_status()
        results.append(
            {
                "ticker": pick["ticker"],
                "status": "submitted",
                "response": response.json(),
                "market": summarize_market(market),
                "diagnostics": diagnostics,
            }
        )
    print(json.dumps({"session_id": session_dir.name, "results": results}, ensure_ascii=False, indent=2))


def load_screener_picks(session_dir: Path) -> list[dict]:
    final_selection = read_json(session_dir / "judge" / "final_selection.json")
    selected = set(final_selection.get("final_top_n") or [])
    picks = []
    for candidate in final_selection.get("ranked_candidates") or []:
        ticker = candidate.get("ticker")
        if not ticker or (selected and ticker not in selected):
            continue
        deep_analysis = read_json(session_dir / "symbols" / ticker / "llm" / "deep_analysis.json")
        picks.append(
            {
                "session_id": session_dir.name,
                "ticker": ticker,
                "direction": "bullish",
                "confidence": float(deep_analysis.get("confidence", 0.5)),
                "setup_type": deep_analysis.get("setup_type") or "unknown",
                "reasoning": candidate.get("selection_reason") or "",
            }
        )
    return picks


def discover_active_markets(scan_pages: int) -> list[dict]:
    merged = {}
    for order in DISCOVERY_ORDERS:
        for page in range(max(scan_pages, 1)):
            for market in fetch_market_page(order, page * PAGE_SIZE):
                market_id = str(market.get("id") or "")
                if market_id:
                    merged[market_id] = market
    return list(merged.values())


def fetch_market_page(order: str, offset: int) -> list[dict]:
    response = requests.get(
        GAMMA_URL,
        params={
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
            "order": order,
            "ascending": "false",
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def match_pick_to_market(
    pick: dict,
    active_markets: list[dict],
    min_volume: float,
    max_spread: float,
) -> tuple[dict | None, dict]:
    relevant = [market for market in active_markets if is_relevant_market(market, pick["ticker"])]
    tradeable = [market for market in relevant if is_tradeable_market(market, min_volume, max_spread)]
    if not tradeable:
        return None, {
            "active_markets_scanned": len(active_markets),
            "relevant_candidates": len(relevant),
            "tradeable_candidates": 0,
        }
    ranked = sorted(tradeable, key=market_score)
    return ranked[0], {
        "active_markets_scanned": len(active_markets),
        "relevant_candidates": len(relevant),
        "tradeable_candidates": len(tradeable),
    }


def is_tradeable_market(market: dict, min_volume: float, max_spread: float) -> bool:
    if market.get("closed") or not market.get("active") or not market.get("acceptingOrders"):
        return False
    volume = safe_float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume"))
    spread = safe_float(market.get("spread"))
    price = extract_yes_price(market)
    if volume < min_volume or spread <= 0 or spread > max_spread:
        return False
    return 0.2 <= price <= 0.8


def is_relevant_market(market: dict, ticker: str) -> bool:
    question = market_text(market)
    if not has_close_keywords(question):
        return False
    return any(alias_matches(question, alias) for alias in build_aliases(ticker))


def market_text(market: dict) -> str:
    event_titles = " ".join(event.get("title", "") for event in market.get("events") or [])
    parts = [
        market.get("question") or "",
        market.get("slug") or "",
        event_titles,
    ]
    return " ".join(part for part in parts if part).upper()


def has_close_keywords(text: str) -> bool:
    has_price_action = any(keyword in text for keyword in ("CLOSE", "CLOSES", "CLOSING", "PRICE"))
    has_strike = any(keyword in text for keyword in ("ABOVE", "BELOW", "AT OR ABOVE", "AT OR BELOW"))
    return has_price_action and has_strike


def build_aliases(ticker: str) -> list[str]:
    return [ticker.upper(), *TICKER_ALIASES.get(ticker.upper(), [])]


def alias_matches(text: str, alias: str) -> bool:
    if alias.isupper() and alias.isalpha() and len(alias) <= 5:
        return bool(re.search(rf"\b{re.escape(alias.upper())}\b", text))
    return alias.upper() in text


def market_score(market: dict) -> tuple[float, float, float]:
    yes_price = extract_yes_price(market)
    distance = abs(yes_price - 0.5)
    spread = safe_float(market.get("spread"))
    volume = safe_float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume"))
    return (distance, spread, -volume)


def extract_yes_price(market: dict) -> float:
    best_ask = safe_float(market.get("bestAsk"))
    if best_ask > 0:
        return best_ask
    last_trade = safe_float(market.get("lastTradePrice"))
    if last_trade > 0:
        return last_trade
    outcome_prices = market.get("outcomePrices")
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except json.JSONDecodeError:
            outcome_prices = None
    if isinstance(outcome_prices, list) and outcome_prices:
        return safe_float(outcome_prices[0])
    return 0.0


def build_trade_request(bot: str, pick: dict, market: dict) -> dict:
    amount = round(BASE_BET * max(min(pick["confidence"], 1.0), 0.0), 2)
    side = "YES" if pick["direction"] == "bullish" else "NO"
    reasoning = f"{pick['ticker']} {pick['setup_type']} | conf {pick['confidence']:.2f}"
    return {
        "bot": bot,
        "action": "buy",
        "market_id": str(market["id"]),
        "side": side,
        "amount": amount,
        "reasoning": reasoning,
        "bot_prediction": {
            "source": "screener",
            "session_id": pick["session_id"],
            "ticker": pick["ticker"],
            "direction": pick["direction"],
            "confidence": pick["confidence"],
            "setup_type": pick["setup_type"][:80],
        },
    }


def summarize_market(market: dict) -> dict:
    return {
        "id": market.get("id"),
        "question": market.get("question"),
        "yes_price": extract_yes_price(market),
        "spread": safe_float(market.get("spread")),
        "volume_24h": safe_float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume")),
    }


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
