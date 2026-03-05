"""
股票池宇宙管理
获取 S&P 500 成分股列表, 或使用自定义列表
"""

import logging
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 备用列表: 高流动性美股 TOP 100 (当网络获取失败时使用)
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


def fetch_sp500_tickers() -> list[str]:
    """从 Wikipedia 获取 S&P 500 成分股列表"""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        tables = pd.read_html(url, header=0)
        df = tables[0]
        tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        logger.info(f"成功获取 S&P 500 成分股: {len(tickers)} 只")
        return tickers
    except Exception as e:
        logger.warning(f"获取 S&P 500 列表失败: {e}")
        return []


def fetch_nasdaq100_tickers() -> list[str]:
    """从 Wikipedia 获取 NASDAQ 100 成分股列表"""
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        tables = pd.read_html(url, header=0)
        # NASDAQ-100 表格通常是第四个
        for table in tables:
            if "Ticker" in table.columns or "Symbol" in table.columns:
                col = "Ticker" if "Ticker" in table.columns else "Symbol"
                tickers = table[col].str.replace(".", "-", regex=False).tolist()
                logger.info(f"成功获取 NASDAQ 100 成分股: {len(tickers)} 只")
                return tickers
        return []
    except Exception as e:
        logger.warning(f"获取 NASDAQ 100 列表失败: {e}")
        return []


def get_stock_universe(custom_watchlist: list[str] | None = None) -> list[str]:
    """
    获取股票宇宙

    优先级:
    1. 自定义观察列表 (如果非空)
    2. S&P 500 + NASDAQ 100 去重合并
    3. 内置备用列表
    """
    if custom_watchlist:
        logger.info(f"使用自定义观察列表: {len(custom_watchlist)} 只")
        return custom_watchlist

    tickers = set()

    sp500 = fetch_sp500_tickers()
    tickers.update(sp500)

    nasdaq100 = fetch_nasdaq100_tickers()
    tickers.update(nasdaq100)

    if not tickers:
        logger.warning("无法从网络获取股票列表, 使用内置备用列表")
        tickers = set(FALLBACK_TICKERS)

    result = sorted(tickers)
    logger.info(f"股票宇宙总计: {len(result)} 只")
    return result
