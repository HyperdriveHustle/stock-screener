"""
新闻语义打标模块
- Provider 抽象（首版接 MiniMax）
- 单条新闻情绪/影响打标
- 缓存与 provenance 追踪
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import requests

from runtime import config
from runtime.utils import first_json_object as _first_json_object
from runtime.utils import safe_float as _safe_float
from providers.cache_store import SQLiteCache

logger = logging.getLogger(__name__)

_SENTIMENT_MAP = {
    "bull": "bullish",
    "bullish": "bullish",
    "positive": "bullish",
    "up": "bullish",
    "bear": "bearish",
    "bearish": "bearish",
    "negative": "bearish",
    "down": "bearish",
    "neutral": "neutral",
    "mixed": "neutral",
}

_IMPACT_MAP = {
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "mid": "medium",
    "low": "low",
}
def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    parsed = _safe_float(v)
    if parsed is None:
        return default
    return float(max(lo, min(hi, parsed)))


def _normalize_sentiment(v: Any) -> str:
    key = str(v or "").strip().lower()
    return _SENTIMENT_MAP.get(key, "neutral")


def _normalize_impact(v: Any) -> str:
    key = str(v or "").strip().lower()
    return _IMPACT_MAP.get(key, "medium")


def _normalize_semantic_payload(payload: dict) -> dict:
    evidence = payload.get("evidence")
    if isinstance(evidence, str):
        evidence_list = [evidence.strip()] if evidence.strip() else []
    elif isinstance(evidence, list):
        evidence_list = [str(x).strip() for x in evidence if str(x).strip()]
    else:
        evidence_list = []

    reasoning = payload.get("reasoning")
    if not reasoning:
        reasoning = payload.get("rationale")
    if not reasoning:
        reasoning = payload.get("summary")

    return {
        "sentiment": _normalize_sentiment(payload.get("sentiment")),
        "impact": _normalize_impact(payload.get("impact")),
        "confidence": _clamp_float(payload.get("confidence"), 0.0, 1.0, 0.5),
        "evidence": evidence_list[:3],
        "reasoning": str(reasoning or "")[:500],
    }


def _keyword_fallback(article: dict, reason: str) -> dict:
    text = f"{article.get('headline', '')} {article.get('summary', '')}".lower()

    bullish = [
        "beat", "upgrade", "outperform", "growth", "record", "buyback", "partnership",
        "breakthrough", "surge", "strong", "guidance raise",
    ]
    bearish = [
        "miss", "downgrade", "underperform", "lawsuit", "probe", "recall", "layoff",
        "warning", "fraud", "decline", "weak", "guidance cut",
    ]

    bull_hits = [kw for kw in bullish if kw in text]
    bear_hits = [kw for kw in bearish if kw in text]

    if len(bull_hits) > len(bear_hits):
        sentiment = "bullish"
    elif len(bear_hits) > len(bull_hits):
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    high_impact_hints = [
        "earnings", "guidance", "merger", "acquisition", "lawsuit", "investigation",
        "sec", "doj", "bankruptcy", "default",
    ]
    if any(kw in text for kw in high_impact_hints):
        impact = "high"
    elif text.strip():
        impact = "medium"
    else:
        impact = "low"

    evidence = (bull_hits + bear_hits)[:3]
    return {
        "sentiment": sentiment,
        "impact": impact,
        "confidence": 0.35,
        "evidence": evidence,
        "reasoning": f"keyword fallback: {reason}",
        "provider": "keyword_fallback",
        "model": "",
        "raw_response": "",
        "think_content": "",
    }


def _build_rollup(items: list[dict]) -> dict:
    sentiment_counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    impact_counts = {"high": 0, "medium": 0, "low": 0}
    score_map = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    impact_weight = {"high": 3.0, "medium": 2.0, "low": 1.0}

    weighted_sum = 0.0
    total_weight = 0.0
    confidence_sum = 0.0

    for item in items:
        sentiment = _normalize_sentiment(item.get("sentiment"))
        impact = _normalize_impact(item.get("impact"))
        confidence = _clamp_float(item.get("confidence"), 0.0, 1.0, 0.5)

        sentiment_counts[sentiment] += 1
        impact_counts[impact] += 1

        weight = impact_weight[impact] * max(confidence, 0.05)
        weighted_sum += score_map[sentiment] * weight
        total_weight += weight
        confidence_sum += confidence

    article_count = len(items)
    avg_confidence = (confidence_sum / article_count) if article_count > 0 else None
    weighted_score = None
    if total_weight > 0:
        weighted_score = (weighted_sum / total_weight) * 100.0

    overall_sentiment = "neutral"
    if weighted_score is not None:
        if weighted_score >= 20:
            overall_sentiment = "bullish"
        elif weighted_score <= -20:
            overall_sentiment = "bearish"

    impact_priority = ["high", "medium", "low"]
    overall_impact = "low"
    best_count = -1
    for key in impact_priority:
        if impact_counts[key] > best_count:
            best_count = impact_counts[key]
            overall_impact = key

    return {
        "article_count": article_count,
        "sentiment_counts": sentiment_counts,
        "impact_counts": impact_counts,
        "average_confidence": avg_confidence,
        "weighted_sentiment_score": weighted_score,
        "overall_sentiment": overall_sentiment,
        "overall_impact": overall_impact,
    }


class MiniMaxSemanticProvider:
    """MiniMax 语义打标 provider"""

    def __init__(self, api_key: str, model: str, endpoint: str, timeout_seconds: int):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def tag_article(self, ticker: str, article: dict, prompt_version: str) -> dict:
        system_prompt = (
            "You are a disciplined US equities news analyst. "
            "Return strict JSON only, no markdown. "
            "Schema: sentiment(bullish|neutral|bearish), impact(low|medium|high), "
            "confidence(0~1), evidence(array of <=3 short strings), reasoning(short string)."
        )

        user_payload = {
            "ticker": ticker,
            "prompt_version": prompt_version,
            "headline": article.get("headline", ""),
            "summary": article.get("summary", ""),
            "source": article.get("source", ""),
            "datetime": article.get("datetime", 0),
            "url": article.get("url", ""),
        }

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Tag this single news item for short-term swing impact.\n"
                        f"Input JSON:\n{json.dumps(user_payload, ensure_ascii=False)}"
                    ),
                },
            ],
        }

        resp = requests.post(
            self.endpoint,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()

        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        raw_response = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        think_content = (
            message.get("reasoning_content")
            or message.get("think_content")
            or choice.get("reasoning_content")
            or ""
        )

        parsed = _first_json_object(raw_response)
        if not isinstance(parsed, dict):
            raise ValueError("semantic provider returned non-json payload")

        result = _normalize_semantic_payload(parsed)
        result.update(
            {
                "provider": "minimax",
                "model": self.model,
                "raw_response": raw_response,
                "think_content": str(think_content or ""),
            }
        )
        return result


class SemanticNewsTagger:
    """新闻逐条语义打标（带缓存）"""

    def __init__(self, cache: SQLiteCache | None = None):
        self.cache = cache or SQLiteCache(config.CACHE["db_file"])

        self.enabled = bool(config.NEWS.get("enable_semantic_tagging", True))
        self.provider_name = str(config.NEWS.get("semantic_provider", "minimax")).strip().lower()
        self.prompt_version = str(config.NEWS.get("semantic_prompt_version", "news_semantic_v1")).strip()
        self.max_articles_per_stock = int(
            config.NEWS.get(
                "semantic_max_articles_per_stock",
                config.FEATURES.get("max_news_articles_per_stock", 20),
            )
        )
        self.enable_fallback = bool(config.NEWS.get("semantic_enable_fallback", True))
        self.cache_ttl_seconds = int(float(config.CACHE.get("news_semantic_ttl_hours", 72)) * 3600)

        self.cache_stats = {
            "semantic_hit": 0,
            "semantic_miss": 0,
            "semantic_network_refresh": 0,
            "semantic_fallback": 0,
            "semantic_disabled": 0,
            "semantic_error": 0,
        }
        self.semantic_meta: dict[str, dict] = {}

        self.provider = None
        if self.provider_name == "minimax" and config.MINIMAX_API_KEY:
            self.provider = MiniMaxSemanticProvider(
                api_key=config.MINIMAX_API_KEY,
                model=str(config.NEWS.get("semantic_model", "MiniMax-M2.5")),
                endpoint=str(config.NEWS.get("semantic_endpoint", "https://api.minimaxi.com/v1/chat/completions")),
                timeout_seconds=int(config.NEWS.get("semantic_timeout_seconds", 20)),
            )

    @staticmethod
    def _article_fingerprint(article: dict) -> str:
        return SQLiteCache.news_fingerprint(article or {})

    def _cache_key(self, ticker: str, fingerprint: str) -> str:
        return f"news_semantic:{self.provider_name}:{self.prompt_version}:{ticker}:{fingerprint}"

    def _build_meta(
        self,
        ticker: str,
        *,
        retrieval_mode: str,
        article_count: int,
        mode_counts: dict[str, int],
        reason: str = "",
    ) -> dict:
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=max(1, self.cache_ttl_seconds))
        return {
            "source": self.provider_name or "news_semantic",
            "symbol": ticker,
            "data_type": "news_semantic",
            "as_of": now.isoformat(),
            "fetched_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "retrieval_mode": retrieval_mode,
            "article_count": article_count,
            "cache_hit_count": int(mode_counts.get("cache_hit", 0)),
            "cache_miss_count": int(mode_counts.get("cache_miss", 0)),
            "network_refresh_count": int(mode_counts.get("network_refresh", 0)),
            "cache_fallback_count": int(mode_counts.get("cache_fallback", 0)),
            "disabled_reason": reason,
        }

    def _pick_retrieval_mode(self, mode_counts: dict[str, int], article_count: int) -> str:
        if article_count <= 0:
            return "cache_miss"
        if mode_counts.get("network_refresh", 0) > 0:
            return "network_refresh"
        if mode_counts.get("cache_fallback", 0) > 0:
            return "cache_fallback"
        if mode_counts.get("cache_hit", 0) > 0 and mode_counts.get("cache_hit", 0) == article_count:
            return "cache_hit"
        return "cache_miss"

    def tag_batch(self, news_by_ticker: dict[str, list[dict]]) -> dict[str, dict]:
        self.semantic_meta = {}
        results: dict[str, dict] = {}
        for ticker, articles in news_by_ticker.items():
            results[ticker] = self.tag_articles(ticker=ticker, articles=articles)
        return results

    def tag_articles(self, ticker: str, articles: list[dict]) -> dict:
        selected = list(articles or [])
        if self.max_articles_per_stock > 0:
            selected = selected[: self.max_articles_per_stock]

        if not self.enabled:
            self.cache_stats["semantic_disabled"] += len(selected)
            self.semantic_meta[ticker] = self._build_meta(
                ticker,
                retrieval_mode="cache_miss",
                article_count=len(selected),
                mode_counts={},
                reason="feature_disabled",
            )
            return {"by_fingerprint": {}, "rollup": _build_rollup([])}

        by_fingerprint: dict[str, dict] = {}
        mode_counts = {
            "cache_hit": 0,
            "cache_miss": 0,
            "network_refresh": 0,
            "cache_fallback": 0,
        }

        for article in selected:
            fingerprint = self._article_fingerprint(article)
            cache_key = self._cache_key(ticker, fingerprint)
            cached = self.cache.get(cache_key)
            if isinstance(cached, dict) and cached:
                item = dict(cached)
                item["retrieval_mode"] = "cache_hit"
                by_fingerprint[fingerprint] = item
                mode_counts["cache_hit"] += 1
                self.cache_stats["semantic_hit"] += 1
                continue

            mode_counts["cache_miss"] += 1
            self.cache_stats["semantic_miss"] += 1

            semantic = None
            if self.provider is not None:
                try:
                    semantic = self.provider.tag_article(
                        ticker=ticker,
                        article=article,
                        prompt_version=self.prompt_version,
                    )
                except Exception as e:
                    self.cache_stats["semantic_error"] += 1
                    logger.debug(f"语义打标失败 {ticker}: {e}")

            retrieval_mode = "network_refresh"
            if semantic is None:
                if not self.enable_fallback:
                    continue
                semantic = _keyword_fallback(article, reason="provider_unavailable_or_failed")
                retrieval_mode = "cache_fallback"
                mode_counts["cache_fallback"] += 1
                self.cache_stats["semantic_fallback"] += 1
            else:
                mode_counts["network_refresh"] += 1
                self.cache_stats["semantic_network_refresh"] += 1

            semantic["prompt_version"] = self.prompt_version
            semantic["article_fingerprint"] = fingerprint
            semantic["retrieval_mode"] = retrieval_mode
            semantic["tagged_at"] = datetime.utcnow().isoformat()

            self.cache.set(
                key=cache_key,
                payload=semantic,
                ttl_seconds=self.cache_ttl_seconds,
                source=str(semantic.get("provider") or self.provider_name),
                symbol=ticker,
                data_type="news_semantic",
                as_of=semantic.get("tagged_at") or datetime.utcnow().isoformat(),
            )
            by_fingerprint[fingerprint] = semantic

        rollup = _build_rollup(list(by_fingerprint.values()))
        retrieval_mode = self._pick_retrieval_mode(mode_counts, article_count=len(selected))
        reason = ""
        if self.provider is None:
            reason = "missing_provider_api_key"

        self.semantic_meta[ticker] = self._build_meta(
            ticker,
            retrieval_mode=retrieval_mode,
            article_count=len(selected),
            mode_counts=mode_counts,
            reason=reason,
        )
        return {
            "by_fingerprint": by_fingerprint,
            "rollup": rollup,
        }

    def get_semantic_meta(self, ticker: str) -> dict:
        return dict(self.semantic_meta.get(ticker, {}))
