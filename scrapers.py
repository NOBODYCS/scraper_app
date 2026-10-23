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
# czyjesteldorado.pl — /search endpoint; datacenter-blocked, works from
# residential IPs (the self-hosted worker). ELDORADO_DEBUG=1 dumps raw HTML.
# --------------------------------------------------------------------------

def _parse_search_cards(html: str) -> list[Offer]:
    offers = []
    h3_re = re.compile(r'<h3[^>]*>(?P<inner>.*?)</h3>', re.S)
    for m in h3_re.finditer(html):
        inner = m.group("inner")
        link_m = re.search(r'<a[^>]*href="(?P<url>[^"]+)"', inner)
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
        offers.append(Offer(
            title=title[:120],
            url=link_m.group("url") if link_m else "",
            salary=_strip_tags(salary_m.group("sal")) + " zł" if salary_m else "",
            location=loc_m.group(1) if loc_m else "",
            skills=[_strip_tags(s) for s in skills],
            source="czyjesteldorado.pl",
        ))
    return offers


def scrape_eldorado() -> list[Offer]:
    import time

    offers: dict[str, Offer] = {}
    queries = [
        "administrator systemów", "administrator it", "it support",
        "administrator serwerów", "administrator sieci",
        "system administrator", "windows administrator",
        "administrator warszawa", "inżynier systemów warszawa",
        "administrator zdalnie",
    ]
    pages = int(os.getenv("ELDORADO_PAGES", "3"))
    for q in queries:
        q_enc = urllib.parse.quote(q, safe="")
        for page in range(1, pages + 1):
            try:
                u = f"https://czyjesteldorado.pl/search?q={q_enc}&page={page}"
                html = _get(u, timeout=45).decode("utf-8", "replace")
                if page == 1 and os.getenv("ELDORADO_DEBUG"):
                    Path("/tmp/eldorado_search.html").write_text(html, encoding="utf-8")
                    log.info("ELDORADO_DEBUG: saved /tmp/eldorado_search.html (%d bytes)", len(html))
                got = _parse_search_cards(html)
                if not got and page > 1:
                    break
                for o in got:
                    offers.setdefault(o.dedup_key, o)
                time.sleep(FETCH_DELAY)
            except Exception as e:
                log.warning("czyjesteldorado.pl scrape failed for q=%s page=%d: %s", q, page, e)
                break
    log.info("czyjesteldorado.pl: %d offers", len(offers))
    return list(offers.values())


SCRAPERS = {
    "czyjesteldorado.pl": scrape_eldorado,
    "pracuj.pl": scrape_pracuj,
    "justjoin.it": scrape_justjoin,
    "rocketjobs.pl": scrape_rocketjobs,
}

# Lower index wins when the same offer appears on multiple boards.
SCRAPE_ORDER = ["czyjesteldorado.pl", "pracuj.pl", "justjoin.it", "rocketjobs.pl"]


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
