"""Job-board scrapers for Polish IT job market — deep search edition.

Each scraper returns a list of Offer dicts. Scaping runs entirely on the
self-hosted worker (residential IP) to avoid datacenter blocks.

Cross-site dedup: one offer appears on many boards. `dedup_key` normalizes
title+company (diacritics, gender suffixes like (k/m), punctuation) and
`scrape_all` keeps the FIRST occurrence, preferring sources in SCRAPE_ORDER
(offer URL is kept as extra evidence; URL-level dedup is also applied).
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

import html as html_mod
import urllib.parse
import urllib.request
import urllib.error

log = logging.getLogger("job-alerts.scrapers")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
}

# How deep to go: pages per listing URL (each page ~50 offers)
PAGES_PER_URL = int(os.getenv("SCRAPER_PAGES_PER_URL", "3"))
# Delay between page fetches (seconds) — politeness / block avoidance
FETCH_DELAY = float(os.getenv("SCRAPER_FETCH_DELAY", "1.5"))


@dataclass
class Offer:
    title: str
    company: str = ""
    location: str = ""
    work_mode: str = ""
    salary: str = ""
    url: str = ""
    skills: list[str] = field(default_factory=list)
    description: str = ""
    source: str = ""

    # --- dedup fuse -----------------------------------------------------
    _SUFFIX_NOISE = re.compile(
        r"[\(\[]\s*[a-z ,/]*\s*[\)\]]|m/k|k/m|f/m|x|k/m/n|m/f/k|f/m/x|w/m|k|\bwsz\b",
        re.I,
    )

    @staticmethod
    def _norm_text(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = s.lower()
        s = Offer._SUFFIX_NOISE.sub(" ", s)
        s = re.sub(r"[^a-z0-9]+", "", s)
        return s[:70]

    @property
    def dedup_key(self) -> str:
        return f"{self._norm_text(self.title)}|{self._norm_text(self.company)}"

    @property
    def url_key(self) -> str:
        u = (self.url or "").lower()
        u = re.sub(r"[?#].*$", "", u)
        return u


def _get(url: str, timeout: int = 30, headers: dict | None = None) -> bytes:
    h = dict(BASE_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _paged(urls: list[str]) -> list[str]:
    """Expand listing URLs into paginated variants (pracuj uses ?pn=N)."""
    out = []
    for u in urls:
        out.append(u)
        for pn in range(2, PAGES_PER_URL + 1):
            sep = "&" if "?" in u else "?"
            out.append(f"{u}{sep}pn={pn}")
    return out


# --------------------------------------------------------------------------
# pracuj.pl — __NEXT_DATA__ JSON; deep keyword + category searches
# --------------------------------------------------------------------------

def scrape_pracuj() -> list[Offer]:
    import time

    offers: dict[str, Offer] = {}
    # Targeted IT-infra keyword searches + broad IT category, Warszawa + remote
    searches = [
        "administrator systemowy",
        "administrator it",
        "administrator serwerów",
        "it support",
        "helpdesk",
        "inżynier systemów",
        "administrator sieci",
        "system administrator",
    ]
    urls = [
        "https://www.pracuj.pl/praca/" + urllib.parse.quote(s, safe="") + "%3Bkw"
        for s in searches
    ]
    urls += [
        "https://www.pracuj.pl/praca?cc=0301&wsn=Warszawa",
        "https://www.pracuj.pl/praca?cc=0301001&wsn=Warszawa",
        "https://www.pracuj.pl/praca?cc=0301&wpo=Zdalna",
    ]
    for u in _paged(urls):
        try:
            html = _get(u, timeout=40).decode("utf-8", "replace")
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                html, re.S,
            )
            if not m:
                continue
            data = json.loads(m.group(1))
            queries = (
                data.get("props", {}).get("pageProps", {})
                .get("dehydratedState", {}).get("queries", [])
            )
            got = 0
            for q in queries:
                qd = q.get("state", {}).get("data") or {}
                if not isinstance(qd, dict) or "groupedOffers" not in qd:
                    continue
                for g in qd.get("groupedOffers", []):
                    off = Offer(
                        title=g.get("jobTitle", ""),
                        company=g.get("companyName", ""),
                        salary=g.get("salaryDisplayText", ""),
                        url=(g.get("offers") or [{}])[0].get("offerAbsoluteUri", "")
                            if g.get("offers") else "",
                        work_mode=", ".join(g.get("workModes") or []),
                        description=_strip_tags(g.get("aiSummary", ""))[:600],
                        source="pracuj.pl",
                    )
                    locs = [o.get("displayWorkplace", "") for o in g.get("offers", [])]
                    off.location = "; ".join(dict.fromkeys(l for l in locs if l))
                    if off.title and off.url and off.dedup_key not in offers:
                        offers[off.dedup_key] = off
                        got += 1
            if got == 0 and "pn=" in u:
                pass  # exhausted; remaining pages skipped silently
            time.sleep(FETCH_DELAY)
        except Exception as e:
            log.warning("pracuj.pl scrape failed for %s: %s", u, e)
    log.info("pracuj.pl: %d offers", len(offers))
    return list(offers.values())


# --------------------------------------------------------------------------
# justjoin.it / rocketjobs.pl — server-rendered cards; deep city/remote pages
# --------------------------------------------------------------------------

_JUNK_SKILL = re.compile(r"\b(left|right|super offer|wygas|nowa|new\b|top\b|urgent)\b|\dd\b", re.I)


def _parse_jjit_cards(html: str, source: str, ctx_location: str = "") -> list[Offer]:
    offers = []
    anchor_re = re.compile(
        r'<a href="(?P<url>https?://[^"]+)"[^>]*class="offer_list_offer_title_link"'
        r'[^>]*>(?P<title>[^<]+)</a>'
    )
    anchors = list(anchor_re.finditer(html))
    for idx, m in enumerate(anchors):
        title = html_mod.unescape(_strip_tags(m.group("title")))
        if not title:
            continue
        start = anchors[idx - 1].end() if idx > 0 else max(0, m.start() - 4000)
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else m.end() + 6000
        before = html[start:m.start()]
        after = html[m.end():end]

        comp = ""
        for pm in re.finditer(r'<p class="[^"]*">([^<]{2,80})</p>', before[-2500:]):
            val = html_mod.unescape(_strip_tags(pm.group(1)))
            if val.lower() in ("hybrid", "remote", "zdalnie", "hybrydowo", "stacjonarnie", "on-site"):
                continue
            comp = val
        # map-pin icon is followed by nested svg tags before the <p>: match
        # across them with a lazy dot-all instead of [^>]*
        locm = re.search(
            r'lucide-map-pin.*?</svg></div><p class="[^"]*">([^<]{1,60})</p>',
            before[-2500:], re.S,
        )
        mode_m = re.search(r'<p class="[^"]*">(Hybrid|Remote|Zdalnie|Hybrydowo|Stacjonarnie|On-site)</p>', before[-2500:])
        sal_m = re.search(
            r'<span[^>]*>([\d., \s]+?(?:-\s*[\d., \s]+?)?)</span><!--[^>]*-->'
            r'\s*<span[^>]*>([A-Z]{2,4}/[a-z]+|[A-Z]{3})</span>',
            after[:2000],
        )
        skills = []
        for s in re.findall(r'<div class="MuiBox-root[^"]*">([A-Za-z0-9.#+/\- &;]{2,30})</div>', after[:4000]):
            s = html_mod.unescape(_strip_tags(s))
            if _JUNK_SKILL.search(s) or not s:
                continue
            skills.append(s)
            if len(skills) >= 8:
                break
        location = html_mod.unescape(_strip_tags(locm.group(1))) if locm else ""
        if not location and ctx_location:
            location = ctx_location
        offers.append(Offer(
            title=title,
            company=html_mod.unescape(comp) if comp else "",
            location=location,
            salary=(sal_m.group(1).strip() + " " + sal_m.group(2)) if sal_m else "",
            work_mode=mode_m.group(1) if mode_m else "",
            url=m.group("url"),
            skills=skills,
            source=source,
        ))
    return offers


def _scrape_jjit_family(base: str, slugs: list[tuple[str, str]], source: str) -> list[Offer]:
    """slugs: (url-slug, context-location) — the listing page itself tells us
    the location when the card markup doesn't (e.g. /warszawa, /remote)."""
    import time

    offers: dict[str, Offer] = {}
    for slug, ctx_loc in slugs:
        try:
            html = _get(f"{base}/{slug}", timeout=40).decode("utf-8", "replace")
            for o in _parse_jjit_cards(html, source, ctx_loc):
                offers.setdefault(o.dedup_key, o)
            time.sleep(FETCH_DELAY)
        except Exception as e:
            log.warning("%s scrape failed for %s: %s", source, slug, e)
    log.info("%s: %d offers", source, len(offers))
    return list(offers.values())


