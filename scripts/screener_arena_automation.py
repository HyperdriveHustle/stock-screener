#!/usr/bin/env python3
"""Automation-friendly wrapper for Screener Arena tasks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


STOCK_SCREENER_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = STOCK_SCREENER_ROOT.parents[2]
ENV_CLAWBOT_ARENA_ROOT = os.getenv("CLAWBOT_ARENA_ROOT", "").strip()
DEFAULT_CLAWBOT_ROOT = (
    Path(ENV_CLAWBOT_ARENA_ROOT).expanduser()
    if ENV_CLAWBOT_ARENA_ROOT
    else WORKSPACE_ROOT / "30-ideas" / "tasks" / "clawbot-arena"
)
DEFAULT_RUNS_DIR = STOCK_SCREENER_ROOT / "output" / "runs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automation wrapper for Screener Arena")
    parser.add_argument("--python-bin", default=sys.executable, help="Python interpreter used for child scripts")
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR), help="Path to stock-screener output/runs")
    parser.add_argument("--clawbot-root", default=str(DEFAULT_CLAWBOT_ROOT), help="Path to clawbot-arena repo")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")

    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit-latest", help="Submit the latest valid session to Arena")
    submit.add_argument("--run-prefix", help="Only consider sessions with this prefix")
    submit.add_argument("--allow-empty", action="store_true", help="Allow sessions whose final_top_n is empty")
    submit.add_argument("--poly-bot", help="Override SCREENER_POLY_BOT for this run")
    submit.add_argument("--pm-scan-pages", type=int, default=8, help="Polymarket discovery pages")
    submit.add_argument("--dry-run", action="store_true", help="Print commands without executing them")

    maintenance = subparsers.add_parser("maintenance", help="Run Arena backfill and PM sync")
    maintenance.add_argument("--skip-backfill", action="store_true", help="Skip daily bar backfill")
    maintenance.add_argument("--skip-pm-sync", action="store_true", help="Skip Polymarket sync")
    maintenance.add_argument("--pm-last-n", type=int, default=100, help="Number of recent PM trades to sync per bot")
    maintenance.add_argument("--dry-run", action="store_true", help="Print commands without executing them")

    report = subparsers.add_parser("report", help="Generate Screener markdown report")
    report.add_argument("--days", type=int, default=7, help="Window length in days")
    report.add_argument("--bot", help="Optional screener bot id")
    report.add_argument("--min-evaluated", type=int, default=1, help="Minimum evaluated picks in leaderboard section")
    report.add_argument("--output", help="Optional explicit output path")
    report.add_argument("--stdout-only", action="store_true", help="Print report to stdout")
    report.add_argument("--dry-run", action="store_true", help="Print commands without executing them")

    return parser


def main() -> None:
    load_local_env(STOCK_SCREENER_ROOT / ".env")
    args = build_parser().parse_args()
    if args.command == "submit-latest":
        run_submit_latest(args)
        return
    if args.command == "maintenance":
        run_maintenance(args)
        return
    run_report(args)


def run_submit_latest(args) -> None:
    arena_url = require_env("SCREENER_ARENA_URL")
    arena_api_key = require_env("SCREENER_ARENA_API_KEY")
    session_dir = find_latest_session(Path(args.runs_dir), args.run_prefix, allow_empty=args.allow_empty)
    clawbot_root = resolve_existing_dir(args.clawbot_root, "clawbot-arena root")
    command = [
        args.python_bin,
        str(STOCK_SCREENER_ROOT / "scripts" / "screener_arena_cycle.py"),
        "--python-bin",
        args.python_bin,
        "--clawbot-root",
        str(clawbot_root),
        "submit",
        "--session-dir",
        str(session_dir),
        "--arena-url",
        arena_url,
        "--arena-api-key",
        arena_api_key,
    ]
    poly_bot = args.poly_bot or os.getenv("SCREENER_POLY_BOT", "").strip()
    if poly_bot:
        command.extend(
            [
                "--poly-bot",
                poly_bot,
                "--poly-arena-url",
                os.getenv("SCREENER_POLY_ARENA_URL", "http://127.0.0.1:8046"),
                "--pm-scan-pages",
                str(args.pm_scan_pages),
            ]
        )
    run_command(command, dry_run=args.dry_run, note=f"selected session: {session_dir.name}")


def run_maintenance(args) -> None:
    clawbot_root = resolve_existing_dir(args.clawbot_root, "clawbot-arena root")
    commands = []
    if not args.skip_backfill:
        commands.append([args.python_bin, str(clawbot_root / "scripts" / "backfill_screener.py")])
    if not args.skip_pm_sync:
        commands.append(
            [
                args.python_bin,
                str(clawbot_root / "scripts" / "sync_screener_pm_bets.py"),
                "--poly-arena-url",
                os.getenv("SCREENER_POLY_ARENA_URL", "http://127.0.0.1:8046"),
                "--last-n",
                str(args.pm_last_n),
            ]
        )
    for command in commands:
        run_command(command, dry_run=args.dry_run)


def run_report(args) -> None:
    clawbot_root = resolve_existing_dir(args.clawbot_root, "clawbot-arena root")
    command = [
        args.python_bin,
        str(clawbot_root / "scripts" / "generate_screener_report.py"),
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
    run_command(command, dry_run=args.dry_run)


def find_latest_session(runs_dir: Path, run_prefix: str | None, allow_empty: bool) -> Path:
    candidates = sorted([path for path in runs_dir.iterdir() if path.is_dir()], reverse=True)
    for path in candidates:
        if run_prefix and not path.name.startswith(run_prefix):
            continue
        final_selection_path = path / "judge" / "final_selection.json"
        if not final_selection_path.exists():
            continue
        payload = json.loads(final_selection_path.read_text(encoding="utf-8"))
        if allow_empty or payload.get("final_top_n"):
            return path
    raise SystemExit("No valid screener session found for automation")


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def resolve_existing_dir(raw_path: str, label: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(
            f"{label} not found: {path}. Pass --clawbot-root or set CLAWBOT_ARENA_ROOT."
        )
    return path


def run_command(command: list[str], dry_run: bool, note: str | None = None) -> None:
    if note:
        print(note)
    print("$", " ".join(command))
    if dry_run:
        return
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
