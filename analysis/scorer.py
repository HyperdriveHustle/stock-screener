"""
特征输出模块
将技术面 / 盘前 / 新闻 / 基本面汇总为完整特征包，供 LLM 分析
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from runtime import config
from runtime.utils import news_fingerprint
from runtime.utils import safe_float as _safe_float
from runtime.utils import safe_int as _safe_int
from analysis.analyzer import TechnicalProfile


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _normalize_provenance(meta: dict | None) -> dict:
    item = dict(meta or {})
    fetched_at = _parse_iso_datetime(item.get("fetched_at"))
    expires_at = _parse_iso_datetime(item.get("expires_at"))
    now = datetime.now(timezone.utc)

    if fetched_at is not None and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    age_seconds = None
    if fetched_at is not None:
        age_seconds = int((now - fetched_at).total_seconds())

    ttl_seconds = None
    if fetched_at is not None and expires_at is not None:
        ttl_seconds = int((expires_at - fetched_at).total_seconds())

    stale = False
    if expires_at is not None:
        stale = expires_at < now

    normalized = {
        "source": item.get("source", ""),
        "symbol": item.get("symbol", ""),
        "data_type": item.get("data_type", ""),
        "as_of": item.get("as_of", ""),
        "fetched_at": item.get("fetched_at", ""),
        "expires_at": item.get("expires_at", ""),
        "retrieval_mode": item.get("retrieval_mode", ""),
        "age_seconds": age_seconds,
        "ttl_seconds": ttl_seconds,
        "stale": stale,
    }
    for key, value in item.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _extract_numeric_series(df: pd.DataFrame | None, column: str) -> pd.Series:
    if df is None or not isinstance(df, pd.DataFrame) or column not in df.columns:
        return pd.Series(dtype="float64")
    s = pd.to_numeric(df[column], errors="coerce")
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    return s.astype("float64")


def _safe_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return (numerator / denominator) * 100


def _quantile_key(q: float) -> str:
    return f"p{int(round(q * 100)):02d}"


def _series_distribution_stats(
    series: pd.Series,
    quantiles: list[float] | None = None,
) -> dict:
    q_values = quantiles
    if q_values is None:
        q_values = [float(q) for q in config.FEATURE_STATS.get("distribution_quantiles", [])]
    q_values = sorted({q for q in q_values if 0 < q < 1})

    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    result = {
        "sample_size": int(len(clean)),
        "mean": float(clean.mean()) if not clean.empty else None,
        "median": float(clean.median()) if not clean.empty else None,
        "std": float(clean.std(ddof=0)) if not clean.empty else None,
        "min": float(clean.min()) if not clean.empty else None,
        "max": float(clean.max()) if not clean.empty else None,
        "positive_ratio": float((clean > 0).mean()) if not clean.empty else None,
    }
    for q in q_values:
        result[_quantile_key(q)] = float(clean.quantile(q)) if not clean.empty else None
    return result


def _build_fact_sheet(
    technical: TechnicalProfile,
    ticker_info: dict,
    history_df: pd.DataFrame | None,
) -> dict:
    close = _extract_numeric_series(history_df, "Close")
    high = _extract_numeric_series(history_df, "High")
    low = _extract_numeric_series(history_df, "Low")

    last_close = _safe_float(technical.last_close)
    high_52w_hist = _safe_float(high.tail(252).max()) if not high.empty else None
    low_52w_hist = _safe_float(low.tail(252).min()) if not low.empty else None
    high_52w_info = _safe_float(ticker_info.get("fiftyTwoWeekHigh"))
    low_52w_info = _safe_float(ticker_info.get("fiftyTwoWeekLow"))

    dist_to_high_hist = None
    dist_to_low_hist = None
    position_pct = None
    if last_close is not None and high_52w_hist is not None:
        dist_to_high_hist = _safe_pct(high_52w_hist - last_close, high_52w_hist)
    if last_close is not None and low_52w_hist is not None:
        dist_to_low_hist = _safe_pct(last_close - low_52w_hist, low_52w_hist)
    if (
        last_close is not None
        and high_52w_hist is not None
        and low_52w_hist is not None
        and high_52w_hist > low_52w_hist
    ):
        position_pct = _safe_pct(last_close - low_52w_hist, high_52w_hist - low_52w_hist)

    return {
        "last_close": last_close,
        "daily_change_pct": _safe_float(technical.daily_change_pct),
        "fifty_two_week": {
            "history_high": high_52w_hist,
            "history_low": low_52w_hist,
            "info_high": high_52w_info,
            "info_low": low_52w_info,
            "dist_to_history_high_pct": dist_to_high_hist,
            "dist_to_history_low_pct": dist_to_low_hist,
            "position_in_history_range_pct": position_pct,
            "history_info_high_diff_pct": (
                _safe_pct(high_52w_info - high_52w_hist, high_52w_hist)
                if high_52w_info is not None and high_52w_hist is not None
                else None
            ),
            "history_info_low_diff_pct": (
                _safe_pct(low_52w_info - low_52w_hist, low_52w_hist)
                if low_52w_info is not None and low_52w_hist is not None
                else None
            ),
        },
        "pattern_flags": {
            "near_52w_high": bool(technical.near_52w_high),
            "near_52w_low": bool(technical.near_52w_low),
            "breakout_signal": bool(technical.breakout_signal),
        },
    }


def _build_ev_inputs(history_df: pd.DataFrame | None) -> dict:
    stats_cfg = config.FEATURE_STATS
    horizons = [
        int(h)
        for h in stats_cfg.get("forward_horizons_days", [])
        if str(h).isdigit() and int(h) > 0
    ]
    horizons = sorted(set(horizons))
    forward_quantiles = [
        float(q)
        for q in stats_cfg.get("forward_quantiles", [])
        if isinstance(q, (int, float)) and 0 < float(q) < 1
    ]
    forward_quantiles = sorted(set(forward_quantiles))

    close = _extract_numeric_series(history_df, "Close")
    daily_ret_pct = close.pct_change().dropna() * 100 if not close.empty else pd.Series(dtype="float64")

    forward = {}
    for h in horizons:
        key = f"{h}d"
        if close.empty or len(close) <= h:
            item = {
                "sample_size": 0,
                "mean_pct": None,
                "median_pct": None,
                "win_rate": None,
                "best_pct": None,
                "worst_pct": None,
            }
            for q in forward_quantiles:
                item[f"{_quantile_key(q)}_pct"] = None
            forward[key] = item
            continue

        fwd = (close.shift(-h) / close - 1.0).iloc[:-h] * 100
        fwd = fwd.replace([np.inf, -np.inf], np.nan).dropna()
        if fwd.empty:
            item = {
                "sample_size": 0,
                "mean_pct": None,
                "median_pct": None,
                "win_rate": None,
                "best_pct": None,
                "worst_pct": None,
            }
            for q in forward_quantiles:
                item[f"{_quantile_key(q)}_pct"] = None
            forward[key] = item
            continue

        item = {
            "sample_size": int(len(fwd)),
            "mean_pct": float(fwd.mean()),
            "median_pct": float(fwd.median()),
            "win_rate": float((fwd > 0).mean()),
            "best_pct": float(fwd.max()),
            "worst_pct": float(fwd.min()),
        }
        for q in forward_quantiles:
            item[f"{_quantile_key(q)}_pct"] = float(fwd.quantile(q))
        forward[key] = item

    return {
        "daily_return_distribution_pct": _series_distribution_stats(daily_ret_pct),
        "forward_return_distribution_pct": forward,
    }


def _build_execution_context(history_df: pd.DataFrame | None) -> dict:
    window = int(config.FEATURE_STATS.get("execution_window_days", 20))
    window = max(1, window)

    close = _extract_numeric_series(history_df, "Close")
    open_ = _extract_numeric_series(history_df, "Open")
    high = _extract_numeric_series(history_df, "High")
    low = _extract_numeric_series(history_df, "Low")
    volume = _extract_numeric_series(history_df, "Volume")

    if close.empty or volume.empty:
        return {
            "window_days": window,
            "dollar_volume": {},
            "volume_profile": {},
            "range_profile_pct": {},
            "gap_profile_pct": {},
        }

    aligned = pd.concat(
        [
            close.rename("close"),
            open_.rename("open"),
            high.rename("high"),
            low.rename("low"),
            volume.rename("volume"),
        ],
        axis=1,
    ).dropna()

    if aligned.empty:
        return {
            "window_days": window,
            "dollar_volume": {},
            "volume_profile": {},
            "range_profile_pct": {},
            "gap_profile_pct": {},
        }

    w = aligned.tail(window)
    dollar_volume = w["close"] * w["volume"]
    intraday_range_pct = (w["high"] - w["low"]) / w["close"] * 100
    prev_close = aligned["close"].shift(1)
    gap_pct = ((aligned["open"] / prev_close) - 1.0) * 100
    gap_pct = gap_pct.tail(window).dropna()

    return {
        "window_days": window,
        "dollar_volume": {
            "mean": _safe_float(dollar_volume.mean()),
            "median": _safe_float(dollar_volume.median()),
            "p20": _safe_float(dollar_volume.quantile(0.20)),
            "p80": _safe_float(dollar_volume.quantile(0.80)),
            "last": _safe_float((aligned["close"] * aligned["volume"]).iloc[-1]),
        },
        "volume_profile": {
            "mean": _safe_float(w["volume"].mean()),
            "median": _safe_float(w["volume"].median()),
            "std": _safe_float(w["volume"].std(ddof=0)),
            "cv": (
                _safe_float(w["volume"].std(ddof=0) / w["volume"].mean())
                if _safe_float(w["volume"].mean()) not in (None, 0)
                else None
            ),
            "last": _safe_float(aligned["volume"].iloc[-1]),
        },
        "range_profile_pct": _series_distribution_stats(intraday_range_pct),
        "gap_profile_pct": {
            "distribution": _series_distribution_stats(gap_pct),
            "abs_distribution": _series_distribution_stats(gap_pct.abs()),
        },
    }


def _build_drawdown_context(history_df: pd.DataFrame | None) -> dict:
    close = _extract_numeric_series(history_df, "Close")
    windows = [
        int(w)
        for w in config.FEATURE_STATS.get("drawdown_windows_days", [])
        if str(w).isdigit() and int(w) > 1
    ]
    windows = sorted(set(windows))

    result = {}
    for w in windows:
        key = f"{w}d"
        if close.empty or len(close) < w:
            result[key] = {
                "sample_size": 0,
                "window_return_pct": None,
                "current_drawdown_pct": None,
                "max_drawdown_pct": None,
            }
            continue

        segment = close.tail(w)
        running_max = segment.cummax()
        dd = (segment / running_max - 1.0) * 100
        window_return = (segment.iloc[-1] / segment.iloc[0] - 1.0) * 100
        result[key] = {
            "sample_size": int(len(segment)),
            "window_return_pct": float(window_return),
            "current_drawdown_pct": float(dd.iloc[-1]),
            "max_drawdown_pct": float(dd.min()),
        }
    return result


def _build_market_linkage(
    history_df: pd.DataFrame | None,
    spy_history_df: pd.DataFrame | None,
) -> dict:
    windows = [
        int(w)
        for w in config.FEATURE_STATS.get("market_linkage_windows_days", [])
        if str(w).isdigit() and int(w) > 1
    ]
    windows = sorted(set(windows))

    stock_close = _extract_numeric_series(history_df, "Close")
    spy_close = _extract_numeric_series(spy_history_df, "Close")

    if stock_close.empty or spy_close.empty:
        empty = {}
        for w in windows:
            empty[f"{w}d"] = {
                "sample_size": 0,
                "corr": None,
                "beta": None,
                "relative_return_pct": None,
                "tracking_error_pct": None,
            }
        return empty

    stock_ret = stock_close.pct_change().dropna()
    spy_ret = spy_close.pct_change().dropna()
    aligned = pd.concat(
        [stock_ret.rename("stock"), spy_ret.rename("spy")],
        axis=1,
    ).dropna()

    result = {}
    for w in windows:
        key = f"{w}d"
        sample = aligned.tail(w)
        if sample.empty:
            result[key] = {
                "sample_size": 0,
                "corr": None,
                "beta": None,
                "relative_return_pct": None,
                "tracking_error_pct": None,
            }
            continue

        spy_var = float(sample["spy"].var(ddof=0))
        cov = float(sample["stock"].cov(sample["spy"]))
        beta = cov / spy_var if spy_var != 0 else None
        stock_cum = float((1.0 + sample["stock"]).prod() - 1.0)
        spy_cum = float((1.0 + sample["spy"]).prod() - 1.0)
        rel_return = (stock_cum - spy_cum) * 100
        tracking_error = float((sample["stock"] - sample["spy"]).std(ddof=0) * 100)

        result[key] = {
            "sample_size": int(len(sample)),
            "corr": float(sample["stock"].corr(sample["spy"])) if len(sample) > 1 else None,
            "beta": float(beta) if beta is not None else None,
            "relative_return_pct": rel_return,
            "tracking_error_pct": tracking_error,
        }
    return result


def _build_valuation_consistency(ticker_info: dict, last_close: float | None) -> dict:
    market_cap = _safe_float(ticker_info.get("marketCap"))
    total_revenue = _safe_float(ticker_info.get("totalRevenue"))
    trailing_pe = _safe_float(ticker_info.get("trailingPE"))
    forward_pe = _safe_float(ticker_info.get("forwardPE"))
    reported_ps = _safe_float(ticker_info.get("priceToSalesTrailing12Months"))

    implied_ps = None
    if market_cap is not None and total_revenue is not None and total_revenue != 0:
        implied_ps = market_cap / total_revenue

    earnings_yield_ttm_pct = None
    if trailing_pe is not None and trailing_pe != 0:
        earnings_yield_ttm_pct = 100.0 / trailing_pe

    earnings_yield_fwd_pct = None
    if forward_pe is not None and forward_pe != 0:
        earnings_yield_fwd_pct = 100.0 / forward_pe

    target_mean = _safe_float(ticker_info.get("targetMeanPrice"))
    upside_to_target_pct = None
    if target_mean is not None and last_close is not None and last_close != 0:
        upside_to_target_pct = ((target_mean / last_close) - 1.0) * 100

    return {
        "implied_price_to_sales_from_market_cap": implied_ps,
        "reported_price_to_sales_ttm": reported_ps,
        "implied_vs_reported_ps_diff_pct": (
            _safe_pct(implied_ps - reported_ps, reported_ps)
            if implied_ps is not None and reported_ps is not None and reported_ps != 0
            else None
        ),
        "earnings_yield_ttm_pct": earnings_yield_ttm_pct,
        "earnings_yield_fwd_pct": earnings_yield_fwd_pct,
        "upside_to_analyst_target_mean_pct": upside_to_target_pct,
    }


def _build_ohlc_recent(history_df: pd.DataFrame | None) -> list[dict]:
    days = int(config.FEATURE_STATS.get("ohlc_recent_days", 0))
    if days <= 0:
        return []
    if history_df is None or history_df.empty:
        return []

    columns = {"Open", "High", "Low", "Close", "Volume"}
    if not columns.issubset(set(history_df.columns)):
        return []

    frame = history_df.copy().tail(days)
    rows = []
    for idx, row in frame.iterrows():
        ts = idx
        if isinstance(ts, pd.Timestamp) and ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        rows.append(
            {
                "timestamp": ts.isoformat() if isinstance(ts, pd.Timestamp) else str(ts),
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_float(row.get("Volume")),
            }
        )
    return rows


def _build_support_resistance(history_df: pd.DataFrame | None) -> dict:
    if history_df is None or history_df.empty:
        return {}
    if not {"High", "Low", "Close"}.issubset(set(history_df.columns)):
        return {}

    window = int(config.FEATURE_STATS.get("support_resistance_window_days", 120))
    max_levels = int(config.FEATURE_STATS.get("support_resistance_max_levels", 8))
    tol_pct = float(config.FEATURE_STATS.get("support_resistance_merge_tolerance_pct", 0.5))
    window = max(20, window)
    max_levels = max(1, max_levels)
    tol_pct = max(0.0, tol_pct)

    frame = history_df.copy().tail(window)
    high = _extract_numeric_series(frame, "High")
    low = _extract_numeric_series(frame, "Low")
    close = _extract_numeric_series(frame, "Close")
    if high.empty or low.empty or close.empty:
        return {}

    pivot_highs = high[(high.shift(1) < high) & (high.shift(-1) < high)].dropna()
    pivot_lows = low[(low.shift(1) > low) & (low.shift(-1) > low)].dropna()

    if pivot_highs.empty:
        pivot_highs = pd.Series([high.max()], dtype="float64")
    if pivot_lows.empty:
        pivot_lows = pd.Series([low.min()], dtype="float64")

    def _dedupe_levels(values: pd.Series, desc: bool) -> list[float]:
        sorted_values = sorted(
            [float(v) for v in values.dropna().tolist()],
            reverse=desc,
        )
        selected = []
        for candidate in sorted_values:
            if candidate <= 0:
                continue
            if not selected:
                selected.append(candidate)
            else:
                if all(abs(candidate - s) / s * 100 > tol_pct for s in selected if s > 0):
                    selected.append(candidate)
            if len(selected) >= max_levels:
                break
        return selected

    resistance_levels = _dedupe_levels(pivot_highs, desc=False)
    support_levels = _dedupe_levels(pivot_lows, desc=True)
    current_close = float(close.iloc[-1])

    resistance_levels = sorted([x for x in resistance_levels if x >= current_close])
    support_levels = sorted([x for x in support_levels if x <= current_close], reverse=True)

    if not resistance_levels:
        fallback_resistance = [float(v) for v in pivot_highs.tolist() if float(v) >= current_close]
        if fallback_resistance:
            resistance_levels = sorted(fallback_resistance)[:max_levels]

    if not support_levels:
        fallback_support = [float(v) for v in pivot_lows.tolist() if float(v) <= current_close]
        if fallback_support:
            support_levels = sorted(fallback_support, reverse=True)[:max_levels]

    nearest_resistance = None
    if resistance_levels:
        higher = [x for x in resistance_levels if x >= current_close]
        nearest_resistance = min(higher) if higher else min(resistance_levels, key=lambda x: abs(x - current_close))

    nearest_support = None
    if support_levels:
        lower = [x for x in support_levels if x <= current_close]
        nearest_support = max(lower) if lower else min(support_levels, key=lambda x: abs(x - current_close))

    return {
        "lookback_days": int(len(frame)),
        "current_close": current_close,
        "resistance_levels": resistance_levels,
        "support_levels": support_levels,
        "nearest_resistance": nearest_resistance,
        "nearest_support": nearest_support,
    }


def _iter_leaf_values(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_leaf_values(v)
        return
    if isinstance(value, list):
        for v in value:
            yield from _iter_leaf_values(v)
        return
    yield value


@dataclass
class StockFeatures:
    """单只股票的完整特征包"""

    ticker: str
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    exchange: str = ""
    country: str = ""
    currency: str = ""

    # 排序优先级 (仅用于展示顺序, 非加权总分)
    technical_priority: float = 0.0

    # 核心块
    technical: dict = field(default_factory=dict)
    premarket: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    fundamentals: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)
    analyst: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    liquidity: dict = field(default_factory=dict)
    fact_sheet: dict = field(default_factory=dict)
    ev_inputs: dict = field(default_factory=dict)
    drawdown_context: dict = field(default_factory=dict)
    execution_context: dict = field(default_factory=dict)
    market_linkage: dict = field(default_factory=dict)
    valuation_consistency: dict = field(default_factory=dict)
    ohlc_recent: list[dict] = field(default_factory=list)
    support_resistance: dict = field(default_factory=dict)
    options_summary: dict = field(default_factory=dict)
    sector_context: dict = field(default_factory=dict)
    upcoming_events: dict = field(default_factory=dict)

    # 元信息
    tags: list[str] = field(default_factory=list)
    data_quality: dict = field(default_factory=dict)
    data_provenance: dict = field(default_factory=dict)
    source_meta: dict = field(default_factory=dict)


class FeatureAssembler:
    """构建特征包"""

    def build(
        self,
        technical: TechnicalProfile,
        premarket_data: dict | None,
        news_data: dict | None,
        news_sentiment_data: dict | None,
        news_semantic_data: dict | None,
        news_articles: list[dict] | None,
        ticker_info: dict | None,
        options_summary: dict | None = None,
        sector_context: dict | None = None,
        upcoming_events: dict | None = None,
        source_meta: dict | None = None,
        history_df: pd.DataFrame | None = None,
        spy_history_df: pd.DataFrame | None = None,
    ) -> StockFeatures:
        info = ticker_info or {}
        result = StockFeatures(ticker=technical.ticker)

        # 基础画像
        result.company_name = str(info.get("shortName") or info.get("longName") or technical.ticker)
        result.sector = str(info.get("sector") or "")
        result.industry = str(info.get("industry") or "")
        result.exchange = str(info.get("exchange") or info.get("fullExchangeName") or "")
        result.country = str(info.get("country") or "")
        result.currency = str(info.get("currency") or "USD")

        # 技术优先级: 仅用于排序, 不参与固定权重打分
        result.technical_priority = float(technical.technical_score)

        # 技术块
        result.technical = {
            "technical_score": technical.technical_score,
            "daily_change_pct": technical.daily_change_pct,
            "price": {
                "last_close": technical.last_close,
                "prev_close": technical.prev_close,
            },
            "moving_averages": {
                "ma5": technical.ma5,
                "ma10": technical.ma10,
                "ma20": technical.ma20,
                "ma50": technical.ma50,
                "ma200": technical.ma200,
                "ma_alignment_score": technical.ma_alignment_score,
            },
            "momentum": {
                "rsi": technical.rsi,
                "macd_value": technical.macd_value,
                "macd_signal": technical.macd_signal,
                "macd_histogram": technical.macd_histogram,
                "macd_cross": technical.macd_cross,
                "momentum_score": technical.momentum_score,
            },
            "volume_price": {
                "volume_ratio": technical.volume_ratio,
                "obv_trend": technical.obv_trend,
                "volume_score": technical.volume_score,
            },
            "volatility": {
                "atr": technical.atr,
                "atr_pct": technical.atr_pct,
                "bb_upper": technical.bb_upper,
                "bb_lower": technical.bb_lower,
                "bb_position": technical.bb_position,
            },
            "relative_strength": {
                "rs_vs_spy_20d_pct": technical.rs_vs_spy,
                "relative_strength_score": technical.relative_strength_score,
            },
            "patterns": {
                "near_52w_high": technical.near_52w_high,
                "near_52w_low": technical.near_52w_low,
                "breakout_signal": technical.breakout_signal,
            },
        }

        # 盘前块
        premarket = premarket_data or {}
        result.premarket = {
            "has_premarket": bool(premarket.get("has_premarket")),
            "price_source": premarket.get("price_source", ""),
            "premarket_price": _safe_float(premarket.get("premarket_price")),
            "premarket_change_pct": _safe_float(premarket.get("premarket_change_pct")),
            "regular_price": _safe_float(premarket.get("regular_price")),
            "regular_change_pct": _safe_float(premarket.get("regular_change_pct")),
            "prev_close": _safe_float(premarket.get("prev_close")),
        }

        # 新闻块
        raw_articles = news_articles or []
        max_news = int(config.FEATURES["max_news_articles_per_stock"])
        semantic_map = {}
        semantic_rollup = {}
        if isinstance(news_semantic_data, dict):
            semantic_map = dict(news_semantic_data.get("by_fingerprint") or {})
            semantic_rollup = dict(news_semantic_data.get("rollup") or {})
        compact_articles = []
        for article in raw_articles[:max_news]:
            compact = {
                "datetime": _safe_int(article.get("datetime")),
                "headline": article.get("headline", ""),
                "summary": article.get("summary", ""),
                "source": article.get("source", ""),
                "url": article.get("url", ""),
                "related": article.get("related", ""),
                "image": article.get("image", ""),
            }
            fingerprint = news_fingerprint(article)
            semantic = semantic_map.get(fingerprint)
            if isinstance(semantic, dict) and semantic:
                compact["semantic"] = dict(semantic)
            compact_articles.append(compact)

        result.news = {
            "status": (news_data or {}).get("status", "unavailable"),
            "unavailable_reason": (news_data or {}).get("unavailable_reason", ""),
            "sentiment_score": (news_data or {}).get("score"),
            "sentiment_label": (news_data or {}).get("sentiment"),
            "article_count": (news_data or {}).get("article_count", 0),
            "bullish_keyword_hits": (news_data or {}).get("bullish_count", 0),
            "bearish_keyword_hits": (news_data or {}).get("bearish_count", 0),
            "top_headline": (news_data or {}).get("top_headline", ""),
            "provider_sentiment_raw": dict(news_sentiment_data or {}),
            "semantic_rollup": semantic_rollup,
            "recent_articles": compact_articles,
        }

        # 基本面/估值/分析师/事件
        result.fundamentals = {
            "market_cap": _safe_float(info.get("marketCap")),
            "enterprise_value": _safe_float(info.get("enterpriseValue")),
            "shares_outstanding": _safe_float(info.get("sharesOutstanding")),
            "float_shares": _safe_float(info.get("floatShares")),
            "beta": _safe_float(info.get("beta")),
            "revenue_growth": _safe_float(info.get("revenueGrowth")),
            "earnings_growth": _safe_float(info.get("earningsGrowth")),
            "gross_margins": _safe_float(info.get("grossMargins")),
            "operating_margins": _safe_float(info.get("operatingMargins")),
            "ebitda_margins": _safe_float(info.get("ebitdaMargins")),
            "profit_margins": _safe_float(info.get("profitMargins")),
            "return_on_equity": _safe_float(info.get("returnOnEquity")),
            "return_on_assets": _safe_float(info.get("returnOnAssets")),
            "total_revenue": _safe_float(info.get("totalRevenue")),
            "ebitda": _safe_float(info.get("ebitda")),
            "net_income_to_common": _safe_float(info.get("netIncomeToCommon")),
            "operating_cashflow": _safe_float(info.get("operatingCashflow")),
            "free_cashflow": _safe_float(info.get("freeCashflow")),
            "total_cash": _safe_float(info.get("totalCash")),
            "total_debt": _safe_float(info.get("totalDebt")),
            "debt_to_equity": _safe_float(info.get("debtToEquity")),
            "current_ratio": _safe_float(info.get("currentRatio")),
            "quick_ratio": _safe_float(info.get("quickRatio")),
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "payout_ratio": _safe_float(info.get("payoutRatio")),
        }

        result.valuation = {
            "trailing_pe": _safe_float(info.get("trailingPE")),
            "forward_pe": _safe_float(info.get("forwardPE")),
            "price_to_sales_ttm": _safe_float(info.get("priceToSalesTrailing12Months")),
            "price_to_book": _safe_float(info.get("priceToBook")),
            "enterprise_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
            "enterprise_to_revenue": _safe_float(info.get("enterpriseToRevenue")),
            "peg_ratio": _safe_float(info.get("pegRatio")),
            "fifty_two_week_high": _safe_float(info.get("fiftyTwoWeekHigh")),
            "fifty_two_week_low": _safe_float(info.get("fiftyTwoWeekLow")),
        }

        result.analyst = {
            "recommendation_key": info.get("recommendationKey", ""),
            "recommendation_mean": _safe_float(info.get("recommendationMean")),
            "number_of_analyst_opinions": _safe_int(info.get("numberOfAnalystOpinions")),
            "target_mean_price": _safe_float(info.get("targetMeanPrice")),
            "target_high_price": _safe_float(info.get("targetHighPrice")),
            "target_low_price": _safe_float(info.get("targetLowPrice")),
        }

        result.events = {
            "earnings_timestamp": _safe_int(info.get("earningsTimestamp")),
            "earnings_timestamp_start": _safe_int(info.get("earningsTimestampStart")),
            "earnings_timestamp_end": _safe_int(info.get("earningsTimestampEnd")),
            "ex_dividend_date": _safe_int(info.get("exDividendDate")),
            "dividend_date": _safe_int(info.get("dividendDate")),
        }

        result.liquidity = {
            "average_volume_10d": _safe_float(info.get("averageVolume10days")),
            "average_volume": _safe_float(info.get("averageVolume")),
            "fifty_day_average": _safe_float(info.get("fiftyDayAverage")),
            "two_hundred_day_average": _safe_float(info.get("twoHundredDayAverage")),
            "fifty_day_change_pct": _safe_float(info.get("fiftyDayAverageChangePercent")),
            "two_hundred_day_change_pct": _safe_float(info.get("twoHundredDayAverageChangePercent")),
            "short_ratio": _safe_float(info.get("shortRatio")),
            "short_percent_of_float": _safe_float(info.get("shortPercentOfFloat")),
            "held_percent_institutions": _safe_float(info.get("heldPercentInstitutions")),
            "held_percent_insiders": _safe_float(info.get("heldPercentInsiders")),
        }

        result.fact_sheet = _build_fact_sheet(
            technical=technical,
            ticker_info=info,
            history_df=history_df,
        )
        result.ev_inputs = _build_ev_inputs(history_df=history_df)
        result.drawdown_context = _build_drawdown_context(history_df=history_df)
        result.execution_context = _build_execution_context(history_df=history_df)
        result.market_linkage = _build_market_linkage(
            history_df=history_df,
            spy_history_df=spy_history_df,
        )
        result.valuation_consistency = _build_valuation_consistency(
            ticker_info=info,
            last_close=_safe_float(technical.last_close),
        )
        result.ohlc_recent = _build_ohlc_recent(history_df=history_df)
        result.support_resistance = _build_support_resistance(history_df=history_df)
        result.options_summary = dict(options_summary or {})
        result.sector_context = dict(sector_context or {})
        result.upcoming_events = dict(upcoming_events or {})

        result.tags = list(technical.tags)
        result.data_quality = self._calc_data_quality(result)
        source_meta = source_meta or {}
        result.data_provenance = {
            "technical": _normalize_provenance(source_meta.get("history")),
            "premarket": _normalize_provenance(source_meta.get("ticker_info")),
            "news": _normalize_provenance(source_meta.get("company_news")),
            "fundamentals": _normalize_provenance(source_meta.get("ticker_info")),
            "valuation": _normalize_provenance(source_meta.get("ticker_info")),
            "analyst": _normalize_provenance(source_meta.get("ticker_info")),
            "events": _normalize_provenance(source_meta.get("ticker_info")),
            "liquidity": _normalize_provenance(source_meta.get("ticker_info")),
            "fact_sheet": _normalize_provenance(source_meta.get("history")),
            "ev_inputs": _normalize_provenance(source_meta.get("history")),
            "drawdown_context": _normalize_provenance(source_meta.get("history")),
            "execution_context": _normalize_provenance(source_meta.get("history")),
            "market_linkage": _normalize_provenance(
                source_meta.get("spy_history") or source_meta.get("history")
            ),
            "valuation_consistency": _normalize_provenance(source_meta.get("ticker_info")),
            "ohlc_recent": _normalize_provenance(source_meta.get("history")),
            "support_resistance": _normalize_provenance(source_meta.get("history")),
            "options_summary": _normalize_provenance(source_meta.get("options_summary")),
            "sector_context": _normalize_provenance(source_meta.get("sector_context")),
            "upcoming_events": _normalize_provenance(source_meta.get("upcoming_events")),
            "news_provider_sentiment": _normalize_provenance(source_meta.get("news_sentiment")),
            "news_semantic": _normalize_provenance(source_meta.get("news_semantic")),
        }
        result.source_meta = {
            "generated_at": datetime.utcnow().isoformat(),
            "news_articles_attached": len(compact_articles),
            "history_rows": int(len(history_df)) if isinstance(history_df, pd.DataFrame) else 0,
        }
        return result

    @staticmethod
    def _calc_data_quality(feature: StockFeatures) -> dict:
        sections = {
            "technical": feature.technical,
            "premarket": feature.premarket,
            "news": feature.news,
            "fundamentals": feature.fundamentals,
            "valuation": feature.valuation,
            "analyst": feature.analyst,
            "events": feature.events,
            "liquidity": feature.liquidity,
            "fact_sheet": feature.fact_sheet,
            "ev_inputs": feature.ev_inputs,
            "drawdown_context": feature.drawdown_context,
            "execution_context": feature.execution_context,
            "market_linkage": feature.market_linkage,
            "valuation_consistency": feature.valuation_consistency,
            "ohlc_recent": feature.ohlc_recent,
            "support_resistance": feature.support_resistance,
            "options_summary": feature.options_summary,
            "sector_context": feature.sector_context,
            "upcoming_events": feature.upcoming_events,
        }
        coverage = {}
        for section_name, section in sections.items():
            values = []
            if isinstance(section, (dict, list)):
                values = list(_iter_leaf_values(section))
            if not values:
                coverage[section_name] = 0.0
                continue
            non_empty = sum(v is not None and v != "" for v in values)
            coverage[section_name] = round(non_empty / len(values), 3)

        return {
            "section_coverage": coverage,
            "overall_coverage": round(sum(coverage.values()) / max(1, len(coverage)), 3),
        }


def build_market_context(
    market_summary: dict | None,
    market_news_bundle: dict | None,
    macro_events: list[dict] | None = None,
    market_regime: dict | None = None,
    market_provenance: dict | None = None,
) -> dict:
    """
    生成市场上下文, 可复用到每只股票的单独 LLM 调用
    """
    market_news_bundle = market_news_bundle or {}
    market_provenance = market_provenance or {}
    raw_news = {}
    compact_news_provenance = {}

    for category, articles in market_news_bundle.items():
        raw_news[category] = [dict(article or {}) for article in (articles or [])]
        compact_news_provenance[category] = _normalize_provenance(
            (market_provenance.get("market_news") or {}).get(category)
        )

    return {
        "schema_version": "v3.market_context",
        "context_id": f"mc_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "generated_at": datetime.utcnow().isoformat(),
        "market_summary": market_summary or {},
        "market_regime": market_regime or {},
        "market_news_digest": raw_news,
        "upcoming_macro_events": [dict(x or {}) for x in (macro_events or [])],
        "data_provenance": {
            "market_summary": _normalize_provenance(market_provenance.get("market_summary")),
            "market_regime": _normalize_provenance(market_provenance.get("market_regime")),
            "market_news": compact_news_provenance,
            "upcoming_macro_events": _normalize_provenance(
                market_provenance.get("upcoming_macro_events")
            ),
        },
    }
