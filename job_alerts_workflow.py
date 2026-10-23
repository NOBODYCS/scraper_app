"""job-alerts workflow: scrape Polish job boards daily, match to profile, email via Gmail.

Runs on-behalf-of the triggering user (OBO), so Gmail uses Wojciech's own
connected account. Deploy with hardened=true for OBO.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pydantic

import mistralai.workflows as workflows
from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
    uses_connectors,
)

from email_builder import build_email
from matcher import match_offers
from scrapers import scrape_all

log = logging.getLogger("job-alerts.workflow")

gmail_connector = connector("gmail")


class JobAlertsInput(pydantic.BaseModel):
    recipient: str = "wcieslak99@proton.me"
    max_matches: int = 15
    dry_run: bool = False


@workflows.activity(name="collect-and-match")
async def collect_and_match() -> dict:
    offers, status = scrape_all()
    matches = match_offers(offers)
    log.info("total offers: %d, matches: %d", len(offers), len(matches))
    return {"matches": matches, "status": status}


@workflows.activity(name="send-digest")
async def send_digest(
    matches: list,
    status: dict,
    recipient: str,
    dry_run: bool,
    gmail: ToolCallClient = Depends(gmail_connector),
) -> dict:
    date_str = datetime.now().strftime("%Y-%m-%d")
    subject, html_body, attachments = build_email(matches, status, date_str)
    if dry_run:
        log.info("dry-run: would send %r to %s with %d attachments", subject, recipient, len(attachments))
        return {"subject": subject, "sent": False}
    result = await gmail.call_tool(
        tool_name="send_email",
        arguments={
            "to": [recipient],
            "subject": subject,
            "htmlBody": html_body,
            "attachments": attachments,
        },
    )
    log.info("email sent: %s", result)
    return {"subject": subject, "sent": True}


@workflows.workflow.define(name="job-alerts", on_behalf_of=True)
@uses_connectors(gmail_connector)
class JobAlertsWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, prompt: JobAlertsInput) -> dict:
        data = await collect_and_match()
        return await send_digest(
            data["matches"], data["status"], prompt.recipient, prompt.dry_run
        )
