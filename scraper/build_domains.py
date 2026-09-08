#!/usr/bin/env python3
"""
Rebuild domains.csv from the Tranco top-1M list.

Why Tranco: it is a research-grade ranking, averaged over 30 days and combining
several underlying sources, published specifically so that studies can cite a
list that someone else can re-derive. Vendor "top sites" pages change silently
and can't be reproduced; Tranco can. That makes the tracked-domain list
auditable rather than a pile of domains someone picked by feel.

Selection, in rank order:
  1. Cheap reject of known infrastructure — CDNs, DNS, ad tech, telemetry.
     This is a shortcut, not the real test; the list can never be complete.
  2. Drop adult sites. Out of scope for this index, and excluded explicitly
     rather than quietly, so the omission is visible.
  3. The actual test: fetch the homepage and require HTTP 200 with an HTML
     content type. This is what separates a site from an endpoint. Blocklists
     can't enumerate the infrastructure in the top of this list —
     windowsupdate.com, msftconnecttest.com, registrar parking domains and a
     hundred CDN hostnames all look like ordinary domains — but none of them
     serve a document, so none of them survive a probe. A crawler policy only
     means something for a domain that serves documents.
  4. Deduplicate on the *final* hostname after redirects, which collapses
     youtu.be into youtube.com, wa.me into whatsapp.com, and the ccTLD front
     doors of the big multinationals, so one brand can't take 40 slots.
  5. Keep the first TARGET_COUNT survivors.

The Tranco rank is written to the output so every row can be traced back to the
source list. Re-run this to refresh the panel; the diff is the methodology.

This script makes network requests and takes a while — it is a list-building
tool run occasionally by hand, not part of the weekly scrape.

Usage:
    python3 scraper/build_domains.py path/to/top-1m.csv
"""

import csv
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

TARGET_COUNT = 500
MAX_CANDIDATES = 6000   # how far down the ranking to look before giving up
PROBE_WORKERS = 16
PROBE_TIMEOUT = 8
OUT_FILE = Path(__file__).resolve().parent / "domains.csv"
USER_AGENT = (
    "AICrawlerIndexBot/0.1 (+https://dear-robots.com; list building, one-off)"
)

# --- infrastructure: serves machines, not readers -------------------------
INFRA_SUBSTRINGS = (
    "akamai", "akadns", "edgekey", "edgesuite", "cloudfront", "fastly",
    "azureedge", "azurefd", "llnwd", "cdn77", "stackpathdns", "cachefly",
    "gstatic", "googleapis", "googleusercontent", "googlevideo", "ggpht",
    "fbcdn", "cdninstagram", "licdn", "twimg", "redditstatic", "redditmedia",
    "gtld-servers", "root-servers", "nsone", "ultradns", "dnsmadeeasy",
    "domaincontrol", "apple-dns", "aaplimg", "icloud-content",
    "amazonaws", "cloudfunctions", "appspot", "azurewebsites", "windows.net",
    "cloudflare-dns", "cloudflareinsights", "workers.dev",
    "-dns", "dns.", "ntp.", "whatsapp.net", "tiktokcdn", "tiktokv",
    "bytefcdn", "ibyteimg", "byteoversea", "pstatp", "snssdk",
    "cdn.", ".cdn", "static.", "media-", "img.", "assets.",
)
INFRA_EXACT = {
    "cloudflare.net", "cloudflare.com", "amazonaws.com", "akamai.net",
    "akamaiedge.net", "akadns.net", "fastly.net", "gtld-servers.net",
    "googletagmanager.com", "google-analytics.com", "googlesyndication.com",
    "googleadservices.com", "doubleclick.net", "gvt1.com", "gvt2.com",
    "ezviz7.com", "hicloudcam.com", "tuyaus.com", "tuyacn.com", "xiaoyi.com",
    "miwifi.com", "qq.com.cn", "sharethis.com", "onetrust.com", "cookielaw.org",
    "demdex.net", "omtrdc.net", "2o7.net", "everesttech.net", "adobedtm.com",
    "scorecardresearch.com", "quantserve.com", "adnxs.com", "adsrvr.org",
    "rubiconproject.com", "pubmatic.com", "casalemedia.com", "openx.net",
    "indexww.com", "criteo.com", "criteo.net", "taboola.com", "outbrain.com",
    "appsflyersdk.com", "appsflyer.com", "adjust.com", "branch.io",
    "crashlytics.com", "firebaseio.com", "app-measurement.com",
    "microsoftonline.com", "office365.com", "sharepoint.com", "live.net",
    "trafficmanager.net", "msedge.net", "msecnd.net", "aria.microsoft.com",
    "digicert.com", "globalsign.com", "sectigo.com", "letsencrypt.org",
    "ocsp.pki.goog", "entrust.net", "godaddy.net",
    "t.co", "bit.ly", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly",
}
INFRA_TLDS = (".arpa",)
# Observed in the top of the ranking: connectivity checks, registrars, parking,
# and link shorteners that just redirect into a site already in the list.
INFRA_EXACT |= {
    "windowsupdate.com", "msftncsi.com", "msftconnecttest.com", "msedge.net",
    "googledomains.com", "afternic.com", "registrar-servers.com", "dnsowl.com",
    "dns-parking.com", "nic.ru", "reg.ru", "gandi.net", "one.one", "akam.net",
    "app-analytics-services.com", "amazon-adsystem.com", "applovin.com",
    "adtrafficquality.google", "aliyuncs.com", "gwfb.net", "vedcdnlb.com",
    "steamserver.net", "myfritz.net", "a2z.com", "example.com", "example.org",
    "youtu.be", "wa.me", "t.me", "discord.gg", "forms.gle", "goo.gle",
    "office.net", "live.net", "windows.com", "cloud.microsoft",
    "vkuserphoto.ru", "yandex.net", "unity3d.com", "sentry-cdn.com",
}

