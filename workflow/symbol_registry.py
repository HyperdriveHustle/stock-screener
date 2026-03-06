from __future__ import annotations

import json
import os
from typing import Any

from runtime import config
from providers.universe import FALLBACK_TICKERS


def _default_record(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "exchange": "",
        "primary_listing": True,
        "asset_type": "equity",
        "country": "US",
        "currency": "USD",
        "sector": "",
        "industry": "",
        "is_active": True,
        "is_common_stock": True,
        "is_etf": False,
        "is_adr": False,
        "is_spac": False,
        "market_cap_bucket": "",
        "avg_dollar_volume_bucket": "",
        "universe_tags": ["fallback_seed"],
    }


def _bucket_market_cap(value: Any) -> str:
    try:
        cap = float(value)
    except Exception:
        return ""
    if cap >= 2e11:
        return "mega"
    if cap >= 1e10:
        return "large"
    if cap >= 2e9:
        return "mid"
    if cap > 0:
        return "small"
    return ""


def _bucket_dollar_volume(value: Any) -> str:
    try:
        adv = float(value)
    except Exception:
        return ""
    if adv >= 1e9:
        return "ultra"
    if adv >= 2e8:
        return "high"
    if adv >= 5e7:
        return "medium"
    if adv > 0:
        return "low"
    return ""


class SymbolRegistry:
    def __init__(self, registry_file: str | None = None):
        self.registry_file = registry_file or config.SYMBOL_REGISTRY["master_file"]

    def _load_master(self) -> dict[str, dict]:
        if not os.path.exists(self.registry_file):
            return {}
        try:
            with open(self.registry_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}

        records = payload if isinstance(payload, list) else payload.get("symbols", [])
        result = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").strip().upper()
            if ticker:
                result[ticker] = item
        return result

    def resolve(self, tickers_override: list[str] | None = None) -> list[dict]:
        master = self._load_master()
        if tickers_override:
            tickers = [ticker.strip().upper() for ticker in tickers_override if ticker.strip()]
        elif config.CUSTOM_WATCHLIST:
            tickers = [ticker.strip().upper() for ticker in config.CUSTOM_WATCHLIST if ticker.strip()]
        elif master:
            tickers = sorted(master.keys())
        elif config.SYMBOL_REGISTRY.get("seed_from_fallback_on_missing", True):
            tickers = list(FALLBACK_TICKERS)
        else:
            tickers = []

        resolved = []
        seen = set()
        for ticker in tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
            record = dict(master.get(ticker) or _default_record(ticker))
            record["ticker"] = ticker
            if "universe_tags" not in record or not isinstance(record["universe_tags"], list):
                record["universe_tags"] = []
            resolved.append(record)
        return resolved

    @staticmethod
    def enrich(records: list[dict], ticker_infos: dict[str, dict], dollar_volume_map: dict[str, float]) -> list[dict]:
        enriched = []
        for record in records:
            item = dict(record)
            ticker = item.get("ticker", "")
            info = ticker_infos.get(ticker, {})
            quote_type = str(info.get("quoteType") or "").lower()
            item["exchange"] = str(info.get("exchange") or info.get("fullExchangeName") or item.get("exchange") or "")
            item["country"] = str(info.get("country") or item.get("country") or "US")
            item["currency"] = str(info.get("currency") or item.get("currency") or "USD")
            item["sector"] = str(info.get("sector") or item.get("sector") or "")
            item["industry"] = str(info.get("industry") or item.get("industry") or "")
            item["is_etf"] = bool(item.get("is_etf")) or quote_type == "etf"
            item["is_common_stock"] = bool(item.get("is_common_stock", True)) and quote_type != "etf"
            item["is_active"] = bool(item.get("is_active", True))
            item["market_cap_bucket"] = _bucket_market_cap(info.get("marketCap"))
            item["avg_dollar_volume_bucket"] = _bucket_dollar_volume(dollar_volume_map.get(ticker))
            enriched.append(item)
        return enriched
