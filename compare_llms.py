#!/usr/bin/env python3
"""Compare LLM outputs across models using existing run data.

Usage:
  .venv/bin/python3 compare_llms.py                           # all models, latest run
  .venv/bin/python3 compare_llms.py --models qwen3.5-plus     # single model
  .venv/bin/python3 compare_llms.py --models kimi-k2.5,glm-5  # specific models
  .venv/bin/python3 compare_llms.py --run <run_dir>           # specific run
  .venv/bin/python3 compare_llms.py --merge prev.json         # merge new results into existing
"""

import argparse
import json
import os
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Config ──────────────────────────────────────────────────────

MODELS = {
    "minimax": {
        "provider": "openai_compat",
        "provider_url": "https://api.minimaxi.com/v1/chat/completions",
        "api_key": os.environ.get("MINIMAX_API_KEY", ""),
        "model": "MiniMax-M2.5",
    },
    "kimi-k2.5": {
        "provider": "openai_compat",
        "provider_url": "https://coding.dashscope.aliyuncs.com/v1/chat/completions",
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "model": "kimi-k2.5",
    },
    "glm-5": {
        "provider": "openai_compat",
        "provider_url": "https://coding.dashscope.aliyuncs.com/v1/chat/completions",
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "model": "glm-5",
    },
    "qwen3.5-plus": {
        "provider": "openai_compat",
        "provider_url": "https://coding.dashscope.aliyuncs.com/v1/chat/completions",
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "model": "qwen3.5-plus",
    },
    "gemini-3.1-pro": {
        "provider": "gemini",
        "api_key": os.environ.get("GEMINI_API_KEY", ""),
        "model": "gemini-3.1-pro-preview",
    },
}

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

OUTPUT_DIR = "output/llm_compare"
MAX_WORKERS_PER_MODEL = 4  # parallel calls within a model
MAX_WORKERS_MODELS = 2     # parallel models (conservative to avoid rate limits)


# ── Helpers ─────────────────────────────────────────────────────

def _find_latest_run() -> str:
    runs_dir = "output/runs"
    if not os.path.isdir(runs_dir):
        raise FileNotFoundError(f"No runs directory: {runs_dir}")
    entries = sorted(os.listdir(runs_dir), reverse=True)
    for entry in entries:
        candidate = os.path.join(runs_dir, entry)
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "run_manifest.json")):
            return candidate
    raise FileNotFoundError("No valid run found")


def _discover_tickers(run_dir: str) -> list[str]:
    symbols_dir = os.path.join(run_dir, "symbols")
    if not os.path.isdir(symbols_dir):
        return []
    tickers = []
    for name in sorted(os.listdir(symbols_dir)):
        if os.path.isdir(os.path.join(symbols_dir, name)):
            tickers.append(name)
    return tickers


# ── LLM Call ────────────────────────────────────────────────────

def call_llm(cfg: dict, system_prompt: str, user_payload: dict, timeout: int = 240) -> dict:
    if not cfg.get("api_key"):
        return {"ok": False, "error": "no_api_key", "elapsed": 0}

    if cfg.get("provider") == "gemini":
        return _call_gemini(cfg, system_prompt, user_payload, timeout)

    body = json.dumps({
        "model": cfg["model"],
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }).encode()

    req = urllib.request.Request(cfg["provider_url"], data=body, headers={
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())
        elapsed = round(time.time() - t0, 1)
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        parsed = _first_json(content)
        return {"ok": True, "data": parsed, "elapsed": elapsed, "usage": usage}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        return {"ok": False, "error": str(e)[:200], "elapsed": elapsed}


def _call_gemini(cfg: dict, system_prompt: str, user_payload: dict, timeout: int) -> dict:
    """Call Gemini native API (generateContent)."""
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model = cfg["model"]
    api_key = cfg["api_key"]
    url = f"{base_url}/models/{model}:generateContent?key={api_key}"

    user_text = json.dumps(user_payload, ensure_ascii=False)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4000,
            "responseMimeType": "application/json",
        },
    }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())
        elapsed = round(time.time() - t0, 1)
        content = result["candidates"][0]["content"]["parts"][0]["text"]
        usage_meta = result.get("usageMetadata", {})
        usage = {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0),
        }
        parsed = _first_json(content)
        return {"ok": True, "data": parsed, "elapsed": elapsed, "usage": usage}
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
        # If top-level is a list, take first dict element
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
                    return json.loads(text[start:i+1])
    return {}


# ── Single-model runner ─────────────────────────────────────────

