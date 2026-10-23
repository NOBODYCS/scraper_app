"""Job-board scrapers for Polish IT job market.

Each scraper returns a list of dicts:
    title, company, location, work_mode, salary, url, skills, description, source

Designed to degrade gracefully: a failing board never kills the run.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
import os

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

    @property
    def dedup_key(self) -> str:
        norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())[:60]
        return f"{norm(self.title)}|{norm(self.company)}"


def _get(url: str, timeout: int = 30, headers: dict | None = None) -> bytes:
    h = dict(BASE_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


# --------------------------------------------------------------------------
# pracuj.pl  — __NEXT_DATA__ JSON embedded in server-rendered listing pages
# --------------------------------------------------------------------------

PRACUJ_CATEGORIES = [
    "cc=0301",      # IT - ogólnie
    "cc=0301001",   # administracja
    "cc=0301012",   # sieci
]
PRACUJ_KEYWORD = "administrator+systemowy"  # also fetch a keyword search


def scrape_pracuj() -> list[Offer]:
    offers = []
    seen = set()
    urls = ["https://www.pracuj.pl/praca?" + c for c in PRACUJ_CATEGORIES]
    urls.append(f"https://www.pracuj.pl/praca/{PRACUJ_KEYWORD}%3Bkw")
    for u in urls:
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
                    if off.title and off.url and off.dedup_key not in seen:
                        seen.add(off.dedup_key)
                        offers.append(off)
        except Exception as e:
            log.warning("pracuj.pl scrape failed for %s: %s", u, e)
    log.info("pracuj.pl: %d offers", len(offers))
    return offers


# --------------------------------------------------------------------------
# justjoin.it / rocketjobs.pl  — server-rendered offer cards
# Both sites run the same platform; cards share the
# `offer_list_offer_title_link` anchor class and MUI markup.
# --------------------------------------------------------------------------

def _parse_jjit_cards(html: str, source: str) -> list[Offer]:
    """Parse justjoin.it-family offer cards.

    Layout (server-rendered MUI): each card has a title anchor with class
    `offer_list_offer_title_link`; company name and location appear in <p>
    tags BEFORE the title; salary spans and skill chips follow it.
    """
    offers = []
    anchor_re = re.compile(
        r'<a href="(?P<url>https?://[^"]+)"[^>]*class="offer_list_offer_title_link"'
        r'[^>]*>(?P<title>[^<]+)</a>'
    )
    anchors = list(anchor_re.finditer(html))
    for idx, m in enumerate(anchors):
        title = _strip_tags(m.group("title"))
        if not title:
            continue
        # card window: from end of previous anchor to start of next
        start = anchors[idx - 1].end() if idx > 0 else max(0, m.start() - 4000)
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else m.end() + 6000
        before = html[start:m.start()]
        after = html[m.end():end]

        # company: last standalone <p> before the title that isn't a mode
        comp = ""
        for pm in re.finditer(r'<p class="[^"]*">([^<]{2,80})</p>', before[-2500:]):
            val = _strip_tags(pm.group(1))
            if val.lower() in ("hybrid", "remote", "zdalnie", "hybrydowo", "stacjonarnie", "on-site"):
                continue
            comp = val
        # location: <p> following the map-pin icon
        locm = re.search(r'lucide-map-pin[^>]*></svg></div><p class="[^"]*">([^<]{1,60})</p>', before[-2500:])
        mode_m = re.search(r'<p class="[^"]*">(Hybrid|Remote|Zdalnie|Hybrydowo|Stacjonarnie|On-site)</p>', before[-2500:])
        sal_m = re.search(
            r'<span[^>]*>([\d.,\u00a0\s]+?(?:-\s*[\d.,\u00a0\s]+?)?)</span><!--[^>]*-->'
            r'\s*<span[^>]*>([A-Z]{2,4}/[a-z]+|[A-Z]{3})</span>',
            after[:2000],
        )
        skills = [
            _strip_tags(s)
            for s in re.findall(
                r'<div class="MuiBox-root[^"]*">([A-Za-z0-9.#/+\- ]{2,30})</div>', after[:4000]
            )[:8]
        ]
        offers.append(Offer(
            title=title,
            company=comp,
            location=_strip_tags(locm.group(1)) if locm else "",
            salary=(sal_m.group(1).strip() + " " + sal_m.group(2)) if sal_m else "",
            work_mode=mode_m.group(1) if mode_m else "",
            url=m.group("url"),
            skills=skills,
            source=source,
        ))
    return offers


def scrape_justjoin() -> list[Offer]:
    offers = []
    for slug in ["all-locations", "all-locations/devops", "all-locations/admin"]:
        try:
            html = _get(f"https://justjoin.it/job-offers/{slug}", timeout=40).decode("utf-8", "replace")
            offers.extend(_parse_jjit_cards(html, "justjoin.it"))
        except Exception as e:
            log.warning("justjoin.it scrape failed for %s: %s", slug, e)
    uniq = {o.dedup_key: o for o in offers}
    log.info("justjoin.it: %d offers", len(uniq))
    return list(uniq.values())


def scrape_rocketjobs() -> list[Offer]:
    offers = []
    for slug in ["wszystkie-lokalizacje/it", "wszystkie-lokalizacje"]:
        try:
            html = _get(f"https://rocketjobs.pl/oferty-pracy/{slug}", timeout=40).decode("utf-8", "replace")
            offers.extend(_parse_jjit_cards(html, "rocketjobs.pl"))
        except Exception as e:
            log.warning("rocketjobs.pl scrape failed for %s: %s", slug, e)
    uniq = {o.dedup_key: o for o in offers}
    log.info("rocketjobs.pl: %d offers", len(uniq))
    return list(uniq.values())


# --------------------------------------------------------------------------
# czyjesteldorado.pl  — Next.js app; best-effort JSON-LD / heading scrape.
# The site aggressively blocks some datacenter IPs; failure is non-fatal.
# --------------------------------------------------------------------------

def scrape_eldorado() -> list[Offer]:
    """Scrape czyjesteldorado.pl /search pages.

    The site blocks datacenter IPs; from a residential IP (the self-hosted
    worker) the pages render server-side with offer cards as <article> or
    list markup. Parses title/company/salary/location/skills from card text;
    URL is linked from the card when present.
    """
    offers = []
    queries = ["administrator", "it+support", "helpdesk"]
    for q in queries:
        try:
            html = _get(f"https://czyjesteldorado.pl/search?q={q}", timeout=45).decode("utf-8", "replace")
            if os.getenv("ELDORADO_DEBUG"):
                Path("/tmp/eldorado_search.html").write_text(html, encoding="utf-8")
                log.info("ELDORADO_DEBUG: saved /tmp/eldorado_search.html (%d bytes)", len(html))
            offers.extend(_parse_search_cards(html))
        except Exception as e:
            log.warning("czyjesteldorado.pl scrape failed for q=%s: %s", q, e)
    uniq = {o.dedup_key: o for o in offers}
    log.info("czyjesteldorado.pl: %d offers", len(uniq))
    return list(uniq.values())


SCRAPERS = {
    "pracuj.pl": scrape_pracuj,
    "justjoin.it": scrape_justjoin,
    "rocketjobs.pl": scrape_rocketjobs,
    "czyjesteldorado.pl": scrape_eldorado,
}


def scrape_all() -> tuple[list[Offer], dict[str, str]]:
    """Run every scraper; return merged offers and per-board status."""
    all_offers: dict[str, Offer] = {}
    status: dict[str, str] = {}
    for name, fn in SCRAPERS.items():
        try:
            got = fn()
            status[name] = f"ok ({len(got)} offers)"
            for o in got:
                all_offers.setdefault(o.dedup_key, o)
        except Exception as e:
            status[name] = f"error: {e}"
    return list(all_offers.values()), status


def _parse_search_cards(html: str) -> list[Offer]:
    """Parse offer cards from a czyjesteldorado.pl /search page.

    Cards render as headings followed by company/location/seniority text and
    skill chips; best-effort structured extraction used until the exact
    markup is confirmed from a reachable IP (set ELDORADO_DEBUG=1 on the
    worker to dump the raw page for parser tuning).
    """
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
            r'(?P<sal>\d[\d\u00a0.,]*\s*[\u2013-]\s*\d[\d\u00a0.,]*)\s*z\u0142', after
        )
        skills = re.findall(r'>([A-Za-z0-9.#/+\-]{2,30})</', after)[:8]
        # company/location/seniority render adjacent to the title in card text
        loc_m = re.search(r'(Warszawa|Zdalnie|Krak\u00f3w|Wroc\u0142aw|Gda\u0144sk|Pozna\u0144|\u0141\u00f3d\u017a|Katowice|Szczecin|Lublin|Bia\u0142ystok|Bydgoszcz|Hybrydowo)', after)
        offers.append(Offer(
            title=title[:120],
            url=link_m.group("url") if link_m else "",
            salary=_strip_tags(salary_m.group("sal")) + " z\u0142" if salary_m else "",
            location=loc_m.group(1) if loc_m else "",
            skills=[_strip_tags(s) for s in skills],
            source="czyjesteldorado.pl",
        ))
    return offers
