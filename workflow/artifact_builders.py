from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from runtime import config
from analysis.scorer import StockFeatures


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_news_ts(article: dict) -> int:
    try:
        return int(article.get("datetime") or 0)
    except Exception:
        return 0


def build_market_context_compact(market_context: dict, features: list[StockFeatures]) -> dict:
    total = len(features)
    advancers = 0
    above_ma20 = 0
    above_ma50 = 0
    strong_gappers = 0
    sector_scores: dict[str, list[float]] = {}

    for feature in features:
        tech = feature.technical or {}
        price = tech.get("price", {})
        moving_averages = tech.get("moving_averages", {})
        relative_strength = tech.get("relative_strength", {})
        premarket = feature.premarket or {}

        daily_change = _safe_float(tech.get("daily_change_pct"))
        last_close = _safe_float(price.get("last_close"))
        ma20 = _safe_float(moving_averages.get("ma20"))
        ma50 = _safe_float(moving_averages.get("ma50"))
        rs = _safe_float(relative_strength.get("rs_vs_spy_20d_pct")) or 0.0

        if daily_change is not None and daily_change > 0:
            advancers += 1
        if last_close is not None and ma20 is not None and last_close > ma20:
            above_ma20 += 1
        if last_close is not None and ma50 is not None and last_close > ma50:
            above_ma50 += 1
        if abs(_safe_float(premarket.get("premarket_change_pct")) or 0.0) >= config.PREMARKET["gap_moderate_threshold"]:
            strong_gappers += 1

        sector = feature.sector or "Unknown"
        sector_scores.setdefault(sector, []).append(rs)

    sector_leadership = []
    for sector, values in sector_scores.items():
        if not values:
            continue
        sector_leadership.append(
            {
                "sector": sector,
                "average_relative_strength_pct": round(sum(values) / len(values), 3),
                "sample_size": len(values),
            }
        )
    sector_leadership.sort(key=lambda item: item["average_relative_strength_pct"], reverse=True)

    return {
        "schema_version": "v1.market_context_compact",
        "generated_at": datetime.utcnow().isoformat(),
        "market_summary": dict(market_context.get("market_summary") or {}),
        "risk_calendar": list((market_context.get("upcoming_macro_events") or [])[:15]),
        "market_regime_compact": {
            "all_symbols": list((market_context.get("market_regime") or {}).get("all_symbols") or []),
            "symbol_groups": dict((market_context.get("market_regime") or {}).get("symbol_groups") or {}),
        },
        "breadth": {
            "universe_sample_size": total,
            "advancers_ratio": round(advancers / total, 3) if total else None,
            "above_ma20_ratio": round(above_ma20 / total, 3) if total else None,
            "above_ma50_ratio": round(above_ma50 / total, 3) if total else None,
            "moderate_or_higher_gap_ratio": round(strong_gappers / total, 3) if total else None,
        },
        "leadership": {
            "top_sectors": sector_leadership[:5],
            "bottom_sectors": list(reversed(sector_leadership[-5:])),
        },
        "data_provenance": dict(market_context.get("data_provenance") or {}),
    }


