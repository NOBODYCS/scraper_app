"""job-alerts workflow: deep-scrape Polish job boards, two-stage match, email via Gmail.

Regular (non-OBO) workflow: connector credentials resolve from the worker's
identity, i.e. the Mistral API key the worker runs under. Run the worker with
Wojciech's personal Mistral API key so Gmail uses his connected account.
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
from matcher import llm_rescore, match_offers
from scrapers import scrape_all

log = logging.getLogger("job-alerts.workflow")

gmail_connector = connector("gmail")


class JobAlertsInput(pydantic.BaseModel):
    recipient: str = "wcieslak99@proton.me"
    dry_run: bool = False
    llm_match: bool = True


@workflows.activity(name="collect-and-match")
async def collect_and_match(llm_match: bool) -> dict:
    offers, status = scrape_all()
    matches = match_offers(offers)
    log.info("total offers: %d, stage-1 matches: %d", len(offers), len(matches))
    # funnel diagnostics: where do offers die, per source board?
    from matcher import ROLE_GATE, location_ok
    per_src = {}
    for o in offers:
        d = per_src.setdefault(o.source, {"total": 0, "role_gate": 0, "loc_ok": 0, "scored": 0})
        d["total"] += 1
        if not ROLE_GATE.search(o.title or ""):
            continue
        d["role_gate"] += 1
        if not location_ok(o):
            continue
        d["loc_ok"] += 1
    for m in matches:
        per_src.setdefault(m["offer"].source, {"total": 0, "role_gate": 0, "loc_ok": 0, "scored": 0})["scored"] += 1
    log.info("funnel per board: %s", per_src)
    if llm_match:
        matches = await llm_rescore(matches)
        log.info("stage-2 (LLM) matches: %d", len(matches))
    serializable = [
        {
            "match": m["match"],
            "llm_score": m.get("llm_score"),
            "llm_reason": m.get("llm_reason", ""),
            "offer": {
                "title": m["offer"].title,
                "company": m["offer"].company,
                "location": m["offer"].location,
                "work_mode": m["offer"].work_mode,
                "salary": m["offer"].salary,
                "url": m["offer"].url,
                "skills": m["offer"].skills,
                "source": m["offer"].source,
            },
        }
        for m in matches
    ]
    return {"matches": serializable, "status": status}


@workflows.activity(name="send-digest")
async def send_digest(
    matches: list,
    status: dict,
    recipient: str,
    dry_run: bool,
    gmail: ToolCallClient = Depends(gmail_connector),
) -> dict:
    date_str = datetime.now().strftime("%Y-%m-%d")
    subject, html_body, plain_body = build_email(matches, status, date_str)
    if dry_run:
        log.info("dry-run: would send %r to %s", subject, recipient)
        return {"subject": subject, "sent": False}
    result = await gmail.call_tool(
        tool_name="send_email",
        arguments={
            "to": [recipient],
            "subject": subject,
            "body": plain_body,
            "htmlBody": html_body,
        },
    )
    log.info("email sent: %s", result)
    return {"subject": subject, "sent": True}


@workflows.workflow.define(name="job-alerts")
@uses_connectors(gmail_connector)
class JobAlertsWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, prompt: JobAlertsInput) -> dict:
        data = await collect_and_match(prompt.llm_match)
        return await send_digest(
            data["matches"], data["status"], prompt.recipient, prompt.dry_run
        )
