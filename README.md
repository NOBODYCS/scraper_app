# scraper_app

Daily Polish job-board scraper + profile matcher + Gmail digest, running as a
Mistral managed workflow.

## What it does

1. Scrapes offers from:
   - pracuj.pl (embedded `__NEXT_DATA__` JSON)
   - justjoin.it (server-rendered offer cards)
   - rocketjobs.pl (same platform markup as justjoin.it)
   - czyjesteldorado.pl (best-effort; the site blocks some datacenter IPs)
2. Scores each offer 0-100% against Wojciech's competency profile
   (IT Systems Engineer: Windows/Linux, AD/M365/Entra/Intune, virtualization,
   networking, monitoring, automation) — `matcher.py`
   Hard location filter: offers must be EITHER Warsaw (local/hybrid) OR fully
   remote — everything else is discarded.
3. Sends a daily digest email via the Gmail connector (on-behalf-of user)
   with the TOP 15 matches and the best-fitting approved CV variant attached
   (`data/cv/` — selection only, content is never modified).

## Running (self-hosted)

The worker self-registers the `job-alerts` workflow with Mistral and a daily
schedule triggers it. Gmail connector credentials resolve from the worker's
identity — run it with Wojciech's personal Mistral API key.

    cp .env.example .env   # set MISTRAL_API_KEY
    docker compose up -d --build

Daily schedule (Mistral side) triggers the registered `job-alerts` workflow.

Pipeline-only test:

    python -c "from scrapers import scrape_all; from matcher import match_offers; o,s=scrape_all(); print(s, len(o), len(match_offers(o)))"
