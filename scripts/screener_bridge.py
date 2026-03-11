#!/usr/bin/env python3
"""Submit stock-screener final selections to Screener Arena."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit final_selection.json to Screener Arena")
    parser.add_argument("--session-dir", required=True, help="Path to output/runs/<session_id>")
    parser.add_argument("--arena-url", default="http://localhost:8766", help="Screener Arena base URL")
    parser.add_argument("--api-key", required=True, help="Screener bot API key")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without POSTing")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    session_dir = Path(args.session_dir).resolve()
    payload = build_payload(session_dir)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    response = requests.post(
        f"{args.arena_url.rstrip('/')}/api/screener/submit-picks",
        headers={"Authorization": f"Bearer {args.api_key}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    print(response.text)


def build_payload(session_dir: Path) -> dict:
    final_selection = _load_json(session_dir / "judge" / "final_selection.json")
    session_id = session_dir.name
    selected = set(final_selection.get("final_top_n") or [])
    if not selected:
        raise ValueError(
            f"final_top_n is empty in {session_dir / 'judge' / 'final_selection.json'}. "
            "Refusing to submit — pipeline may not have completed successfully."
        )
    picks = []
    for candidate in final_selection.get("ranked_candidates") or []:
        ticker = candidate.get("ticker")
        if not ticker or ticker not in selected:
            continue
        deep_analysis = _load_deep_analysis(session_dir, ticker)
        picks.append(
            {
                "ticker": ticker,
                "direction": "bullish",
                "confidence": deep_analysis.get("confidence", 0.5),
                "final_rank": candidate.get("final_rank"),
                "setup_type": deep_analysis.get("setup_type"),
                "reasoning": candidate.get("selection_reason"),
            }
        )
    return {"session_id": session_id, "picks": picks}


def _load_deep_analysis(session_dir: Path, ticker: str) -> dict:
    path = session_dir / "symbols" / ticker / "llm" / "deep_analysis.json"
    try:
        return _load_json(path)
    except FileNotFoundError:
        print(f"  WARNING: deep_analysis.json not found for {ticker}, using defaults", flush=True)
        return {}


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    main()
