from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def news_fingerprint(article: dict) -> str:
    base = "|".join(
        [
            str((article or {}).get("url", "")).strip(),
            str((article or {}).get("headline", "")).strip(),
            str((article or {}).get("datetime", "")).strip(),
            str((article or {}).get("source", "")).strip(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def first_json_object(text: str) -> dict | None:
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

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


def normalize_history_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None

    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    else:
        columns = [str(col) for col in frame.columns]
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(set(columns)) and len(columns) >= 5:
            suffix_pattern = all(
                idx == 0 or columns[idx].startswith(f"{columns[0]}.")
                for idx in range(min(5, len(columns)))
            )
            if suffix_pattern:
                renamed = columns[:]
                renamed[:5] = ["Open", "High", "Low", "Close", "Volume"]
                frame.columns = renamed

    if "Close" not in frame.columns:
        return None

    frame = frame.dropna(how="all")
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_convert(None)
    return frame
