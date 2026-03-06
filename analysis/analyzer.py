"""
分析引擎模块
技术分析 + 新闻情绪分析
"""

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
try:
    import pandas_ta as ta
except ImportError:
    from analysis import pandas_ta_compat as ta

from runtime import config

logger = logging.getLogger(__name__)


@dataclass
class TechnicalProfile:
    """单只股票的技术分析结果"""
    ticker: str

    # 价格信息
    last_close: float = 0.0
    prev_close: float = 0.0
    daily_change_pct: float = 0.0

    # 均线
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma50: float = 0.0
    ma200: float = 0.0
    ma_alignment_score: float = 0.0  # 0-100

    # 动量
    rsi: float = 50.0
    macd_value: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_cross: str = "none"  # "golden", "death", "none"
    momentum_score: float = 0.0  # 0-100

    # 量价
    volume_ratio: float = 1.0  # 当日量 / 20日均量
    obv_trend: str = "flat"   # "up", "down", "flat"
    volume_score: float = 0.0  # 0-100

    # 波动率
    atr: float = 0.0
    atr_pct: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_position: float = 0.5  # 0=下轨, 1=上轨

    # 相对强度
    rs_vs_spy: float = 0.0    # 相对 SPY 的超额收益 (20日)
    relative_strength_score: float = 0.0  # 0-100

    # 形态信号
    near_52w_high: bool = False
    near_52w_low: bool = False
    breakout_signal: bool = False

    # 综合技术得分
    technical_score: float = 0.0  # 0-100

    # 附加标签
    tags: list = field(default_factory=list)