# --- adult: deliberately out of scope, stated rather than hidden ----------
ADULT_SUBSTRINGS = (
    "porn", "xxx", "xvideos", "xnxx", "xhamster", "hentai", "rule34",
    "onlyfans", "chaturbate", "stripchat", "livejasmin", "bongacams",
    "cam4", "camsoda", "myfreecams", "brazzers", "redtube", "youporn",
    "tube8", "spankbang", "eporner", "erome", "fapello", "nhentai",
    "sex.com", "sexy", "escort", "adultfriend", "fetlife", "motherless",
    "javhd", "jav", "hanime", "e-hentai", "exhentai",
)

# --- multinationals whose ccTLD front doors collapse to one entry ---------
BRAND_SLDS = {
    "google", "amazon", "ebay", "yahoo", "msn", "bing", "apple", "microsoft",
    "netflix", "booking", "aliexpress", "alibaba", "paypal", "spotify",
    "samsung", "sony", "adobe", "oracle", "ibm", "intel", "nvidia", "dell",
    "hp", "lenovo", "asus", "canon", "nikon", "philips", "siemens", "bosch",
    "nike", "adidas", "zara", "ikea", "hm", "uniqlo", "decathlon", "carrefour",
    "mcdonalds", "starbucks", "cocacola", "pepsi", "nestle", "unilever",
    "toyota", "honda", "bmw", "mercedes-benz", "volkswagen", "audi", "ford",
    "hsbc", "citibank", "visa", "mastercard", "americanexpress",
    "booking", "expedia", "airbnb", "agoda", "trivago", "kayak",
    "uber", "lyft", "grab", "shopify", "wix", "squarespace", "godaddy",
    "wordpress", "tumblr", "medium", "linkedin", "twitter", "instagram",
    "facebook", "tiktok", "snapchat", "pinterest", "reddit", "discord",
    "telegram", "whatsapp", "viber", "line", "wechat", "skype", "zoom",
}

