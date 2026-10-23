# scraper_app

Daily Polish job-board scraper + profile matcher + Gmail digest, running as a
self-hosted Mistral workflow worker (Docker / Portainer).

## What it does

1. Scrapes offers from:
   - pracuj.pl (embedded `__NEXT_DATA__` JSON)
   - justjoin.it (server-rendered offer cards)
   - rocketjobs.pl (same platform markup as justjoin.it)
   - czyjesteldorado.pl (best-effort; the site blocks some datacenter IPs)
2. Scores each offer 0-100% against Wojciech's competency profile
   (IT Systems Engineer: Windows/Linux, AD/M365/Entra/Intune, virtualization,
   networking, monitoring, automation) — `matcher.py`
   - Only matches ≥ 70% are included.
   - Hard location filter: offers must be EITHER Warsaw (local/hybrid) OR
     fully remote — everything else is discarded.
3. Sends a daily digest email via the Gmail connector with the TOP 20 matches.

## Running (self-hosted)

The worker self-registers the `job-alerts` workflow with Mistral; a daily
schedule triggers it. Gmail connector credentials resolve from the worker's
identity — run it with Wojciech's personal Mistral API key.

Portainer stack: use `docker-compose.yml` (image `ghcr.io/nobodycs/scraper_app:latest`),
set `MISTRAL_API_KEY` as a stack environment variable.

Every push to `main` rebuilds the image via GitHub Actions (with a live
scrape smoke test) and publishes to GHCR.

## Pipeline-only test

    python -c "from scrapers import scrape_all; from matcher import match_offers; o,s=scrape_all(); print(s, len(o), len(match_offers(o)))"