def scrape_justjoin() -> list[Offer]:
    return _scrape_jjit_family(
        "https://justjoin.it/job-offers",
        [
            ("warszawa", "Warszawa"),
            ("remote", "Zdalnie"),
            ("all-locations/devops", ""),
            ("all-locations/admin", ""),
            ("all-locations/support", ""),
        ],
        "justjoin.it",
    )


def scrape_rocketjobs() -> list[Offer]:
    return _scrape_jjit_family(
        "https://rocketjobs.pl/oferty-pracy",
        [
            ("warszawa", "Warszawa"),
            ("wszystkie-lokalizacje/it", ""),
            ("wszystkie-lokalizacje", ""),
        ],
        "rocketjobs.pl",
    )


# --------------------------------------------------------------------------
# czyjesteldorado.pl — OFFICIAL MCP server (https://czyjesteldorado.pl/_mcp).
# Structured JSON via search_jobs: no HTML parsing, no rate-limit roulette.
# Falls back to the legacy HTML scraper below if the MCP server is unavailable.

ELDORADO_MCP = "https://czyjesteldorado.pl/_mcp"

_ELDO_QUERIES = [
    "administrator",
    "administrator systemów",
    "it support",
    "inżynier systemów",
    "windows administrator",
    "helpdesk",
    # dream companies: direct queries so their offers always enter the pool
    # regardless of title vocabulary
    "cd projekt",
]