# --- categories: curated for recognisable sites, heuristics for the rest --
CATEGORY_MAP = {
    # news & current affairs
    "nytimes.com": "news", "washingtonpost.com": "news", "wsj.com": "news",
    "reuters.com": "news", "bbc.com": "news", "bbc.co.uk": "news",
    "theguardian.com": "news", "cnn.com": "news", "apnews.com": "news",
    "bloomberg.com": "news", "forbes.com": "news", "ft.com": "news",
    "economist.com": "news", "businessinsider.com": "news", "nypost.com": "news",
    "chicagotribune.com": "news", "abc.net.au": "news", "npr.org": "news",
    "aljazeera.com": "news", "dw.com": "news", "france24.com": "news",
    "cbsnews.com": "news", "nbcnews.com": "news", "abcnews.go.com": "news",
    "foxnews.com": "news", "usatoday.com": "news", "latimes.com": "news",
    "telegraph.co.uk": "news", "independent.co.uk": "news", "dailymail.co.uk": "news",
    "thetimes.co.uk": "news", "spiegel.de": "news", "zeit.de": "news",
    "lemonde.fr": "news", "lefigaro.fr": "news", "elpais.com": "news",
    "corriere.it": "news", "repubblica.it": "news", "asahi.com": "news",
    "nikkei.com": "news", "scmp.com": "news", "straitstimes.com": "news",
    "thehindu.com": "news", "timesofindia.indiatimes.com": "news",
    "globo.com": "news", "clarin.com": "news", "haaretz.com": "news",
    "politico.com": "news", "axios.com": "news", "thehill.com": "news",
    "newsweek.com": "news", "time.com": "news", "theatlantic.com": "news",
    "newyorker.com": "news", "vanityfair.com": "news", "vox.com": "news",
    "slate.com": "news", "salon.com": "news", "huffpost.com": "news",
    "buzzfeednews.com": "news", "propublica.org": "news", "cnbc.com": "news",
    "marketwatch.com": "news", "barrons.com": "news", "fortune.com": "news",
    # tech press
    "techcrunch.com": "tech-news", "theverge.com": "tech-news",
    "wired.com": "tech-news", "arstechnica.com": "tech-news",
    "engadget.com": "tech-news", "gizmodo.com": "tech-news",
    "zdnet.com": "tech-news", "cnet.com": "tech-news", "mashable.com": "tech-news",
    "venturebeat.com": "tech-news", "theregister.com": "tech-news",
    "tomshardware.com": "tech-news", "anandtech.com": "tech-news",
    "9to5mac.com": "tech-news", "macrumors.com": "tech-news",
    "androidauthority.com": "tech-news", "xda-developers.com": "tech-news",
    "hackernews.com": "tech-news", "news.ycombinator.com": "tech-news",
    # developer & documentation
    "github.com": "dev", "stackoverflow.com": "dev", "gitlab.com": "dev",
    "bitbucket.org": "dev", "npmjs.com": "dev", "pypi.org": "dev",
    "developer.mozilla.org": "dev", "readthedocs.io": "dev",
    "stackexchange.com": "dev", "serverfault.com": "dev", "superuser.com": "dev",
    "kaggle.com": "dev", "huggingface.co": "dev", "codepen.io": "dev",
    "jsfiddle.net": "dev", "replit.com": "dev", "leetcode.com": "dev",
    "geeksforgeeks.org": "dev", "w3schools.com": "dev", "freecodecamp.org": "dev",
    "digitalocean.com": "dev", "docker.com": "dev", "kubernetes.io": "dev",
    "rust-lang.org": "dev", "python.org": "dev", "golang.org": "dev",
    "nodejs.org": "dev", "php.net": "dev", "postgresql.org": "dev",
    # forums & Q&A
    "reddit.com": "forum", "quora.com": "forum", "discord.com": "forum",
    "4chan.org": "forum", "ycombinator.com": "forum", "producthunt.com": "forum",
    # publishing platforms
    "medium.com": "publishing", "substack.com": "publishing",
    "wordpress.com": "publishing", "blogger.com": "publishing",
    "tumblr.com": "publishing", "ghost.org": "publishing",
    "notion.so": "publishing", "wix.com": "publishing",
    "squarespace.com": "publishing", "webflow.com": "publishing",
    # reference
    "wikipedia.org": "reference", "wikimedia.org": "reference",
    "wiktionary.org": "reference", "britannica.com": "reference",
    "imdb.com": "reference", "goodreads.com": "reference",
    "dictionary.com": "reference", "merriam-webster.com": "reference",
    "fandom.com": "reference", "archive.org": "reference",
    "wolframalpha.com": "reference", "genius.com": "reference",
    # academic & science
    "nature.com": "academic", "sciencedirect.com": "academic",
    "springer.com": "academic", "arxiv.org": "academic", "jstor.org": "academic",
    "pubmed.ncbi.nlm.nih.gov": "academic", "researchgate.net": "academic",
    "academia.edu": "academic", "ssrn.com": "academic", "ieee.org": "academic",
    "acm.org": "academic", "plos.org": "academic", "wiley.com": "academic",
    "tandfonline.com": "academic", "sagepub.com": "academic",
    "scholar.google.com": "academic", "semanticscholar.org": "academic",
    "biorxiv.org": "academic", "mdpi.com": "academic", "frontiersin.org": "academic",
    # education
    "coursera.org": "education", "udemy.com": "education", "edx.org": "education",
    "khanacademy.org": "education", "duolingo.com": "education",
    "mit.edu": "education", "harvard.edu": "education", "stanford.edu": "education",
    "berkeley.edu": "education", "ox.ac.uk": "education", "cam.ac.uk": "education",
    "quizlet.com": "education", "chegg.com": "education", "coursehero.com": "education",
    "scribd.com": "education", "slideshare.net": "education",
    # reviews & local
    "yelp.com": "reviews", "tripadvisor.com": "reviews",
    "trustpilot.com": "reviews", "glassdoor.com": "reviews",
    "rottentomatoes.com": "reviews", "metacritic.com": "reviews",
    "g2.com": "reviews", "capterra.com": "reviews",
    # commerce
    "amazon.com": "ecommerce", "ebay.com": "ecommerce", "etsy.com": "ecommerce",
    "walmart.com": "ecommerce", "target.com": "ecommerce", "bestbuy.com": "ecommerce",
    "aliexpress.com": "ecommerce", "alibaba.com": "ecommerce", "shein.com": "ecommerce",
    "temu.com": "ecommerce", "wayfair.com": "ecommerce", "homedepot.com": "ecommerce",
    "lowes.com": "ecommerce", "costco.com": "ecommerce", "ikea.com": "ecommerce",
    "shopify.com": "ecommerce", "rakuten.co.jp": "ecommerce", "mercadolibre.com": "ecommerce",
    "flipkart.com": "ecommerce", "newegg.com": "ecommerce",
    # social
    "linkedin.com": "social", "pinterest.com": "social", "x.com": "social",
    "twitter.com": "social", "facebook.com": "social", "instagram.com": "social",
    "tiktok.com": "social", "snapchat.com": "social", "threads.net": "social",
    "mastodon.social": "social", "bsky.app": "social", "vk.com": "social",
    "weibo.com": "social", "telegram.org": "social",
    # media & streaming
    "youtube.com": "media", "netflix.com": "media", "twitch.tv": "media",
    "spotify.com": "media", "soundcloud.com": "media", "vimeo.com": "media",
    "hulu.com": "media", "disneyplus.com": "media", "max.com": "media",
    "primevideo.com": "media", "dailymotion.com": "media", "bandcamp.com": "media",
    # jobs
    "indeed.com": "jobs", "linkedin.com/jobs": "jobs", "monster.com": "jobs",
    "ziprecruiter.com": "jobs", "dice.com": "jobs", "lever.co": "jobs",
    "greenhouse.io": "jobs", "workday.com": "jobs",
    # travel
    "booking.com": "travel", "airbnb.com": "travel", "expedia.com": "travel",
    "kayak.com": "travel", "agoda.com": "travel", "trivago.com": "travel",
    "hotels.com": "travel", "skyscanner.net": "travel", "marriott.com": "travel",
    # real estate
    "zillow.com": "real-estate", "realtor.com": "real-estate",
    "redfin.com": "real-estate", "trulia.com": "real-estate",
    "rightmove.co.uk": "real-estate", "zoopla.co.uk": "real-estate",
    # finance
    "investopedia.com": "finance", "morningstar.com": "finance",
    "nerdwallet.com": "finance", "coindesk.com": "finance",
    "yahoo.com": "portal", "msn.com": "portal",
    # health
    "webmd.com": "health", "mayoclinic.org": "health", "healthline.com": "health",
    "nih.gov": "health", "cdc.gov": "health", "who.int": "health",
    "medlineplus.gov": "health", "drugs.com": "health",
    # government
    "usa.gov": "government", "irs.gov": "government", "europa.eu": "government",
    "gov.uk": "government", "canada.ca": "government", "un.org": "government",
    # sports
    "espn.com": "sports", "bleacherreport.com": "sports", "nba.com": "sports",
    "nfl.com": "sports", "mlb.com": "sports", "fifa.com": "sports",
    "skysports.com": "sports", "goal.com": "sports",
    # AI products (their own crawler policies are of obvious interest)
    "openai.com": "ai", "anthropic.com": "ai", "perplexity.ai": "ai",
    "midjourney.com": "ai", "stability.ai": "ai", "cohere.com": "ai",
}

