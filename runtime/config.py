"""
美股短线选股系统 - 配置文件
所有可调参数集中管理
"""

import os
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default

# ============================================================
# API Keys
# ============================================================
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

# ============================================================
# 股票池初始筛选条件
# ============================================================
UNIVERSE = {
    "min_price": 5.0,            # 最低股价 ($)
    "max_price": 500.0,          # 最高股价 ($)
    "min_avg_volume": 500_000,   # 最低 20 日日均成交量
    "min_market_cap": 3e8,       # 最低市值 ($300M)
}

# ============================================================
# 技术分析参数
# ============================================================
TECHNICAL = {
    # 均线周期
    "ma_periods": [5, 10, 20, 50, 200],

    # RSI
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,

    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Bollinger Bands
    "bb_period": 20,
    "bb_std": 2,

    # ATR
    "atr_period": 14,

    # Volume
    "volume_avg_period": 20,
    "volume_surge_threshold": 1.5,   # 量比 > 1.5 视为放量

    # 历史数据天数
    "lookback_days": 250,            # ~1 年交易日
}

# ============================================================
# 采集与特征输出限制
# ============================================================
FEATURES = {
    "max_candidates_for_llm": 30,          # 输入 LLM 的候选股票上限
    "max_news_articles_per_stock": 20,     # LLM payload 中每只股票最多携带新闻条数
}

# ============================================================
# 特征统计参数 (用于 EV/执行层/事实一致性输入)
# ============================================================
FEATURE_STATS = {
    "forward_horizons_days": [1, 3, 5, 10],
    "drawdown_windows_days": [20, 60, 120, 252],
    "market_linkage_windows_days": [20, 60],
    "execution_window_days": 20,
    "distribution_quantiles": [0.05, 0.25, 0.75, 0.95],
    "forward_quantiles": [0.10, 0.90],
    "ohlc_recent_days": 60,
    "support_resistance_window_days": 120,
    "support_resistance_max_levels": 8,
    "support_resistance_merge_tolerance_pct": 0.5,
}

# ============================================================
# 筛选阈值
# ============================================================
FILTER = {
    "min_technical_priority": 20,  # 技术优先级下限 (仅用于候选精简)
    "max_pool_size": 15,         # 股票池最大数量
    "min_pool_size": 3,          # 股票池最小数量 (低于此数不发送)
    "max_atr_pct": 8.0,          # ATR% 上限 (日波幅过大剔除)
    "min_atr_pct": 1.0,          # ATR% 下限 (波动太小不适合短线)
}

# ============================================================
# 盘前异动参数
# ============================================================
PREMARKET = {
    "gap_strong_threshold": 3.0,    # 跳空 >3% 视为强异动
    "gap_moderate_threshold": 1.5,  # 跳空 >1.5% 视为中等异动
    "volume_surge_threshold": 2.0,  # 盘前量比 >2x 视为异常
}

# ============================================================
# 新闻参数
# ============================================================
NEWS = {
    "lookback_hours": 48,           # 新闻回溯时间 (小时)
    "max_articles_per_stock": 20,   # 每只股票最多处理文章数
    "max_tickers_for_news": 100,    # 每轮执行新闻采集的股票上限
    "finnhub_rate_limit": 55,       # Finnhub 免费版每分钟请求数上限
    "market_news_categories": ["general", "forex", "merger"],  # 市场新闻类别
    "enable_provider_sentiment": True,
    "enable_semantic_tagging": _env_bool("ENABLE_SEMANTIC_TAGGING", True),
    "semantic_provider": "minimax",
    "semantic_model": "MiniMax-M2.5",
    "semantic_prompt_version": "news_semantic_v1",
    "semantic_endpoint": "https://api.minimaxi.com/v1/chat/completions",
    "semantic_timeout_seconds": 20,
    "semantic_max_articles_per_stock": _env_int("SEMANTIC_MAX_ARTICLES_PER_STOCK", 20),
    "semantic_enable_fallback": True,
}

# ============================================================
# 期权摘要参数
# ============================================================
OPTIONS = {
    "enabled": True,
    "expiries_limit": 3,
    "contracts_per_side_limit": 80,
    "max_unusual_contracts": 20,
}

# ============================================================
# 板块与同业上下文
# ============================================================
SECTOR_CONTEXT = {
    "max_peer_count": 10,
}

# ============================================================
# 事件日历参数
# ============================================================
EVENTS = {
    "future_days": 14,
    "max_macro_events": 60,
    "max_peer_events": 30,
    "max_company_events": 10,
}

# ============================================================
# Discord 输出格式
# ============================================================
DISCORD = {
    "max_stocks_in_message": 10,    # 单条消息最多展示股票数
    "embed_color_bullish": 0x00C853,   # 看涨绿色
    "embed_color_bearish": 0xFF1744,   # 看跌红色
    "embed_color_neutral": 0xFFAB00,   # 中性黄色
    "embed_color_header":  0x2962FF,   # 头部蓝色
}

