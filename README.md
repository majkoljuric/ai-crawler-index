# AI Crawler Index

Which sites disallow which AI crawlers in `robots.txt`, tracked weekly, with the full history of what changed and when.

Registries and search engines will show you a site's current `robots.txt`. Nobody publishes the diff — when a publisher added `GPTBot` to its disallow list, or reversed a block after signing a licensing deal. That's what this tracks.

## Why this is legally simple

`robots.txt` is a public file that exists specifically to be read by automated agents ([RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html)). Fetching it isn't scraping in any contested sense — it's the one file on the internet whose entire purpose is "please, robots, read this."

## How it works

```
scraper/domains.csv     — list of tracked domains + category
scraper/bots.json       — canonical list of AI crawler user-agents to track
scraper/fetch_robots.py — fetches + parses robots.txt, writes a snapshot
data/runs/YYYY-MM-DD.json — one immutable snapshot per run (history)
data/latest.json        — most recent snapshot (what the site reads)
data/index.json         — list of run dates available
site/index.html         — static dashboard (matrix + change feed)
.github/workflows/scrape.yml — weekly cron: run scraper, commit, deploy
```

Run it yourself:

```bash
python3 scraper/fetch_robots.py
python3 -m http.server   # then open site/index.html
```

No dependencies beyond the Python standard library — deliberately, so there's nothing to break in CI two years from now.

## Status of the data in this repo right now

There are two runs in `data/runs/`, and they are not the same kind of thing:

- **`2026-09-08.json` — a real scrape.** 42 domains fetched live. 39 returned a `robots.txt`; 3 did not, and are recorded as fetch errors rather than quietly dropped: `cnn.com` (TLS chain verification failed on the machine that ran it), `stackoverflow.com` (HTTP 418 — it rejects the scraper's user-agent), `sciencedirect.com` (HTTP 403). A domain with a fetch error has an empty `bots` object, which the dashboard renders as *not yet checked* — not as *allowed*.
- **`2023-08-22.json` — a seed entry, not a scrape.** It records the well-documented August 2023 wave of `GPTBot` blocks by NYT, CNN, Reuters, Chicago Tribune and ABC Australia, sourced from contemporaneous reporting and labeled `"seed entry — not live-fetched"` in the file itself. It exists to give the change feed a prior point to diff against; every pair in it that reporting did not confirm is `"unverified"`, not guessed at.

The change feed stays thin until there are two consecutive live runs to compare.

## Deploying

1. Push this repo to GitHub.
2. Settings → Pages → Source: GitHub Actions.
3. The included workflow (`.github/workflows/scrape.yml`) runs every Monday, commits the new snapshot, and redeploys the site. Trigger it manually the first time from the Actions tab (`workflow_dispatch`) instead of waiting for Monday.

## Extending it

- **Add domains**: edit `scraper/domains.csv`. Keep the list defensible (e.g. "top N by traffic in category X") so additions don't look arbitrary.
- **Add crawlers**: edit `scraper/bots.json` as new AI crawlers show up — this list will need updates more often than the domain list.
- **Backfill history**: the Wayback Machine's CDX API can pull historical `robots.txt` snapshots per domain if you want depth before this repo's first run — that turns an empty change-feed into actual history on day one.
- **The obvious next product**: a CLI or GitHub Action that fails a build if a specific domain's crawler policy changed — that's the tool people would actually install, and this index is what makes it possible.

## Maintenance obligation

This only has value if it stays current. A three-month-stale crawler index is worse than none — it tells someone a site still blocks a crawler that reversed its policy months ago. The weekly cron does the mechanical part; watching for new crawlers to add to `bots.json` is the part that stays a human job.