SOFTWARE_DOMAINS = {
    "google.com", "microsoft.com", "apple.com", "office.com", "azure.com",
    "skype.com", "icloud.com", "whatsapp.com", "dropbox.com", "paypal.com",
    "zoom.us", "adobe.com", "samsung.com", "opera.com", "sentry.io",
    "nginx.com", "nginx.org", "kaspersky.com", "gravatar.com", "ui.com",
    "stripe.com", "okta.com", "teamviewer.com", "anydesk.com", "avast.com",
    "dell.com", "weebly.com", "calendly.com", "newrelic.com", "jotform.com",
    "braze.com", "singular.net", "meta.com", "xbox.com", "synology.com",
    "nextcloud.com", "garmin.com", "capcut.com", "miui.com", "mediafire.com",
    "netlify.app", "vercel.app", "heroku.com", "atlassian.com", "slack.com",
    "salesforce.com", "sap.com", "vmware.com", "cisco.com", "hp.com",
    "lenovo.com", "asus.com", "xiaomi.com", "huawei.com", "intel.com",
    "nvidia.com", "amd.com", "oracle.com", "ibm.com", "qualtrics.com",
    "mailchimp.com", "hubspot.com", "zendesk.com", "freshworks.com",
    "airtable.com", "asana.com", "trello.com", "monday.com", "clickup.com",
    "figma.com", "canva.com", "grammarly.com", "lastpass.com", "1password.com",
    "nordvpn.com", "expressvpn.com", "surfshark.com", "malwarebytes.com",
    "norton.com", "mcafee.com", "bitdefender.com", "eset.com", "avg.com",
    "hcaptcha.com", "cloudinary.com", "twilio.com", "sendgrid.com",
    "docusign.com", "smartsheet.com", "miro.com", "loom.com", "notion.com",
    "unity.com", "epicgames.com", "steampowered.com", "ea.com", "ubisoft.com",
    "roblox.com", "playrix.com", "crazygames.com", "kwai.com", "vivo.com.cn",
    "heytapmobi.com", "ys7.com", "chatgpt.com", "bing.com", "yandex.com",
    "baidu.com", "qq.com", "mail.ru", "ya.ru", "aol.com", "163.com",
    "sohu.com", "uber.com", "myspace.com", "note.com", "gofundme.com",
    "github.io", "amazonvideo.com", "capgemini.com", "crpt.ru",
    "consultant.ru", "allegro.pl", "dropcatch.com", "360yield.com",
    "triplinkintl.com", "mts.ru", "flickr.com", "vk.ru", "finn.no",
}