# ============================================================
# 系统设置
# ============================================================
SYSTEM = {
    "log_level": "INFO",
    "data_dir": "data",
    "output_dir": "output",
    "log_dir": "logs",
    "max_workers": 8,               # 并发线程数
    "yfinance_batch_size": 50,      # yfinance 批量下载分组大小
    "retry_attempts": 3,            # API 失败重试次数
    "retry_delay": 2,               # 重试间隔 (秒)
}

# ============================================================
# 缓存策略 (SQLite)
# ============================================================
CACHE = {
    "db_file": "data/cache.db",
    "history_ttl_hours": 24,         # 日线行情缓存
    "ticker_info_ttl_minutes": 30,   # 实时信息缓存
    "company_news_ttl_minutes": 20,  # 个股新闻缓存
    "market_news_ttl_minutes": 30,   # 市场新闻缓存
    "news_sentiment_ttl_minutes": 60,
    "news_semantic_ttl_hours": 72,
    "options_ttl_minutes": 30,
    "economic_calendar_ttl_minutes": 60,
    "history_incremental_lookback_days": 7,  # 增量补拉回看窗口
}

# ============================================================
# LLM 输入包设置 (按股票单独调用)
# ============================================================
LLM = {
    "inputs_root_dir": "output/llm_inputs",
    "analysis_horizon": "1-2w",
    "model_profiles": ["tech", "news", "fund", "judge"],  # 每只股票可并行分析的角色
}

# ============================================================
# Symbol Registry / Session / Pipeline vNext
# ============================================================
SYMBOL_REGISTRY = {
    "master_file": "data/symbol_registry.json",
    "seed_from_fallback_on_missing": True,
}

SESSION_CONFIG = {
    "market_timezone": "America/New_York",
    "market_open_time": "09:30",
    "market_close_time": "16:00",
    "pre_market_label": "pre_market",
    "intraday_label": "intraday",
    "post_close_label": "post_close",
}

DATA_CONFIG = {
    "history_lookback_days": TECHNICAL["lookback_days"],
    "news_lookback_hours": NEWS["lookback_hours"],
    "max_company_news_per_symbol": NEWS["max_articles_per_stock"],
    "market_news_categories": list(NEWS["market_news_categories"]),
    "economic_calendar_days_ahead": EVENTS["future_days"],
}

BUDGET_CONFIG = {
    "max_symbols_for_news": 150,
    "max_symbols_for_triage": 80,
    "max_symbols_for_deep_analysis": 20,
    "final_selection_count": 10,
    "enable_live_llm": bool(MINIMAX_API_KEY),
    "llm_timeout_seconds": _env_int("LLM_TIMEOUT_SECONDS", 25),
}

PIPELINE_CONFIG = {
    "enabled_stages": {
        "market_context": True,
        "candidate_generators": True,
        "compact_triage": True,
        "deep_analysis": True,
        "cross_stock_judge": True,
        "outcome_store": True,
    },
    "schema_versions": {
        "run_manifest": "v1.run_manifest",
        "session_context": "v1.session_context",
        "compact_card": "v1.compact_card",
        "full_dossier": "v1.full_dossier",
        "trace": "v1.symbol_trace",
        "triage": "triage_v1",
        "deep_analysis": "deep_analysis_v1",
        "portfolio_judge": "portfolio_judge_v1",
        "candidate_set": "v1.candidate_set",
        "outcome_store": "v1.outcome_store",
    },
    "artifact_root_dir": "output/runs",
    "llm": {
        "provider": "minimax",
        "model": NEWS["semantic_model"],
        "endpoint": NEWS["semantic_endpoint"],
        "triage_prompt_version": "triage_v1",
        "deep_analysis_prompt_version": "deep_analysis_v1",
        "portfolio_judge_prompt_version": "portfolio_judge_v1",
    },
    "candidate_generators": {
        "premarket_dislocation": {
            "strong_gap_pct": PREMARKET["gap_strong_threshold"],
            "moderate_gap_pct": PREMARKET["gap_moderate_threshold"],
        },
        "news_attention": {
            "min_articles": 2,
            "high_impact_weighted_score": 20.0,
        },
        "event": {
            "near_term_days": 14,
        },
        "price_action": {
            "min_volume_ratio": TECHNICAL["volume_surge_threshold"],
            "min_relative_strength_pct": 5.0,
            "breakout_bonus": 1.0,
        },
        "sector_rotation": {
            "min_relative_strength_pct": 4.0,
            "min_peer_count": 2,
        },
    },
}

# ============================================================
# 市场上下文 (原始数据透传)
# ============================================================
MARKET_CONTEXT = {
    "return_windows_days": [1, 5, 20],
    "symbol_groups": {
        "equity": ["SPY", "QQQ", "IWM"],
        "sector_etf": ["SMH", "SOXX", "XLK", "XLF", "XLE", "XLV"],
        "volatility": ["^VIX"],
        "rates": ["^TNX"],
        "fx": ["DX-Y.NYB"],
    },
}

# ============================================================
# 自定义股票观察列表 (可选, 留空则使用自动获取的 S&P500)
# 如果填入股票代码, 系统将仅扫描这些股票
# ============================================================
CUSTOM_WATCHLIST = [
    # 示例: "AAPL", "MSFT", "NVDA", "TSLA"
    # 留空 [] 则自动获取 S&P 500 成分股
]
