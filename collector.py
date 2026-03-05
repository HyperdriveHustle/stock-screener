"""
数据采集模块
使用 yfinance 获取行情与公司信息, Finnhub 获取新闻
支持 SQLite 缓存与增量拉取
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import StringIO
from typing import Any

import pandas as pd
import requests
import yfinance as yf

import config
from cache_store import SQLiteCache

logger = logging.getLogger(__name__)


def _ensure_utc_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return datetime.utcnow().isoformat()


def _build_retrieval_meta(
    cache_meta: dict | None,
    retrieval_mode: str,
    *,
    source: str = "",
    symbol: str = "",
    data_type: str = "",
) -> dict:
    meta = dict(cache_meta or {})
    if source and not meta.get("source"):
        meta["source"] = source
    if symbol and not meta.get("symbol"):
        meta["symbol"] = symbol
    if data_type and not meta.get("data_type"):
        meta["data_type"] = data_type
    meta["retrieval_mode"] = retrieval_mode
    return meta


def _safe_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _safe_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        return None


class MarketDataCollector:
    """行情数据采集器 (yfinance)"""

    def __init__(self, cache: SQLiteCache | None = None):
        self.lookback_days = config.TECHNICAL["lookback_days"]
        self.batch_size = config.SYSTEM["yfinance_batch_size"]
        self.max_workers = config.SYSTEM["max_workers"]
        self.cache = cache or SQLiteCache(config.CACHE["db_file"])
        self.cache_stats = {
            "history_hit": 0,
            "history_miss": 0,
            "history_incremental_fetch": 0,
            "ticker_info_hit": 0,
            "ticker_info_miss": 0,
        }
        self.history_meta: dict[str, dict] = {}
        self.ticker_info_meta: dict[str, dict] = {}

    @staticmethod
    def _history_cache_key(ticker: str) -> str:
        return f"history:{ticker}"

    @staticmethod
    def _ticker_info_cache_key(ticker: str) -> str:
        return f"ticker_info:{ticker}"

    @staticmethod
    def _normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
        """
        统一列名与索引格式
        """
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(how="all")
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        # yfinance 返回的时区不稳定, 统一去 tz 避免 merge 出错
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert(None)
        return df

    @staticmethod
    def _df_to_payload(df: pd.DataFrame) -> dict:
        clean = df.copy()
        return {
            "frame": clean.to_json(orient="split", date_format="iso"),
            "rows": int(len(clean)),
            "last_index": clean.index[-1].isoformat() if len(clean) > 0 else "",
        }

    @staticmethod
    def _df_from_payload(payload: dict | None) -> pd.DataFrame | None:
        if not payload or "frame" not in payload:
            return None
        try:
            df = pd.read_json(StringIO(payload["frame"]), orient="split")
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_convert(None)
            return df.sort_index()
        except Exception:
            return None

    @staticmethod
    def _merge_history(cached_df: pd.DataFrame | None, fresh_df: pd.DataFrame | None) -> pd.DataFrame | None:
        if cached_df is None and fresh_df is None:
            return None
        if cached_df is None:
            return fresh_df
        if fresh_df is None:
            return cached_df
        merged = pd.concat([cached_df, fresh_df], axis=0)
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
        return merged

    @staticmethod
    def _sanitize_for_json(value: Any) -> Any:
        """将 yfinance 返回的复杂对象转成可序列化结构"""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): MarketDataCollector._sanitize_for_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [MarketDataCollector._sanitize_for_json(v) for v in value]
        return str(value)

    def fetch_batch_history(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """
        批量下载历史行情数据 (支持缓存与增量)

        Returns:
            {ticker: DataFrame(Date, Open, High, Low, Close, Volume)} 的字典
        """
        all_data: dict[str, pd.DataFrame] = {}
        cached_data: dict[str, pd.DataFrame] = {}
        fetch_plan: dict[str, datetime] = {}
        self.history_meta = {}

        end_date = datetime.now()
        full_start_date = end_date - timedelta(days=int(self.lookback_days * 1.5))
        incremental_buffer = timedelta(days=int(config.CACHE["history_incremental_lookback_days"]))

        # Step A: 先读取缓存并制定拉取计划
        for ticker in tickers:
            cache_key = self._history_cache_key(ticker)
            cache_meta = self.cache.get_meta(cache_key)
            cached_payload = self.cache.get(cache_key)
            cached_df = self._df_from_payload(cached_payload)

            if cached_df is None or cached_df.empty:
                self.cache_stats["history_miss"] += 1
                self.history_meta[ticker] = _build_retrieval_meta(
                    cache_meta,
                    "cache_miss",
                    source="yfinance",
                    symbol=ticker,
                    data_type="history",
                )
                fetch_plan[ticker] = full_start_date
                continue

            cached_df = self._normalize_history_df(cached_df)
            cached_data[ticker] = cached_df
            self.cache_stats["history_hit"] += 1

            last_date = cached_df.index[-1].to_pydatetime()
            # 当缓存是最近 1 天时, 直接复用缓存
            if (end_date.date() - last_date.date()).days <= 1:
                all_data[ticker] = cached_df
                self.history_meta[ticker] = _build_retrieval_meta(
                    cache_meta,
                    "cache_hit",
                    source="yfinance",
                    symbol=ticker,
                    data_type="history",
                )
                continue

            # 增量拉取: 从上次最后日期向前回看几天, 避免漏数据
            incremental_start = max(full_start_date, last_date - incremental_buffer)
            fetch_plan[ticker] = incremental_start

        if not fetch_plan:
            logger.info(
                f"行情数据缓存命中: {len(all_data)}/{len(tickers)} 只, 无需网络拉取"
            )
            return all_data

        # Step B: 对需要拉取的 ticker 分批执行
        to_fetch = list(fetch_plan.keys())
        batches = [
            to_fetch[i: i + self.batch_size]
            for i in range(0, len(to_fetch), self.batch_size)
        ]

        for batch_idx, batch in enumerate(batches):
            # 同批使用最早 start_date, 拉回后按 ticker merge
            batch_start = min(fetch_plan[t] for t in batch)
            batch_str = " ".join(batch)

            logger.info(
                f"下载行情数据 批次 {batch_idx + 1}/{len(batches)} "
                f"({len(batch)} 只, start={batch_start.strftime('%Y-%m-%d')})"
            )

            batch_data = None
            try:
                batch_data = yf.download(
                    batch_str,
                    start=batch_start.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
            except Exception as e:
                logger.error(f"批次 {batch_idx + 1} 下载失败: {e}")

            for ticker in batch:
                fresh_df = None
                if batch_data is not None and not batch_data.empty:
                    try:
                        if len(batch) == 1:
                            fresh_df = batch_data.copy()
                        else:
                            fresh_df = batch_data[ticker].copy()
                        fresh_df = self._normalize_history_df(fresh_df)
                    except Exception:
                        fresh_df = None

                merged = self._merge_history(cached_data.get(ticker), fresh_df)
                if merged is None or merged.empty or len(merged) < 20:
                    # 网络拉取失败时回退缓存
                    if ticker in cached_data:
                        all_data[ticker] = cached_data[ticker]
                        fallback_meta = self.cache.get_meta(self._history_cache_key(ticker))
                        self.history_meta[ticker] = _build_retrieval_meta(
                            fallback_meta,
                            "cache_fallback",
                            source="yfinance",
                            symbol=ticker,
                            data_type="history",
                        )
                    continue

                all_data[ticker] = merged
                self.cache.set(
                    key=self._history_cache_key(ticker),
                    payload=self._df_to_payload(merged),
                    ttl_seconds=int(config.CACHE["history_ttl_hours"] * 3600),
                    source="yfinance",
                    symbol=ticker,
                    data_type="history",
                    as_of=merged.index[-1].isoformat(),
                )
                refresh_meta = self.cache.get_meta(self._history_cache_key(ticker))
                self.history_meta[ticker] = _build_retrieval_meta(
                    refresh_meta,
                    "network_refresh",
                    source="yfinance",
                    symbol=ticker,
                    data_type="history",
                )
                self.cache_stats["history_incremental_fetch"] += 1

            # 批次间短暂暂停, 避免频率限制
            if batch_idx < len(batches) - 1:
                time.sleep(0.5)

        logger.info(
            f"行情数据完成: {len(all_data)}/{len(tickers)} | "
            f"缓存命中={self.cache_stats['history_hit']} "
            f"缓存未命中={self.cache_stats['history_miss']} "
            f"网络更新={self.cache_stats['history_incremental_fetch']}"
        )
        return all_data

    def fetch_ticker_info(self, tickers: list[str]) -> dict[str, dict]:
        """
        并发获取每只股票的实时信息 (带缓存)

        Returns:
            {ticker: info_dict}
        """
        results: dict[str, dict] = {}
        to_fetch: list[str] = []
        self.ticker_info_meta = {}

        for ticker in tickers:
            cache_key = self._ticker_info_cache_key(ticker)
            cache_meta = self.cache.get_meta(cache_key)
            cached_info = self.cache.get(cache_key)
            if isinstance(cached_info, dict) and cached_info:
                results[ticker] = cached_info
                self.cache_stats["ticker_info_hit"] += 1
                self.ticker_info_meta[ticker] = _build_retrieval_meta(
                    cache_meta,
                    "cache_hit",
                    source="yfinance",
                    symbol=ticker,
                    data_type="ticker_info",
                )
            else:
                to_fetch.append(ticker)
                self.cache_stats["ticker_info_miss"] += 1
                self.ticker_info_meta[ticker] = _build_retrieval_meta(
                    cache_meta,
                    "cache_miss",
                    source="yfinance",
                    symbol=ticker,
                    data_type="ticker_info",
                )

        def _fetch_one(ticker: str) -> tuple[str, dict | None]:
            try:
                info = yf.Ticker(ticker).info
                if not isinstance(info, dict) or not info:
                    return ticker, None
                safe_info = self._sanitize_for_json(info)
                return ticker, safe_info
            except Exception as e:
                logger.debug(f"获取 {ticker} info 失败: {e}")
                return ticker, None

        if to_fetch:
            logger.info(
                f"获取股票实时信息 ({len(to_fetch)} 只需网络请求, {self.max_workers} 线程)..."
            )
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(_fetch_one, t): t for t in to_fetch}
                done_count = 0
                for future in as_completed(futures):
                    ticker, info = future.result()
                    done_count += 1
                    if info:
                        results[ticker] = info
                        self.cache.set(
                            key=self._ticker_info_cache_key(ticker),
                            payload=info,
                            ttl_seconds=int(config.CACHE["ticker_info_ttl_minutes"] * 60),
                            source="yfinance",
                            symbol=ticker,
                            data_type="ticker_info",
                            as_of=_ensure_utc_timestamp(datetime.utcnow()),
                        )
                        refresh_meta = self.cache.get_meta(self._ticker_info_cache_key(ticker))
                        self.ticker_info_meta[ticker] = _build_retrieval_meta(
                            refresh_meta,
                            "network_refresh",
                            source="yfinance",
                            symbol=ticker,
                            data_type="ticker_info",
                        )
                    if done_count % 50 == 0:
                        logger.info(f"  进度: {done_count}/{len(to_fetch)}")

        logger.info(
            f"实时信息完成: {len(results)}/{len(tickers)} | "
            f"缓存命中={self.cache_stats['ticker_info_hit']} "
            f"网络请求={len(to_fetch)}"
        )
        return results

    def get_history_meta(self, ticker: str) -> dict:
        return dict(self.history_meta.get(ticker, {}))

    def get_ticker_info_meta(self, ticker: str) -> dict:
        return dict(self.ticker_info_meta.get(ticker, {}))

    def get_premarket_data(self, ticker_infos: dict[str, dict]) -> dict[str, dict]:
        """
        从 ticker info 中提取盘前/常规价格信息

        Returns:
            {ticker: {
                premarket_price,
                premarket_change_pct,
                regular_price,
                regular_change_pct,
                prev_close,
                has_premarket,
                price_source
            }}
        """
        premarket = {}
        for ticker, info in ticker_infos.items():
            try:
                prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
                pre_price = info.get("preMarketPrice")
                regular_price = info.get("regularMarketPrice") or info.get("currentPrice")

                if not prev_close or prev_close <= 0:
                    continue

                premarket_change_pct = None
                if pre_price:
                    premarket_change_pct = round(((pre_price - prev_close) / prev_close) * 100, 2)

                regular_change_pct = None
                if regular_price:
                    regular_change_pct = round(((regular_price - prev_close) / prev_close) * 100, 2)

                premarket[ticker] = {
                    "premarket_price": pre_price or 0.0,
                    "premarket_change_pct": premarket_change_pct,
                    "regular_price": regular_price or 0.0,
                    "regular_change_pct": regular_change_pct,
                    "prev_close": prev_close,
                    "has_premarket": pre_price is not None,
                    "price_source": "premarket" if pre_price is not None else "regular_market",
                }
            except Exception:
                continue

        return premarket


class NewsCollector:
    """新闻数据采集器 (Finnhub)"""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, cache: SQLiteCache | None = None):
        self.api_key = config.FINNHUB_API_KEY
        self.rate_limit = config.NEWS["finnhub_rate_limit"]
        self.cache = cache or SQLiteCache(config.CACHE["db_file"])
        self._request_count = 0
        self._last_reset = time.time()
        self.cache_stats = {
            "company_news_hit": 0,
            "company_news_miss": 0,
            "market_news_hit": 0,
            "market_news_miss": 0,
            "news_sentiment_hit": 0,
            "news_sentiment_miss": 0,
            "economic_calendar_hit": 0,
            "economic_calendar_miss": 0,
        }
        self.company_news_meta: dict[str, dict] = {}
        self.market_news_meta: dict[str, dict] = {}
        self.news_sentiment_meta: dict[str, dict] = {}
        self.economic_calendar_meta: dict[str, dict] = {}

    @staticmethod
    def _company_news_cache_key(ticker: str, from_date: str, to_date: str) -> str:
        return f"company_news:{ticker}:{from_date}:{to_date}"

    @staticmethod
    def _market_news_cache_key(category: str) -> str:
        return f"market_news:{category}"

    @staticmethod
    def _news_sentiment_cache_key(ticker: str) -> str:
        return f"news_sentiment:{ticker}"

    @staticmethod
    def _economic_calendar_cache_key(from_date: str, to_date: str) -> str:
        return f"economic_calendar:{from_date}:{to_date}"

    def _rate_limit_wait(self):
        """简单的速率限制"""
        self._request_count += 1
        if self._request_count >= self.rate_limit:
            elapsed = time.time() - self._last_reset
            if elapsed < 60:
                wait_time = 60 - elapsed + 1
                logger.debug(f"Finnhub 速率限制, 等待 {wait_time:.0f}s")
                time.sleep(wait_time)
            self._request_count = 0
            self._last_reset = time.time()

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list | None:
        """Finnhub API GET 请求"""
        if not self.api_key:
            return None

        self._rate_limit_wait()

        url = f"{self.BASE_URL}/{endpoint}"
        final_params = dict(params or {})
        final_params["token"] = self.api_key

        for attempt in range(config.SYSTEM["retry_attempts"]):
            try:
                resp = requests.get(url, params=final_params, timeout=10)
                if resp.status_code == 429:
                    logger.warning("Finnhub 429 限流, 等待 60s...")
                    time.sleep(60)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                logger.debug(f"Finnhub 请求失败 (第{attempt + 1}次): {e}")
                if attempt < config.SYSTEM["retry_attempts"] - 1:
                    time.sleep(config.SYSTEM["retry_delay"])
        return None

    def fetch_company_news(self, ticker: str) -> list[dict]:
        """
        获取单只股票最近新闻 (缓存 + 去重)

        Returns:
            [{headline, summary, source, datetime, url, ...}, ...]
        """
        now = datetime.now()
        from_date = (now - timedelta(hours=config.NEWS["lookback_hours"])).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")
        cache_key = self._company_news_cache_key(ticker, from_date, to_date)
        cache_meta = self.cache.get_meta(cache_key)

        data = self.cache.get(cache_key)
        if isinstance(data, list):
            self.cache_stats["company_news_hit"] += 1
            self.company_news_meta[ticker] = _build_retrieval_meta(
                cache_meta,
                "cache_hit",
                source="finnhub",
                symbol=ticker,
                data_type="company_news",
            )
        else:
            self.cache_stats["company_news_miss"] += 1
            self.company_news_meta[ticker] = _build_retrieval_meta(
                cache_meta,
                "cache_miss",
                source="finnhub",
                symbol=ticker,
                data_type="company_news",
            )
            data = self._get("company-news", {
                "symbol": ticker,
                "from": from_date,
                "to": to_date,
            })
            if not data:
                data = []
            data = sorted(data, key=lambda x: x.get("datetime", 0), reverse=True)
            data = data[: config.NEWS["max_articles_per_stock"]]
            self.cache.set(
                key=cache_key,
                payload=data,
                ttl_seconds=int(config.CACHE["company_news_ttl_minutes"] * 60),
                source="finnhub",
                symbol=ticker,
                data_type="company_news",
                as_of=_ensure_utc_timestamp(datetime.utcnow()),
            )
            refresh_meta = self.cache.get_meta(cache_key)
            self.company_news_meta[ticker] = _build_retrieval_meta(
                refresh_meta,
                "network_refresh",
                source="finnhub",
                symbol=ticker,
                data_type="company_news",
            )

        # 持久去重 + 返回最近 N 条稳定记录
        deduped = self.cache.store_news_and_get_recent(
            symbol=ticker,
            articles=data,
            limit=config.NEWS["max_articles_per_stock"],
        )
        return deduped

    def fetch_news_batch(self, tickers: list[str]) -> dict[str, list[dict]]:
        """
        批量获取多只股票的新闻

        Returns:
            {ticker: [articles]}
        """
        if not self.api_key:
            logger.warning("未配置 FINNHUB_API_KEY, 跳过新闻采集")
            return {}

        results = {}
        self.company_news_meta = {}
        logger.info(f"采集新闻数据 ({len(tickers)} 只)...")

        for i, ticker in enumerate(tickers):
            articles = self.fetch_company_news(ticker)
            if articles:
                results[ticker] = articles

            if (i + 1) % 20 == 0:
                logger.info(f"  新闻进度: {i + 1}/{len(tickers)}")

        logger.info(
            f"新闻采集完成: {len(results)} 只有相关新闻 | "
            f"缓存命中={self.cache_stats['company_news_hit']} "
            f"网络请求={self.cache_stats['company_news_miss']}"
        )
        return results

    def fetch_market_news(self, category: str = "general") -> list[dict]:
        """
        获取市场总体新闻 (缓存)

        Args:
            category: general, forex, crypto, merger
        """
        cache_key = self._market_news_cache_key(category)
        cache_meta = self.cache.get_meta(cache_key)
        cached = self.cache.get(cache_key)
        if isinstance(cached, list):
            self.cache_stats["market_news_hit"] += 1
            self.market_news_meta[category] = _build_retrieval_meta(
                cache_meta,
                "cache_hit",
                source="finnhub",
                symbol="MARKET",
                data_type=f"market_news:{category}",
            )
            return cached

        self.cache_stats["market_news_miss"] += 1
        self.market_news_meta[category] = _build_retrieval_meta(
            cache_meta,
            "cache_miss",
            source="finnhub",
            symbol="MARKET",
            data_type=f"market_news:{category}",
        )
        data = self._get("news", {"category": category})
        news = data if isinstance(data, list) else []
        self.cache.set(
            key=cache_key,
            payload=news,
            ttl_seconds=int(config.CACHE["market_news_ttl_minutes"] * 60),
            source="finnhub",
            symbol="MARKET",
            data_type=f"market_news:{category}",
            as_of=_ensure_utc_timestamp(datetime.utcnow()),
        )
        refresh_meta = self.cache.get_meta(cache_key)
        self.market_news_meta[category] = _build_retrieval_meta(
            refresh_meta,
            "network_refresh",
            source="finnhub",
            symbol="MARKET",
            data_type=f"market_news:{category}",
        )
        return news

    def fetch_market_news_bundle(self) -> dict[str, list[dict]]:
        """按配置批量获取市场新闻类别"""
        bundle = {}
        self.market_news_meta = {}
        for category in config.NEWS.get("market_news_categories", []):
            bundle[category] = self.fetch_market_news(category=category)
        return bundle

    @staticmethod
    def _normalize_economic_calendar(raw: Any) -> list[dict]:
        if isinstance(raw, list):
            return [dict(x or {}) for x in raw]
        if isinstance(raw, dict):
            for key in ("economicCalendar", "calendar", "events", "data"):
                val = raw.get(key)
                if isinstance(val, list):
                    return [dict(x or {}) for x in val]
        return []

    def fetch_news_sentiment(self, ticker: str) -> dict:
        """
        获取 Finnhub 新闻情绪原始结果
        """
        if not self.api_key or not config.NEWS.get("enable_provider_sentiment", True):
            return {}

        cache_key = self._news_sentiment_cache_key(ticker)
        cache_meta = self.cache.get_meta(cache_key)
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict):
            self.cache_stats["news_sentiment_hit"] += 1
            self.news_sentiment_meta[ticker] = _build_retrieval_meta(
                cache_meta,
                "cache_hit",
                source="finnhub",
                symbol=ticker,
                data_type="news_sentiment",
            )
            return cached

        self.cache_stats["news_sentiment_miss"] += 1
        self.news_sentiment_meta[ticker] = _build_retrieval_meta(
            cache_meta,
            "cache_miss",
            source="finnhub",
            symbol=ticker,
            data_type="news_sentiment",
        )
        data = self._get("news-sentiment", {"symbol": ticker})
        payload = data if isinstance(data, dict) else {}
        self.cache.set(
            key=cache_key,
            payload=payload,
            ttl_seconds=int(config.CACHE["news_sentiment_ttl_minutes"] * 60),
            source="finnhub",
            symbol=ticker,
            data_type="news_sentiment",
            as_of=_ensure_utc_timestamp(datetime.utcnow()),
        )
        refresh_meta = self.cache.get_meta(cache_key)
        self.news_sentiment_meta[ticker] = _build_retrieval_meta(
            refresh_meta,
            "network_refresh",
            source="finnhub",
            symbol=ticker,
            data_type="news_sentiment",
        )
        return payload

    def fetch_news_sentiment_batch(self, tickers: list[str]) -> dict[str, dict]:
        result = {}
        self.news_sentiment_meta = {}
        for ticker in tickers:
            payload = self.fetch_news_sentiment(ticker)
            if payload:
                result[ticker] = payload
        return result

    def fetch_economic_calendar(self, days_ahead: int | None = None) -> list[dict]:
        """
        获取未来宏观事件日历 (原始字段透传)
        """
        if not self.api_key:
            return []

        horizon_days = days_ahead if days_ahead is not None else int(config.EVENTS["future_days"])
        now = datetime.utcnow()
        from_date = now.strftime("%Y-%m-%d")
        to_date = (now + timedelta(days=max(1, horizon_days))).strftime("%Y-%m-%d")
        cache_key = self._economic_calendar_cache_key(from_date, to_date)
        cache_meta = self.cache.get_meta(cache_key)
        cached = self.cache.get(cache_key)
        if isinstance(cached, list):
            self.cache_stats["economic_calendar_hit"] += 1
            self.economic_calendar_meta["macro"] = _build_retrieval_meta(
                cache_meta,
                "cache_hit",
                source="finnhub",
                symbol="MACRO",
                data_type="economic_calendar",
            )
            return cached[: int(config.EVENTS["max_macro_events"])]

        self.cache_stats["economic_calendar_miss"] += 1
        self.economic_calendar_meta["macro"] = _build_retrieval_meta(
            cache_meta,
            "cache_miss",
            source="finnhub",
            symbol="MACRO",
            data_type="economic_calendar",
        )
        data = self._get(
            "calendar/economic",
            {
                "from": from_date,
                "to": to_date,
            },
        )
        events = self._normalize_economic_calendar(data)
        self.cache.set(
            key=cache_key,
            payload=events,
            ttl_seconds=int(config.CACHE["economic_calendar_ttl_minutes"] * 60),
            source="finnhub",
            symbol="MACRO",
            data_type="economic_calendar",
            as_of=_ensure_utc_timestamp(datetime.utcnow()),
        )
        refresh_meta = self.cache.get_meta(cache_key)
        self.economic_calendar_meta["macro"] = _build_retrieval_meta(
            refresh_meta,
            "network_refresh",
            source="finnhub",
            symbol="MACRO",
            data_type="economic_calendar",
        )
        return events[: int(config.EVENTS["max_macro_events"])]

    def get_company_news_meta(self, ticker: str) -> dict:
        return dict(self.company_news_meta.get(ticker, {}))

    def get_market_news_meta_bundle(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self.market_news_meta.items()}

    def get_news_sentiment_meta(self, ticker: str) -> dict:
        return dict(self.news_sentiment_meta.get(ticker, {}))

    def get_economic_calendar_meta(self) -> dict:
        return dict(self.economic_calendar_meta.get("macro", {}))


class OptionsCollector:
    """期权摘要采集器 (yfinance)"""

    def __init__(self, cache: SQLiteCache | None = None):
        self.cache = cache or SQLiteCache(config.CACHE["db_file"])
        self.cache_stats = {
            "options_hit": 0,
            "options_miss": 0,
        }
        self.options_meta: dict[str, dict] = {}

    @staticmethod
    def _options_cache_key(ticker: str) -> str:
        return f"options_summary:{ticker}"

    @staticmethod
    def _select_contracts(df: pd.DataFrame, spot_price: float | None, limit: int) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        if limit <= 0 or len(df) <= limit:
            return df.copy()
        frame = df.copy()
        if spot_price is None or "strike" not in frame.columns:
            return frame.head(limit)
        frame["distance_to_spot"] = (pd.to_numeric(frame["strike"], errors="coerce") - spot_price).abs()
        frame = frame.sort_values(by=["distance_to_spot"], ascending=True).head(limit)
        return frame.drop(columns=["distance_to_spot"], errors="ignore")

    @staticmethod
    def _calc_max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
        if calls.empty and puts.empty:
            return None
        strikes = sorted(
            {
                float(v)
                for v in pd.concat(
                    [
                        pd.to_numeric(calls.get("strike"), errors="coerce"),
                        pd.to_numeric(puts.get("strike"), errors="coerce"),
                    ],
                    axis=0,
                ).dropna()
            }
        )
        if not strikes:
            return None

        call_strike = pd.to_numeric(calls.get("strike"), errors="coerce").fillna(0.0)
        put_strike = pd.to_numeric(puts.get("strike"), errors="coerce").fillna(0.0)
        call_oi = pd.to_numeric(calls.get("openInterest"), errors="coerce").fillna(0.0)
        put_oi = pd.to_numeric(puts.get("openInterest"), errors="coerce").fillna(0.0)

        best_strike = None
        best_pain = None
        for settle in strikes:
            call_loss = ((settle - call_strike).clip(lower=0.0) * call_oi).sum()
            put_loss = ((put_strike - settle).clip(lower=0.0) * put_oi).sum()
            pain = float(call_loss + put_loss)
            if best_pain is None or pain < best_pain:
                best_pain = pain
                best_strike = settle
        return _safe_float(best_strike)

    @staticmethod
    def _extract_unusual(
        expiry: str,
        calls: pd.DataFrame,
        puts: pd.DataFrame,
    ) -> list[dict]:
        rows = []
        for option_type, side in (("call", calls), ("put", puts)):
            if side.empty:
                continue
            frame = side.copy()
            frame["volume"] = pd.to_numeric(frame.get("volume"), errors="coerce").fillna(0.0)
            frame["openInterest"] = pd.to_numeric(frame.get("openInterest"), errors="coerce").fillna(0.0)
            frame["impliedVolatility"] = pd.to_numeric(
                frame.get("impliedVolatility"), errors="coerce"
            ).fillna(0.0)
            frame["lastPrice"] = pd.to_numeric(frame.get("lastPrice"), errors="coerce")
            frame["strike"] = pd.to_numeric(frame.get("strike"), errors="coerce")
            frame = frame.dropna(subset=["strike"])
            frame["volume_oi_ratio"] = frame["volume"] / frame["openInterest"].replace(0, pd.NA)
            frame["volume_oi_ratio"] = pd.to_numeric(frame["volume_oi_ratio"], errors="coerce").fillna(0.0)
            frame = frame.sort_values(by=["volume_oi_ratio", "volume"], ascending=False)

            for _, item in frame.head(20).iterrows():
                rows.append(
                    {
                        "expiry": expiry,
                        "option_type": option_type,
                        "strike": _safe_float(item.get("strike")),
                        "volume": _safe_int(item.get("volume")),
                        "open_interest": _safe_int(item.get("openInterest")),
                        "volume_oi_ratio": _safe_float(item.get("volume_oi_ratio")),
                        "implied_volatility": _safe_float(item.get("impliedVolatility")),
                        "last_price": _safe_float(item.get("lastPrice")),
                    }
                )
        rows.sort(
            key=lambda x: (
                x.get("volume_oi_ratio") or 0.0,
                x.get("volume") or 0,
            ),
            reverse=True,
        )
        return rows[: int(config.OPTIONS["max_unusual_contracts"])]

    def fetch_options_summary(
        self,
        ticker: str,
        spot_price: float | None = None,
    ) -> dict:
        if not config.OPTIONS.get("enabled", True):
            return {}

        cache_key = self._options_cache_key(ticker)
        cache_meta = self.cache.get_meta(cache_key)
        cached = self.cache.get(cache_key)
        if isinstance(cached, dict):
            self.cache_stats["options_hit"] += 1
            self.options_meta[ticker] = _build_retrieval_meta(
                cache_meta,
                "cache_hit",
                source="yfinance",
                symbol=ticker,
                data_type="options_summary",
            )
            return cached

        self.cache_stats["options_miss"] += 1
        self.options_meta[ticker] = _build_retrieval_meta(
            cache_meta,
            "cache_miss",
            source="yfinance",
            symbol=ticker,
            data_type="options_summary",
        )

        try:
            yf_ticker = yf.Ticker(ticker)
            expiries = list(yf_ticker.options or [])
        except Exception as e:
            logger.debug(f"读取 {ticker} 期权到期日失败: {e}")
            expiries = []

        expiry_limit = int(config.OPTIONS["expiries_limit"])
        selected_expiries = expiries[: max(0, expiry_limit)]
        if not selected_expiries:
            payload = {}
        else:
            per_expiry = []
            unusual_rows = []
            total_call_oi = 0.0
            total_put_oi = 0.0
            total_call_vol = 0.0
            total_put_vol = 0.0

            for expiry in selected_expiries:
                try:
                    chain = yf_ticker.option_chain(expiry)
                    calls = chain.calls if isinstance(chain.calls, pd.DataFrame) else pd.DataFrame()
                    puts = chain.puts if isinstance(chain.puts, pd.DataFrame) else pd.DataFrame()
                except Exception as e:
                    logger.debug(f"拉取 {ticker} {expiry} 期权链失败: {e}")
                    continue

                calls = self._select_contracts(
                    calls,
                    spot_price=spot_price,
                    limit=int(config.OPTIONS["contracts_per_side_limit"]),
                )
                puts = self._select_contracts(
                    puts,
                    spot_price=spot_price,
                    limit=int(config.OPTIONS["contracts_per_side_limit"]),
                )

                call_oi = float(pd.to_numeric(calls.get("openInterest"), errors="coerce").fillna(0.0).sum())
                put_oi = float(pd.to_numeric(puts.get("openInterest"), errors="coerce").fillna(0.0).sum())
                call_vol = float(pd.to_numeric(calls.get("volume"), errors="coerce").fillna(0.0).sum())
                put_vol = float(pd.to_numeric(puts.get("volume"), errors="coerce").fillna(0.0).sum())
                total_call_oi += call_oi
                total_put_oi += put_oi
                total_call_vol += call_vol
                total_put_vol += put_vol

                atm_call_iv = None
                atm_put_iv = None
                if spot_price is not None:
                    if not calls.empty:
                        calls_idx = (
                            pd.to_numeric(calls.get("strike"), errors="coerce") - float(spot_price)
                        ).abs().idxmin()
                        atm_call_iv = _safe_float(calls.loc[calls_idx].get("impliedVolatility"))
                    if not puts.empty:
                        puts_idx = (
                            pd.to_numeric(puts.get("strike"), errors="coerce") - float(spot_price)
                        ).abs().idxmin()
                        atm_put_iv = _safe_float(puts.loc[puts_idx].get("impliedVolatility"))

                per_expiry.append(
                    {
                        "expiry": expiry,
                        "call_open_interest": call_oi,
                        "put_open_interest": put_oi,
                        "call_volume": call_vol,
                        "put_volume": put_vol,
                        "put_call_open_interest_ratio": (put_oi / call_oi) if call_oi > 0 else None,
                        "put_call_volume_ratio": (put_vol / call_vol) if call_vol > 0 else None,
                        "atm_implied_volatility": {
                            "call": atm_call_iv,
                            "put": atm_put_iv,
                        },
                        "max_pain_strike": self._calc_max_pain(calls=calls, puts=puts),
                    }
                )

                unusual_rows.extend(self._extract_unusual(expiry=expiry, calls=calls, puts=puts))

            unusual_rows.sort(
                key=lambda x: (
                    x.get("volume_oi_ratio") or 0.0,
                    x.get("volume") or 0,
                ),
                reverse=True,
            )
            unusual_rows = unusual_rows[: int(config.OPTIONS["max_unusual_contracts"])]

            nearest_expiry = per_expiry[0]["expiry"] if per_expiry else ""
            days_to_nearest = None
            if nearest_expiry:
                try:
                    days_to_nearest = (datetime.fromisoformat(nearest_expiry) - datetime.utcnow()).days
                except Exception:
                    days_to_nearest = None

            payload = {
                "as_of": datetime.utcnow().isoformat(),
                "spot_price": _safe_float(spot_price),
                "expiries_analyzed": [x.get("expiry", "") for x in per_expiry],
                "nearest_expiry": nearest_expiry,
                "days_to_nearest_expiry": days_to_nearest,
                "aggregate": {
                    "call_open_interest": total_call_oi,
                    "put_open_interest": total_put_oi,
                    "call_volume": total_call_vol,
                    "put_volume": total_put_vol,
                    "put_call_open_interest_ratio": (
                        total_put_oi / total_call_oi if total_call_oi > 0 else None
                    ),
                    "put_call_volume_ratio": (
                        total_put_vol / total_call_vol if total_call_vol > 0 else None
                    ),
                },
                "expiries": per_expiry,
                "unusual_contracts": unusual_rows,
            }

        self.cache.set(
            key=cache_key,
            payload=payload,
            ttl_seconds=int(config.CACHE["options_ttl_minutes"] * 60),
            source="yfinance",
            symbol=ticker,
            data_type="options_summary",
            as_of=_ensure_utc_timestamp(datetime.utcnow()),
        )
        refresh_meta = self.cache.get_meta(cache_key)
        self.options_meta[ticker] = _build_retrieval_meta(
            refresh_meta,
            "network_refresh",
            source="yfinance",
            symbol=ticker,
            data_type="options_summary",
        )
        return payload

    def fetch_options_batch(
        self,
        tickers: list[str],
        spot_prices: dict[str, float | None] | None = None,
    ) -> dict[str, dict]:
        results = {}
        for ticker in tickers:
            payload = self.fetch_options_summary(
                ticker=ticker,
                spot_price=(spot_prices or {}).get(ticker),
            )
            if payload:
                results[ticker] = payload
        return results

    def get_options_meta(self, ticker: str) -> dict:
        return dict(self.options_meta.get(ticker, {}))


class UniverseFilter:
    """初始池过滤器: 基于流动性/市值/价格的硬性筛选"""

    @staticmethod
    def apply(
        history_data: dict[str, pd.DataFrame],
        ticker_infos: dict[str, dict],
    ) -> list[str]:
        """
        从原始数据中过滤出符合条件的候选股票

        Returns:
            通过筛选的 ticker 列表
        """
        passed = []
        filtered_reasons = {"price": 0, "volume": 0, "market_cap": 0, "data": 0}

        for ticker, df in history_data.items():
            info = ticker_infos.get(ticker, {})

            # 检查数据充足性
            if len(df) < 50:
                filtered_reasons["data"] += 1
                continue

            try:
                last_close = float(df["Close"].iloc[-1])
            except (KeyError, IndexError, TypeError):
                filtered_reasons["data"] += 1
                continue

            # 价格过滤
            if last_close < config.UNIVERSE["min_price"] or last_close > config.UNIVERSE["max_price"]:
                filtered_reasons["price"] += 1
                continue

            # 成交量过滤 (20日均量)
            try:
                avg_vol = float(df["Volume"].tail(20).mean())
                if avg_vol < config.UNIVERSE["min_avg_volume"]:
                    filtered_reasons["volume"] += 1
                    continue
            except Exception:
                filtered_reasons["volume"] += 1
                continue

            # 市值过滤
            market_cap = info.get("marketCap", 0)
            if market_cap and market_cap < config.UNIVERSE["min_market_cap"]:
                filtered_reasons["market_cap"] += 1
                continue

            passed.append(ticker)

        logger.info(
            f"初始池过滤完成: {len(passed)} 只通过 | "
            f"剔除原因 - 价格:{filtered_reasons['price']} "
            f"成交量:{filtered_reasons['volume']} "
            f"市值:{filtered_reasons['market_cap']} "
            f"数据不足:{filtered_reasons['data']}"
        )
        return passed