def build_compact_card(
    feature: StockFeatures,
    registry_record: dict,
    generator_summary: dict,
    news_articles: list[dict],
    company_events: list[dict],
    session_context: dict,
) -> dict:
    latest_news = sorted(news_articles, key=_parse_news_ts, reverse=True)[:5]
    technical = feature.technical or {}
    price = technical.get("price", {})
    moving_averages = technical.get("moving_averages", {})
    momentum = technical.get("momentum", {})
    volatility = technical.get("volatility", {})
    relative_strength = technical.get("relative_strength", {})
    premarket = feature.premarket or {}
    news = feature.news or {}
    execution = feature.execution_context or {}
    dollar_volume = (execution.get("dollar_volume") or {}).get("mean")
    support_resistance = feature.support_resistance or {}

    return {
        "schema_version": config.PIPELINE_CONFIG["schema_versions"]["compact_card"],
        "generated_at": datetime.utcnow().isoformat(),
        "session": {
            "session_id": session_context["session_id"],
            "market_date": session_context["market_date"],
            "session_type": session_context["session_type"],
        },
        "ticker": feature.ticker,
        "identity": {
            "company_name": feature.company_name,
            "sector": feature.sector,
            "industry": feature.industry,
            "exchange": feature.exchange,
            "country": feature.country,
            "currency": feature.currency,
        },
        "registry": dict(registry_record),
        "raw": {
            "latest_news": [
                {
                    "datetime": article.get("datetime"),
                    "headline": article.get("headline", ""),
                    "source": article.get("source", ""),
                    "url": article.get("url", ""),
                }
                for article in latest_news
            ],
            "upcoming_company_events": list(company_events[:5]),
        },
        "derived": {
            "liquidity": {
                "avg_dollar_volume_20d": _safe_float(dollar_volume),
                "avg_volume_20d": _safe_float((feature.liquidity or {}).get("average_volume_10d")),
            },
            "premarket": {
                "has_premarket": bool(premarket.get("has_premarket")),
                "premarket_change_pct": _safe_float(premarket.get("premarket_change_pct")),
                "regular_change_pct": _safe_float(premarket.get("regular_change_pct")),
            },
            "price_action": {
                "last_close": _safe_float(price.get("last_close")),
                "ma20": _safe_float(moving_averages.get("ma20")),
                "ma50": _safe_float(moving_averages.get("ma50")),
                "rsi": _safe_float(momentum.get("rsi")),
                "atr_pct": _safe_float(volatility.get("atr_pct")),
                "relative_strength_vs_spy_20d_pct": _safe_float(relative_strength.get("rs_vs_spy_20d_pct")),
                "breakout_signal": bool((technical.get("patterns") or {}).get("breakout_signal")),
                "volume_ratio": _safe_float((technical.get("volume_price") or {}).get("volume_ratio")),
                "nearest_support": _safe_float(support_resistance.get("nearest_support")),
                "nearest_resistance": _safe_float(support_resistance.get("nearest_resistance")),
            },
            "attention": {
                "news_article_count": int(news.get("article_count") or 0),
                "provider_sentiment_weighted_score": _safe_float(
                    ((news.get("semantic_rollup") or {}).get("weighted_sentiment_score"))
                ),
                "latest_news_timestamp": latest_news[0].get("datetime") if latest_news else None,
                "company_event_count": len(company_events),
            },
            "generator_summary": dict(generator_summary),
        },
        "compact_summary": {
            "why_this_symbol_recalled": list(generator_summary.get("triggered_generators") or []),
            "main_risk_flags": list(generator_summary.get("risk_flags") or []),
            "coverage": feature.data_quality,
            "compression": {
                "raw_news_count": len(news_articles),
                "attached_news_count": len(latest_news),
                "truncated_news_count": max(0, len(news_articles) - len(latest_news)),
            },
        },
        "data_provenance": dict(feature.data_provenance or {}),
    }


def build_full_dossier(
    feature: StockFeatures,
    compact_card: dict,
    generator_summary: dict,
    raw_evidence: dict,
    triage_output: dict,
    session_context: dict,
) -> dict:
    return {
        "schema_version": config.PIPELINE_CONFIG["schema_versions"]["full_dossier"],
        "generated_at": datetime.utcnow().isoformat(),
        "session": {
            "session_id": session_context["session_id"],
            "market_date": session_context["market_date"],
            "session_type": session_context["session_type"],
        },
        "ticker": feature.ticker,
        "identity": compact_card.get("identity") or {},
        "raw": dict(raw_evidence),
        "derived": {
            "stock_context": asdict(feature),
            "generator_summary": dict(generator_summary),
        },
        "compact_summary": compact_card.get("compact_summary") or {},
        "prior_judgment": {
            "triage": dict(triage_output or {}),
        },
        "data_quality": dict(feature.data_quality or {}),
        "data_provenance": dict(feature.data_provenance or {}),
    }


def build_outcome_seed(
    *,
    session_context: dict,
    triage_outputs: dict[str, dict],
    deep_outputs: dict[str, dict],
    final_selection: dict,
) -> dict:
    final_top_n = set(final_selection.get("final_top_n") or [])
    entries = []
    deep_by_ticker = {ticker: payload for ticker, payload in deep_outputs.items()}

    for ticker, triage in triage_outputs.items():
        deep = deep_by_ticker.get(ticker, {})
        entries.append(
            {
                "ticker": ticker,
                "session_id": session_context["session_id"],
                "market_date": session_context["market_date"],
                "triage_verdict": triage.get("triage_verdict"),
                "triage_confidence": triage.get("triage_confidence"),
                "deep_confidence": deep.get("confidence"),
                "selected_final": ticker in final_top_n,
                "forward_return_1d": None,
                "forward_return_3d": None,
                "forward_return_5d": None,
                "forward_return_10d": None,
                "max_drawdown_10d": None,
                "max_drawup_10d": None,
                "hit_trigger": None,
                "hit_invalidation": None,
                "realized_volatility": None,
            }
        )

    return {
        "schema_version": config.PIPELINE_CONFIG["schema_versions"]["outcome_store"],
        "generated_at": datetime.utcnow().isoformat(),
        "session_id": session_context["session_id"],
        "market_date": session_context["market_date"],
        "entries": entries,
    }
