# Screener Arena Automation

This document turns the Screener Arena workflow into four schedulable jobs:

1. `submit-latest`
2. `backfill`
3. `pm-sync`
4. `report`

All four jobs run through one wrapper:

[`scripts/screener_arena_automation.py`](/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener/scripts/screener_arena_automation.py)

## Environment

Add these to local `.env` in `stock-screener/`:

```env
SCREENER_ARENA_URL=http://127.0.0.1:8876
SCREENER_ARENA_API_KEY=your_screener_bot_api_key
SCREENER_POLY_BOT=screener-glm5-v1
SCREENER_POLY_ARENA_URL=http://127.0.0.1:8046
CLAWBOT_ARENA_ROOT=/absolute/path/to/clawbot-arena
```

If `SCREENER_POLY_BOT` is empty, automation will skip PM order submission.

If `CLAWBOT_ARENA_ROOT` is omitted, the wrapper falls back to the current workspace layout. If that inferred path does not exist, the command now fails fast with a clear error.

The Arena-side scripts also need:

```env
SYSTEM_API_KEY=your_arena_system_api_key
ARENA_DB_PATH=/absolute/path/to/arena.db
```

## Commands

Submit the latest valid session:

```bash
python scripts/screener_arena_automation.py submit-latest
```

Submit only sessions matching a prefix:

```bash
python scripts/screener_arena_automation.py submit-latest --run-prefix 20260307_pre_market_
```

Run close-of-day maintenance:

```bash
python scripts/screener_arena_automation.py maintenance
```

Generate a weekly report:

```bash
python scripts/screener_arena_automation.py report --days 7
```

Preview what automation would run:

```bash
python scripts/screener_arena_automation.py submit-latest --dry-run
python scripts/screener_arena_automation.py maintenance --dry-run
python scripts/screener_arena_automation.py report --dry-run
```

## Scheduling

Suggested timeline in New York market time:

- `06:30` submit latest screener session to Arena
- `16:30` backfill daily bars and refresh screener leaderboard
- `18:00` sync Poly Arena trades back into Arena
- `18:10` generate report

`launchd` templates live in:

- [`docs/launchd/com.hyperdrive.screener-arena.submit.plist`](/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener/docs/launchd/com.hyperdrive.screener-arena.submit.plist)
- [`docs/launchd/com.hyperdrive.screener-arena.maintenance.plist`](/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener/docs/launchd/com.hyperdrive.screener-arena.maintenance.plist)
- [`docs/launchd/com.hyperdrive.screener-arena.pm-sync.plist`](/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener/docs/launchd/com.hyperdrive.screener-arena.pm-sync.plist)
- [`docs/launchd/com.hyperdrive.screener-arena.report.plist`](/Users/huxiaohui/workspace/code/vibe_coding/life-os/20-wealth/trading-hub/stock-screener/docs/launchd/com.hyperdrive.screener-arena.report.plist)

Replace the placeholder paths before loading them.

## Load launchd jobs

```bash
mkdir -p ~/Library/LaunchAgents
cp docs/launchd/com.hyperdrive.screener-arena.submit.plist ~/Library/LaunchAgents/
cp docs/launchd/com.hyperdrive.screener-arena.maintenance.plist ~/Library/LaunchAgents/
cp docs/launchd/com.hyperdrive.screener-arena.pm-sync.plist ~/Library/LaunchAgents/
cp docs/launchd/com.hyperdrive.screener-arena.report.plist ~/Library/LaunchAgents/

launchctl unload ~/Library/LaunchAgents/com.hyperdrive.screener-arena.submit.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.hyperdrive.screener-arena.maintenance.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.hyperdrive.screener-arena.pm-sync.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.hyperdrive.screener-arena.report.plist 2>/dev/null || true

launchctl load ~/Library/LaunchAgents/com.hyperdrive.screener-arena.submit.plist
launchctl load ~/Library/LaunchAgents/com.hyperdrive.screener-arena.maintenance.plist
launchctl load ~/Library/LaunchAgents/com.hyperdrive.screener-arena.pm-sync.plist
launchctl load ~/Library/LaunchAgents/com.hyperdrive.screener-arena.report.plist
```

## Operational notes

- `submit-latest` only picks the newest session whose `judge/final_selection.json` exists and has non-empty `final_top_n`.
- `maintenance --skip-pm-sync` is the backfill job at `16:30`.
- `maintenance --skip-backfill` is the PM sync job at `18:00`.
- `report` calls Arena-side `generate_screener_report.py`.
- PM matching still depends on Polymarket actually listing relevant stock close markets. No listing means `no_market`, not automation failure.
