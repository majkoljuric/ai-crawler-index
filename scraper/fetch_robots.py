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
DOMAINS_DIR = ROOT / "data" / "domains"
SUMMARY_FILE = ROOT / "data" / "summary.json"

USER_AGENT = "AICrawlerIndexBot/0.1 (+https://dear-robots.com; contact: you@example.org)"
TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 1.0  # be polite; this is a courtesy fetch, not a stress test

# "Partial" is useless without knowing which paths, so the rules are stored too.
# They're keyed by user-agent group rather than by bot: a file's "*" group is
# usually what most of the 25 bots match, and storing it once per group instead
# of once per bot keeps the snapshot from bloating ~25x.
#
# Every path is stored, uncapped. A truncated rule set is worse than useless to
# anything consuming this as data: a caller can't tell "these are the rules"
# from "these are some of the rules", and a path that got cut is invisible
# rather than obviously missing. Completeness costs bytes; correctness for
# consumers is worth more.

# Bumped whenever the shape of a snapshot changes, so anything reading these
# files can tell which contract it's holding.
SCHEMA_VERSION = 2


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
    Split a robots.txt body into {user-agent: {"disallow": [...], "allow": [...]}}.
    Consecutive `User-agent:` lines share the rules that follow them, per the spec.

    `Allow` is captured as well as `Disallow`. A disallow list on its own can
    only say what is shut; sites routinely carve exceptions back out of a broad
    block (`Disallow: /` + `Allow: /blog`), and dropping those makes the rules
    read as more restrictive than they are.
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
            # A new User-agent line after we've already seen a rule for the
            # current group starts a *new* group per spec.
            if current_agents and any(
                groups.get(a, {}).get("disallow") or groups.get(a, {}).get("allow")
                for a in current_agents
            ):
                current_agents = []
            current_agents.append(value)
            for a in current_agents:
                groups.setdefault(a, {"disallow": [], "allow": []})
        elif field in ("disallow", "allow") and current_agents:
            for a in current_agents:
                groups[a][field].append(value)
        # Crawl-delay/Sitemap intentionally ignored
    return groups


def evaluate_bot(groups, bot_name):
    """
    Return (status, named) where `named` says whether the bot had its own
    User-agent group.

    That second value carries most of the meaning. A site with
    `Disallow: /search` under `*` gives every AI crawler "partial", but that is
    ordinary crawl hygiene, not a position on AI. A crawler named in its own
    block is a decision someone made about that crawler specifically. Without
    this distinction the two are indistinguishable in the data, and the
    interesting signal drowns in the generic one.
    """
    # Most-specific match: exact bot name beats "*"
    matched_agent = None
    for agent in groups:
        if agent.lower() == bot_name.lower():
            matched_agent = agent
            break
    named = matched_agent is not None
    if matched_agent is None:
        matched_agent = "*" if "*" in groups else None

    if matched_agent is None:
        return "allowed", False  # not mentioned anywhere, no wildcard block either

    disallow = groups[matched_agent]["disallow"]
    allow = groups[matched_agent]["allow"]
    if any(p.strip() == "/" for p in disallow):
        # A blanket block with carve-outs isn't a blanket block. Sites use
        # `Disallow: /` + `Allow: /x` to open exactly one thing.
        return ("partial" if any(p.strip() for p in allow) else "blocked"), named
    if any(p.strip() for p in disallow):
        return "partial", named
    return "allowed", named


def status_for_bot(groups, bot_name):
    return evaluate_bot(groups, bot_name)[0]


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
            "named": [],
            "rules": {},
        }
        if fetched["body"]:
            groups = parse_groups(fetched["body"])
            evaluated = {bot: evaluate_bot(groups, bot) for bot in bots}
            entry["bots"] = {bot: status for bot, (status, _) in evaluated.items()}
            # Bots the site addressed by name, rather than sweeping up under "*".
            entry["named"] = [bot for bot, (_, named) in evaluated.items() if named]

            # Keep only the groups that actually decided one of our verdicts —
            # a big robots.txt may hold dozens of groups for crawlers we don't
            # track, and none of them explain anything on the site.
            deciding = {bot if named else "*" for bot, (_, named) in evaluated.items()}
            for agent in sorted(deciding):
                if agent not in groups:
                    continue
                disallow = [p for p in groups[agent]["disallow"] if p.strip()]
                allow = [p for p in groups[agent]["allow"] if p.strip()]
                entry["rules"][agent] = {
                    "disallow": disallow,
                    "allow": allow,
                    "total": len(disallow),
                    "total_allow": len(allow),
                }
        results.append(entry)
        time.sleep(REQUEST_DELAY_SECONDS)
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": checked_at,
        "bots_tracked": sorted(bots),
        "domain_count": len(results),
        "domains": results,
    }