# heuristics for anything not curated above; first match wins, so the specific
# patterns must precede the broad TLD fallbacks
HEURISTICS = (
    (re.compile(r"\.edu$|\.ac\.[a-z]{2}$|university|college"), "education"),
    (re.compile(r"\.gov$|\.gov\.[a-z]{2}$|\.mil$|\.gouv\.|\.gob\."), "government"),
    (re.compile(r"wiki"), "reference"),
    # \b matters: without it "press" matches wordpress and "post" matches
    # postgresql, which is how wordpress.org first came out as a news site.
    (re.compile(r"\bnews|times\.|tribune|herald|gazette|\bpress\b|zeitung|noticias"), "news"),
    (re.compile(r"\bshop\b|\bstore\b|market|\bbuy\b|\bdeal"), "ecommerce"),
    (re.compile(r"blog"), "publishing"),
    (re.compile(r"forum|community"), "forum"),
    (re.compile(r"bank|\bpay\b|finance|invest|crypto|coin"), "finance"),
    (re.compile(r"soft|app\b|cloud|tech|digital|data|api|dev\b|host|vpn"), "software"),
    (re.compile(r"\.org$"), "nonprofit"),
)


def is_infrastructure(domain):
    if domain in INFRA_EXACT:
        return True
    if any(domain.endswith(t) for t in INFRA_TLDS):
        return True
    return any(s in domain for s in INFRA_SUBSTRINGS)