_ELDO_MODE_LABEL = {"remote": "Zdalnie", "hybrid": "Hybrydowo", "office": "Stacjonarnie"}


def _eldorado_mcp_call(phrase: str, work_modes: list) -> list:
    import json as _json
    import urllib.request as _rq

    def post(body, sid=None):
        h = {"Content-Type": "application/json", "Accept": "application/json",
             "User-Agent": UA}
        if sid:
            h["Mcp-Session-Id"] = sid
        req = _rq.Request(ELDORADO_MCP, data=_json.dumps(body).encode(), headers=h)
        with _rq.urlopen(req, timeout=60) as r:
            return r.headers.get("Mcp-Session-Id"), r.read().decode("utf-8", "replace")

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "scraper_app", "version": "1.0"}}}
    sid, _ = post(init)
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "search_jobs",
                       "arguments": {"phrase": phrase, "sortOrder": "newest",
                                     "workModes": work_modes}}}
    _, raw = post(call, sid)
    resp = _json.loads(raw)
    text = resp["result"]["content"][0]["text"]
    return _json.loads(text).get("jobs", [])


def scrape_eldorado() -> list:
    import time

    offers: dict[str, Offer] = {}
    work_modes = ["remote", "hybrid", "office"]
    for q in _ELDO_QUERIES:
        try:
            jobs = _eldorado_mcp_call(q, work_modes)
            for j in jobs:
                modes = ", ".join(_ELDO_MODE_LABEL.get(m, m) for m in j.get("work_modes", []))
                sal = ""
                if j.get("salary_from") and j.get("salary_to"):
                    sal = f"{j['salary_from']} – {j['salary_to']} zł"
                elif j.get("salary_from"):
                    sal = f"od {j['salary_from']} zł"
                o = Offer(
                    title=j.get("title", ""),
                    company=j.get("company", ""),
                    location="; ".join(j.get("cities", [])),
                    work_mode=modes,
                    salary=sal,
                    url=j.get("url", ""),
                    skills=list(j.get("keywords", []))[:10],
                    description="",
                    source="czyjesteldorado.pl",
                )
                if o.title:
                    offers.setdefault(o.dedup_key, o)
            log.info("czyjesteldorado.pl MCP: q=%r -> %d offers (total %d)",
                     q, len(jobs), len(offers))
            time.sleep(1.0)
        except Exception as e:
            log.warning("czyjesteldorado.pl MCP failed for q=%r: %s; falling back to HTML", q, e)
            try:
                for o in _scrape_eldorado_html():
                    offers.setdefault(o.dedup_key, o)
            except Exception as e2:
                log.warning("czyjesteldorado.pl HTML fallback also failed: %s", e2)
            break
    log.info("czyjesteldorado.pl: %d offers", len(offers))
    return list(offers.values())


