# Screener Arena

This repo submits stock-screener outputs into Screener Arena and optionally into Poly Arena.

## Commands

Submit one session to Arena:

```bash
python scripts/screener_arena_cycle.py submit \
  --session-dir output/runs/20260307_pre_market_063433 \
  --arena-url http://127.0.0.1:8876 \
  --arena-api-key "$SCREENER_API_KEY"
```

Submit to Arena and Poly Arena:

```bash
python scripts/screener_arena_cycle.py submit \
  --session-dir output/runs/20260307_pre_market_063433 \
  --arena-url http://127.0.0.1:8876 \
  --arena-api-key "$SCREENER_API_KEY" \
  --poly-bot screener-glm5-v1 \
  --poly-arena-url http://127.0.0.1:8046
```

Sync Polymarket trades back into Arena:

```bash
python scripts/screener_arena_cycle.py sync \
  --poly-arena-url http://127.0.0.1:8046
```

Generate a report:

```bash
python scripts/screener_arena_cycle.py report --days 7
```

## Notes

- `scripts/screener_bridge.py` only submits tickers in `final_top_n`.
- `scripts/screener_to_poly.py` now scans active markets directly instead of relying on Gamma search.
- PM matching still depends on Polymarket actually listing relevant stock close markets.
- Arena-side PM sync and report generation live in the `clawbot-arena` repo.
- Use `--clawbot-root` or `CLAWBOT_ARENA_ROOT` when `clawbot-arena` is not in the default workspace location.
