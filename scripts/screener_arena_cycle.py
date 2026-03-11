#!/usr/bin/env python3
"""Orchestrate Screener Arena submit/sync/report tasks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
STOCK_SCREENER_ROOT = Path(__file__).resolve().parents[1]
ENV_CLAWBOT_ARENA_ROOT = os.getenv("CLAWBOT_ARENA_ROOT", "").strip()
CLAWBOT_ROOT = (
    Path(ENV_CLAWBOT_ARENA_ROOT).expanduser()
    if ENV_CLAWBOT_ARENA_ROOT
    else WORKSPACE_ROOT / "30-ideas" / "tasks" / "clawbot-arena"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Screener Arena workflow tasks")
    parser.add_argument("--python-bin", default=sys.executable, help="Python interpreter to use for child scripts")
    parser.add_argument("--clawbot-root", default=str(CLAWBOT_ROOT), help="Path to clawbot-arena repo")

    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="Submit picks to Arena and optionally place PM bets")
    submit.add_argument("--session-dir", required=True, help="Path to output/runs/<session_id>")
    submit.add_argument("--arena-url", required=True, help="Arena base URL")
    submit.add_argument("--arena-api-key", required=True, help="Screener bot API key for Arena")
    submit.add_argument("--poly-bot", help="Poly Arena bot id; if omitted, skip PM submit")
    submit.add_argument("--poly-arena-url", default="http://127.0.0.1:8046", help="Poly Arena base URL")
    submit.add_argument("--pm-scan-pages", type=int, default=8, help="Pages to scan in Polymarket discovery")

    sync = subparsers.add_parser("sync", help="Sync PM trades into Arena")
    sync.add_argument("--poly-arena-url", default="http://127.0.0.1:8046", help="Poly Arena base URL")
    sync.add_argument("--bot", action="append", dest="bots", help="Specific screener bot id to sync")
    sync.add_argument("--last-n", type=int, default=100, help="How many trades to fetch per bot")

    report = subparsers.add_parser("report", help="Generate screener markdown report")
    report.add_argument("--days", type=int, default=7, help="Window length in days")
    report.add_argument("--bot", help="Optional screener bot id")
    report.add_argument("--min-evaluated", type=int, default=1, help="Minimum evaluated picks to appear in report leaderboard")
    report.add_argument("--output", help="Write report to this path")
    report.add_argument("--stdout-only", action="store_true", help="Print report instead of writing file")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    clawbot_root = resolve_existing_dir(args.clawbot_root, "clawbot-arena root")
    if args.command == "submit":
        run_submit(args, clawbot_root)
        return
    if args.command == "sync":
        run_sync(args, clawbot_root)
        return
    run_report(args, clawbot_root)


def run_submit(args, clawbot_root: Path) -> None:
    bridge_script = STOCK_SCREENER_ROOT / "scripts" / "screener_bridge.py"
    run_command(
        [
            args.python_bin,
            str(bridge_script),
            "--session-dir",
            str(Path(args.session_dir).resolve()),
            "--arena-url",
            args.arena_url,
            "--api-key",
            args.arena_api_key,
        ]
    )
    if not args.poly_bot:
        return
    poly_script = STOCK_SCREENER_ROOT / "scripts" / "screener_to_poly.py"
    run_command(
        [
            args.python_bin,
            str(poly_script),
            "--session-dir",
            str(Path(args.session_dir).resolve()),
            "--bot",
            args.poly_bot,
            "--poly-arena-url",
            args.poly_arena_url,
            "--scan-pages",
            str(args.pm_scan_pages),
        ]
    )


def run_sync(args, clawbot_root: Path) -> None:
    sync_script = clawbot_root / "scripts" / "sync_screener_pm_bets.py"
    command = [
        args.python_bin,
        str(sync_script),
        "--poly-arena-url",
        args.poly_arena_url,
        "--last-n",
        str(args.last_n),
    ]
    for bot in args.bots or []:
        command.extend(["--bot", bot])
    run_command(command)


def run_report(args, clawbot_root: Path) -> None:
    report_script = clawbot_root / "scripts" / "generate_screener_report.py"
    command = [
        args.python_bin,
        str(report_script),
        "--days",
        str(args.days),
        "--min-evaluated",
        str(args.min_evaluated),
    ]
    if args.bot:
        command.extend(["--bot", args.bot])
    if args.output:
        command.extend(["--output", args.output])
    if args.stdout_only:
        command.append("--stdout-only")
    run_command(command)


def run_command(command: list[str]) -> None:
    print("$", " ".join(command))
    subprocess.run(command, check=True)


def resolve_existing_dir(raw_path: str, label: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(
            f"{label} not found: {path}. Pass --clawbot-root or set CLAWBOT_ARENA_ROOT."
        )
    return path


if __name__ == "__main__":
    main()