def _run_single_model(
    model_name: str,
    cfg: dict,
    triage_requests: dict,
    deep_requests: dict,
    market_ctx: dict,
    tickers: list[str],
) -> dict:
    """Run all phases for one model with parallel triage/deep calls."""
    print(f"\n{'='*60}")
    print(f"  MODEL: {model_name}")
    print(f"{'='*60}")

    model_results = {"triage": {}, "deep": {}, "judge": None}

    # ── Phase 1: Parallel Triage ──
    triage_jobs = {}
    for t in tickers:
        if t not in triage_requests:
            continue
        msgs = triage_requests[t].get("messages", [])
        if len(msgs) >= 2:
            triage_jobs[t] = json.loads(msgs[1]["content"])

    if triage_jobs:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_PER_MODEL) as pool:
            futures = {
                pool.submit(call_llm, cfg, TRIAGE_SYSTEM, payload): ticker
                for ticker, payload in triage_jobs.items()
            }
            for future in as_completed(futures):
                t = futures[future]
                result = future.result()
                model_results["triage"][t] = result
                if result["ok"]:
                    v = result["data"].get("triage_verdict", "?")
                    c = result["data"].get("triage_confidence", "?")
                    print(f"  [{model_name}] Triage {t}: {v} (conf={c}) [{result['elapsed']}s]")
                else:
                    print(f"  [{model_name}] Triage {t}: ERROR {result['error'][:60]} [{result['elapsed']}s]")

    # ── Phase 2: Parallel Deep ──
    deep_jobs = {}
    for t in tickers:
        if t not in deep_requests:
            continue
        msgs = deep_requests[t].get("messages", [])
        if len(msgs) >= 2:
            deep_jobs[t] = json.loads(msgs[1]["content"])

    if deep_jobs:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_PER_MODEL) as pool:
            futures = {
                pool.submit(call_llm, cfg, DEEP_SYSTEM, payload): ticker
                for ticker, payload in deep_jobs.items()
            }
            for future in as_completed(futures):
                t = futures[future]
                result = future.result()
                model_results["deep"][t] = result
                if result["ok"]:
                    setup = result["data"].get("setup_type", "?")
                    c = result["data"].get("confidence", "?")
                    print(f"  [{model_name}] Deep {t}: {setup} (conf={c}) [{result['elapsed']}s]")
                else:
                    print(f"  [{model_name}] Deep {t}: ERROR {result['error'][:60]} [{result['elapsed']}s]")

    # ── Phase 3: Judge (must wait for all deep) ──
    deep_for_judge = {}
    for t, r in model_results["deep"].items():
        if r.get("ok") and r.get("data"):
            deep_for_judge[t] = r["data"]

    if deep_for_judge:
        judge_payload = {
            "selection_count": 5,
            "market_context_compact": market_ctx,
            "deep_analysis_by_ticker": deep_for_judge,
        }
        print(f"  [{model_name}] Judge ({len(deep_for_judge)} candidates)...", flush=True)
        result = call_llm(cfg, JUDGE_SYSTEM, judge_payload, timeout=240)
        if result["ok"]:
            top = result["data"].get("final_top_n", [])
            print(f"  [{model_name}] Judge: top={top} [{result['elapsed']}s]")
        else:
            print(f"  [{model_name}] Judge: ERROR {result['error'][:60]} [{result['elapsed']}s]")
        model_results["judge"] = result

    return model_results


# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=str, default="", help="Run directory path")
    parser.add_argument("--models", type=str, default="", help="Comma-separated model names (default: all)")
    parser.add_argument("--merge", type=str, default="", help="Merge results into existing JSON file")
    parser.add_argument("--serial", action="store_true", help="Disable parallel model execution")
    args = parser.parse_args()

    run_dir = args.run or _find_latest_run()
    tickers = _discover_tickers(run_dir)
    print(f"Run: {run_dir}")
    print(f"Tickers: {tickers}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load API keys from .env if not in environment
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        env_vars = {}
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
        if not MODELS["minimax"]["api_key"] and env_vars.get("MINIMAX_API_KEY"):
            MODELS["minimax"]["api_key"] = env_vars["MINIMAX_API_KEY"]
        dashscope_key = env_vars.get("DASHSCOPE_API_KEY", "")
        if dashscope_key:
            for name in ("kimi-k2.5", "glm-5", "qwen3.5-plus"):
                if not MODELS[name]["api_key"]:
                    MODELS[name]["api_key"] = dashscope_key
        if not MODELS["gemini-3.1-pro"]["api_key"] and env_vars.get("GEMINI_API_KEY"):
            MODELS["gemini-3.1-pro"]["api_key"] = env_vars["GEMINI_API_KEY"]

    # Select models
    if args.models:
        selected = [m.strip() for m in args.models.split(",")]
        run_models = {k: v for k, v in MODELS.items() if k in selected}
        if not run_models:
            print(f"ERROR: No matching models. Available: {', '.join(MODELS.keys())}")
            return
    else:
        run_models = MODELS

    # Load existing artifacts
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

    print(f"Loaded: {len(triage_requests)} triage requests, {len(deep_requests)} deep requests")
    print(f"Models: {', '.join(run_models.keys())}")
    parallel_mode = "serial" if args.serial else f"parallel (models={MAX_WORKERS_MODELS}, calls={MAX_WORKERS_PER_MODEL})"
    print(f"Execution: {parallel_mode}")
    print("=" * 60)

    all_results = {}
    t_start = time.time()

    if args.serial or len(run_models) == 1:
        # Serial model execution
        for model_name, cfg in run_models.items():
            all_results[model_name] = _run_single_model(
                model_name, cfg, triage_requests, deep_requests, market_ctx, tickers,
            )
    else:
        # Parallel model execution
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_MODELS) as pool:
            futures = {
                pool.submit(
                    _run_single_model,
                    model_name, cfg, triage_requests, deep_requests, market_ctx, tickers,
                ): model_name
                for model_name, cfg in run_models.items()
            }
            for future in as_completed(futures):
                model_name = futures[future]
                all_results[model_name] = future.result()

    total_elapsed = round(time.time() - t_start, 1)
    print(f"\nTotal wall time: {total_elapsed}s")

    # Merge with existing results if requested
    if args.merge and os.path.exists(args.merge):
        existing = json.load(open(args.merge))
        existing.update(all_results)
        all_results = existing
        print(f"Merged with {args.merge} ({len(all_results)} models total)")

    # Save results
    output_path = os.path.join(OUTPUT_DIR, "comparison_v3.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"Results saved to {output_path}")

    # Print summary
    _print_summary(all_results, tickers)


def _print_summary(all_results: dict, tickers: list[str]):
    models = list(all_results.keys())
    col_w = 22

    print(f"\n{'='*100}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*100}")

    # Triage
    print(f"\n{'─'*100}")
    print("  TRIAGE VERDICTS")
    print(f"{'─'*100}")
    header = f"{'Ticker':<8}"
    for m in models:
        header += f"  {m:<{col_w}}"
    print(header)
    for t in tickers:
        row = f"{t:<8}"
        for m in models:
            r = all_results[m]["triage"].get(t, {})
            if r.get("ok"):
                v = str(r["data"].get("triage_verdict", "?"))
                c = str(r["data"].get("triage_confidence", "?"))
                cell = f"{v}({c})"
                row += f"  {cell:<{col_w}}"
            else:
                row += f"  {'SKIP':<{col_w}}"
        print(row)

    # Deep
    print(f"\n{'─'*100}")
    print("  DEEP ANALYSIS")
    print(f"{'─'*100}")
    header = f"{'Ticker':<8}"
    for m in models:
        header += f"  {m:<{col_w}}"
    print(header)
    for t in tickers:
        row = f"{t:<8}"
        for m in models:
            r = all_results[m]["deep"].get(t, {})
            if r.get("ok"):
                setup = str(r["data"].get("setup_type", "?"))[:14]
                c = str(r["data"].get("confidence", "?"))
                cell = f"{setup}({c})"
                row += f"  {cell:<{col_w}}"
            elif r:
                row += f"  {'ERROR':<{col_w}}"
            else:
                row += f"  {'SKIP':<{col_w}}"
        print(row)

    # Judge
    print(f"\n{'─'*100}")
    print("  FINAL JUDGE TOP PICKS")
    print(f"{'─'*100}")
    for m in models:
        r = all_results[m].get("judge") or {}
        if r.get("ok"):
            ranked = r["data"].get("ranked_candidates", [])
            picks = []
            for rc in ranked[:5]:
                picks.append(f"{rc.get('ticker','?')}(#{rc.get('final_rank','?')})")
            top = r["data"].get("final_top_n", [])
            print(f"  {m:<{col_w}}: {', '.join(picks) or top}")
        else:
            err = r.get("error", "no judge")
            print(f"  {m:<{col_w}}: ERROR - {str(err)[:60]}")

    # Timing
    print(f"\n{'─'*100}")
    print("  TIMING (seconds)")
    print(f"{'─'*100}")
    for m in models:
        triage_t = sum(r.get("elapsed", 0) for r in all_results[m]["triage"].values())
        deep_t = sum(r.get("elapsed", 0) for r in all_results[m]["deep"].values())
        judge_t = (all_results[m].get("judge") or {}).get("elapsed", 0)
        total = round(triage_t + deep_t + judge_t, 1)
        print(f"  {m:<{col_w}}: triage={triage_t:.0f}s  deep={deep_t:.0f}s  judge={judge_t:.0f}s  total={total}s")


if __name__ == "__main__":
    main()