# czyjesteldorado.pl — /search endpoint; datacenter-blocked, works from
# residential IPs (the self-hosted worker). ELDORADO_DEBUG=1 dumps raw HTML.
# --------------------------------------------------------------------------

def _parse_search_cards(html: str) -> list[Offer]:
    offers = []
    h3_re = re.compile(r'<h3[^>]*>(?P<inner>.*?)</h3>', re.S)
    h3s = list(h3_re.finditer(html))
    for i, m in enumerate(h3s):
        inner = m.group("inner")
        # the offer anchor may wrap the h3, sit inside it, or follow it —
        # search the whole card region (inside, before, and the card body)
        region = inner
        if i + 1 < len(h3s):
            region += html[m.end():h3s[i + 1].start()][:800]
        link_m = re.search(r'<a[^>]*href="(?P<url>/[^"]+|https?://[^"]+)"', region)
        title = _strip_tags(inner)
        if not title or len(title) < 4:
            continue
        after = html[m.end():m.end() + 1200]
        salary_m = re.search(
            r'(?P<sal>\d[\d .,]*\s*[–-]\s*\d[\d .,]*)\s*zł', after
        )
        skills = re.findall(r'>([A-Za-z0-9.#/+\-]{2,30})</', after)[:8]
        loc_m = re.search(
            r'(Warszawa|Zdalnie|Zdalna|Remote|Kraków|Wrocław|Gdańsk|Poznań|'
            r'Łódź|Katowice|Szczecin|Lublin|Bydgoszcz|Hybrydowo|Hybryda|Hybryd)', after
        )
        raw_url = link_m.group("url") if link_m else ""
        if raw_url and raw_url.startswith("/"):
            raw_url = f"https://czyjesteldorado.pl{raw_url}"
        offers.append(Offer(
            title=title[:120],
            url=raw_url,
            salary=_strip_tags(salary_m.group("sal")) + " zł" if salary_m else "",
            location=loc_m.group(1) if loc_m else "",
            skills=[_strip_tags(s) for s in skills],
            source="czyjesteldorado.pl",
        ))
    return offers


ELDORADO_DELAY = float(os.getenv("ELDORADO_DELAY", "4.0"))


def _eldorado_get(url: str, timeout: int = 45) -> str:
    """GET with 429 backoff: eldorado rate-limits aggressive scrapers."""
    import time

    for attempt in range(3):
        try:
            return _get(url, timeout=timeout).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                wait = 30 * (attempt + 1)
                log.warning("czyjesteldorado.pl rate-limited (%d), backing off %ds", e.code, wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"eldorado GET failed after retries: {url}")


def _scrape_eldorado_html() -> list[Offer]:
    import time

    offers: dict[str, Offer] = {}
    # High-yield queries only: location-targeted terms let the site's own
    # search do the filtering, so fewer requests cover more ground.
    queries = [
        "administrator warszawa",
        "administrator zdalnie",
        "administrator systemów",
        "administrator it",
        "inżynier systemów warszawa",
        "it support warszawa",
    ]
    pages = int(os.getenv("ELDORADO_PAGES", "2"))
    for q in queries:
        q_enc = urllib.parse.quote(q, safe="")
        for page in range(1, pages + 1):
            try:
                u = f"https://czyjesteldorado.pl/search?q={q_enc}&page={page}"
                html = _eldorado_get(u)
                if page == 1 and q == queries[0] and os.getenv("ELDORADO_DEBUG"):
                    Path("/tmp/eldorado_search.html").write_text(html, encoding="utf-8")
                    log.info("ELDORADO_DEBUG: saved /tmp/eldorado_search.html (%d bytes)", len(html))
                got = _parse_search_cards(html)
                if not got and page > 1:
                    break
                for o in got:
                    offers.setdefault(o.dedup_key, o)
                time.sleep(ELDORADO_DELAY)
            except Exception as e:
                log.warning("czyjesteldorado.pl scrape failed for q=%s page=%d: %s", q, page, e)
                break
    log.info("czyjesteldorado.pl: %d offers", len(offers))
    return list(offers.values())


