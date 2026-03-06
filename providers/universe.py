from __future__ import annotations

import json
import logging
import os

from runtime import config

logger = logging.getLogger(__name__)

FALLBACK_TICKERS = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
    "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "AVGO", "HD", "CVX",
    "MRK", "ABBV", "LLY", "COST", "PEP", "KO", "ADBE", "WMT", "MCD",
    "CRM", "CSCO", "ACN", "TMO", "ABT", "NFLX", "DHR", "LIN", "AMD",
    "CMCSA", "VZ", "NKE", "TXN", "PM", "NEE", "UNP", "RTX", "INTC",
    "BMY", "QCOM", "HON", "LOW", "AMGN", "UPS", "SBUX", "BA", "CAT",
    "GS", "ELV", "BLK", "SPGI", "DE", "ISRG", "GILD", "MDLZ", "ADP",
    "AMAT", "ADI", "SYK", "BKNG", "VRTX", "MMC", "LRCX", "REGN",
    "CI", "NOW", "MU", "PANW", "SNPS", "CDNS", "KLAC", "MRVL",
    "CRWD", "FTNT", "ABNB", "DASH", "COIN", "PLTR", "SQ", "SHOP",
    "SNOW", "NET", "DDOG", "ZS", "OKTA", "MELI", "SE", "UBER",
    "LYFT", "RIVN", "LCID", "SOFI", "ARM", "SMCI", "IONQ",
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
