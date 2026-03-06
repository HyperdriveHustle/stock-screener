from __future__ import annotations

import json
import logging
from datetime import datetime

import requests

from runtime import config

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _first_json_object(text: str) -> dict | None:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(stripped)):
        char = stripped[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = stripped[start: idx + 1]
                try:
                    payload = json.loads(snippet)
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    return None
    return None


def _compact_news_items(items: list[dict], limit: int = 5) -> list[dict]:
    compact = []
    for item in (items or [])[:limit]:
        compact.append(
            {
                "datetime": item.get("datetime"),
                "headline": item.get("headline", ""),
                "summary": str(item.get("summary", ""))[:400],
                "source": item.get("source", ""),
                "url": item.get("url", ""),
            }
        )
    return compact


def _prepare_deep_payload(full_dossier: dict) -> dict:
    derived = dict(full_dossier.get("derived") or {})
    stock_context = dict(derived.get("stock_context") or {})
    raw = dict(full_dossier.get("raw") or {})
    news_block = dict(stock_context.get("news") or {})
    options_block = dict(stock_context.get("options_summary") or {})
    sector_block = dict(stock_context.get("sector_context") or {})
    events_block = dict(stock_context.get("upcoming_events") or {})

    if "recent_articles" in news_block:
        news_block["recent_articles"] = _compact_news_items(news_block.get("recent_articles") or [])
    if "peers" in sector_block:
        sector_block["peers"] = list((sector_block.get("peers") or [])[:5])
    if "company_events" in events_block:
        events_block["company_events"] = list((events_block.get("company_events") or [])[:5])
    if "peer_events" in events_block:
        events_block["peer_events"] = list((events_block.get("peer_events") or [])[:5])
    if "macro_events" in events_block:
        events_block["macro_events"] = list((events_block.get("macro_events") or [])[:10])

    return {
        "schema_version": full_dossier.get("schema_version"),
        "session": full_dossier.get("session"),
        "ticker": full_dossier.get("ticker"),
        "identity": full_dossier.get("identity"),
        "compact_summary": full_dossier.get("compact_summary"),
        "prior_judgment": full_dossier.get("prior_judgment"),
        "derived_focus": {
            "generator_summary": derived.get("generator_summary") or {},
            "technical": stock_context.get("technical") or {},
            "premarket": stock_context.get("premarket") or {},
            "news": news_block,
            "fundamentals": stock_context.get("fundamentals") or {},
            "valuation": stock_context.get("valuation") or {},
            "liquidity": stock_context.get("liquidity") or {},
            "fact_sheet": stock_context.get("fact_sheet") or {},
            "market_linkage": stock_context.get("market_linkage") or {},
            "support_resistance": stock_context.get("support_resistance") or {},
            "options_summary": {
                "aggregate": options_block.get("aggregate") or {},
                "nearest_expiry": options_block.get("nearest_expiry"),
                "days_to_nearest_expiry": options_block.get("days_to_nearest_expiry"),
                "unusual_contracts": list((options_block.get("unusual_contracts") or [])[:5]),
            },
            "sector_context": sector_block,
            "upcoming_events": events_block,
            "data_quality": full_dossier.get("data_quality") or {},
        },
        "raw_focus": {
            "latest_news": _compact_news_items(raw.get("news") or []),
            "company_events": list(((raw.get("events") or {}).get("company_events") or [])[:5]),
            "peer_events": list(((raw.get("events") or {}).get("peer_events") or [])[:5]),
        },
        "transport_adaptation": {
            "mode": "compact_live_payload",
            "raw_news_limit": 5,
            "peer_limit": 5,
            "event_limit": 5,
        },
    }


def _prepare_final_payload(deep_analysis_by_ticker: dict[str, dict]) -> dict[str, dict]:
    prepared = {}
    for ticker, payload in deep_analysis_by_ticker.items():
        prepared[ticker] = {
            "setup_type": payload.get("setup_type"),
            "bull_case": list((payload.get("bull_case") or [])[:5]),
            "bear_case": list((payload.get("bear_case") or [])[:5]),
            "trigger": payload.get("trigger"),
            "invalidation": payload.get("invalidation"),
            "holding_window": payload.get("holding_window"),
            "execution_notes": list((payload.get("execution_notes") or [])[:5]),
            "confidence": payload.get("confidence"),
            "execution_mode": payload.get("execution_mode"),
        }
    return prepared


class StructuredLLMRunner:
    def __init__(self):
        llm_cfg = config.PIPELINE_CONFIG["llm"]
        self.enabled = bool(config.BUDGET_CONFIG.get("enable_live_llm"))
        self.api_key = config.MINIMAX_API_KEY
        self.endpoint = llm_cfg["endpoint"]
        self.model = llm_cfg["model"]
        self.timeout = int(config.BUDGET_CONFIG["llm_timeout_seconds"])

    def _call_json(self, *, system_prompt: str, user_payload: dict) -> tuple[dict, dict]:
        request_payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }

        if not self.enabled or not self.api_key:
            return request_payload, {}

        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=request_payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            content = (
                ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            )
            parsed = _first_json_object(content)
            if isinstance(parsed, dict):
                return request_payload, parsed
            logger.warning("LLM 返回无法解析为 JSON，回退 fallback proxy")
        except Exception as exc:
            logger.warning("LLM 调用失败，回退 fallback proxy: %s", exc)
        return request_payload, {}

    def triage(self, market_context_compact: dict, compact_card: dict) -> tuple[dict, dict]:
        system_prompt = (
            "You are running compact triage for a US equities swing-trading pipeline. "
            "Use only the provided facts. Return strict JSON with keys: "
            "triage_verdict(keep|observe|reject), triage_confidence(0~1), "
            "why_keep(array), why_reject(array), missing_info_requests(array), risk_flags(array)."
        )
        user_payload = {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["triage"],
            "market_context_compact": market_context_compact,
            "compact_card": compact_card,
        }
        logger.info("LLM triage request: %s", compact_card.get("ticker"))
        request_payload, response_payload = self._call_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        if response_payload:
            response_payload.setdefault("schema_version", config.PIPELINE_CONFIG["schema_versions"]["triage"])
            response_payload.setdefault("execution_mode", "live_llm")
            return request_payload, response_payload
        return request_payload, self._fallback_triage(compact_card)

    def deep_analysis(
        self,
        market_context: dict,
        full_dossier: dict,
    ) -> tuple[dict, dict]:
        system_prompt = (
            "You are producing deep single-stock analysis for a US 1-2 week swing-trading pipeline. "
            "Return strict JSON with keys: setup_type, bull_case(array), bear_case(array), "
            "trigger, invalidation, holding_window, execution_notes(array), confidence(0~1)."
        )
        user_payload = {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["deep_analysis"],
            "market_context": market_context,
            "full_dossier": _prepare_deep_payload(full_dossier),
        }
        logger.info("LLM deep analysis request: %s", full_dossier.get("ticker"))
        request_payload, response_payload = self._call_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        if response_payload:
            response_payload.setdefault("schema_version", config.PIPELINE_CONFIG["schema_versions"]["deep_analysis"])
            response_payload.setdefault("execution_mode", "live_llm")
            return request_payload, response_payload
        return request_payload, self._fallback_deep_analysis(full_dossier)

    def final_judge(
        self,
        market_context_compact: dict,
        deep_analysis_by_ticker: dict[str, dict],
        selection_count: int,
    ) -> tuple[dict, dict]:
        system_prompt = (
            "You are the cross-stock judge for a US equities swing-trading pipeline. "
            "Return strict JSON with keys: final_top_n(array), ranked_candidates(array of objects with "
            "ticker, final_rank, selection_reason, rejection_reason, portfolio_overlap_flags), summary."
        )
        user_payload = {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["portfolio_judge"],
            "selection_count": selection_count,
            "market_context_compact": market_context_compact,
            "deep_analysis_by_ticker": _prepare_final_payload(deep_analysis_by_ticker),
        }
        logger.info("LLM final judge request: %d candidates", len(deep_analysis_by_ticker))
        request_payload, response_payload = self._call_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        if response_payload:
            response_payload.setdefault("schema_version", config.PIPELINE_CONFIG["schema_versions"]["portfolio_judge"])
            response_payload.setdefault("execution_mode", "live_llm")
            return request_payload, response_payload
        return request_payload, self._fallback_final_judge(deep_analysis_by_ticker, selection_count)

    def _fallback_triage(self, compact_card: dict) -> dict:
        summary = compact_card.get("derived", {})
        generator_summary = summary.get("generator_summary") or {}
        score = int(generator_summary.get("triggered_count") or 0)
        data_quality = _safe_float(
            ((compact_card.get("compact_summary") or {}).get("coverage") or {}).get("overall_coverage"),
            0.0,
        )
        strong_gap = abs(_safe_float((summary.get("premarket") or {}).get("premarket_change_pct"), 0.0))
        news_count = int((summary.get("attention") or {}).get("news_article_count") or 0)

        verdict = "reject"
        if score >= 3 or strong_gap >= config.PREMARKET["gap_strong_threshold"]:
            verdict = "keep"
        elif score >= 1 or news_count >= 2:
            verdict = "observe"

        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["triage"],
            "execution_mode": "fallback_proxy",
            "generated_at": datetime.utcnow().isoformat(),
            "triage_verdict": verdict,
            "triage_confidence": round(min(0.8, 0.25 + score * 0.12 + data_quality * 0.2), 3),
            "why_keep": list(generator_summary.get("triggered_generators") or []),
            "why_reject": [] if verdict != "reject" else ["insufficient_recall_signals"],
            "missing_info_requests": [] if data_quality >= 0.5 else ["improve_data_quality"],
            "risk_flags": list(generator_summary.get("risk_flags") or []),
        }

    def _fallback_deep_analysis(self, full_dossier: dict) -> dict:
        stock_context = ((full_dossier.get("derived") or {}).get("stock_context") or {})
        generator_summary = ((full_dossier.get("derived") or {}).get("generator_summary") or {})
        support_resistance = stock_context.get("support_resistance") or {}
        setup_type = "event_driven"
        if "price_action_generator" in (generator_summary.get("triggered_generators") or []):
            setup_type = "price_action"
        elif "premarket_dislocation_generator" in (generator_summary.get("triggered_generators") or []):
            setup_type = "premarket_dislocation"

        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["deep_analysis"],
            "execution_mode": "fallback_proxy",
            "generated_at": datetime.utcnow().isoformat(),
            "setup_type": setup_type,
            "bull_case": list(generator_summary.get("triggered_generators") or []),
            "bear_case": list(generator_summary.get("risk_flags") or []),
            "trigger": {
                "type": "evidence_follow_through",
                "note": "Fallback proxy uses generator evidence instead of live LLM reasoning.",
            },
            "invalidation": {
                "nearest_support": support_resistance.get("nearest_support"),
                "note": "Review if catalyst or price follow-through fails.",
            },
            "holding_window": config.LLM["analysis_horizon"],
            "execution_notes": [
                "fallback_proxy_active",
                "replace_with_live_llm_for_directional judgment",
            ],
            "confidence": round(
                min(0.75, 0.3 + 0.1 * int(generator_summary.get("triggered_count") or 0)),
                3,
            ),
        }

    def _fallback_final_judge(
        self,
        deep_analysis_by_ticker: dict[str, dict],
        selection_count: int,
    ) -> dict:
        ranked = sorted(
            deep_analysis_by_ticker.items(),
            key=lambda item: _safe_float(item[1].get("confidence"), 0.0),
            reverse=True,
        )
        ranked_candidates = []
        final_top_n = []
        for idx, (ticker, payload) in enumerate(ranked, start=1):
            selected = idx <= selection_count
            if selected:
                final_top_n.append(ticker)
            ranked_candidates.append(
                {
                    "ticker": ticker,
                    "final_rank": idx,
                    "selection_reason": "highest_available_proxy_confidence" if selected else "",
                    "rejection_reason": "" if selected else "ranked_below_selection_cutoff",
                    "portfolio_overlap_flags": [],
                    "confidence": payload.get("confidence"),
                }
            )

        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["portfolio_judge"],
            "execution_mode": "fallback_proxy",
            "generated_at": datetime.utcnow().isoformat(),
            "final_top_n": final_top_n,
            "ranked_candidates": ranked_candidates,
            "summary": {
                "note": "Fallback proxy active. Replace with live LLM for production-quality cross-stock judgment.",
                "selection_count": selection_count,
            },
        }
