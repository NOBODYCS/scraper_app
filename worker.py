"""job-alerts worker entrypoint (self-hosted).

Env:
- MISTRAL_API_KEY — Wojciech's personal Mistral API key; connector
  credentials (Gmail) resolve from this identity
- JOB_ALERTS_RECIPIENT — optional override, default wcieslak99@proton.me
"""

import asyncio
import logging
import os

import mistralai.workflows as workflows

import job_alerts_workflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("job-alerts")

if os.getenv("JOB_ALERTS_RECIPIENT"):
    log.info("recipient override: %s", os.getenv("JOB_ALERTS_RECIPIENT"))
log.info("job-alerts worker starting")


async def main() -> None:
    await workflows.run_worker([job_alerts_workflow.JobAlertsWorkflow])


if __name__ == "__main__":
    asyncio.run(main())
