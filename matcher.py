"""Two-stage matching of offers against Wojciech's competency profile.

Stage 1 (free, local): keyword gate + location rule + regex scoring.
Stage 2 (optional, cheap): batched Mistral LLM re-scoring of the shortlist —
real reasoning about fit instead of keyword counting. Controlled by
JOB_ALERTS_LLM_MATCH=1 (default on; set 0 to disable).
"""

from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("job-alerts.matcher")

SKILL_WEIGHTS: list[tuple[str, int]] = [
    (r"\b(system(?:s)? administrator|administrator system[oó]w|sysadmin|it administrator|administrator it)\b", 30),
    (r"\b(windows server|windows|linux|ubuntu|debian)\b", 14),
    (r"\b(active directory|\bad\b|entra\s?i?d?|azure ad|azure ad connect|intune|microsoft 365|m365|exchange online|office 365|defender|laps)\b", 14),
    (r"\b(powershell|bash|python)\b", 10),
    (r"\b(vmware|proxmox|hyper-?v|virtualization|wirtualizacja|vsphere)\b", 10),
    (r"\b(zabbix|grafana|monitoring|easyvista|itsm|service desk)\b", 10),
    (r"\b(veeam|backup|nas|synology|qnap|storage|san)\b", 8),
    (r"\b(fortinet|fortigate|ubiquiti|mikrotik|cisco|ruckus|vlan|firewall|wifi|wlan|lan|switching|nac|802\.1x|sdwan|sd-wan|vpn)\b", 10),
    (r"\b(gpo|group policy|wsus|wds|dns|dhcp|deployment|endpoint|helpdesk|help desk)\b", 8),
    (r"\b(automation|automatyzacja|scripting|skrypty)\b", 6),
    (r"\b(itil|itsm|sla|mttr|ticket|inciden[ct]|root[- ]cause)\b", 6),
    (r"\b(cloud|chmura|hybrid|hibryd|on[- ]?prem|infrastructure|infrastruktura)\b", 6),
    (r"\b(junior|mid|regular|specjalista)\b", 8),
    (r"\b(senior|lead|architect|gł[oó]wny)\b", -10),
    (r"\b(developer|programista|software engineer|backend|frontend|fullstack|full[- ]stack)\b", -35),
    (r"\b(java(?!script)|sap abap|flutter|swift|kotlin)\b", -25),
]

ROLE_GATE = re.compile(
    r"(admin|system|infrastruktur|it |it\b|network|sieci|helpdesk|help desk| "
    r"wsparcie|support|technolog|technic|devops|monitoring|cloud|chmura|windows|linux|m365|microsoft)",
    re.I,
)

WARSAW = re.compile(r"warszawa|warsaw|wwa|wola|mokot[óo]w|praga|ursyn[óo]w|bielany|bemowo|wilan[óo]w|żoliborz|ochota|śródmieście", re.I)
REMOTE = re.compile(r"zdaln|remote|work from home|ca[łl]a polska|whole poland", re.I)


def location_ok(offer) -> bool:
    """Hard rule: Warsaw (local/hybrid) OR fully remote — nothing else."""
    text = f"{offer.location or ''} {offer.work_mode or ''} {offer.title or ''}"
    if REMOTE.search(text):
        return True
    if WARSAW.search(text):
        return True
    return False


def score_offer(offer) -> int:
    text = " ".join([
        offer.title, offer.company, offer.description,
        " ".join(offer.skills or []), offer.work_mode,
    ]).lower()
    score = 0
    for pattern, weight in SKILL_WEIGHTS:
        if re.search(pattern, text, re.I):
            score += weight
    return max(0, min(100, score))


