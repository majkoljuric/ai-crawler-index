# Dear Robots

**Which websites let AI crawlers train on them — and which ones shut them out.**

[dear-robots.com](https://dear-robots.com) · updated weekly · free to use

Every site on the web can publish rules telling AI crawlers where they may and
may not go. Almost nobody reads them, and nothing tracks how they change. This
does: the web's most-visited sites, checked every week, with the history of who
changed their mind and when.

## What you can do with it

- **Check any site, instantly.** Type a domain and see exactly which of 25 AI
  crawlers it lets in, which it shuts out, and which paths are off-limits.
- **See who's serious.** Most sites have blanket rules that catch every crawler
  by accident. A much smaller number named an AI crawler on purpose. The index
  separates the two, because only one of them is a decision.
- **Watch policy shift.** When a publisher blocks a new crawler — or reverses a
  block after signing a licensing deal — it shows up in the change feed.
- **Build on the data.** Every reading is available as JSON over plain HTTPS,
  no key required. See [Using it as an API](#using-it-as-an-api).

## Is this allowed?

Yes. These rules live in a public file that exists specifically so automated
agents will read it — it's the one file on the internet whose entire purpose is
"please, robots, read this." Reading it isn't scraping in any contested sense,
and we read nothing else.

## What the statuses mean

| status | meaning |
|---|---|
| **blocked** | the whole site is closed to that crawler |
| **partial** | some paths are closed, the rest are open |
| **allowed** | nothing is closed to it |
| **not yet checked** | the site didn't respond when we last looked |

That last one matters: a site we couldn't reach is shown as a gap in coverage,
never as a site that welcomes every crawler. We'd rather show you a hole than
fill it with a guess.

## Named vs inherited — the distinction that carries the meaning

A site with `Disallow: /search` under `User-agent: *` gives *every* AI crawler a
"partial" verdict. That is ordinary crawl hygiene, not a position on AI. A
crawler named in its own `User-agent` block is a decision somebody made about
that crawler specifically.

Without separating those two, the interesting signal drowns in the generic one —
youtube.com and nytimes.com both look like walls of "partial", when in fact
YouTube names *none* of the 25 tracked crawlers and the NYT names 22.

So each snapshot records both — plus the rules themselves, because "partial" is
not an answer if you can't see which paths.

## The data contract

Snapshots are plain JSON served over HTTPS, so they are already usable as a
read-only API. `schema_version` is at the top of every file and gets bumped
whenever the shape changes, so a consumer can tell which contract it holds.

```jsonc
{
  "schema_version": 2,
  "checked_at": "2026-09-08T00:00:00+00:00",
  "bots_tracked": ["Ai2Bot", "Amazonbot", ...],   // the 25 crawlers
  "domain_count": 500,
  "domains": [
    {
      "domain": "nytimes.com",
      "category": "news",
      "tranco_rank": "154",
      "http_status": 200,
      "fetch_error": null,
      "bots":  {"GPTBot": "blocked", "Amazonbot": "partial", ...},
      "named": ["GPTBot", "Amazonbot", ...],       // addressed by name, not via *
      "rules": {                                    // keyed by user-agent group
        "*":          {"disallow": ["/search", ...], "total": 86},
        "Amazonbot":  {"disallow": ["/wirecutter/"], "total": 1}
      }
    }
  ]
}
```

Reading it: a bot's verdict is `bots[bot]`. The rules behind that verdict are
`rules[bot]` if the bot is in `named`, otherwise `rules["*"]`. A domain with a
`fetch_error` has an empty `bots` object — absence of data, never permission.

`rules` is stored **complete and uncapped**. A truncated rule set is worse than
useless to a consumer: you cannot distinguish "these are the rules" from "these
are some of the rules", and a path that got cut is invisible rather than
obviously missing.

Files:

| path | contents |
|---|---|
| `data/latest.json` | the most recent snapshot, all domains, **with rules** |
| `data/domains/<domain>.json` | one domain, self-contained, with rules |
| `data/domains/index.json` | the domains available |
| `data/summary.json` | latest.json **without** rule bodies (`rules_omitted: true`) |
| `data/runs/YYYY-MM-DD.json` | every historical snapshot, immutable |
| `data/runs/YYYY-MM-DD.summary.json` | the slim copy of that run |
| `data/index.json` | the run dates available |
| `scraper/bots.json` | the tracked crawlers, with operator and purpose |

Rule bodies are most of the payload, so there are two shapes of the same data.
Take `latest.json` or a domain file when you want the rules; take
`summary.json` when you only need verdicts and would rather not pull several
megabytes to get them. Anything with `"rules_omitted": true` is telling you the
rules were left out on purpose, not that the site has none — never read a
missing `rules` key there as "no rules".

### Using it as an API

GitHub Pages serves these with `Access-Control-Allow-Origin: *` and
`Content-Type: application/json`, so they are readable directly from a browser
with no proxy and no key:

```bash
curl https://dear-robots.com/data/domains/nytimes.com.json
```

```js
const r = await fetch('https://dear-robots.com/data/domains/nytimes.com.json');
const site = await r.json();
const verdict = site.bots['GPTBot'];                       // "blocked"
const rules  = site.named.includes('GPTBot')
  ? site.rules['GPTBot']                                   // its own block
  : site.rules['*'];                                       // inherited
```

Fetch a single domain rather than `latest.json` when you only need one — the
full snapshot is measured in megabytes, a domain file in kilobytes. Per-domain
files are overwritten each run and dropped when a domain leaves the panel, so a
site that stopped being tracked returns 404 rather than a frozen answer.
Responses carry an `ETag`; send `If-None-Match` and you'll get a cheap 304
between weekly runs.

The dashboard draws named verdicts as filled dots and inherited ones as hollow,
so provenance rides on shape rather than colour and survives a greyscale print;
clicking any dot shows the rules behind it.

## How the tracked panel is chosen

`scraper/domains.csv` is generated, not hand-picked — the methodology is the
script:

```bash
curl -L -o /tmp/tranco.zip https://tranco-list.eu/top-1m.csv.zip
unzip -o /tmp/tranco.zip -d /tmp
python3 scraper/build_domains.py /tmp/top-1m.csv
```

It walks [Tranco](https://tranco-list.eu/) — a research-grade ranking published
so that studies can cite a list someone else can re-derive — in rank order and
keeps the first 500 domains that survive:

1. **Not infrastructure.** A blocklist catches the obvious CDN/DNS/ad-tech cases.
2. **Not adult.** Out of scope, and excluded explicitly rather than quietly.
3. **Actually serves a document.** The real test: fetch the homepage, require
   HTTP 200 and an HTML content type. Blocklists can never enumerate the
   infrastructure at the top of this ranking — `windowsupdate.com`,
   `msftconnecttest.com`, registrar parking domains — but none of them serve a
   document, so none survive a probe. A crawler policy only means something for
   a domain that serves documents.
4. **Not a duplicate front door.** Deduplicated on the hostname *after*
   redirects, which collapses `youtu.be` into `youtube.com` and the ccTLD
   variants of the multinationals.

Every row keeps its `tranco_rank`, so any inclusion can be audited back to the
source list. Categories are best-effort labels applied by a curated map plus
heuristics — the ranking is the reproducible part, the category is a
convenience. `--recategorize` re-labels the existing list without re-probing, so
fixing a label never reshuffles which domains are in the panel.

---

# Running it yourself

Everything below is for maintaining the project, not for using it.

## Layout

```
scraper/domains.csv      — the tracked panel: domain, category, Tranco rank
scraper/bots.json        — the AI crawler user-agents to track
scraper/fetch_robots.py  — reads each site's rules, writes a snapshot
scraper/build_domains.py — rebuilds domains.csv from the Tranco top-1M list
data/latest.json         — most recent snapshot
data/domains/*.json      — per-domain files, current state
data/runs/YYYY-MM-DD.json — one immutable snapshot per run (history)
data/index.json          — run dates available
site/index.html          — the dashboard
.github/workflows/scrape.yml — weekly cron: run, commit, deploy
```

```bash
python3 scraper/fetch_robots.py                     # a full run
python3 scraper/fetch_robots.py --rebuild-domain-files   # regenerate per-domain files only
python3 -m http.server            # then open site/index.html
```

No dependencies beyond the Python standard library — deliberately, so there's
nothing to break in CI two years from now.

## What's in `data/runs/` right now

Not every run is the same kind of thing, and the files say so:

- **The dated live runs** are real readings of every tracked site. Sites that
  didn't respond carry a `fetch_error` and an empty `bots` object, which the
  dashboard shows as *not yet checked* — never as *allowed*.
- **`2023-08-22.json` is a seed entry, not a reading.** It records the
  well-documented August 2023 wave of `GPTBot` blocks by NYT, CNN, Reuters,
  Chicago Tribune and ABC Australia, sourced from contemporaneous reporting and
  labeled `"seed entry — not live-fetched"` inside the file. It exists to give
  the change feed a prior point to compare against. Anything reporting didn't
  confirm is `"unverified"` rather than guessed at.

The change feed stays empty until two live runs exist to compare, and says so on
the site rather than showing a filler list.

## Deploying

1. Push to GitHub.
2. Settings → Pages → Source: GitHub Actions.
3. The workflow runs every Monday, commits the new snapshot and redeploys.
   Trigger it by hand the first time from the Actions tab (`workflow_dispatch`).

## Extending it

- **Add crawlers**: edit `scraper/bots.json` as new ones appear. This needs
  updating far more often than the domain list, and it's the part that stays a
  human job.
- **Change the panel**: re-run `build_domains.py` with a different
  `TARGET_COUNT`. Growing it well past its current size will want parallel
  fetching and sharded output.
- **Backfill history**: the Wayback Machine's CDX API can pull historical
  readings per domain, which turns an empty change feed into real history on
  day one.
- **The obvious next thing**: a CLI or CI check that fails a build when a given
  site's crawler policy changes. That's the tool people would install, and this
  index is what makes it possible.

## The one obligation

This only has value if it stays current. A three-month-stale crawler index is
worse than none — it tells someone a site still blocks a crawler that reversed
its policy months ago.
