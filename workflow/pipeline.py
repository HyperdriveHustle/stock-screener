from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from runtime import config
from analysis.analyzer import NewsSentimentAnalyzer, TechnicalAnalyzer, TechnicalProfile
from workflow.artifact_builders import (
    build_compact_card,
    build_full_dossier,
    build_market_context_compact,
    build_outcome_seed,
)
from runtime.artifact_store import ArtifactStore
from providers.collector import MarketDataCollector, NewsCollector, OptionsCollector
from workflow.llm_funnel import StructuredLLMRunner
from delivery.notifier import DiscordNotifier, format_console_report
from analysis.scorer import FeatureAssembler, StockFeatures, build_market_context
from providers.semantic_tagger import SemanticNewsTagger
from runtime.session_controller import create_session_context
from workflow.symbol_registry import SymbolRegistry
from runtime.trace_store import SymbolTrace

logger = logging.getLogger(__name__)


def _configure_yfinance_cache() -> None:
    cache_dir = os.path.join(config.SYSTEM["data_dir"], "yfinance_cache")
    os.makedirs(cache_dir, exist_ok=True)
    setter = getattr(yf, "set_tz_cache_location", None)
    if callable(setter):
        setter(cache_dir)


def _normalize_history_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    if "Close" not in frame.columns:
        return None
    frame = frame.dropna(how="all")
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert(None)
    return frame


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


def _build_symbol_snapshot(symbol: str, frame: pd.DataFrame | None, windows_days: list[int]) -> dict:
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
    cfg = config.MARKET_CONTEXT
    windows_days = sorted({int(v) for v in cfg.get("return_windows_days", []) if int(v) > 0})
    symbol_groups = cfg.get("symbol_groups", {})

    ordered_symbols: list[str] = []
    seen = set()
    for symbols in symbol_groups.values():
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
    as_of = _max_as_of([_frame_as_of_iso(symbol_frames.get(symbol)) for symbol in ordered_symbols])
    provenance = {
        "source": "yfinance",
        "symbol": ",".join(ordered_symbols),
        "data_type": "market_regime",
        "fetched_at": datetime.utcnow().isoformat(),
        "as_of": as_of,
        "retrieval_mode": "network_refresh",
    }
    return regime, provenance


def get_market_summary(
    spy_data: pd.DataFrame | None,
    vix_override: float | None = None,
    vix_as_of_override: str = "",
) -> dict:
    summary = {
        "spy_change": 0.0,
        "spy_close": None,
        "spy_prev_close": None,
        "spy_as_of": "",
        "vix": 0.0,
        "vix_as_of": vix_as_of_override or "",
    }
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
    events.sort(key=lambda item: item["event_time"])
    return events


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _avg_dollar_volume(history_df: pd.DataFrame | None, window: int = 20) -> float:
    if history_df is None or history_df.empty:
        return 0.0
    if not {"Close", "Volume"}.issubset(set(history_df.columns)):
        return 0.0
    frame = history_df.tail(window)
    try:
        return float((frame["Close"] * frame["Volume"]).mean())
    except Exception:
        return 0.0


def _history_payload(df: pd.DataFrame | None) -> dict:
    normalized = _normalize_history_frame(df)
    if normalized is None or normalized.empty:
        return {"rows": 0, "as_of": "", "ohlcv": []}
    records = []
    for idx, row in normalized.tail(config.FEATURE_STATS.get("ohlc_recent_days", 60)).iterrows():
        ts = idx.to_pydatetime().isoformat() if isinstance(idx, pd.Timestamp) else str(idx)
        records.append(
            {
                "timestamp": ts,
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_float(row.get("Volume")),
            }
        )
    return {
        "rows": int(len(normalized)),
        "as_of": _frame_as_of_iso(normalized),
        "ohlcv": records,
    }