# czyjesteldorado aggregates the boards (incl. justjoin.it / rocketjobs.pl
# listings), so those two are redundant sources — their scrapers are kept in
# this file but disabled. Re-enable by adding them back to both dicts below.


# ---------------------------------------------------------------------------
# Offer enrichment: fetch the offer's detail page and extract the full
# posting text (JSON-LD JobPosting description). Eldorado blocks datacenter
# IPs for HTML pages but the self-hosted worker runs on a residential IP.
# Failure-safe: no description -> the offer keeps its tag-only signal.
# ---------------------------------------------------------------------------

ENRICH_TIMEOUT = int(os.getenv("ENRICH_TIMEOUT", "25"))
ENRICH_DELAY = float(os.getenv("ENRICH_DELAY", "1.0"))


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def enrich_offer(offer: Offer) -> Offer:
    """Fetch the offer detail page and fill offer.description with the
    posting text. Only czyjesteldorado.pl offers are enriched (their MCP
    gives no description); pracuj offers already carry one."""
    if offer.description or not offer.url or "czyjesteldorado.pl" not in offer.url:
        return offer
    try:
        html = _get(offer.url, timeout=ENRICH_TIMEOUT).decode("utf-8", "replace")
        m = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.S
        )
        if m:
            try:
                j = json.loads(m.group(1))
                desc = _strip_html(j.get("description", ""))[:2000]
                if desc:
                    offer.description = desc
            except json.JSONDecodeError:
                pass
        if not offer.description:
            # fallback: meta description
            mm = re.search(r'name="description" content="([^"]{40,})"', html)
            if mm:
                offer.description = _strip_html(mm.group(1))[:2000]
    except Exception as e:
        log.info("enrich failed for %s: %s", offer.url, str(e)[:80])
    return offer


def enrich_offers(offers: list, max_count: int | None = None) -> list:
    """Enrich gated candidates in place. Only offers lacking a description
    are fetched; pass the post-gate candidate list, not the raw pool."""
    import time

    todo = [o for o in offers if not o.description and o.url and "czyjesteldorado.pl" in o.url]
    if max_count:
        todo = todo[:max_count]
    log.info("enriching %d offers (of %d candidates)", len(todo), len(offers))
    for i, o in enumerate(todo):
        enrich_offer(o)
        if i < len(todo) - 1:
            time.sleep(ENRICH_DELAY)
    return offers


SCRAPERS = {
    "czyjesteldorado.pl": scrape_eldorado,
    "pracuj.pl": scrape_pracuj,
}

# Lower index wins when the same offer appears on multiple boards.
SCRAPE_ORDER = ["czyjesteldorado.pl", "pracuj.pl"]


def scrape_all() -> tuple[list[Offer], dict[str, str]]:
    """Run every scraper; merge with the cross-site dedup fuse.

    The same offer is often present on many sites. Dedup happens in two
    layers: (1) title+company normalized key, (2) identical target URL.
    The first board to report an offer (SCRAPE_ORDER) keeps it, so each
    digest entry appears exactly once.
    """
    per_board: dict[str, list[Offer]] = {}
    status: dict[str, str] = {}
    for name in SCRAPE_ORDER:
        try:
            got = SCRAPERS[name]()
            per_board[name] = got
            status[name] = f"ok ({len(got)} offers)"
        except Exception as e:
            per_board[name] = []
            status[name] = f"error: {e}"

    merged: dict[str, Offer] = {}
    url_seen: set[str] = set()
    duplicates = 0
    for name in SCRAPE_ORDER:
        for o in per_board.get(name, []):
            key = o.dedup_key
            ukey = o.url_key
            # remember every URL even for dropped duplicates, so a later
            # offer with a different title but the same target URL is caught
            if ukey:
                if ukey in url_seen:
                    duplicates += 1
                    continue
                url_seen.add(ukey)
            if key in merged:
                duplicates += 1
                continue
            merged[key] = o
    log.info("merged %d unique offers (%d cross-site duplicates dropped)",
             len(merged), duplicates)
    return list(merged.values()), status