class TechnicalAnalyzer:
    """技术分析引擎"""

    def __init__(self, spy_data: pd.DataFrame | None = None):
        """
        Args:
            spy_data: SPY 的 DataFrame, 用于计算相对强度
        """
        self.spy_data = spy_data
        self.cfg = config.TECHNICAL

    def analyze(self, ticker: str, df: pd.DataFrame) -> TechnicalProfile | None:
        """
        对单只股票执行完整的技术分析

        Args:
            ticker: 股票代码
            df: OHLCV DataFrame (需有 Open, High, Low, Close, Volume 列)

        Returns:
            TechnicalProfile 对象, 或 None (数据不足时)
        """
        if df is None or len(df) < 50:
            return None

        # 确保列名标准化 (yfinance 有时返回 MultiIndex)
        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # 确保必要列存在
        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(set(df.columns)):
            # 尝试小写
            df.columns = [c.capitalize() for c in df.columns]
            if not required_cols.issubset(set(df.columns)):
                return None

        profile = TechnicalProfile(ticker=ticker)

        try:
            self._calc_price_info(df, profile)
            self._calc_moving_averages(df, profile)
            self._calc_momentum(df, profile)
            self._calc_volume(df, profile)
            self._calc_volatility(df, profile)
            self._calc_relative_strength(df, profile)
            self._calc_patterns(df, profile)
            self._calc_final_score(profile)
        except Exception as e:
            logger.debug(f"分析 {ticker} 时出错: {e}")
            return None

        return profile

    def _calc_price_info(self, df: pd.DataFrame, p: TechnicalProfile):
        """基本价格信息"""
        p.last_close = float(df["Close"].iloc[-1])
        p.prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else p.last_close
        if p.prev_close > 0:
            p.daily_change_pct = ((p.last_close - p.prev_close) / p.prev_close) * 100

    def _calc_moving_averages(self, df: pd.DataFrame, p: TechnicalProfile):
        """均线计算与排列评分"""
        close = df["Close"]

        mas = {}
        for period in self.cfg["ma_periods"]:
            if len(close) >= period:
                mas[period] = float(close.rolling(period).mean().iloc[-1])
            else:
                mas[period] = float("nan")

        p.ma5 = mas.get(5, 0)
        p.ma10 = mas.get(10, 0)
        p.ma20 = mas.get(20, 0)
        p.ma50 = mas.get(50, 0)
        p.ma200 = mas.get(200, 0)

        # 均线排列评分 (多头排列 = 高分)
        score = 0
        price = p.last_close

        # 价格在各均线之上各加分
        if price > p.ma5 > 0:
            score += 5
        if price > p.ma10 > 0:
            score += 5
        if price > p.ma20 > 0:
            score += 10
        if price > p.ma50 > 0:
            score += 15
        if price > p.ma200 > 0:
            score += 15

        # 短期均线在长期均线之上
        if p.ma5 > p.ma20 > 0:
            score += 10
        if p.ma10 > p.ma50 > 0:
            score += 10
        if p.ma20 > p.ma50 > 0:
            score += 10
        if p.ma50 > p.ma200 > 0:
            score += 20

        # MA20 斜率 (最近5日变化)
        if len(close) >= 25:
            ma20_now = float(close.rolling(20).mean().iloc[-1])
            ma20_5ago = float(close.rolling(20).mean().iloc[-6])
            if ma20_5ago > 0:
                slope = (ma20_now - ma20_5ago) / ma20_5ago
                if slope > 0.01:
                    p.tags.append("MA20↑")

        p.ma_alignment_score = min(score, 100)

    def _calc_momentum(self, df: pd.DataFrame, p: TechnicalProfile):
        """RSI + MACD 动量计算"""
        close = df["Close"]

        # RSI
        rsi_series = ta.rsi(close, length=self.cfg["rsi_period"])
        if rsi_series is not None and len(rsi_series) > 0:
            p.rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50

        # MACD
        macd_df = ta.macd(
            close,
            fast=self.cfg["macd_fast"],
            slow=self.cfg["macd_slow"],
            signal=self.cfg["macd_signal"],
        )
        if macd_df is not None and not macd_df.empty:
            cols = macd_df.columns
            p.macd_value = float(macd_df[cols[0]].iloc[-1]) if not pd.isna(macd_df[cols[0]].iloc[-1]) else 0
            p.macd_signal = float(macd_df[cols[2]].iloc[-1]) if not pd.isna(macd_df[cols[2]].iloc[-1]) else 0
            p.macd_histogram = float(macd_df[cols[1]].iloc[-1]) if not pd.isna(macd_df[cols[1]].iloc[-1]) else 0

            # 检测金叉/死叉
            if len(macd_df) >= 2:
                prev_hist = float(macd_df[cols[1]].iloc[-2]) if not pd.isna(macd_df[cols[1]].iloc[-2]) else 0
                if prev_hist <= 0 < p.macd_histogram:
                    p.macd_cross = "golden"
                    p.tags.append("MACD金叉")
                elif prev_hist >= 0 > p.macd_histogram:
                    p.macd_cross = "death"
                    p.tags.append("MACD死叉")

        # 动量综合评分
        score = 0

        # RSI 评分
        if 40 <= p.rsi <= 60:
            score += 15  # 中性区间, 有空间
        elif 30 <= p.rsi < 40:
            score += 25  # 超跌反弹区
            p.tags.append("RSI超卖区回升")
        elif 60 < p.rsi <= 70:
            score += 20  # 强势但未过热
        elif p.rsi < 30:
            score += 20  # 深度超卖
            p.tags.append("RSI深度超卖")
        elif p.rsi > 80:
            score += 0   # 严重过热
            p.tags.append("RSI过热")
        else:
            score += 10  # 70-80 轻微过热

        # MACD 评分
        if p.macd_cross == "golden":
            score += 30
        elif p.macd_histogram > 0 and p.macd_value > 0:
            score += 20  # MACD 在零线上且柱状图为正
        elif p.macd_histogram > 0:
            score += 15
        elif p.macd_cross == "death":
            score += 0

        # MACD 柱状图动量方向
        if macd_df is not None and len(macd_df) >= 3:
            recent_hist = macd_df[cols[1]].tail(3).values
            recent_hist = [float(x) if not pd.isna(x) else 0 for x in recent_hist]
            if recent_hist[-1] > recent_hist[-2] > recent_hist[-3]:
                score += 10  # 连续放大
                p.tags.append("MACD动量增强")

        p.momentum_score = min(score, 100)

    def _calc_volume(self, df: pd.DataFrame, p: TechnicalProfile):
        """量价分析"""
        volume = df["Volume"]
        close = df["Close"]

        # 量比
        avg_vol = float(volume.tail(self.cfg["volume_avg_period"]).mean())
        last_vol = float(volume.iloc[-1])
        p.volume_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        # OBV 趋势
        obv = ta.obv(close, volume)
        if obv is not None and len(obv) >= 20:
            obv_ma = float(obv.tail(20).mean())
            obv_now = float(obv.iloc[-1])
            if obv_now > obv_ma * 1.02:
                p.obv_trend = "up"
            elif obv_now < obv_ma * 0.98:
                p.obv_trend = "down"
            else:
                p.obv_trend = "flat"

        # 量价评分
        score = 0

        # 量比评分
        if p.volume_ratio > 3.0:
            score += 30
            p.tags.append("巨量")
        elif p.volume_ratio > config.TECHNICAL["volume_surge_threshold"]:
            score += 20
            p.tags.append("放量")
        elif p.volume_ratio > 1.0:
            score += 10
        else:
            score += 5  # 缩量

        # 量价配合: 上涨放量加分, 下跌放量减分
        if p.daily_change_pct > 0 and p.volume_ratio > 1.2:
            score += 25  # 上涨放量
            p.tags.append("量价齐升")
        elif p.daily_change_pct < 0 and p.volume_ratio > 1.5:
            score -= 10  # 下跌放量 (不好)

        # OBV 趋势
        if p.obv_trend == "up":
            score += 20
        elif p.obv_trend == "down":
            score -= 5

        # 最近 5 日量能趋势
        if len(volume) >= 5:
            vol_5d = volume.tail(5).values
            if all(vol_5d[i] <= vol_5d[i + 1] for i in range(len(vol_5d) - 1)):
                score += 10  # 连续放量
                p.tags.append("连续放量")

        p.volume_score = max(0, min(score, 100))

    def _calc_volatility(self, df: pd.DataFrame, p: TechnicalProfile):
        """波动率与布林带"""
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        # ATR
        atr_series = ta.atr(high, low, close, length=self.cfg["atr_period"])
        if atr_series is not None and len(atr_series) > 0:
            p.atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0
            p.atr_pct = round((p.atr / p.last_close) * 100, 2) if p.last_close > 0 else 0

        # Bollinger Bands
        bb = ta.bbands(close, length=self.cfg["bb_period"], std=self.cfg["bb_std"])
        if bb is not None and not bb.empty:
            cols = bb.columns
            p.bb_upper = float(bb[cols[2]].iloc[-1]) if not pd.isna(bb[cols[2]].iloc[-1]) else 0
            p.bb_lower = float(bb[cols[0]].iloc[-1]) if not pd.isna(bb[cols[0]].iloc[-1]) else 0

            if p.bb_upper > p.bb_lower > 0:
                p.bb_position = (p.last_close - p.bb_lower) / (p.bb_upper - p.bb_lower)
                p.bb_position = max(0, min(1, p.bb_position))

                # 布林带收窄后突破
                if len(bb) >= 20:
                    bw_series = (bb[cols[2]] - bb[cols[0]]) / bb[cols[1]]
                    bw_now = float(bw_series.iloc[-1]) if not pd.isna(bw_series.iloc[-1]) else 999
                    bw_mean = float(bw_series.tail(20).mean()) if not pd.isna(bw_series.tail(20).mean()) else 999
                    if bw_now < bw_mean * 0.8:
                        p.tags.append("布林收窄")

    def _calc_relative_strength(self, df: pd.DataFrame, p: TechnicalProfile):
        """相对强度 vs SPY"""
        if self.spy_data is None or len(self.spy_data) < 20:
            p.relative_strength_score = 50
            return

        close = df["Close"]
        spy_close = self.spy_data["Close"]

        # 对齐日期
        common_idx = close.index.intersection(spy_close.index)
        if len(common_idx) < 20:
            p.relative_strength_score = 50
            return

        stock_ret_20d = (float(close.loc[common_idx[-1]]) / float(close.loc[common_idx[-20]]) - 1) * 100
        spy_ret_20d = (float(spy_close.loc[common_idx[-1]]) / float(spy_close.loc[common_idx[-20]]) - 1) * 100

        p.rs_vs_spy = round(stock_ret_20d - spy_ret_20d, 2)

        # RS 评分
        if p.rs_vs_spy > 10:
            p.relative_strength_score = 100
            p.tags.append("极强RS")
        elif p.rs_vs_spy > 5:
            p.relative_strength_score = 80
            p.tags.append("强RS")
        elif p.rs_vs_spy > 2:
            p.relative_strength_score = 65
        elif p.rs_vs_spy > 0:
            p.relative_strength_score = 55
        elif p.rs_vs_spy > -5:
            p.relative_strength_score = 35
        else:
            p.relative_strength_score = 15

    def _calc_patterns(self, df: pd.DataFrame, p: TechnicalProfile):
        """关键价位与形态识别"""
        close = df["Close"]
        high = df["High"]

        # 52 周高低点
        if len(close) >= 200:
            high_52w = float(high.tail(252).max())
            low_52w = float(df["Low"].tail(252).min())

            # 距 52 周新高不到 5%
            if p.last_close > high_52w * 0.95:
                p.near_52w_high = True
                p.tags.append("近52周新高")

            # 距 52 周新低不到 10%
            if p.last_close < low_52w * 1.10:
                p.near_52w_low = True
                p.tags.append("近52周新低")

        # 突破信号: 收盘价突破 20 日最高价
        if len(high) >= 20:
            high_20d = float(high.tail(21).iloc[:-1].max())  # 排除最近一天
            if p.last_close > high_20d and p.volume_ratio > 1.2:
                p.breakout_signal = True
                p.tags.append("突破20日高点")

    def _calc_final_score(self, p: TechnicalProfile):
        """计算技术面综合得分"""
        p.technical_score = round(
            p.ma_alignment_score * 0.25
            + p.momentum_score * 0.25
            + p.volume_score * 0.25
            + p.relative_strength_score * 0.25,
            1,
        )


