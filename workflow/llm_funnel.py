from __future__ import annotations

import json
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests

from runtime import config
from runtime.utils import first_json_object as _first_json_object

logger = logging.getLogger(__name__)


def _market_date_from_session(session_payload: dict | None) -> date | None:
    session = dict(session_payload or {})
    market_date = str(session.get("market_date") or "").strip()
    if market_date:
        try:
            return datetime.fromisoformat(market_date).date()
        except Exception:
            pass

    market_tz = ZoneInfo(config.SESSION_CONFIG["market_timezone"])
    return datetime.now(market_tz).date()


def _recalc_days_to_expiry(nearest_expiry: str | None, session_payload: dict | None) -> int | None:
    """Recalculate option expiry off the session's market date, not local machine date."""
    if not nearest_expiry:
        return None
    try:
        expiry_date = datetime.fromisoformat(nearest_expiry).date()
        market_date = _market_date_from_session(session_payload)
        if market_date is None:
            return None
        return max(0, (expiry_date - market_date).days)
    except Exception:
        return None


_NEWS_RELEVANCE_THRESHOLD = 0.4


def _compact_news_items(items: list[dict], limit: int = 5) -> list[dict]:
    compact = []
    for item in (items or []):
        if len(compact) >= limit:
            break
        semantic = item.get("semantic")
        if isinstance(semantic, dict) and semantic:
            relevance = semantic.get("relevance")
            if isinstance(relevance, (int, float)) and relevance < _NEWS_RELEVANCE_THRESHOLD:
                continue
        entry = {
            "datetime": item.get("datetime"),
            "headline": item.get("headline", ""),
            "summary": str(item.get("summary", ""))[:400],
            "source": item.get("source", ""),
        }
        if isinstance(semantic, dict) and semantic:
            entry["semantic"] = {
                "sentiment": semantic.get("sentiment"),
                "impact": semantic.get("impact"),
                "confidence": semantic.get("confidence"),
                "relevance": semantic.get("relevance"),
            }
        compact.append(entry)
    return compact


def _prepare_deep_payload(full_dossier: dict) -> dict:
    derived = dict(full_dossier.get("derived") or {})
    stock_context = dict(derived.get("stock_context") or {})
    raw = dict(full_dossier.get("raw") or {})
    news_block = dict(stock_context.get("news") or {})
    options_block = dict(stock_context.get("options_summary") or {})
    sector_block = dict(stock_context.get("sector_context") or {})
    events_block = dict(stock_context.get("upcoming_events") or {})
    analyst_block = dict(stock_context.get("analyst") or {})
    valuation_consistency = dict(stock_context.get("valuation_consistency") or {})

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
            "analyst": analyst_block,
            "valuation_consistency": valuation_consistency,
            "liquidity": stock_context.get("liquidity") or {},
            "fact_sheet": stock_context.get("fact_sheet") or {},
            "market_linkage": stock_context.get("market_linkage") or {},
            "support_resistance": stock_context.get("support_resistance") or {},
            "options_summary": {
                "as_of": options_block.get("as_of"),
                "aggregate": options_block.get("aggregate") or {},
                "nearest_expiry": options_block.get("nearest_expiry"),
                "days_to_nearest_expiry": _recalc_days_to_expiry(
                    options_block.get("nearest_expiry"),
                    full_dossier.get("session"),
                ),
                "expiries": list((options_block.get("expiries") or [])[:3]),
                "unusual_contracts": list((options_block.get("unusual_contracts") or [])[:5]),
            },
            "ohlc_recent_20d": list((stock_context.get("ohlc_recent") or [])[-20:]),
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
            logger.warning("LLM 返回无法解析为 JSON，返回 unavailable")
        except Exception as exc:
            logger.warning("LLM 调用失败，返回 unavailable: %s", exc)
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
        return request_payload, self._unavailable_triage()

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
        return request_payload, self._unavailable_deep_analysis()

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
        return request_payload, self._unavailable_final_judge(selection_count)

    def _unavailable_triage(self) -> dict:
        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["triage"],
            "execution_mode": "unavailable",
            "generated_at": datetime.utcnow().isoformat(),
            "triage_verdict": "unavailable",
            "triage_confidence": None,
            "why_keep": [],
            "why_reject": ["live_llm_unavailable"],
            "missing_info_requests": ["live_llm_required_for_triage"],
            "risk_flags": [],
            "unavailable_reason": "live_llm_unavailable",
        }

    def _unavailable_deep_analysis(self) -> dict:
        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["deep_analysis"],
            "execution_mode": "unavailable",
            "generated_at": datetime.utcnow().isoformat(),
            "analysis_status": "unavailable",
            "setup_type": None,
            "bull_case": [],
            "bear_case": [],
            "trigger": None,
            "invalidation": None,
            "holding_window": None,
            "execution_notes": ["live_llm_required_for_deep_analysis"],
            "confidence": None,
            "unavailable_reason": "live_llm_unavailable",
        }

    def _unavailable_final_judge(self, selection_count: int) -> dict:
        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["portfolio_judge"],
            "execution_mode": "unavailable",
            "generated_at": datetime.utcnow().isoformat(),
            "final_top_n": [],
            "ranked_candidates": [],
            "unavailable_reason": "live_llm_unavailable",
            "summary": {
                "note": "Cross-stock judge unavailable. No final recommendations were produced.",
                "selection_count": selection_count,
            },
        }