def _evaluate_eligibility(
    registry_record: dict,
    history_df: pd.DataFrame | None,
    ticker_info: dict | None,
) -> dict:
    info = ticker_info or {}
    reasons = []
    metrics = {
        "history_rows": int(len(history_df)) if history_df is not None else 0,
        "last_close": _safe_last_close(history_df),
        "avg_volume_20d": None,
        "avg_dollar_volume_20d": _avg_dollar_volume(history_df),
        "market_cap": _safe_float(info.get("marketCap")),
    }

    if not registry_record.get("is_active", True):
        reasons.append("inactive_symbol")
    if registry_record.get("is_etf"):
        reasons.append("not_common_stock")
    if registry_record.get("is_adr"):
        reasons.append("adr_excluded")
    if registry_record.get("is_spac"):
        reasons.append("spac_excluded")

    if history_df is None or history_df.empty or len(history_df) < 50:
        reasons.append("insufficient_history")

    if history_df is not None and not history_df.empty and "Volume" in history_df.columns:
        try:
            metrics["avg_volume_20d"] = float(history_df["Volume"].tail(20).mean())
        except Exception:
            metrics["avg_volume_20d"] = None

    last_close = metrics["last_close"]
    if last_close is None:
        reasons.append("missing_last_close")
    else:
        if last_close < config.UNIVERSE["min_price"] or last_close > config.UNIVERSE["max_price"]:
            reasons.append("price_out_of_range")

    avg_volume = metrics["avg_volume_20d"]
    if avg_volume is None or avg_volume < config.UNIVERSE["min_avg_volume"]:
        reasons.append("insufficient_volume")

    market_cap = metrics["market_cap"]
    if market_cap is not None and market_cap < config.UNIVERSE["min_market_cap"]:
        reasons.append("market_cap_too_small")

    decision = "pass" if not reasons else "reject"
    return {
        "decision": decision,
        "reason_codes": reasons if reasons else ["tradable", "data_ready"],
        "metrics": metrics,
    }


