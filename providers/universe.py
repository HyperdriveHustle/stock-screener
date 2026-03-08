from __future__ import annotations

import json
import logging
import os

from runtime import config

logger = logging.getLogger(__name__)

FALLBACK_TICKERS = [
    # --- Technology / Software (12) ---
    "AAPL", "MSFT", "GOOGL", "META", "ADBE", "CRM", "NOW", "CSCO", "ACN", "ORCL",
    "PANW", "CRWD",
    # --- Semiconductors (8) ---
    "NVDA", "AVGO", "AMD", "QCOM", "TXN", "AMAT", "LRCX", "MU",
    # --- Consumer Discretionary (8) ---
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "BKNG", "TJX",
    # --- Consumer Staples (6) ---
    "WMT", "PG", "KO", "PEP", "COST", "PM",
    # --- Healthcare / Pharma (10) ---
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "TMO", "ABT", "AMGN", "VRTX", "ISRG",
    # --- Financials (8) ---
    "JPM", "V", "MA", "GS", "BLK", "SPGI", "BRK-B", "MMC",
    # --- Industrials (8) ---
    "CAT", "HON", "UNP", "RTX", "DE", "BA", "UPS", "GE",
    # --- Energy (5) ---
    "XOM", "CVX", "COP", "SLB", "EOG",
    # --- Utilities (4) ---
    "NEE", "DUK", "SO", "AEP",
    # --- Real Estate (4) ---
    "PLD", "AMT", "EQIX", "SPG",
    # --- Materials (5) ---
    "LIN", "APD", "SHW", "FCX", "NEM",
    # --- Communication Services (5) ---
    "NFLX", "DIS", "CMCSA", "T", "VZ",
    # --- High-Growth / Speculative (12) ---
    "PLTR", "COIN", "SQ", "SHOP", "SNOW", "DDOG", "ARM", "SMCI",
    "ABNB", "DASH", "UBER", "MELI",
]


def _load_registry_tickers() -> list[str]:
    registry_file = config.SYMBOL_REGISTRY["master_file"]
    if not os.path.exists(registry_file):
        return []
    try:
        with open(registry_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("读取 symbol registry 失败: %s", exc)
        return []

    records = payload if isinstance(payload, list) else payload.get("symbols", [])
    tickers = []
    for item in records:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker:
            tickers.append(ticker)
    return sorted(set(tickers))


def get_stock_universe(custom_watchlist: list[str] | None = None) -> list[str]:
    if custom_watchlist:
        tickers = [ticker.strip().upper() for ticker in custom_watchlist if ticker.strip()]
        logger.info("使用自定义观察列表: %d 只", len(tickers))
        return tickers

    registry_tickers = _load_registry_tickers()
    if registry_tickers:
        logger.info("使用本地 symbol registry: %d 只", len(registry_tickers))
        return registry_tickers

    logger.warning("未找到可用的 symbol registry，回退内置种子列表")
    return list(FALLBACK_TICKERS)
