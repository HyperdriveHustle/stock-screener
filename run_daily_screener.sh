#!/bin/bash

# ==============================================================================
# 每天定时运行指定股票筛选的入口脚本
# ==============================================================================

# 加载用户环境变量
source ~/.bash_profile 2>/dev/null || source ~/.zshrc 2>/dev/null || true

PROJECT_DIR="/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener"
cd "$PROJECT_DIR" || exit 1

# 直接使用相对路径的虚拟环境 python 解释器
# 因为您的项目可能移动过目录，导致 .venv/bin/activate 里面的硬编码路径失效。
# 直接调用 .venv/bin/python 是最安全的做法。
PYTHON_BIN=".venv/bin/python"

# 你的自选股列表:
# AAPL (苹果), TSLA (特斯拉), NVDA (英伟达), GOOG (谷歌), MSFT (微软)
# AMZN (亚马逊), WDC (西部数据/闪迪母公司), UNH (联合健康), MCD (麦当劳)
TICKERS="AAPL,TSLA,NVDA,GOOG,MSFT,AMZN,WDC,UNH,MCD"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始运行短线选股系统..."

# 执行选股主程序
"$PYTHON_BIN" main.py --tickers "$TICKERS"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 选股系统运行结束。"
