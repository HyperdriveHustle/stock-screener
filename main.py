#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from runtime import config
from delivery.notifier import DiscordNotifier
from workflow.pipeline import run_screening


def setup_logging() -> logging.Logger:
    os.makedirs(config.SYSTEM["log_dir"], exist_ok=True)
    log_file = os.path.join(
        config.SYSTEM["log_dir"],
        f"screener_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    logging.basicConfig(
        level=getattr(logging, config.SYSTEM["log_level"]),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="美股短线选股系统 vNext")
    parser.add_argument("--test", action="store_true", help="测试 Discord Webhook 连接")
    parser.add_argument("--dry-run", action="store_true", help="仅本地输出，不推送 Discord")
    parser.add_argument("--tickers", type=str, default="", help="指定股票列表，逗号分隔")
    args = parser.parse_args()

    logger = setup_logging()
    if args.test:
        DiscordNotifier().send_test_message()
        return

    tickers_override = None
    if args.tickers:
        tickers_override = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()]
        logger.info("Ticker override: %s", tickers_override)

    try:
        run_screening(tickers_override=tickers_override, dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as exc:
        logger.exception("Screening failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