def is_adult(domain):
    return any(s in domain for s in ADULT_SUBSTRINGS)


def brand_key(domain):
    """Second-level label, used only to collapse multinational ccTLD variants."""
    parts = domain.split(".")
    return parts[0] if parts else domain


def categorize(domain):
    if domain in CATEGORY_MAP:
        return CATEGORY_MAP[domain]
    if domain in SOFTWARE_DOMAINS:
        return "software"
    for pattern, category in HEURISTICS:
        if pattern.search(domain):
            return category
    return "other"


def recategorize():
    """
    Re-apply categories to the existing list without re-probing.

    Categories are best-effort labelling; the selection itself is what has to be
    reproducible. Re-running the probe to fix a label would reshuffle which
    domains are in the list at all, because a site that happens to time out on
    the day you run it would drop out. So label edits are a separate pass.
    """
    rows = list(csv.DictReader(open(OUT_FILE, newline="", encoding="utf-8")))
    for row in rows:
        row["category"] = categorize(row["domain"])
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "category", "tranco_rank"])
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    print(f"Recategorized {len(rows)} domains")
    print("Categories: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    return 0


def probe(candidate):
    """
    Fetch the homepage. A domain that serves an HTML document is a site; one
    that errors, times out, or returns non-HTML is an endpoint. Returns the
    candidate annotated with the outcome and the post-redirect hostname.
    """
    rank, domain = candidate
    url = f"https://{domain}/"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final = (urlparse(resp.geturl()).hostname or domain).lower()
            final = re.sub(r"^www\.", "", final)
            if resp.status != 200:
                return (rank, domain, False, final, f"http {resp.status}")
            if "text/html" not in ctype:
                return (rank, domain, False, final, f"content-type {ctype[:40]!r}")
            return (rank, domain, True, final, "ok")
    except urllib.error.HTTPError as e:
        return (rank, domain, False, domain, f"http {e.code}")
    except Exception as e:  # noqa: BLE001 — an unreachable host is just a reject
        return (rank, domain, False, domain, type(e).__name__)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--recategorize":
        return recategorize()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    source = Path(sys.argv[1])

    # Cheap pass first, so the network only sees plausible candidates.
    candidates = []
    skipped = {"infrastructure": 0, "adult": 0, "brand-duplicate": 0,
               "no-html": 0, "redirect-duplicate": 0}
    seen_brands = set()

    with open(source, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(candidates) >= MAX_CANDIDATES:
                break
            if len(row) < 2:
                continue
            rank, domain = row[0], row[1].strip().lower()
            if is_infrastructure(domain):
                skipped["infrastructure"] += 1
                continue
            if is_adult(domain):
                skipped["adult"] += 1
                continue
            key = brand_key(domain)
            if key in BRAND_SLDS:
                if key in seen_brands:
                    skipped["brand-duplicate"] += 1
                    continue
                seen_brands.add(key)
            candidates.append((rank, domain))

    print(f"Probing up to {len(candidates)} candidates for a live HTML homepage…")

    selected = []
    seen_final = set()
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        for rank, domain, ok, final, reason in pool.map(probe, candidates):
            if len(selected) >= TARGET_COUNT:
                break
            if not ok:
                skipped["no-html"] += 1
                continue
            # Two domains that land on the same place are one site.
            if final in seen_final:
                skipped["redirect-duplicate"] += 1
                continue
            seen_final.add(final)
            selected.append({
                "domain": domain,
                "category": categorize(domain),
                "tranco_rank": rank,
            })

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "category", "tranco_rank"])
        writer.writeheader()
        writer.writerows(selected)

    categories = {}
    for row in selected:
        categories[row["category"]] = categories.get(row["category"], 0) + 1

    print(f"Wrote {OUT_FILE} with {len(selected)} domains")
    print(f"Skipped: {skipped}")
    print("Categories: " + ", ".join(
        f"{k}={v}" for k, v in sorted(categories.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
