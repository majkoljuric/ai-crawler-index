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

## Deliberate blocks vs blanket rules

Most sites have general rules that catch every crawler at once — blocking
`/search` or `/admin` is routine housekeeping, not a position on AI. A crawler
named in its own rule is a decision somebody made about that crawler.

Lump the two together and the interesting signal disappears. YouTube and the New
York Times both look like walls of "partial", when in fact **YouTube names none
of the 25 tracked crawlers and the NYT names 22**.

The index separates them everywhere: filled dots were named on purpose, hollow
ones were swept up by a general rule. Only **29% of the top 500 name an AI
crawler at all**.

## The data

Every reading is plain JSON over HTTPS with CORS enabled — no key, no signup,
usable directly from a browser or a script.

`schema_version` sits at the top of every file and changes only when the shape
does, so a client can tell what it's holding.

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

## Which sites are in the index

The 500 most-visited sites on the web that actually serve pages — selected by an
independent traffic ranking ([Tranco](https://tranco-list.eu/), published so that
research can cite a list anyone can re-derive), not by hand.

Four things are excluded:

- **Infrastructure** — CDNs, DNS, ad networks. The test isn't a blocklist but
  whether the domain serves a real page at all: update and connectivity-check
  endpoints like `windowsupdate.com` and `msftconnecttest.com`, and registrar
  parking domains, serve no documents and drop out on that basis. A crawler
  policy only means something for a site with pages on it.
- **Adult sites** — out of scope, and left out openly rather than quietly.
- **Duplicate front doors** — `youtu.be` folds into `youtube.com`, and the
  country variants of the big multinationals collapse to one entry, so a single
  brand can't occupy forty slots.
- **Anything that didn't respond** — shown as *not yet checked*, never counted
  as open.

Every entry keeps its rank, so any site's inclusion can be traced back to the
source list. The category labels are a convenience and approximate; the ranking
is the part that's reproducible.

---

# How it's built

Reference for anyone working on the code. Nothing here is needed to use the
index or the API.

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

Python standard library only, no dependencies.

## A note on the run history

Not every file in `data/runs/` is the same kind of thing, and each one says so:

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

## Changing what's tracked

- **Crawlers** live in `scraper/bots.json`. New AI crawlers appear regularly and
  this list is the part that needs a human.
- **The panel** is generated, not hand-edited. Change `TARGET_COUNT` in
  `build_domains.py` and re-run it against a fresh Tranco list:

  ```bash
  curl -L -o /tmp/tranco.zip https://tranco-list.eu/top-1m.csv.zip
  unzip -o /tmp/tranco.zip -d /tmp
  python3 scraper/build_domains.py /tmp/top-1m.csv
  ```

  It probes each candidate for a live HTML homepage, so a run takes a few
  minutes. `--recategorize` re-labels the existing list without re-probing,
  which keeps a label fix from reshuffling the panel. Growing much past the
  current size wants parallel fetching and sharded output first.