class NewsSentimentAnalyzer:
    """
    基于关键词的新闻情绪分析 (MVP 版本)
    生产环境建议替换为 FinBERT 或 LLM API
    """

    # 看涨关键词 (英文)
    BULLISH_KEYWORDS = [
        "beat", "beats", "exceeded", "surpass", "upgrade", "upgraded",
        "outperform", "buy", "bullish", "strong", "surge", "surged",
        "soar", "soared", "rally", "rallied", "record", "growth",
        "positive", "profit", "boost", "boosted", "raise", "raised",
        "guidance", "upside", "breakout", "innovation", "launch",
        "partnership", "deal", "acquisition", "expand", "momentum",
        "optimistic", "accelerat", "dividend", "buyback", "approve",
        "breakthrough", "milestone", "exceed", "impressive", "robust",
    ]

    # 看跌关键词 (英文)
    BEARISH_KEYWORDS = [
        "miss", "missed", "below", "downgrade", "downgraded",
        "underperform", "sell", "bearish", "weak", "decline",
        "plunge", "plunged", "crash", "fall", "fell", "loss",
        "negative", "warning", "cut", "layoff", "lawsuit",
        "investigation", "recall", "delay", "risk", "concern",
        "disappoint", "disappointing", "slash", "slashed",
        "bankruptcy", "default", "fraud", "scandal", "fine",
        "penalty", "probe", "subpoena", "delist",
    ]

    def analyze_articles(self, articles: list[dict]) -> dict:
        """
        分析一组新闻文章的情绪

        Returns:
            {
                score: 0-100,
                sentiment: "bullish" / "bearish" / "neutral",
                article_count: int,
                top_headline: str,
                bullish_count: int,
                bearish_count: int,
            }
        """
        if not articles:
            return {
                "score": 50,
                "sentiment": "neutral",
                "article_count": 0,
                "top_headline": "",
                "bullish_count": 0,
                "bearish_count": 0,
            }

        bullish_hits = 0
        bearish_hits = 0
        total_articles = len(articles)

        for article in articles:
            text = (
                (article.get("headline", "") + " " + article.get("summary", ""))
                .lower()
            )

            for kw in self.BULLISH_KEYWORDS:
                if kw in text:
                    bullish_hits += 1

            for kw in self.BEARISH_KEYWORDS:
                if kw in text:
                    bearish_hits += 1

        # 计算得分
        total_hits = bullish_hits + bearish_hits
        if total_hits == 0:
            score = 50  # 中性
        else:
            bull_ratio = bullish_hits / total_hits
            score = round(bull_ratio * 100)

        # 新闻量加成: 有大量新闻说明关注度高, 轻微加分
        if total_articles >= 10:
            score = min(100, score + 5)
        elif total_articles >= 5:
            score = min(100, score + 2)

        # 分类
        if score >= 60:
            sentiment = "bullish"
        elif score <= 40:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        top_headline = articles[0].get("headline", "") if articles else ""

        return {
            "score": score,
            "sentiment": sentiment,
            "article_count": total_articles,
            "top_headline": top_headline,
            "bullish_count": bullish_hits,
            "bearish_count": bearish_hits,
        }