SAFE_DOMAIN = re.compile(r"^[a-z0-9.-]+$")


def slim(snapshot):
    """
    The same snapshot without the rule bodies.

    Rules are the bulk of the payload and the dashboard doesn't need them to
    draw the grid — it needs them only for the one cell someone clicks, which
    it fetches from the per-domain file on demand. Serving the full snapshot to
    every visitor means shipping several megabytes to render dots.

    This is a convenience for the page, not the published contract: the full
    snapshot and the per-domain files stay complete.
    """
    return {
        "schema_version": snapshot["schema_version"],
        "checked_at": snapshot["checked_at"],
        "bots_tracked": snapshot["bots_tracked"],
        "domain_count": snapshot["domain_count"],
        "rules_omitted": True,
        "domains": [
            {k: v for k, v in entry.items() if k != "rules"}
            for entry in snapshot["domains"]
        ],
    }


def write_domain_files(snapshot):
    """
    Emit one self-contained file per domain at data/domains/<domain>.json.

    Anyone wanting a single site shouldn't have to pull a multi-megabyte
    snapshot to get one entry. Each file repeats schema_version and checked_at
    so it stands alone — a caller holding one of these never has to fetch a
    second file to know how old it is or what shape it's in.

    These are overwritten in place rather than dated: they are "current state",
    and the dated files under runs/ remain the history.
    """
    DOMAINS_DIR.mkdir(parents=True, exist_ok=True)
    written = set()

    for entry in snapshot["domains"]:
        domain = entry["domain"]
        # Paths come from a CSV that a human edits, so don't trust it blindly
        # with a filesystem write.
        if not SAFE_DOMAIN.match(domain) or ".." in domain:
            print(f"  skipping unsafe domain name: {domain!r}")
            continue
        payload = {
            "schema_version": snapshot["schema_version"],
            "checked_at": snapshot["checked_at"],
            **entry,
        }
        path = DOMAINS_DIR / f"{domain}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        written.add(path.name)

    # Drop files for domains no longer tracked, or the API would keep serving
    # an answer for a site we stopped checking, with a checked_at that never
    # moves again.
    removed = 0
    for stale in DOMAINS_DIR.glob("*.json"):
        if stale.name not in written and stale.name != "index.json":
            stale.unlink()
            removed += 1

    with open(DOMAINS_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": snapshot["schema_version"],
            "checked_at": snapshot["checked_at"],
            "domains": sorted(e["domain"] for e in snapshot["domains"]),
        }, f, indent=2)

    print(f"Wrote {len(written)} per-domain files"
          + (f", removed {removed} stale" if removed else ""))


def rebuild_domain_files():
    """Regenerate the per-domain files from the current latest.json."""
    with open(LATEST_FILE, encoding="utf-8") as f:
        snapshot = json.load(f)
    if "schema_version" not in snapshot:
        print("latest.json predates schema_version; re-run the scraper first")
        return 1
    write_domain_files(snapshot)

    slim_snapshot = slim(snapshot)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(slim_snapshot, f, indent=2)
    date_tag = snapshot["checked_at"][:10]
    with open(RUNS_DIR / f"{date_tag}.summary.json", "w", encoding="utf-8") as f:
        json.dump(slim_snapshot, f, indent=2)
    print(f"Wrote {SUMMARY_FILE.name} and {date_tag}.summary.json")
    return 0


def main():
    if "--rebuild-domain-files" in sys.argv:
        return rebuild_domain_files()

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

    # Slim copies for the dashboard. The dated one exists so the change feed can
    # diff against the previous run without pulling that run's rule bodies too.
    slim_snapshot = slim(snapshot)
    with open(SUMMARY_FILE, "w") as f:
        json.dump(slim_snapshot, f, indent=2)
    with open(RUNS_DIR / f"{date_tag}.summary.json", "w") as f:
        json.dump(slim_snapshot, f, indent=2)

    index = []
    if INDEX_FILE.exists():
        with open(INDEX_FILE) as f:
            index = json.load(f)
    if date_tag not in index:
        index.append(date_tag)
        index.sort()
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)

    write_domain_files(snapshot)

    print(f"Wrote {run_path} ({len(snapshot['domains'])} domains checked)")


if __name__ == "__main__":
    sys.exit(main())