def match_offers(offers: list) -> list[dict]:
    """Stage 1: gate + score. Returns [{'offer': Offer, 'match': int}]."""
    results = []
    for o in offers:
        if not ROLE_GATE.search(o.title or ""):
            continue
        if not location_ok(o):
            continue
        s = score_offer(o)
        if s < 50:
            continue
        results.append({"offer": o, "match": s})
    results.sort(key=lambda r: r["match"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stage 2: LLM re-scoring (batched, cheap, optional)
# ---------------------------------------------------------------------------

PROFILE = """\
Wojciech Cieślak — IT Systems Engineer, 3+ years experience.
Current: Systems Administrator for Central, South & Eastern Europe at Antalis —
manages systems/infrastructure for 60+ sites in 7 countries (900+ users), primary
escalation point for 9 local IT departments.
Core stack: Windows Server & Linux (Ubuntu/Debian); Active Directory, Entra ID,
Azure AD Connect, Intune, Exchange Online, Microsoft 365 governance; VMware,
Proxmox, Hyper-V; Fortinet/Ubiquiti/MikroTik networking (VLANs, L2/L3, WLAN);
Zabbix & Grafana monitoring; PowerShell/Bash/Python automation; Veeam backups,
NAS; EasyVista ITSM; SOP development, root-cause analysis.
Level: mid (3 years) — junior/mid roles fit best; senior roles are a stretch.
Location requirement: Warsaw (onsite/hybrid) or fully remote ONLY.
NOT a developer: no software engineering, backend/frontend, Java, SAP ABAP."""

LLM_PROMPT = """You are a strict technical recruiter matching job offers to a candidate.

CANDIDATE PROFILE:
{profile}

JOB OFFERS (JSON array, each has id, title, company, location, work_mode, skills):
{{offers}}

For EACH offer, rate fit 0-100 considering: overlap with the core stack,
seniority fit (mid level, ~3 yrs), and whether the role is genuinely a
systems/infrastructure role (NOT development). Offers mentioning the candidate
must relocate or that are pure developer roles get low scores.

Respond ONLY with a JSON object: {{"scores": [{{"id": <id>, "score": <0-100>, "reason": "<max 15 words>"}}]}}"""


async def llm_rescore(matches: list[dict], api_key: str | None = None) -> list[dict]:
    """Batched LLM re-scoring of stage-1 matches. Mutates match values."""
    if os.getenv("JOB_ALERTS_LLM_MATCH", "1") != "1" or not matches:
        return matches

    api_key = api_key or os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        log.warning("LLM match enabled but MISTRAL_API_KEY missing; skipping stage 2")
        return matches

    from mistralai.client import Mistral

    client = Mistral(api_key=api_key)
    model = os.getenv("JOB_ALERTS_LLM_MODEL", "mistral-small-latest")
    batch_size = 20

    enriched = list(matches)
    for start in range(0, len(enriched), batch_size):
        batch = enriched[start:start + batch_size]
        payload = [
            {
                "id": i,
                "title": m["offer"].title[:120],
                "company": (m["offer"].company or "")[:60],
                "location": (m["offer"].location or "")[:60],
                "work_mode": (m["offer"].work_mode or "")[:40],
                "skills": (m["offer"].skills or [])[:10],
            }
            for i, m in enumerate(batch)
        ]
        prompt = LLM_PROMPT.format(profile=PROFILE, offers=json.dumps(payload, ensure_ascii=False))
        try:
            import asyncio

            def _call():
                return client.chat.complete(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
            resp = await asyncio.to_thread(_call)
            data = json.loads(resp.choices[0].message.content)
            for item in data.get("scores", []):
                idx = int(item.get("id", -1))
                if 0 <= idx < len(batch):
                    batch[idx]["llm_score"] = int(item.get("score", 0))
                    batch[idx]["llm_reason"] = str(item.get("reason", ""))[:120]
        except Exception as e:
            log.warning("LLM batch %d re-scoring failed: %s", start, e)

    for m in enriched:
        if "llm_score" in m:
            # blend: 50% regex + 50% LLM
            m["match"] = max(0, min(100, (m["match"] + m["llm_score"]) // 2))
    enriched.sort(key=lambda r: r["match"], reverse=True)
    return enriched
