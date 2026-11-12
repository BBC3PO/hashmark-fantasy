# Hashmark Fantasy

A free fantasy football site built around your team: matchup, best lineup, rooting guide, injury watch, rankings, start/sit, trade analyzer, waivers, research and news. Every feature is free.

## How it runs

- `site/` is the website, a single `index.html` plus `data.json`.
- `refresh.py` builds `site/data.json` from public Sleeper and ESPN data (Python standard library only).
  Run it locally with `python3 refresh.py site/data.json [ESPN_LEAGUE_ID ...]`.
- `.github/workflows/refresh.yml` runs `refresh.py` every hour and commits the new `data.json`.
  Those commits include `[CF-Pages-Skip]`, so Cloudflare doesn't rebuild every hour.
- `functions/` holds the Cloudflare Pages Functions:
  - `/api/data` serves the newest `data.json` straight from this repository.
  - `/api/league/<id>` loads a public ESPN league live, so "Sync league" is instant.
- `leagues.txt` lists ESPN league IDs to bake into the hourly data (one per line). This is optional, because live sync works without it.

## Cloudflare Pages settings

- Framework preset: None
- Build command: (leave empty)
- Build output directory: `site`

Data: Sleeper (Rotowire, Sportradar) and ESPN public APIs. Not affiliated with either.
