#!/usr/bin/env python3
"""Run Claude Opus 4.6 through the same stock screener evaluation via `claude -p`.

Uses the Claude Code CLI as the LLM backend — no API key needed.
Output format matches comparison_v2.json for merging.
"""

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ──────────────────────────────────────────────────────

MODEL_NAME = "claude-opus-4-6"
RUN_DIR = ""  # auto-detect latest
OUTPUT_DIR = "output/llm_compare"
MAX_WORKERS = 4  # parallel calls

TRIAGE_SYSTEM = (
    "You are running compact triage for a US equities swing-trading pipeline. "
    "Use only the provided facts. Return strict JSON.\n\n"
    "Decision criteria:\n"
    "- keep: clear setup exists (breakout, support bounce, catalyst) + favorable risk/reward + sector/macro tailwind\n"
    "- reject: broken structure (below key MAs, weak sector, negative sentiment) with no offsetting catalyst\n"
    "- observe: mixed signals or needs better entry timing\n"
    "You MUST differentiate across candidates — use all three verdicts where appropriate.\n\n"
    "Confidence calibration (spread across full range, do NOT cluster):\n"
    "- 0.85+: overwhelming evidence in one direction\n"
    "- 0.60-0.85: solid case but some counter-arguments\n"
    "- 0.40-0.60: genuinely uncertain, mixed signals\n"
    "- <0.40: very weak or conflicting evidence\n\n"
    "Keys: triage_verdict(keep|observe|reject), triage_confidence(0~1), "
    "why_keep(array), why_reject(array), missing_info_requests(array), risk_flags(array)."
)

DEEP_SYSTEM = (
    "You are producing deep single-stock analysis for a US 1-2 week swing-trading pipeline. "
    "Return strict JSON.\n\n"
    "Format requirements:\n"
    "- setup_type: use snake_case (e.g. mean_reversion_bounce, breakout_continuation)\n"
    "- bull_case / bear_case: 4-6 specific points each, cite exact numbers from the data\n"
    "- trigger / invalidation: include specific price levels AND volume or indicator conditions\n"
    "- holding_window: specify concrete date anchors (earnings, ex-div, FOMC) when relevant\n\n"
    "Confidence calibration (relative to other candidates in this batch):\n"
    "- Best setup with clear edge: 0.70+\n"
    "- Decent opportunity with notable risks: 0.50-0.70\n"
    "- Marginal or counter-trend: 0.30-0.50\n"
    "Do NOT cluster all values around the same number — spread them to reflect real differences.\n\n"
    "Keys: setup_type, bull_case(array), bear_case(array), "
    "trigger, invalidation, holding_window, execution_notes(array), confidence(0~1)."
)

JUDGE_SYSTEM = (
    "You are the cross-stock judge for a US equities swing-trading pipeline. "
    "Return strict JSON.\n\n"
    "Ranking criteria:\n"
    "- Prioritize risk-adjusted opportunity: strong setup + favorable sector + manageable downside\n"
    "- Penalize broken technicals, weak sectors, and elevated macro risk\n"
    "- For each candidate: provide selection_reason (why picked) OR rejection_reason (why not)\n"
    "- portfolio_overlap_flags: use snake_case tags (e.g. defensive_sector, dividend_play, momentum)\n\n"
    "Keys: final_top_n(array), ranked_candidates(array of objects with "
    "ticker, final_rank, selection_reason, rejection_reason, portfolio_overlap_flags), summary."
)


def _find_latest_run() -> str:
    runs_dir = "output/runs"
    entries = sorted(os.listdir(runs_dir), reverse=True)
    for entry in entries:
        candidate = os.path.join(runs_dir, entry)
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "run_manifest.json")):
            return candidate
    raise FileNotFoundError("No valid run found")


def _discover_tickers(run_dir: str) -> list[str]:
    symbols_dir = os.path.join(run_dir, "symbols")
    return sorted(d for d in os.listdir(symbols_dir) if os.path.isdir(os.path.join(symbols_dir, d)))


def call_claude(system_prompt: str, user_payload: dict, timeout: int = 240) -> dict:
    """Call Claude Opus 4.6 via `claude -p` CLI."""
    full_prompt = (
        f"SYSTEM: {system_prompt}\n\n"
        f"USER DATA (respond with strict JSON only, no markdown):\n"
        f"{json.dumps(user_payload, ensure_ascii=False)}"
    )

    t0 = time.time()
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", MODEL_NAME, "--allowedTools", ""],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CLAUDECODE": ""},
        )
        elapsed = round(time.time() - t0, 1)

        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[:200], "elapsed": elapsed}

        content = result.stdout.strip()
        parsed = _first_json(content)
        if not parsed:
            return {"ok": False, "error": f"no_json_in_output: {content[:100]}", "elapsed": elapsed}

        return {"ok": True, "data": parsed, "elapsed": elapsed, "raw_length": len(content)}

    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        return {"ok": False, "error": "timeout", "elapsed": elapsed}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        return {"ok": False, "error": str(e)[:200], "elapsed": elapsed}


def _first_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    return item
            return {}
        return parsed
    except json.JSONDecodeError:
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i, c in enumerate(text[start:], start):
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        pass
    return {}


