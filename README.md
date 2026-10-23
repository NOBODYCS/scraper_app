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

## Running

Deployed as a Mistral managed deployment (hardened, OBO) from this repo.
Daily schedule triggers the `job-alerts` workflow.

Local dry run:

    python worker.py  # requires mistralai-workflows and MISTRAL_API_KEY

Pipeline-only test:

    python -c "from scrapers import scrape_all; from matcher import match_offers; o,s=scrape_all(); print(s, len(o), len(match_offers(o)))"
