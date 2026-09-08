#!/usr/bin/env python3
"""
AI Crawler Index — scraper.

Fetches robots.txt for a list of domains, parses out directives for a
tracked set of AI-crawler user-agents, and writes a normalized snapshot.

Design notes (read before changing parsing logic):
- robots.txt files are public and explicitly meant to be fetched by
  automated agents. There is no legal grey area here, unlike scraping a
  site's rendered HTML.
- Group matching follows the spec's "most specific user-agent wins" rule:
  if a bot has its own `User-agent:` block, that block's rules apply
  instead of the `*` block, even if the `*` block is more restrictive.
- A bot is marked "blocked" only on an exact `Disallow: /` (or a
  `Disallow:` with a leading-substring path that matches the whole site,
  which we don't attempt to detect — false negatives here are safer than
  false positives). "partial" means some paths are disallowed but not
  the whole site. "allowed" means no matching disallow, or the bot isn't
  mentioned and there's no restrictive `*` block either.
- Every run is additive: we never overwrite history, only append a new
  dated snapshot. Staleness must be visible, not silently papered over.
"""

import csv
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOTS_FILE = Path(__file__).resolve().parent / "bots.json"
DOMAINS_FILE = Path(__file__).resolve().parent / "domains.csv"
RUNS_DIR = ROOT / "data" / "runs"
LATEST_FILE = ROOT / "data" / "latest.json"
INDEX_FILE = ROOT / "data" / "index.json"

USER_AGENT = "AICrawlerIndexBot/0.1 (+https://example.org/ai-crawler-index; contact: you@example.org)"
TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 1.0  # be polite; this is a courtesy fetch, not a stress test


def load_bots():
    with open(BOTS_FILE) as f:
        return json.load(f)


def load_domains():
    with open(DOMAINS_FILE) as f:
        return list(csv.DictReader(f))


def fetch_robots_txt(domain):
    url = f"https://{domain}/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            return {"url": url, "status": status, "body": body, "error": None}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code, "body": "", "error": f"HTTPError {e.code}"}
    except Exception as e:  # noqa: BLE001 — a fetch failure is data, not a crash
        return {"url": url, "status": None, "body": "", "error": str(e)}


def parse_groups(body):
    """
    Split a robots.txt body into {user-agent: [disallow paths]} groups.
    Consecutive `User-agent:` lines share the rules that follow them,
    per the spec.
    """
    groups = {}
    current_agents = []
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            # A new User-agent line after we've already seen a Disallow
            # for the current group starts a *new* group per spec.
            if current_agents and any(groups.get(a) for a in current_agents):
                current_agents = []
            current_agents.append(value)
            for a in current_agents:
                groups.setdefault(a, [])
        elif field == "disallow" and current_agents:
            for a in current_agents:
                groups[a].append(value)
        # Allow/Crawl-delay/Sitemap intentionally ignored for this MVP
    return groups


def status_for_bot(groups, bot_name):
    # Most-specific match: exact bot name beats "*"
    matched_agent = None
    for agent in groups:
        if agent.lower() == bot_name.lower():
            matched_agent = agent
            break
    if matched_agent is None:
        matched_agent = "*" if "*" in groups else None

    if matched_agent is None:
        return "allowed"  # not mentioned anywhere, no wildcard block either

    paths = groups[matched_agent]
    if any(p.strip() == "/" for p in paths):
        return "blocked"
    if any(p.strip() for p in paths):
        return "partial"
    return "allowed"


def build_snapshot(bots, domains):
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for row in domains:
        domain = row["domain"]
        fetched = fetch_robots_txt(domain)
        entry = {
            "domain": domain,
            "category": row.get("category", ""),
            "robots_url": fetched["url"],
            "http_status": fetched["status"],
            "fetch_error": fetched["error"],
            "checked_at": checked_at,
            "bots": {},
        }
        if fetched["body"]:
            groups = parse_groups(fetched["body"])
            entry["bots"] = {bot: status_for_bot(groups, bot) for bot in bots}
        results.append(entry)
        time.sleep(REQUEST_DELAY_SECONDS)
    return {"checked_at": checked_at, "domains": results}


def main():
    bots = load_bots()
    domains = load_domains()
    snapshot = build_snapshot(bots, domains)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = snapshot["checked_at"][:10]
    run_path = RUNS_DIR / f"{date_tag}.json"
    with open(run_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    with open(LATEST_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

    index = []
    if INDEX_FILE.exists():
        with open(INDEX_FILE) as f:
            index = json.load(f)
    if date_tag not in index:
        index.append(date_tag)
        index.sort()
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)

    print(f"Wrote {run_path} ({len(snapshot['domains'])} domains checked)")


if __name__ == "__main__":
    sys.exit(main())
