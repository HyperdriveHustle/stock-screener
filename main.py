#!/usr/bin/env python3
"""
美股短线选股系统 - 特征聚合版
主入口: 编排数据采集 → 分析 → 特征汇总 → 输出 LLM payload

Usage:
    python main.py              # 运行完整流程
    python main.py --test       # 测试 Discord Webhook 连接
    python main.py --dry-run    # 运行但不发送 Discord (仅控制台输出)
    python main.py --tickers AAPL,MSFT,NVDA  # 仅扫描指定股票
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

import config
from analyzer import NewsSentimentAnalyzer, TechnicalAnalyzer, TechnicalProfile
from collector import MarketDataCollector, NewsCollector, OptionsCollector, UniverseFilter
from notifier import DiscordNotifier, format_console_report
from scorer import (
    FeatureAssembler,
    FeatureFilter,
    StockFeatures,
    build_market_context,
    build_stock_packet,
)
from universe import get_stock_universe


def setup_logging():
    """配置日志"""
    os.makedirs(config.SYSTEM["log_dir"], exist_ok=True)

    log_file = os.path.join(
        config.SYSTEM["log_dir"],
        f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )

    logging.basicConfig(
        level=getattr(logging, config.SYSTEM["log_level"]),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger(__name__)


def get_market_summary(
    spy_data: pd.DataFrame | None,
    vix_override: float | None = None,
    vix_as_of_override: str = "",
) -> dict:
    """
    生成市场环境概要

    Returns:
        {spy_change, spy_close, spy_prev_close, spy_as_of, vix, vix_as_of}
    """
    summary = {
        "spy_change": 0.0,
        "spy_close": None,
        "spy_prev_close": None,
        "spy_as_of": "",
        "vix": 0.0,
        "vix_as_of": vix_as_of_override or "",
    }

    # SPY 涨跌幅
    if spy_data is not None and len(spy_data) >= 2:
        try:
            last = float(spy_data["Close"].iloc[-1])
            prev = float(spy_data["Close"].iloc[-2])
            summary["spy_change"] = ((last - prev) / prev) * 100
            summary["spy_close"] = last
            summary["spy_prev_close"] = prev
            summary["spy_as_of"] = _frame_as_of_iso(_normalize_history_frame(spy_data))
        except Exception:
            pass

    # VIX
    if vix_override is not None:
        summary["vix"] = float(vix_override)
    else:
        try:
            vix_frame = _download_symbol_history("^VIX")
            vix_last = _safe_last_close(vix_frame)
            if vix_last is not None:
                summary["vix"] = float(vix_last)
                summary["vix_as_of"] = _frame_as_of_iso(vix_frame)
        except Exception:
            pass

    return summary


def _normalize_history_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    if "Close" not in frame.columns:
        return None
    return frame.dropna(how="all")


def _safe_last_close(df: pd.DataFrame | None) -> float | None:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _safe_change_pct(df: pd.DataFrame | None, lookback_days: int) -> float | None:
    if df is None or df.empty or len(df) <= lookback_days or "Close" not in df.columns:
        return None
    try:
        last = float(df["Close"].iloc[-1])
        base = float(df["Close"].iloc[-(lookback_days + 1)])
        if base == 0:
            return None
        return round(((last - base) / base) * 100, 3)
    except Exception:
        return None


def _safe_delta_abs(df: pd.DataFrame | None, lookback_days: int) -> float | None:
    if df is None or df.empty or len(df) <= lookback_days or "Close" not in df.columns:
        return None
    try:
        last = float(df["Close"].iloc[-1])
        base = float(df["Close"].iloc[-(lookback_days + 1)])
        return round(last - base, 6)
    except Exception:
        return None


def _frame_as_of_iso(df: pd.DataFrame | None) -> str:
    if df is None or df.empty:
        return ""
    idx = df.index[-1]
    if isinstance(idx, pd.Timestamp):
        if idx.tzinfo is not None:
            idx = idx.tz_convert(None)
        return idx.to_pydatetime().isoformat()
    return str(idx)


def _max_as_of(values: list[str]) -> str:
    parsed: list[datetime] = []
    for value in values:
        if not value:
            continue
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            parsed.append(dt)
        except Exception:
            continue
    if not parsed:
        return ""
    return max(parsed).isoformat()


def _download_symbol_history(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    try:
        frame = yf.download(symbol, period=period, progress=False, auto_adjust=False)
        return _normalize_history_frame(frame)
    except Exception:
        return None


def _build_symbol_snapshot(
    symbol: str,
    frame: pd.DataFrame | None,
    windows_days: list[int],
) -> dict:
    returns_pct = {}
    delta_abs = {}
    for window in windows_days:
        key = f"{window}d"
        returns_pct[key] = _safe_change_pct(frame, window)
        delta_abs[key] = _safe_delta_abs(frame, window)
    return {
        "symbol": symbol,
        "close": _safe_last_close(frame),
        "returns_pct": returns_pct,
        "delta_abs": delta_abs,
        "as_of": _frame_as_of_iso(frame),
    }


def get_market_regime(spy_data: pd.DataFrame | None = None) -> tuple[dict, dict]:
    """
    构建市场状态快照: 原始多资产截面 + 多窗口变化
    """
    cfg = getattr(config, "MARKET_CONTEXT", {})
    windows_days = [
        int(v)
        for v in cfg.get("return_windows_days", [])
        if str(v).isdigit() and int(v) > 0
    ]
    windows_days = sorted(set(windows_days))

    symbol_groups = cfg.get("symbol_groups", {})

    ordered_symbols: list[str] = []
    seen = set()
    for _, symbols in symbol_groups.items():
        for symbol in symbols:
            if symbol and symbol not in seen:
                ordered_symbols.append(symbol)
                seen.add(symbol)

    spy_frame = _normalize_history_frame(spy_data)
    symbol_frames: dict[str, pd.DataFrame | None] = {}
    for symbol in ordered_symbols:
        if symbol == "SPY" and spy_frame is not None:
            symbol_frames[symbol] = spy_frame
        else:
            symbol_frames[symbol] = _download_symbol_history(symbol)

    grouped_snapshots = {}
    for group_name, symbols in symbol_groups.items():
        grouped_snapshots[group_name] = {}
        for symbol in symbols:
            grouped_snapshots[group_name][symbol] = _build_symbol_snapshot(
                symbol=symbol,
                frame=symbol_frames.get(symbol),
                windows_days=windows_days,
            )

    regime = {
        "windows_days": windows_days,
        "symbol_groups": grouped_snapshots,
        "all_symbols": ordered_symbols,
    }

    as_of = _max_as_of(
        [_frame_as_of_iso(symbol_frames.get(symbol)) for symbol in ordered_symbols]
    )
    provenance = {
        "source": "yfinance",
        "symbol": ",".join(ordered_symbols),
        "data_type": "market_regime",
        "fetched_at": datetime.utcnow().isoformat(),
        "as_of": as_of,
        "retrieval_mode": "network_refresh",
    }
    return regime, provenance


def build_market_summary_provenance(
    spy_data: pd.DataFrame | None,
    vix_as_of: str = "",
    vix_symbol: str = "^VIX",
) -> dict:
    as_of = _max_as_of([_frame_as_of_iso(_normalize_history_frame(spy_data)), vix_as_of])
    return {
        "source": "yfinance",
        "symbol": f"SPY,{vix_symbol}",
        "data_type": "market_summary",
        "fetched_at": datetime.utcnow().isoformat(),
        "as_of": as_of,
        "retrieval_mode": "network_refresh",
    }


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_event_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _to_naive_utc(value)
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        try:
            return datetime.utcfromtimestamp(int(value))
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _to_naive_utc(datetime.fromisoformat(text))
    except Exception:
        return None


def _safe_history_return_pct(df: pd.DataFrame | None, lookback_days: int) -> float | None:
    if df is None or df.empty or len(df) <= lookback_days:
        return None
    if "Close" not in df.columns:
        return None
    try:
        last = float(df["Close"].iloc[-1])
        base = float(df["Close"].iloc[-(lookback_days + 1)])
        if base == 0:
            return None
        return ((last / base) - 1.0) * 100
    except Exception:
        return None


def _extract_company_event_list(ticker: str, ticker_info: dict | None) -> list[dict]:
    info = ticker_info or {}
    fields = [
        ("earningsTimestamp", "earnings"),
        ("earningsTimestampStart", "earnings_window_start"),
        ("earningsTimestampEnd", "earnings_window_end"),
        ("exDividendDate", "ex_dividend"),
        ("dividendDate", "dividend"),
    ]

    events = []
    for field_name, event_type in fields:
        raw_value = info.get(field_name)
        dt = _parse_event_datetime(raw_value)
        if dt is None:
            continue
        events.append(
            {
                "ticker": ticker,
                "event_type": event_type,
                "event_time": dt.isoformat(),
                "raw_value": raw_value,
                "source_field": field_name,
            }
        )
    events.sort(key=lambda x: x["event_time"])
    return events


def _build_sector_context(
    feature: StockFeatures,
    all_features: list[StockFeatures],
    history_data: dict[str, pd.DataFrame],
) -> dict:
    max_peer_count = int(config.SECTOR_CONTEXT.get("max_peer_count", 10))
    same_sector = [
        f
        for f in all_features
        if f.ticker != feature.ticker and f.sector and f.sector == feature.sector
    ]
    same_sector.sort(
        key=lambda x: (
            float((x.fundamentals or {}).get("market_cap") or 0),
            float(x.technical_priority),
        ),
        reverse=True,
    )
    selected_peers = same_sector[: max(1, max_peer_count)]

    peers = []
    for peer in selected_peers:
        peer_history = history_data.get(peer.ticker)
        peers.append(
            {
                "ticker": peer.ticker,
                "company_name": peer.company_name,
                "sector": peer.sector,
                "industry": peer.industry,
                "market_cap": (peer.fundamentals or {}).get("market_cap"),
                "technical_priority": peer.technical_priority,
                "returns_pct": {
                    "1d": _safe_history_return_pct(peer_history, 1),
                    "5d": _safe_history_return_pct(peer_history, 5),
                    "20d": _safe_history_return_pct(peer_history, 20),
                },
                "relative_strength_vs_spy_20d_pct": (
                    ((peer.technical or {}).get("relative_strength") or {}).get("rs_vs_spy_20d_pct")
                ),
            }
        )

    return {
        "sector": feature.sector,
        "industry": feature.industry,
        "peer_count_total": len(same_sector),
        "peers": peers,
    }


def _build_upcoming_events(
    ticker: str,
    company_events_map: dict[str, list[dict]],
    sector_context: dict,
    macro_events: list[dict],
) -> dict:
    horizon_days = int(config.EVENTS["future_days"])
    max_company_events = int(config.EVENTS["max_company_events"])
    max_peer_events = int(config.EVENTS["max_peer_events"])
    now = datetime.utcnow()
    horizon_end = now + pd.Timedelta(days=max(1, horizon_days))

    def _within_horizon(event_time: object) -> bool:
        dt = _parse_event_datetime(event_time)
        if dt is None:
            return False
        return now <= dt <= horizon_end

    company_events = [
        event for event in company_events_map.get(ticker, []) if _within_horizon(event.get("event_time"))
    ][:max_company_events]

    peer_tickers = [p.get("ticker", "") for p in (sector_context.get("peers") or [])]
    peer_events = []
    for peer_ticker in peer_tickers:
        for event in company_events_map.get(peer_ticker, []):
            if not _within_horizon(event.get("event_time")):
                continue
            peer_events.append(event)
    peer_events.sort(key=lambda x: x.get("event_time", ""))
    peer_events = peer_events[:max_peer_events]

    normalized_macro = []
    for event in macro_events:
        item = dict(event or {})
        candidate_time = (
            item.get("date")
            or item.get("time")
            or item.get("datetime")
            or item.get("eventTime")
            or item.get("timestamp")
        )
        if candidate_time and not _within_horizon(candidate_time):
            continue
        normalized_macro.append(item)
    normalized_macro = normalized_macro[: int(config.EVENTS["max_macro_events"])]

    return {
        "window_days": horizon_days,
        "macro_events": normalized_macro,
        "company_events": company_events,
        "peer_events": peer_events,
    }


def _save_summary_csv(features: list[StockFeatures], csv_file: str):
    rows = []
    for f in features:
        tech = f.technical or {}
        momentum = tech.get("momentum", {})
        vol = tech.get("volatility", {})
        rel = tech.get("relative_strength", {})
        vprice = tech.get("volume_price", {})
        fact_sheet = f.fact_sheet or {}
        fifty_two_week = fact_sheet.get("fifty_two_week", {})
        fwd_5d = ((f.ev_inputs or {}).get("forward_return_distribution_pct", {}) or {}).get("5d", {})
        dd_60d = (f.drawdown_context or {}).get("60d", {})
        exec_ctx = f.execution_context or {}
        dollar_vol = exec_ctx.get("dollar_volume", {})
        linkage_20d = (f.market_linkage or {}).get("20d", {})
        support_resistance = f.support_resistance or {}
        options_agg = (f.options_summary or {}).get("aggregate", {})
        sector_ctx = f.sector_context or {}
        rows.append(
            {
                "ticker": f.ticker,
                "company_name": f.company_name,
                "sector": f.sector,
                "industry": f.industry,
                "technical_priority": f.technical_priority,
                "daily_change_pct": tech.get("daily_change_pct"),
                "rsi": momentum.get("rsi"),
                "atr_pct": vol.get("atr_pct"),
                "volume_ratio": vprice.get("volume_ratio"),
                "rs_vs_spy_20d_pct": rel.get("rs_vs_spy_20d_pct"),
                "premarket_change_pct": (f.premarket or {}).get("premarket_change_pct"),
                "regular_change_pct": (f.premarket or {}).get("regular_change_pct"),
                "news_sentiment_score": (f.news or {}).get("sentiment_score"),
                "news_sentiment_label": (f.news or {}).get("sentiment_label"),
                "news_article_count": (f.news or {}).get("article_count"),
                "market_cap": (f.fundamentals or {}).get("market_cap"),
                "trailing_pe": (f.valuation or {}).get("trailing_pe"),
                "forward_pe": (f.valuation or {}).get("forward_pe"),
                "revenue_growth": (f.fundamentals or {}).get("revenue_growth"),
                "earnings_growth": (f.fundamentals or {}).get("earnings_growth"),
                "debt_to_equity": (f.fundamentals or {}).get("debt_to_equity"),
                "dist_to_52w_high_pct": fifty_two_week.get("dist_to_history_high_pct"),
                "dist_to_52w_low_pct": fifty_two_week.get("dist_to_history_low_pct"),
                "fwd_5d_mean_pct": fwd_5d.get("mean_pct"),
                "fwd_5d_win_rate": fwd_5d.get("win_rate"),
                "max_drawdown_60d_pct": dd_60d.get("max_drawdown_pct"),
                "avg_dollar_volume_window": dollar_vol.get("mean"),
                "market_beta_20d": linkage_20d.get("beta"),
                "nearest_support": support_resistance.get("nearest_support"),
                "nearest_resistance": support_resistance.get("nearest_resistance"),
                "options_put_call_oi_ratio": options_agg.get("put_call_open_interest_ratio"),
                "options_put_call_volume_ratio": options_agg.get("put_call_volume_ratio"),
                "options_nearest_expiry": (f.options_summary or {}).get("nearest_expiry"),
                "peer_count": len(sector_ctx.get("peers", [])) if isinstance(sector_ctx, dict) else None,
                "tags": "|".join(f.tags),
                "overall_coverage": (f.data_quality or {}).get("overall_coverage"),
            }
        )
    pd.DataFrame(rows).to_csv(csv_file, index=False)


def run_screening(
    tickers_override: list[str] | None = None,
    dry_run: bool = False,
):
    """
    执行完整流程

    Args:
        tickers_override: 手动指定股票列表 (覆盖自动获取)
        dry_run: True 时仅输出到控制台, 不推送 Discord
    """
    logger = logging.getLogger(__name__)
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("  美股短线特征聚合系统 启动")
    logger.info("=" * 60)

    # ====== Step 1: 获取股票宇宙 ======
    logger.info("\n📋 Step 1/7: 获取股票宇宙...")
    if tickers_override:
        tickers = tickers_override
    elif config.CUSTOM_WATCHLIST:
        tickers = config.CUSTOM_WATCHLIST
    else:
        tickers = get_stock_universe()

    if not tickers:
        logger.error("无法获取股票列表, 退出")
        return

    logger.info(f"   候选股票: {len(tickers)} 只")

    # ====== Step 2: 下载行情数据 ======
    logger.info("\n📥 Step 2/7: 下载行情数据...")
    market_collector = MarketDataCollector()

    # SPY 数据用于相对强度与市场环境
    spy_data = None
    try:
        spy_df = yf.download("SPY", period="1y", progress=False)
        if spy_df is not None and not spy_df.empty:
            if isinstance(spy_df.columns, pd.MultiIndex):
                spy_df.columns = spy_df.columns.get_level_values(0)
            spy_data = spy_df
            logger.info(f"   SPY 数据: {len(spy_data)} 个交易日")
    except Exception as e:
        logger.warning(f"   SPY 数据下载失败: {e}")

    history_data = market_collector.fetch_batch_history(tickers)
    if not history_data:
        logger.error("无法获取任何行情数据, 退出")
        return

    # ====== Step 3: 获取实时信息 ======
    logger.info("\n📡 Step 3/7: 获取股票实时信息...")
    valid_tickers = list(history_data.keys())
    ticker_infos = market_collector.fetch_ticker_info(valid_tickers)

    # ====== Step 4: 初始池过滤 ======
    logger.info("\n🔍 Step 4/7: 初始池过滤...")
    filtered_tickers = UniverseFilter.apply(history_data, ticker_infos)
    if not filtered_tickers:
        logger.error("过滤后无候选股票, 退出")
        return

    premarket_data = market_collector.get_premarket_data(ticker_infos)
    logger.info(f"   有真实盘前数据: {sum(1 for v in premarket_data.values() if v.get('has_premarket'))} 只")

    # ====== Step 5: 技术分析 ======
    logger.info("\n📐 Step 5/7: 运行技术分析...")
    tech_analyzer = TechnicalAnalyzer(spy_data=spy_data)
    technical_results: dict[str, TechnicalProfile] = {}

    for ticker in filtered_tickers:
        df = history_data.get(ticker)
        if df is not None:
            profile = tech_analyzer.analyze(ticker, df)
            if profile is not None:
                technical_results[ticker] = profile

    logger.info(f"   技术分析完成: {len(technical_results)} 只")

    # ====== Step 6: 新闻采集与情绪分析 ======
    logger.info("\n📰 Step 6/7: 采集新闻并分析情绪...")
    news_collector = NewsCollector()
    sentiment_analyzer = NewsSentimentAnalyzer()

    # 先按技术优先级筛一批做新闻, 避免 API 过载
    tech_sorted = sorted(
        technical_results.items(),
        key=lambda x: x[1].technical_score,
        reverse=True,
    )
    max_tickers_for_news = int(config.NEWS.get("max_tickers_for_news", len(tech_sorted)))
    if max_tickers_for_news <= 0:
        max_tickers_for_news = len(tech_sorted)
    top_tickers_for_news = [t for t, _ in tech_sorted[:max_tickers_for_news]]

    news_by_ticker = news_collector.fetch_news_batch(top_tickers_for_news)
    news_sentiments: dict[str, dict] = {}
    for ticker, articles in news_by_ticker.items():
        news_sentiments[ticker] = sentiment_analyzer.analyze_articles(articles)
    provider_news_sentiments = news_collector.fetch_news_sentiment_batch(top_tickers_for_news)

    market_news_bundle = news_collector.fetch_market_news_bundle()
    macro_events = news_collector.fetch_economic_calendar(days_ahead=int(config.EVENTS["future_days"]))

    # ====== Step 7: 特征汇总与筛选 ======
    logger.info("\n🧩 Step 7/7: 汇总特征并准备 LLM payload...")
    assembler = FeatureAssembler()
    all_features: list[StockFeatures] = []
    spy_history_meta = {}
    if spy_data is not None and not spy_data.empty:
        spy_history_meta = {
            "source": "yfinance",
            "symbol": "SPY",
            "data_type": "history",
            "as_of": _frame_as_of_iso(_normalize_history_frame(spy_data)),
            "fetched_at": datetime.utcnow().isoformat(),
            "retrieval_mode": "network_refresh",
        }

    for ticker, tech_profile in technical_results.items():
        feature_source_meta = {
            "history": market_collector.get_history_meta(ticker),
            "ticker_info": market_collector.get_ticker_info_meta(ticker),
            "company_news": news_collector.get_company_news_meta(ticker),
            "news_sentiment": news_collector.get_news_sentiment_meta(ticker),
            "spy_history": spy_history_meta,
        }
        feature = assembler.build(
            technical=tech_profile,
            premarket_data=premarket_data.get(ticker),
            news_data=news_sentiments.get(ticker),
            news_sentiment_data=provider_news_sentiments.get(ticker),
            news_articles=news_by_ticker.get(ticker, []),
            ticker_info=ticker_infos.get(ticker),
            source_meta=feature_source_meta,
            history_df=history_data.get(ticker),
            spy_history_df=spy_data,
        )
        all_features.append(feature)

    final_features = FeatureFilter.select(all_features)
    company_events_map = {
        ticker: _extract_company_event_list(ticker=ticker, ticker_info=info)
        for ticker, info in ticker_infos.items()
    }
    options_collector = OptionsCollector(cache=market_collector.cache)
    selected_tickers = [f.ticker for f in final_features]
    selected_spot_prices = {
        ticker: getattr(technical_results.get(ticker), "last_close", None)
        for ticker in selected_tickers
    }
    options_by_ticker = options_collector.fetch_options_batch(
        tickers=selected_tickers,
        spot_prices=selected_spot_prices,
    )

    enriched_features: list[StockFeatures] = []
    for feature in final_features:
        ticker = feature.ticker
        sector_context = _build_sector_context(
            feature=feature,
            all_features=all_features,
            history_data=history_data,
        )
        upcoming_events = _build_upcoming_events(
            ticker=ticker,
            company_events_map=company_events_map,
            sector_context=sector_context,
            macro_events=macro_events,
        )

        source_meta = {
            "history": market_collector.get_history_meta(ticker),
            "ticker_info": market_collector.get_ticker_info_meta(ticker),
            "company_news": news_collector.get_company_news_meta(ticker),
            "news_sentiment": news_collector.get_news_sentiment_meta(ticker),
            "spy_history": spy_history_meta,
            "options_summary": options_collector.get_options_meta(ticker),
            "sector_context": {
                "source": "in_memory",
                "symbol": ticker,
                "data_type": "sector_context",
                "as_of": datetime.utcnow().isoformat(),
                "fetched_at": datetime.utcnow().isoformat(),
                "retrieval_mode": "network_refresh",
            },
            "upcoming_events": {
                "source": "finnhub+yfinance",
                "symbol": ticker,
                "data_type": "upcoming_events",
                "as_of": datetime.utcnow().isoformat(),
                "fetched_at": datetime.utcnow().isoformat(),
                "retrieval_mode": "network_refresh",
            },
        }

        rebuilt = assembler.build(
            technical=technical_results[ticker],
            premarket_data=premarket_data.get(ticker),
            news_data=news_sentiments.get(ticker),
            news_sentiment_data=provider_news_sentiments.get(ticker),
            news_articles=news_by_ticker.get(ticker, []),
            ticker_info=ticker_infos.get(ticker),
            options_summary=options_by_ticker.get(ticker),
            sector_context=sector_context,
            upcoming_events=upcoming_events,
            source_meta=source_meta,
            history_df=history_data.get(ticker),
            spy_history_df=spy_data,
        )
        enriched_features.append(rebuilt)
    final_features = enriched_features

    # ====== 输出结果 ======
    market_regime, market_regime_meta = get_market_regime(spy_data=spy_data)
    volatility_group = (market_regime.get("symbol_groups") or {}).get("volatility", {})
    vix_symbol = next(iter(volatility_group.keys()), "^VIX")
    vix_snapshot = volatility_group.get(vix_symbol, {})
    vix_from_regime = vix_snapshot.get("close")
    vix_as_of = vix_snapshot.get("as_of", "")
    market_summary = get_market_summary(
        spy_data,
        vix_override=vix_from_regime,
        vix_as_of_override=vix_as_of,
    )
    market_summary_meta = build_market_summary_provenance(
        spy_data=spy_data,
        vix_as_of=vix_as_of,
        vix_symbol=vix_symbol,
    )
    report = format_console_report(final_features, market_summary)
    print("\n" + report)

    os.makedirs(config.SYSTEM["output_dir"], exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 文本报告
    report_file = os.path.join(
        config.SYSTEM["output_dir"],
        f"pool_{date_str}.txt",
    )
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"报告已保存: {report_file}")

    # 概览 CSV
    if final_features:
        csv_file = os.path.join(
            config.SYSTEM["output_dir"],
            f"pool_{date_str}.csv",
        )
        _save_summary_csv(final_features, csv_file)
        logger.info(f"CSV 已保存: {csv_file}")

    # LLM 输入包 (按股票拆分)
    run_meta = {
        "input_ticker_count": len(tickers),
        "history_ticker_count": len(history_data),
        "filtered_ticker_count": len(filtered_tickers),
        "technical_ticker_count": len(technical_results),
        "feature_ticker_count": len(all_features),
        "candidate_ticker_count": len(final_features),
        "market_regime_symbol_count": len(market_regime.get("all_symbols", [])),
        "collector_cache_stats": market_collector.cache_stats,
        "news_cache_stats": news_collector.cache_stats,
        "options_cache_stats": options_collector.cache_stats,
        "macro_event_count": len(macro_events),
        "cache_backend_stats": {
            "market_collector": market_collector.cache.stats,
            "news_collector": news_collector.cache.stats,
            "options_collector": options_collector.cache.stats,
        },
    }

    market_context = build_market_context(
        market_summary=market_summary,
        market_news_bundle=market_news_bundle,
        macro_events=macro_events,
        market_regime=market_regime,
        market_provenance={
            "market_summary": market_summary_meta,
            "market_regime": market_regime_meta,
            "market_news": news_collector.get_market_news_meta_bundle(),
            "upcoming_macro_events": news_collector.get_economic_calendar_meta(),
        },
    )

    llm_root = os.path.join(config.LLM["inputs_root_dir"], ts_str)
    llm_stocks_dir = os.path.join(llm_root, "stocks")
    os.makedirs(llm_stocks_dir, exist_ok=True)

    market_context_file = os.path.join(llm_root, "market_context.json")
    with open(market_context_file, "w", encoding="utf-8") as f:
        json.dump(market_context, f, ensure_ascii=False, indent=2)

    jobs = []
    for idx, feature in enumerate(final_features, start=1):
        packet = build_stock_packet(
            feature=feature,
            market_context=market_context,
            rank=idx,
        )
        stock_packet_file = os.path.join(
            llm_stocks_dir,
            f"{idx:02d}_{feature.ticker}.json",
        )
        with open(stock_packet_file, "w", encoding="utf-8") as f:
            json.dump(packet, f, ensure_ascii=False, indent=2)

        jobs.append(
            {
                "job_id": f"{ts_str}_{feature.ticker}",
                "ticker": feature.ticker,
                "rank": idx,
                "packet_file": stock_packet_file,
                "model_profiles": list(config.LLM["model_profiles"]),
            }
        )

    jobs_file = os.path.join(llm_root, "llm_jobs.ndjson")
    with open(jobs_file, "w", encoding="utf-8") as f:
        for job in jobs:
            f.write(json.dumps(job, ensure_ascii=False) + "\n")

    manifest = {
        "schema_version": "v3.llm_batch",
        "generated_at": datetime.utcnow().isoformat(),
        "batch_id": ts_str,
        "market_context_file": market_context_file,
        "jobs_file": jobs_file,
        "stock_packet_count": len(final_features),
        "model_profiles": list(config.LLM["model_profiles"]),
        "run_meta": run_meta,
    }
    manifest_file = os.path.join(llm_root, "llm_manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    logger.info(f"LLM 输入包目录已生成: {llm_root}")
    logger.info(f"  - market_context: {market_context_file}")
    logger.info(f"  - stock packets: {len(final_features)} 个")
    logger.info(f"  - jobs: {jobs_file}")
    logger.info(f"  - manifest: {manifest_file}")

    # Discord 推送
    if not dry_run:
        notifier = DiscordNotifier()
        notifier.send_stock_pool(final_features, market_summary)
    else:
        logger.info("--dry-run 模式, 跳过 Discord 推送")

    elapsed = time.time() - start_time
    logger.info(f"\n✅ 全流程完成, 耗时 {elapsed:.1f} 秒")


def main():
    parser = argparse.ArgumentParser(description="美股短线特征聚合系统")
    parser.add_argument(
        "--test", action="store_true",
        help="测试 Discord Webhook 连接",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅输出到控制台, 不推送 Discord",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="指定股票列表, 逗号分隔 (例: AAPL,MSFT,NVDA)",
    )
    args = parser.parse_args()

    logger = setup_logging()

    if args.test:
        logger.info("测试 Discord Webhook 连接...")
        notifier = DiscordNotifier()
        notifier.send_test_message()
        return

    tickers_override = None
    if args.tickers:
        tickers_override = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        logger.info(f"手动指定股票: {tickers_override}")

    try:
        run_screening(
            tickers_override=tickers_override,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        logger.info("\n用户中断, 退出")
    except Exception as e:
        logger.exception(f"系统异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
