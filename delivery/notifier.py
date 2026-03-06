"""
Discord Webhook 通知模块
将特征候选结果格式化后推送到 Discord 频道
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from runtime import config
from analysis.scorer import StockFeatures

logger = logging.getLogger(__name__)


def _market_now() -> datetime:
    """统一使用美东时间展示时间戳"""
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.utcnow()


def _fmt_cap(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:.0f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.2f}%"


class DiscordNotifier:
    """Discord Webhook 推送"""

    def __init__(self):
        self.webhook_url = config.DISCORD_WEBHOOK_URL
        self.colors = config.DISCORD

    def _send_webhook(self, payload: dict) -> bool:
        if not self.webhook_url:
            logger.warning("未配置 DISCORD_WEBHOOK_URL, 跳过推送")
            return False

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 204:
                return True
            logger.error(f"Discord 推送失败: HTTP {resp.status_code} - {resp.text}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Discord 推送异常: {e}")
            return False

    def send_stock_pool(
        self,
        stocks: list[StockFeatures],
        market_summary: dict | None = None,
    ):
        """
        发送完整的每日候选特征报告到 Discord
        """
        if not stocks:
            self._send_empty_report()
            return

        now = _market_now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M ET")

        header_embed = self._build_header_embed(date_str, time_str, stocks, market_summary)
        self._send_webhook({"embeds": [header_embed]})

        limit = min(len(stocks), config.DISCORD["max_stocks_in_message"])
        for i, stock in enumerate(stocks[:limit]):
            embed = self._build_stock_embed(i + 1, stock)
            success = self._send_webhook({"embeds": [embed]})
            if not success:
                logger.error(f"推送 {stock.ticker} 失败, 中止后续推送")
                break

        footer_embed = self._build_footer_embed()
        self._send_webhook({"embeds": [footer_embed]})
        logger.info(f"Discord 推送完成: {limit} 只股票")

    def _build_header_embed(
        self, date_str: str, time_str: str,
        stocks: list[StockFeatures], market_summary: dict | None
    ) -> dict:
        avg_priority = sum(s.technical_priority for s in stocks) / len(stocks)
        avg_coverage = sum((s.data_quality or {}).get("overall_coverage", 0) for s in stocks) / len(stocks)

        sectors = {}
        for s in stocks:
            sec = s.sector or "未知"
            sectors[sec] = sectors.get(sec, 0) + 1
        sector_str = " | ".join(f"{k}: {v}" for k, v in sorted(sectors.items(), key=lambda x: -x[1]))

        description = (
            f"📊 **生成时间:** {time_str}\n"
            f"📈 **候选数量:** {len(stocks)} 只\n"
            f"🧭 **平均技术优先级:** {avg_priority:.1f}\n"
            f"🧱 **平均信息覆盖度:** {avg_coverage:.2f}\n"
            f"🏭 **板块分布:** {sector_str}\n"
        )

        if market_summary:
            market_line = (
                f"\n**── 市场环境 ──**\n"
                f"SPY: {market_summary.get('spy_change', 0):+.2f}% | "
                f"VIX: {market_summary.get('vix', 0):.1f}"
            )
            spy_trend = market_summary.get("spy_trend")
            if spy_trend:
                market_line += f" | 趋势: {spy_trend}"
            description += market_line

        return {
            "title": f"🧩 美股特征候选池 — {date_str}",
            "description": description,
            "color": self.colors["embed_color_header"],
        }

    def _build_stock_embed(self, rank: int, s: StockFeatures) -> dict:
        if s.technical_priority >= 70:
            color = self.colors["embed_color_bullish"]
        elif s.technical_priority >= 50:
            color = self.colors["embed_color_neutral"]
        else:
            color = self.colors["embed_color_bearish"]

        tech = s.technical or {}
        price = tech.get("price", {})
        momentum = tech.get("momentum", {})
        vol = tech.get("volatility", {})
        rs = tech.get("relative_strength", {})
        vp = tech.get("volume_price", {})
        news = s.news or {}
        fm = s.fundamentals or {}
        val = s.valuation or {}
        pre = s.premarket or {}

        premarket_str = ""
        if pre.get("has_premarket") and pre.get("premarket_change_pct") is not None:
            chg = float(pre.get("premarket_change_pct"))
            premarket_str = f"\n🕘 盘前: ${pre.get('premarket_price', 0):.2f} ({chg:+.2f}%)"
        elif pre.get("regular_change_pct") is not None:
            premarket_str = f"\n🕘 常规盘参考: {_fmt_pct(pre.get('regular_change_pct'))}"

        tags_str = " ".join(f"`{t}`" for t in s.tags) if s.tags else ""
        market_cap = _fmt_cap(fm.get("market_cap"))

        fields = [
            {
                "name": "📈 技术快照",
                "value": (
                    f"```\n"
                    f"优先级:   {s.technical_priority:5.1f}\n"
                    f"收盘:     ${float(price.get('last_close') or 0):.2f}\n"
                    f"日涨跌:   {_fmt_pct(tech.get('daily_change_pct'))}\n"
                    f"RSI:      {float(momentum.get('rsi') or 0):.1f}\n"
                    f"量比:     {float(vp.get('volume_ratio') or 0):.2f}x\n"
                    f"ATR%:     {float(vol.get('atr_pct') or 0):.2f}%\n"
                    f"RS/SPY:   {_fmt_pct(rs.get('rs_vs_spy_20d_pct'))}\n"
                    f"```"
                ),
                "inline": True,
            },
            {
                "name": "📰 新闻快照",
                "value": (
                    f"```\n"
                    f"情绪:     {news.get('sentiment_label', 'neutral')}\n"
                    f"情绪分:   {float(news.get('sentiment_score') or 0):.1f}\n"
                    f"新闻数:   {int(news.get('article_count') or 0)}\n"
                    f"多头词:   {int(news.get('bullish_keyword_hits') or 0)}\n"
                    f"空头词:   {int(news.get('bearish_keyword_hits') or 0)}\n"
                    f"```"
                ),
                "inline": True,
            },
            {
                "name": "🏦 基本面快照",
                "value": (
                    f"```\n"
                    f"市值:     {market_cap}\n"
                    f"PE(TTM):  {val.get('trailing_pe')}\n"
                    f"PE(FWD):  {val.get('forward_pe')}\n"
                    f"营收增速: {fm.get('revenue_growth')}\n"
                    f"利润增速: {fm.get('earnings_growth')}\n"
                    f"负债权益: {fm.get('debt_to_equity')}\n"
                    f"```"
                ),
                "inline": True,
            },
        ]

        top_headline = news.get("top_headline", "")
        if top_headline:
            fields.append(
                {
                    "name": "🗞 最新头条",
                    "value": f"*{top_headline}*",
                    "inline": False,
                }
            )

        description = (
            f"**{s.company_name}** | {s.sector or 'N/A'} | {market_cap}"
            f"{premarket_str}\n"
            f"{tags_str}"
        )

        return {
            "title": f"#{rank} {s.ticker} — 技术优先级 {s.technical_priority:.1f}",
            "description": description,
            "color": color,
            "fields": fields,
        }

    def _build_footer_embed(self) -> dict:
        return {
            "description": (
                "⚠️ **风险提示**\n"
                "• 本系统输出的是结构化特征, 非直接投资建议\n"
                "• 建议结合你的 LLM 分析和交易纪律再决策\n"
                "• 短线交易风险高, 请严格控制仓位和止损"
            ),
            "color": 0x9E9E9E,
            "footer": {
                "text": "美股特征聚合系统 | Powered by yfinance + Finnhub + LLM-ready payload",
            },
        }

    def _send_empty_report(self):
        embed = {
            "title": f"🧩 美股特征候选池 — {_market_now().strftime('%Y-%m-%d')}",
            "description": (
                "⚠️ **今日无候选股票通过基础过滤**\n\n"
                "可能原因:\n"
                "• 价格/流动性/波动率过滤较严格\n"
                "• 数据可用性不足"
            ),
            "color": self.colors["embed_color_bearish"],
        }
        self._send_webhook({"embeds": [embed]})

    def send_test_message(self):
        payload = {
            "embeds": [
                {
                    "title": "✅ 测试消息",
                    "description": "Discord Webhook 连接成功! 特征聚合系统已就绪。",
                    "color": self.colors["embed_color_bullish"],
                    "footer": {"text": f"测试时间: {_market_now().strftime('%Y-%m-%d %H:%M:%S ET')}"},
                }
            ]
        }
        success = self._send_webhook(payload)
        if success:
            logger.info("Discord 测试消息发送成功")
        else:
            logger.error("Discord 测试消息发送失败")
        return success


def format_console_report(stocks: list[StockFeatures], market_summary: dict | None = None) -> str:
    """
    生成控制台文本报告 (同时用于保存到文件)
    """
    lines = []
    now = _market_now()

    lines.append("=" * 72)
    lines.append(f"  美股特征候选池 — {now.strftime('%Y-%m-%d %H:%M ET')}")
    lines.append("=" * 72)

    if market_summary:
        market_line = (
            f"  SPY: {market_summary.get('spy_change', 0):+.2f}%  |  "
            f"VIX: {market_summary.get('vix', 0):.1f}"
        )
        spy_trend = market_summary.get("spy_trend")
        if spy_trend:
            market_line += f"  |  趋势: {spy_trend}"
        lines.append(market_line)
        lines.append("-" * 72)

    if not stocks:
        lines.append("  ⚠️ 今日无候选股票通过基础过滤")
        lines.append("=" * 72)
        return "\n".join(lines)

    for i, s in enumerate(stocks):
        tech = s.technical or {}
        momentum = tech.get("momentum", {})
        vol = tech.get("volatility", {})
        rs = tech.get("relative_strength", {})
        vp = tech.get("volume_price", {})
        news = s.news or {}
        fm = s.fundamentals or {}

        lines.append(f"\n  #{i + 1}  {s.ticker:6s}  技术优先级: {s.technical_priority:.1f}")
        lines.append(f"  {s.company_name} | {s.sector or 'N/A'} | 市值: {_fmt_cap(fm.get('market_cap'))}")
        lines.append(
            f"  日涨跌: {_fmt_pct(tech.get('daily_change_pct'))}  "
            f"RSI: {float(momentum.get('rsi') or 0):.1f}  "
            f"量比: {float(vp.get('volume_ratio') or 0):.2f}x  "
            f"ATR%: {float(vol.get('atr_pct') or 0):.2f}%  "
            f"RS/SPY: {_fmt_pct(rs.get('rs_vs_spy_20d_pct'))}"
        )
        lines.append(
            f"  新闻: {news.get('sentiment_label', 'neutral')} "
            f"(score={news.get('sentiment_score', 50)}, count={news.get('article_count', 0)})"
        )
        if s.tags:
            lines.append(f"  标签: {', '.join(s.tags)}")
        headline = news.get("top_headline", "")
        if headline:
            lines.append(f"  头条: {headline}")
        lines.append(f"  信息覆盖度: {(s.data_quality or {}).get('overall_coverage', 0):.2f}")
        lines.append("-" * 72)

    lines.append(f"\n  总计: {len(stocks)} 只候选")
    lines.append("=" * 72)

    return "\n".join(lines)