def main():
    run_dir = RUN_DIR or _find_latest_run()
    tickers = _discover_tickers(run_dir)
    print(f"Run: {run_dir}")
    print(f"Tickers: {tickers}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load artifacts
    mcc_path = os.path.join(run_dir, "market", "market_context_compact.json")
    market_ctx = json.load(open(mcc_path)) if os.path.exists(mcc_path) else {}

    triage_requests = {}
    deep_requests = {}
    for t in tickers:
        tr_path = os.path.join(run_dir, f"symbols/{t}/llm/triage_request.json")
        if os.path.exists(tr_path):
            triage_requests[t] = json.load(open(tr_path))
        dr_path = os.path.join(run_dir, f"symbols/{t}/llm/deep_analysis_request.json")
        if os.path.exists(dr_path):
            deep_requests[t] = json.load(open(dr_path))

    print(f"Loaded: {len(triage_requests)} triage, {len(deep_requests)} deep")
    print(f"Model: {MODEL_NAME} (via claude CLI)")
    print("=" * 60)

    results = {"triage": {}, "deep": {}, "judge": None}
    t_start = time.time()

    # ── Phase 1: Triage (parallel) ──
    print("\n── TRIAGE ──")
    triage_jobs = {}
    for t in tickers:
        if t not in triage_requests:
            continue
        msgs = triage_requests[t].get("messages", [])
        if len(msgs) >= 2:
            triage_jobs[t] = json.loads(msgs[1]["content"])

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(call_claude, TRIAGE_SYSTEM, payload): ticker
            for ticker, payload in triage_jobs.items()
        }
        for future in as_completed(futures):
            t = futures[future]
            r = future.result()
            results["triage"][t] = r
            if r["ok"]:
                v = r["data"].get("triage_verdict", "?")
                c = r["data"].get("triage_confidence", "?")
                print(f"  {t}: {v}({c}) [{r['elapsed']}s]")
            else:
                print(f"  {t}: ERROR {r['error'][:60]} [{r['elapsed']}s]")

    # ── Phase 2: Deep (parallel) ──
    print("\n── DEEP ──")
    deep_jobs = {}
    for t in tickers:
        if t not in deep_requests:
            continue
        msgs = deep_requests[t].get("messages", [])
        if len(msgs) >= 2:
            deep_jobs[t] = json.loads(msgs[1]["content"])

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(call_claude, DEEP_SYSTEM, payload): ticker
            for ticker, payload in deep_jobs.items()
        }
        for future in as_completed(futures):
            t = futures[future]
            r = future.result()
            results["deep"][t] = r
            if r["ok"]:
                setup = r["data"].get("setup_type", "?")
                c = r["data"].get("confidence", "?")
                print(f"  {t}: {setup} (conf={c}) [{r['elapsed']}s]")
            else:
                print(f"  {t}: ERROR {r['error'][:60]} [{r['elapsed']}s]")

    # ── Phase 3: Judge ──
    print("\n── JUDGE ──")
    deep_for_judge = {}
    for t, r in results["deep"].items():
        if r.get("ok") and r.get("data"):
            deep_for_judge[t] = r["data"]

    if deep_for_judge:
        judge_payload = {
            "selection_count": 5,
            "market_context_compact": market_ctx,
            "deep_analysis_by_ticker": deep_for_judge,
        }
        print(f"  Judging {len(deep_for_judge)} candidates...", flush=True)
        r = call_claude(JUDGE_SYSTEM, judge_payload, timeout=300)
        if r["ok"]:
            top = r["data"].get("final_top_n", [])
            print(f"  Result: top={top} [{r['elapsed']}s]")
        else:
            print(f"  ERROR: {r['error'][:60]} [{r['elapsed']}s]")
        results["judge"] = r

    total_elapsed = round(time.time() - t_start, 1)
    print(f"\nTotal wall time: {total_elapsed}s")

    # Merge into existing comparison
    merge_path = os.path.join(OUTPUT_DIR, "comparison_v3.json")
    if os.path.exists(merge_path):
        all_results = json.load(open(merge_path))
    else:
        all_results = {}

    all_results[MODEL_NAME] = results
    with open(merge_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"Merged into {merge_path} ({len(all_results)} models total)")

    # Print summary
    _print_summary(results, tickers)


def _print_summary(results, tickers):
    print(f"\n{'='*60}")
    print(f"  {MODEL_NAME} SUMMARY")
    print(f"{'='*60}")

    # Triage
    print("\nTriage:")
    for t in tickers:
        r = results["triage"].get(t, {})
        if r.get("ok"):
            d = r["data"]
            print(f"  {t:<6} {d.get('triage_verdict','?'):<8} conf={d.get('triage_confidence','?'):<6} [{r['elapsed']}s]")

    # Deep
    print("\nDeep:")
    for t in tickers:
        r = results["deep"].get(t, {})
        if r.get("ok"):
            d = r["data"]
            print(f"  {t:<6} {str(d.get('setup_type','?'))[:30]:<32} conf={d.get('confidence','?'):<6} [{r['elapsed']}s]")

    # Judge
    jr = results.get("judge", {})
    if jr and jr.get("ok"):
        print(f"\nJudge: {jr['data'].get('final_top_n', [])} [{jr['elapsed']}s]")

    # Timing
    all_times = []
    for phase in ["triage", "deep"]:
        for r in results[phase].values():
            if r.get("ok"):
                all_times.append(r["elapsed"])
    if jr and jr.get("ok"):
        all_times.append(jr["elapsed"])

    if all_times:
        import statistics
        print(f"\nLatency: mean={statistics.mean(all_times):.1f}s median={statistics.median(all_times):.1f}s "
              f"max={max(all_times):.1f}s CV={statistics.stdev(all_times)/statistics.mean(all_times):.2f}")


if __name__ == "__main__":
    main()
