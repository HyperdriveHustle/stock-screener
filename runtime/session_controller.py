from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from runtime import config


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


@dataclass
class SessionContext:
    session_id: str
    market_date: str
    session_type: str
    market_timezone: str
    run_at_utc: str
    run_at_market_tz: str
    market_open_time: str
    market_close_time: str
    analysis_horizon: str
    model_profiles: list[str] = field(default_factory=list)
    schema_versions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["schema_version"] = config.PIPELINE_CONFIG["schema_versions"]["session_context"]
        return payload


def create_session_context(now: datetime | None = None) -> SessionContext:
    market_tz_name = config.SESSION_CONFIG["market_timezone"]
    market_tz = ZoneInfo(market_tz_name)
    now_market = now.astimezone(market_tz) if now is not None else datetime.now(market_tz)
    now_utc = now_market.astimezone(timezone.utc)

    market_open = _parse_hhmm(config.SESSION_CONFIG["market_open_time"])
    market_close = _parse_hhmm(config.SESSION_CONFIG["market_close_time"])

    clock_time = now_market.timetz().replace(tzinfo=None)
    if clock_time < market_open:
        session_type = config.SESSION_CONFIG["pre_market_label"]
    elif clock_time < market_close:
        session_type = config.SESSION_CONFIG["intraday_label"]
    else:
        session_type = config.SESSION_CONFIG["post_close_label"]

    session_id = f"{now_market:%Y%m%d}_{session_type}_{now_market:%H%M%S}"
    return SessionContext(
        session_id=session_id,
        market_date=now_market.strftime("%Y-%m-%d"),
        session_type=session_type,
        market_timezone=market_tz_name,
        run_at_utc=now_utc.isoformat(),
        run_at_market_tz=now_market.isoformat(),
        market_open_time=config.SESSION_CONFIG["market_open_time"],
        market_close_time=config.SESSION_CONFIG["market_close_time"],
        analysis_horizon=config.LLM["analysis_horizon"],
        model_profiles=list(config.LLM["model_profiles"]),
        schema_versions=dict(config.PIPELINE_CONFIG["schema_versions"]),
    )