def _build_sector_context(
    feature: StockFeatures,
    all_features: list[StockFeatures],
    history_data: dict[str, pd.DataFrame],
) -> dict:
    max_peer_count = int(config.SECTOR_CONTEXT.get("max_peer_count", 10))
    same_sector = [
        candidate
        for candidate in all_features
        if candidate.ticker != feature.ticker and candidate.sector and candidate.sector == feature.sector
    ]
    same_sector.sort(
        key=lambda item: (
            float((item.fundamentals or {}).get("market_cap") or 0),
            float(item.technical_priority),
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
                "relative_strength_vs_spy_20d_pct": (
                    ((peer.technical or {}).get("relative_strength") or {}).get("rs_vs_spy_20d_pct")
                ),
                "returns_pct": {
                    "1d": _safe_change_pct(peer_history, 1),
                    "5d": _safe_change_pct(peer_history, 5),
                    "20d": _safe_change_pct(peer_history, 20),
                },
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
    horizon_end = datetime.utcnow() + pd.Timedelta(days=max(1, horizon_days))

    def _within_horizon(event_time: object) -> bool:
        dt = _parse_event_datetime(event_time)
        if dt is None:
            return False
        return datetime.utcnow() <= dt <= horizon_end

    company_events = [
        event for event in company_events_map.get(ticker, []) if _within_horizon(event.get("event_time"))
    ][: int(config.EVENTS["max_company_events"])]

    peer_events = []
    for peer in sector_context.get("peers") or []:
        peer_ticker = peer.get("ticker")
        for event in company_events_map.get(peer_ticker, []):
            if _within_horizon(event.get("event_time")):
                peer_events.append(event)
    peer_events.sort(key=lambda item: item.get("event_time", ""))
    peer_events = peer_events[: int(config.EVENTS["max_peer_events"])]

    normalized_macro = []
    for event in macro_events:
        candidate_time = (
            event.get("date")
            or event.get("time")
            or event.get("datetime")
            or event.get("eventTime")
            or event.get("timestamp")
        )
        if candidate_time and not _within_horizon(candidate_time):
            continue
        normalized_macro.append(dict(event or {}))
    normalized_macro = normalized_macro[: int(config.EVENTS["max_macro_events"])]

    return {
        "window_days": horizon_days,
        "macro_events": normalized_macro,
        "company_events": company_events,
        "peer_events": peer_events,
    }


def _build_generator_map(
    features: list[StockFeatures],
    news_by_ticker: dict[str, list[dict]],
    company_events_map: dict[str, list[dict]],
) -> dict[str, dict]:
    cfg = config.PIPELINE_CONFIG["candidate_generators"]
    premarket_cfg = cfg["premarket_dislocation"]
    price_action_cfg = cfg["price_action"]
    sector_rotation_cfg = cfg["sector_rotation"]
    news_cfg = cfg["news_attention"]
    event_cfg = cfg["event"]
    now_utc = datetime.utcnow()
    event_horizon = now_utc + pd.Timedelta(days=max(1, int(event_cfg["near_term_days"])))
    generator_map: dict[str, dict] = {}

    for feature in features:
        ticker = feature.ticker
        tech = feature.technical or {}
        premarket = feature.premarket or {}
        news = feature.news or {}
        semantics = news.get("semantic_rollup") or {}
        volume_ratio = _safe_float(((tech.get("volume_price") or {}).get("volume_ratio")))
        rel_strength = _safe_float(((tech.get("relative_strength") or {}).get("rs_vs_spy_20d_pct")))
        gap_pct = _safe_float(premarket.get("premarket_change_pct"))
        breakout_signal = bool((tech.get("patterns") or {}).get("breakout_signal"))
        peer_count = int((feature.sector_context or {}).get("peer_count_total") or 0)
        article_count = int(news.get("article_count") or 0)
        weighted_sentiment = _safe_float(semantics.get("weighted_sentiment_score"))
        company_events = []
        for event in company_events_map.get(ticker, []):
            event_dt = _parse_event_datetime(event.get("event_time"))
            if event_dt is None:
                continue
            if now_utc <= event_dt <= event_horizon:
                company_events.append(event)
        articles = news_by_ticker.get(ticker, [])
        recent_news_ts = max((_safe_int(article.get("datetime")) or 0 for article in articles), default=0)

        generators = {
            "tradability_generator": {
                "triggered": True,
                "participates_in_recall": False,
                "strength": float((((feature.execution_context or {}).get("dollar_volume") or {}).get("mean")) or 0.0),
                "trigger_features": {
                    "overall_coverage": (feature.data_quality or {}).get("overall_coverage"),
                    "avg_dollar_volume_bucket": feature.execution_context.get("dollar_volume", {}).get("mean"),
                },
            },
            "premarket_dislocation_generator": {
                "triggered": abs(gap_pct or 0.0) >= premarket_cfg["moderate_gap_pct"],
                "participates_in_recall": True,
                "strength": abs(gap_pct or 0.0),
                "trigger_features": {
                    "premarket_change_pct": gap_pct,
                    "has_premarket": bool(premarket.get("has_premarket")),
                },
            },
            "news_attention_generator": {
                "triggered": article_count >= news_cfg["min_articles"] or recent_news_ts > 0,
                "participates_in_recall": True,
                "strength": float(article_count) + abs(weighted_sentiment or 0.0) / 50.0,
                "trigger_features": {
                    "article_count": article_count,
                    "weighted_sentiment_score": weighted_sentiment,
                    "latest_news_timestamp": recent_news_ts,
                },
            },
            "event_generator": {
                "triggered": bool(company_events),
                "participates_in_recall": True,
                "strength": float(len(company_events)),
                "trigger_features": {
                    "company_event_count": len(company_events),
                    "company_events": company_events[:5],
                },
            },
            "price_action_generator": {
                "triggered": bool(breakout_signal) or (volume_ratio or 0.0) >= price_action_cfg["min_volume_ratio"] or abs(rel_strength or 0.0) >= price_action_cfg["min_relative_strength_pct"],
                "participates_in_recall": True,
                "strength": (abs(rel_strength or 0.0) + max(volume_ratio or 0.0, 0.0) + (price_action_cfg["breakout_bonus"] if breakout_signal else 0.0)),
                "trigger_features": {
                    "breakout_signal": breakout_signal,
                    "volume_ratio": volume_ratio,
                    "relative_strength_vs_spy_20d_pct": rel_strength,
                },
            },
            "sector_rotation_generator": {
                "triggered": peer_count >= sector_rotation_cfg["min_peer_count"] and abs(rel_strength or 0.0) >= sector_rotation_cfg["min_relative_strength_pct"],
                "participates_in_recall": True,
                "strength": abs(rel_strength or 0.0),
                "trigger_features": {
                    "sector": feature.sector,
                    "peer_count": peer_count,
                    "relative_strength_vs_spy_20d_pct": rel_strength,
                },
            },
        }

        risk_flags = []
        if (feature.data_quality or {}).get("overall_coverage", 0) < 0.5:
            risk_flags.append("low_data_coverage")
        if feature.premarket and not feature.premarket.get("has_premarket"):
            risk_flags.append("no_premarket_print")
        if not articles:
            risk_flags.append("no_recent_company_news")

        generator_map[ticker] = {
            "by_name": generators,
            "triggered_generators": [
                name for name, payload in generators.items() if payload["triggered"] and payload["participates_in_recall"]
            ],
            "risk_flags": risk_flags,
        }

    for generator_name in [
        "premarket_dislocation_generator",
        "news_attention_generator",
        "event_generator",
        "price_action_generator",
        "sector_rotation_generator",
    ]:
        ranked = sorted(
            [
                (ticker, payload["by_name"][generator_name]["strength"])
                for ticker, payload in generator_map.items()
                if payload["by_name"][generator_name]["triggered"]
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        for rank, (ticker, _) in enumerate(ranked, start=1):
            generator_map[ticker]["by_name"][generator_name]["generator_rank"] = rank

    for payload in generator_map.values():
        triggered_count = len(payload["triggered_generators"])
        recall_score = 0.0
        for generator_name in payload["triggered_generators"]:
            recall_score += float(payload["by_name"][generator_name]["strength"])
        payload["triggered_count"] = triggered_count
        payload["recall_score"] = round(recall_score, 3)
        payload["recall_candidate"] = triggered_count > 0

    return generator_map


def _write_trace(artifact_store: ArtifactStore, ticker: str, trace: SymbolTrace) -> str:
    return artifact_store.write_json(f"symbols/{ticker}/trace.json", trace.to_dict())


def _selected_features_by_ticker(features: list[StockFeatures]) -> dict[str, StockFeatures]:
    return {feature.ticker: feature for feature in features}


def _save_legacy_outputs(output_dir: str, selected_features: list[StockFeatures], report: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    with open(os.path.join(output_dir, f"pool_{date_str}.txt"), "w", encoding="utf-8") as handle:
        handle.write(report)
    if not selected_features:
        return

    rows = []
    for feature in selected_features:
        rows.append(
            {
                "ticker": feature.ticker,
                "company_name": feature.company_name,
                "sector": feature.sector,
                "technical_priority": feature.technical_priority,
                "overall_coverage": (feature.data_quality or {}).get("overall_coverage"),
                "premarket_change_pct": (feature.premarket or {}).get("premarket_change_pct"),
                "article_count": (feature.news or {}).get("article_count"),
            }
        )
    pd.DataFrame(rows).to_csv(os.path.join(output_dir, f"pool_{date_str}.csv"), index=False)


def run_screening(tickers_override: list[str] | None = None, dry_run: bool = False) -> dict:
    start_time = time.time()
    _configure_yfinance_cache()
    session = create_session_context()
    session_dict = session.to_dict()
    artifact_root = config.PIPELINE_CONFIG["artifact_root_dir"]
    artifact_store = ArtifactStore(artifact_root, session.session_id)

    logger.info("Session %s | %s", session.session_id, session.session_type)
    artifact_store.write_json("session_context.json", session_dict)

    registry = SymbolRegistry()
    registry_records = registry.resolve(tickers_override=tickers_override)
    if not registry_records:
        raise RuntimeError("No symbols available from registry")

    tickers = [record["ticker"] for record in registry_records]
    traces = {
        ticker: SymbolTrace(
            session_id=session.session_id,
            ticker=ticker,
            as_of=session.run_at_market_tz,
        )
        for ticker in tickers
    }

    registry_snapshot_ref = artifact_store.write_json("universe/registry_snapshot.json", {"symbols": registry_records})
    for ticker in tickers:
        traces[ticker].add_stage(
            stage="symbol_registry",
            gate_type="system_gate",
            decision="pass",
            reason_codes=["registry_loaded"],
            artifacts={"input_ref": registry_snapshot_ref},
            as_of=session.run_at_market_tz,
        )

    market_collector = MarketDataCollector()
    news_collector = NewsCollector(cache=market_collector.cache)
    sentiment_analyzer = NewsSentimentAnalyzer()
    semantic_tagger = SemanticNewsTagger(cache=news_collector.cache)
    llm_runner = StructuredLLMRunner()
    assembler = FeatureAssembler()

    spy_data = _download_symbol_history("SPY")
    history_data = market_collector.fetch_batch_history(tickers)
    ticker_infos = market_collector.fetch_ticker_info(list(history_data.keys()))

    dollar_volume_map = {ticker: _avg_dollar_volume(history_data.get(ticker)) for ticker in tickers}
    enriched_registry_records = registry.enrich(registry_records, ticker_infos, dollar_volume_map)
    registry_records_by_ticker = {record["ticker"]: record for record in enriched_registry_records}
    artifact_store.write_json("universe/registry_snapshot_enriched.json", {"symbols": enriched_registry_records})

    eligible_tickers = []
    eligibility_details = {}
    for ticker in tickers:
        history_ref = artifact_store.write_json(
            f"symbols/{ticker}/raw/history.json",
            _history_payload(history_data.get(ticker)),
        )
        info_ref = artifact_store.write_json(
            f"symbols/{ticker}/raw/ticker_info.json",
            ticker_infos.get(ticker, {}),
        )
        eligibility = _evaluate_eligibility(
            registry_record=registry_records_by_ticker.get(ticker, {}),
            history_df=history_data.get(ticker),
            ticker_info=ticker_infos.get(ticker),
        )
        eligibility_details[ticker] = eligibility
        traces[ticker].add_stage(
            stage="universe_eligibility",
            gate_type="system_gate",
            decision=eligibility["decision"],
            reason_codes=eligibility["reason_codes"],
            artifacts={
                "input_ref": history_ref,
                "summary_ref": info_ref,
            },
            as_of=session.run_at_market_tz,
        )
        if eligibility["decision"] == "pass":
            eligible_tickers.append(ticker)

    if not eligible_tickers:
        raise RuntimeError("No symbols passed tradability/data-quality eligibility")

    news_budget = int(config.BUDGET_CONFIG["max_symbols_for_news"])
    news_tickers = eligible_tickers[:news_budget]
    skipped_for_news = set(eligible_tickers[news_budget:])
    for ticker in skipped_for_news:
        traces[ticker].add_stage(
            stage="news_budget_gate",
            gate_type="system_gate",
            decision="skip",
            reason_codes=["budget_control"],
            as_of=session.run_at_market_tz,
        )

    premarket_data = market_collector.get_premarket_data({ticker: ticker_infos.get(ticker, {}) for ticker in eligible_tickers})
    news_by_ticker = news_collector.fetch_news_batch(news_tickers)
    provider_news_sentiments = news_collector.fetch_news_sentiment_batch(news_tickers)
    semantic_by_ticker = {}
    for ticker in news_tickers:
        semantic_by_ticker[ticker] = semantic_tagger.tag_articles(
            ticker=ticker,
            articles=news_by_ticker.get(ticker, []),
        )
        artifact_store.write_json(f"symbols/{ticker}/raw/news.json", news_by_ticker.get(ticker, []))
        artifact_store.write_json(f"symbols/{ticker}/raw/news_sentiment.json", provider_news_sentiments.get(ticker, {}))
    for ticker in skipped_for_news:
        artifact_store.write_json(f"symbols/{ticker}/raw/news.json", [])
        artifact_store.write_json(f"symbols/{ticker}/raw/news_sentiment.json", {})

    market_news_bundle = news_collector.fetch_market_news_bundle()
    macro_events = news_collector.fetch_economic_calendar(days_ahead=int(config.EVENTS["future_days"]))

    technical_analyzer = TechnicalAnalyzer(spy_data=spy_data)
    technical_results: dict[str, TechnicalProfile] = {}
    for ticker in eligible_tickers:
        history_df = history_data.get(ticker)
        if history_df is None:
            continue
        profile = technical_analyzer.analyze(ticker, history_df)
        if profile is not None:
            technical_results[ticker] = profile

    features: list[StockFeatures] = []
    company_events_map = {
        ticker: _extract_company_event_list(ticker=ticker, ticker_info=ticker_infos.get(ticker))
        for ticker in eligible_tickers
    }
    spy_history_meta = {
        "source": "yfinance",
        "symbol": "SPY",
        "data_type": "history",
        "as_of": _frame_as_of_iso(_normalize_history_frame(spy_data)),
        "fetched_at": datetime.utcnow().isoformat(),
        "retrieval_mode": "network_refresh",
    } if spy_data is not None else {}

    for ticker in eligible_tickers:
        profile = technical_results.get(ticker)
        if profile is None:
            traces[ticker].add_stage(
                stage="technical_analysis",
                gate_type="system_gate",
                decision="reject",
                reason_codes=["technical_profile_unavailable"],
                as_of=session.run_at_market_tz,
            )
            continue
        feature_source_meta = {
            "history": market_collector.get_history_meta(ticker),
            "ticker_info": market_collector.get_ticker_info_meta(ticker),
            "company_news": news_collector.get_company_news_meta(ticker),
            "news_sentiment": news_collector.get_news_sentiment_meta(ticker),
            "news_semantic": semantic_tagger.get_semantic_meta(ticker),
            "spy_history": spy_history_meta,
        }
        feature = assembler.build(
            technical=profile,
            premarket_data=premarket_data.get(ticker),
            news_data=sentiment_analyzer.analyze_articles(news_by_ticker.get(ticker, [])),
            news_sentiment_data=provider_news_sentiments.get(ticker),
            news_semantic_data=semantic_by_ticker.get(ticker),
            news_articles=news_by_ticker.get(ticker, []),
            ticker_info=ticker_infos.get(ticker),
            source_meta=feature_source_meta,
            history_df=history_data.get(ticker),
            spy_history_df=spy_data,
        )
        features.append(feature)

    if not features:
        raise RuntimeError("No symbols produced feature packets")

    market_regime, market_regime_meta = get_market_regime(spy_data=spy_data)
    volatility_group = (market_regime.get("symbol_groups") or {}).get("volatility", {})
    vix_symbol = next(iter(volatility_group.keys()), "^VIX")
    vix_snapshot = volatility_group.get(vix_symbol, {})
    market_summary = get_market_summary(spy_data, vix_override=vix_snapshot.get("close"), vix_as_of_override=vix_snapshot.get("as_of", ""))
    market_context = build_market_context(
        market_summary=market_summary,
        market_news_bundle=market_news_bundle,
        macro_events=macro_events,
        market_regime=market_regime,
        market_provenance={
            "market_summary": build_market_summary_provenance(spy_data=spy_data, vix_as_of=vix_snapshot.get("as_of", ""), vix_symbol=vix_symbol),
            "market_regime": market_regime_meta,
            "market_news": news_collector.get_market_news_meta_bundle(),
            "upcoming_macro_events": news_collector.get_economic_calendar_meta(),
        },
    )
    market_context_raw_ref = artifact_store.write_json("market/market_context_raw.json", market_context)
    market_context_compact = build_market_context_compact(market_context, features)
    market_context_compact_ref = artifact_store.write_json("market/market_context_compact.json", market_context_compact)
    artifact_store.write_json("market/regime_summary.json", market_regime)

    for feature in features:
        feature.sector_context = _build_sector_context(feature=feature, all_features=features, history_data=history_data)

    generator_map = _build_generator_map(features, news_by_ticker, company_events_map)
    candidate_tickers = []
    for feature in features:
        ticker = feature.ticker
        generators = generator_map[ticker]
        generators_ref = artifact_store.write_json(f"symbols/{ticker}/derived/generators.json", generators)
        decision = "pass" if generators.get("recall_candidate") else "reject"
        traces[ticker].add_stage(
            stage="candidate_generators",
            gate_type="system_gate",
            decision=decision,
            reason_codes=generators.get("triggered_generators") or ["no_generator_trigger"],
            artifacts={"summary_ref": generators_ref},
            as_of=session.run_at_market_tz,
        )
        if decision == "pass":
            candidate_tickers.append(ticker)

    ranked_candidates = sorted(
        candidate_tickers,
        key=lambda ticker: (
            int(generator_map[ticker]["triggered_count"]),
            float(generator_map[ticker]["recall_score"]),
            float((next(feature for feature in features if feature.ticker == ticker).data_quality or {}).get("overall_coverage", 0)),
        ),
        reverse=True,
    )

    triage_budget = int(config.BUDGET_CONFIG["max_symbols_for_triage"])
    triage_tickers = ranked_candidates[:triage_budget]
    skipped_triage_budget = set(ranked_candidates[triage_budget:])
    for ticker in skipped_triage_budget:
        traces[ticker].add_stage(
            stage="compact_triage_budget",
            gate_type="system_gate",
            decision="reject",
            reason_codes=["budget_control"],
            as_of=session.run_at_market_tz,
        )

    feature_by_ticker = _selected_features_by_ticker(features)
    candidate_set_ref = artifact_store.write_json(
        "judge/candidate_set.json",
        {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["candidate_set"],
            "generated_at": datetime.utcnow().isoformat(),
            "session_id": session.session_id,
            "candidate_tickers": triage_tickers,
            "dropped_by_budget": sorted(skipped_triage_budget),
            "generator_breakdown": {
                ticker: generator_map[ticker] for ticker in triage_tickers
            },
        },
    )

    triage_outputs = {}
    compact_cards = {}
    for ticker in triage_tickers:
        feature = feature_by_ticker[ticker]
        compact_card = build_compact_card(
            feature=feature,
            registry_record=registry_records_by_ticker.get(ticker, {}),
            generator_summary=generator_map[ticker],
            news_articles=news_by_ticker.get(ticker, []),
            company_events=company_events_map.get(ticker, []),
            session_context=session_dict,
        )
        compact_cards[ticker] = compact_card
        compact_ref = artifact_store.write_json(f"symbols/{ticker}/derived/compact_card.json", compact_card)
        request_payload, triage_output = llm_runner.triage(
            market_context_compact=market_context_compact,
            compact_card=compact_card,
        )
        artifact_store.write_json(f"symbols/{ticker}/llm/triage_request.json", request_payload)
        triage_ref = artifact_store.write_json(f"symbols/{ticker}/llm/triage.json", triage_output)
        triage_outputs[ticker] = triage_output
        verdict = str(triage_output.get("triage_verdict") or "reject").lower()
        traces[ticker].add_stage(
            stage="compact_triage_llm",
            gate_type="llm_judgment",
            decision="pass" if verdict in {"keep", "observe"} else "reject",
            reason_codes=list(triage_output.get("why_keep") or triage_output.get("why_reject") or ["no_reason"]),
            artifacts={"input_ref": compact_ref, "summary_ref": candidate_set_ref},
            llm_output_ref=triage_ref,
            as_of=session.run_at_market_tz,
        )

    deep_candidates = [
        ticker for ticker, payload in triage_outputs.items()
        if str(payload.get("triage_verdict") or "").lower() in {"keep", "observe"}
    ]
    deep_candidates.sort(
        key=lambda ticker: (
            str(triage_outputs[ticker].get("triage_verdict")) == "keep",
            float(triage_outputs[ticker].get("triage_confidence") or 0),
            float(generator_map[ticker]["recall_score"]),
        ),
        reverse=True,
    )

    deep_budget = int(config.BUDGET_CONFIG["max_symbols_for_deep_analysis"])
    deep_tickers = deep_candidates[:deep_budget]
    deep_budget_skipped = set(deep_candidates[deep_budget:])
    for ticker in deep_budget_skipped:
        traces[ticker].add_stage(
            stage="deep_analysis_budget",
            gate_type="system_gate",
            decision="reject",
            reason_codes=["budget_control"],
            as_of=session.run_at_market_tz,
        )

    options_collector = OptionsCollector(cache=market_collector.cache)
    options_by_ticker = options_collector.fetch_options_batch(
        tickers=deep_tickers,
        spot_prices={ticker: getattr(technical_results.get(ticker), "last_close", None) for ticker in deep_tickers},
    )

    deep_outputs = {}
    deep_artifacts_by_ticker = {}
    enriched_features: list[StockFeatures] = []
    for ticker in deep_tickers:
        feature = feature_by_ticker[ticker]
        sector_context = feature.sector_context
        upcoming_events = _build_upcoming_events(
            ticker=ticker,
            company_events_map=company_events_map,
            sector_context=sector_context,
            macro_events=macro_events,
        )
        rebuilt = assembler.build(
            technical=technical_results[ticker],
            premarket_data=premarket_data.get(ticker),
            news_data=sentiment_analyzer.analyze_articles(news_by_ticker.get(ticker, [])),
            news_sentiment_data=provider_news_sentiments.get(ticker),
            news_semantic_data=semantic_by_ticker.get(ticker),
            news_articles=news_by_ticker.get(ticker, []),
            ticker_info=ticker_infos.get(ticker),
            options_summary=options_by_ticker.get(ticker),
            sector_context=sector_context,
            upcoming_events=upcoming_events,
            source_meta={
                "history": market_collector.get_history_meta(ticker),
                "ticker_info": market_collector.get_ticker_info_meta(ticker),
                "company_news": news_collector.get_company_news_meta(ticker),
                "news_sentiment": news_collector.get_news_sentiment_meta(ticker),
                "news_semantic": semantic_tagger.get_semantic_meta(ticker),
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
            },
            history_df=history_data.get(ticker),
            spy_history_df=spy_data,
        )
        enriched_features.append(rebuilt)
        raw_events_ref = artifact_store.write_json(f"symbols/{ticker}/raw/events.json", upcoming_events)
        raw_options_ref = artifact_store.write_json(f"symbols/{ticker}/raw/options.json", options_by_ticker.get(ticker, {}))
        full_dossier = build_full_dossier(
            feature=rebuilt,
            compact_card=compact_cards[ticker],
            generator_summary=generator_map[ticker],
            raw_evidence={
                "history": _history_payload(history_data.get(ticker)),
                "ticker_info": ticker_infos.get(ticker, {}),
                "news": news_by_ticker.get(ticker, []),
                "news_sentiment": provider_news_sentiments.get(ticker, {}),
                "news_semantic": semantic_by_ticker.get(ticker, {}),
                "options": options_by_ticker.get(ticker, {}),
                "events": upcoming_events,
            },
            triage_output=triage_outputs[ticker],
            session_context=session_dict,
        )
        dossier_ref = artifact_store.write_json(f"symbols/{ticker}/derived/full_dossier.json", full_dossier)
        request_payload, deep_output = llm_runner.deep_analysis(
            market_context=market_context,
            full_dossier=full_dossier,
        )
        artifact_store.write_json(f"symbols/{ticker}/llm/deep_analysis_request.json", request_payload)
        deep_ref = artifact_store.write_json(f"symbols/{ticker}/llm/deep_analysis.json", deep_output)
        deep_outputs[ticker] = deep_output
        deep_artifacts_by_ticker[ticker] = {
            "dossier_ref": dossier_ref,
            "deep_ref": deep_ref,
            "events_ref": raw_events_ref,
            "options_ref": raw_options_ref,
        }
        traces[ticker].add_stage(
            stage="deep_analysis_llm",
            gate_type="llm_judgment",
            decision="pass",
            reason_codes=list(deep_output.get("bull_case") or ["deep_analysis_complete"]),
            artifacts={
                "input_ref": dossier_ref,
                "summary_ref": raw_events_ref,
            },
            llm_output_ref=deep_ref,
            as_of=session.run_at_market_tz,
        )

    final_selection_request, final_selection = llm_runner.final_judge(
        market_context_compact=market_context_compact,
        deep_analysis_by_ticker=deep_outputs,
        selection_count=int(config.BUDGET_CONFIG["final_selection_count"]),
    )
    artifact_store.write_json("judge/final_selection_request.json", final_selection_request)
    final_selection_ref = artifact_store.write_json("judge/final_selection.json", final_selection)

    selected_tickers = list(final_selection.get("final_top_n") or [])
    ranked_candidates = final_selection.get("ranked_candidates") or []
    decision_by_ticker = {item.get("ticker"): item for item in ranked_candidates if item.get("ticker")}
    for ticker in triage_tickers:
        if ticker not in deep_outputs:
            traces[ticker].final_status = "triage_rejected"
            continue
        selected = ticker in selected_tickers
        judge_item = decision_by_ticker.get(ticker, {})
        traces[ticker].add_stage(
            stage="cross_stock_judge",
            gate_type="llm_judgment",
            decision="selected" if selected else "rejected",
            reason_codes=[judge_item.get("selection_reason") or judge_item.get("rejection_reason") or "not_selected"],
            artifacts={"summary_ref": final_selection_ref},
            llm_output_ref=final_selection_ref,
            as_of=session.run_at_market_tz,
        )
        traces[ticker].final_status = "selected" if selected else "judge_rejected"

    for ticker in tickers:
        if traces[ticker].final_status == "pending":
            last_decision = traces[ticker].stages[-1].decision if traces[ticker].stages else "pending"
            if last_decision == "reject":
                traces[ticker].final_status = "system_rejected"
            else:
                traces[ticker].final_status = "observed"
        _write_trace(artifact_store, ticker, traces[ticker])

    selected_features_lookup = {feature.ticker: feature for feature in enriched_features if feature.ticker in selected_tickers}
    selected_features = [selected_features_lookup[ticker] for ticker in selected_tickers if ticker in selected_features_lookup]
    if not selected_features:
        selected_features = enriched_features[: int(config.BUDGET_CONFIG["final_selection_count"])]

    report = format_console_report(selected_features, market_summary)
    print("\n" + report)
    _save_legacy_outputs(config.SYSTEM["output_dir"], selected_features, report)

    if not dry_run and selected_features:
        notifier = DiscordNotifier()
        notifier.send_stock_pool(selected_features, market_summary)
    else:
        logger.info("Skipping Discord push (dry-run or no selected features)")

    outcome_seed = build_outcome_seed(
        session_context=session_dict,
        triage_outputs=triage_outputs,
        deep_outputs=deep_outputs,
        final_selection=final_selection,
    )
    artifact_store.write_json("evaluation/outcome_store.json", outcome_seed)
    artifact_store.write_json(
        "evaluation/placeholder.json",
        {
            "note": "Forward-return backfill is not available at run time. This file seeds future evaluation.",
            "session_id": session.session_id,
        },
    )

    run_manifest = {
        "schema_version": config.PIPELINE_CONFIG["schema_versions"]["run_manifest"],
        "generated_at": datetime.utcnow().isoformat(),
        "session_id": session.session_id,
        "market_date": session.market_date,
        "session_type": session.session_type,
        "input_ticker_count": len(tickers),
        "eligible_ticker_count": len(eligible_tickers),
        "feature_ticker_count": len(features),
        "triage_ticker_count": len(triage_tickers),
        "deep_analysis_ticker_count": len(deep_tickers),
        "selected_ticker_count": len(selected_tickers),
        "artifacts": {
            "session_context": "session_context.json",
            "registry_snapshot": registry_snapshot_ref,
            "market_context_raw": market_context_raw_ref,
            "market_context_compact": market_context_compact_ref,
            "candidate_set": candidate_set_ref,
            "final_selection": final_selection_ref,
            "outcome_store": "evaluation/outcome_store.json",
        },
        "cache_stats": {
            "market_collector": market_collector.cache_stats,
            "news_collector": news_collector.cache_stats,
            "semantic_tagger": semantic_tagger.cache_stats,
            "options_collector": options_collector.cache_stats,
        },
        "runtime_seconds": round(time.time() - start_time, 2),
    }
    artifact_store.write_json("run_manifest.json", run_manifest)
    logger.info("Completed session %s in %.1fs", session.session_id, time.time() - start_time)
    return run_manifest
