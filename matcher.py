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

# Weights calibrated against the competency baseline (2026-10-23):
# strongest = M365/hybrid identity + monitoring/ITSM; intermediate = OS admin,
# scripting, backup; foundational-to-intermediate = networking, virtualization
# (do not overvalue); hard gaps = kubernetes/CI-CD, query SQL, cloud IaaS, certs.
SKILL_WEIGHTS: list[tuple[str, int]] = [
    # Role identity — specific forms strongest...
    (r"\b(system(?:s)? administrator|administrator system[oó]w|sysadmin|it administrator|administrator it|system(?:s)? engineer|inżynier system[oó]w|it systems engineer|systems and infrastructure)\b", 30),
    # ...generic "Administrator <something>" weaker signal
    (r"\b(administrator|administratorka)\b", 12),
    # STRONGEST competencies (baseline: intermediate-strong / strong)
    (r"\b(active directory|entra\s?i?d?|azure ad connect|intune|microsoft 365|m365|exchange online|office 365|sharepoint|teams|cross-forest|hybrid identity)\b", 16),
    (r"\b(zabbix|grafana|easyvista|itsm)\b", 14),
    (r"\b(monitoring|obserwowalność)\b", 8),
    (r"\b(itil|sla|mttr|sop|root[- ]cause)\b", 8),
    # INTERMEDIATE competencies
    (r"\b(windows server|windows|linux|ubuntu|debian)\b", 12),
    (r"\b(powershell|bash|python)\b", 9),
    (r"\b(veeam|backup|nas|synology|qnap)\b", 8),
    (r"\b(gpo|group policy|wsus|wds|dns|dhcp|endpoint management|defender|laps)\b", 8),
    # FOUNDATIONAL-TO-INTERMEDIATE — explicitly reduced per baseline
    (r"\b(vmware|proxmox|hyper-?v|virtualization|wirtualizacja|vsphere)\b", 6),
    (r"\b(fortinet|fortigate|ubiquiti|mikrotik|ruckus|vlan|firewall|wlan|802\.1x|nac\b|sdwan|sd-wan)\b", 6),
    (r"\b(cisco|ccna|ccnp)\b", 4),
    # Contextual positives
    (r"\b(automation|automatyzacja|scripting|skrypty)\b", 5),
    (r"\b(hybrid|hibryd|on[- ]?prem|infrastructure|infrastruktura|patching|patch management)\b", 6),
    (r"\b(mid|regular)\b", 8),
    # Level direction
    (r"\b(junior|młodszy|młodsza|mlodszy|stażysta|intern)\b", -15),
    (r"\b(helpdesk|help desk|service desk|wsparcie techniczne|1st line|first line|linia wsparcia)\b", -15),
    (r"\b(senior|lead|architect|gł[oó]wny|starsz)\b", -10),
    # NOT a developer / wrong discipline
    (r"\b(developer|programista|software engineer|backend|frontend|fullstack|full[- ]stack)\b", -35),
    (r"\b(java(?!script)|sap abap|flutter|swift|kotlin)\b", -35),
    # HARD GAPS from the baseline (weights recalibrated 2026-10-23):
    # kubernetes/ci-cd, terraform/ansible and cloud lines carry NO regex
    # penalty — adjacent/learnable tooling should not hide offers from the
    # digest; the stage-2 LLM still judges them as requirements.
    # Database penalty covers query-level/DBA work; mssql is deliberately
    # excluded — server-level MSSQL upkeep is the candidate's documented
    # level, and regex cannot tell upkeep from DBA work (LLM judges).
    (r"\b(oracle|dba|database administrator|administrator baz danych|postgresql|mysql|t[- ]sql)\b", -15),
    (r"\b(rhel|red hat|rhcsa|rhce)\b", -10),
    (r"\b(cissp|cism|ccsp|mcse|certif)\b", -10),
]

# Companies Wojciech would want to hear from even below the normal bar:
# these get into stage 2 at >=30 instead of >=50. The match % itself stays
# honest — only the entry threshold is relaxed, the LLM still judges fit.
DREAM_COMPANIES = re.compile(
    r"cd\s?projekt|cdpr|techland|allegro|nvidia|valve|riot|epic games|"
    r"blizzard|ubisoft|paradox|11\s?bit|people can fly|ci games|"
    r"bloober|flying wild hog|square enix|netflix|spotify|google|microsoft|"
    r"aws|cloudflare|digital ocean|gitlab",
    re.I,
)


def is_dream_company(company: str) -> bool:
    return bool(DREAM_COMPANIES.search(company or ""))


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


# Hard-gap patterns: these describe technologies the candidate does NOT
# claimably know. They are penalized at full weight only when they appear in
# requirement/description text; in a skill-tag list they are aspirational
# tags, not hard requirements, so they are penalized at 1/3 weight and the
# stage-2 LLM makes the real call.
HARD_GAP_PATTERNS = re.compile(
    r"\b(oracle|dba|database administrator|administrator baz danych|postgresql|mysql|t[- ]sql|"
    r"rhel|red hat|rhcsa|rhce|"
    r"cissp|cism|ccsp|mcse|certif)",
    re.I,
)


def score_offer(offer) -> int:
    desc = (offer.description or "").lower()
    text = " ".join([
        offer.title, offer.company, offer.description,
        " ".join(offer.skills or []), offer.work_mode,
    ]).lower()
    score = 0
    for pattern, weight in SKILL_WEIGHTS:
        if re.search(pattern, text, re.I):
            # negative gap weights: full penalty only when the gap is stated
            # in the description (a requirement), not merely tagged
            if weight < 0 and HARD_GAP_PATTERNS.search(pattern):
                if not re.search(pattern, desc, re.I):
                    score += weight // 3
                    continue
            score += weight
    return max(0, min(100, score))


def match_offers(offers: list) -> list[dict]:
    """Stage 1: gate + score. Returns [{'offer': Offer, 'match': int}].

    Broad-recall filter: boards differ in how much text an offer carries
    (pracuj gives full descriptions, justjoin only title+skills). The score
    threshold scales with available signal: 50 with a description, 35
    without. Stage 2 (LLM) then re-scores with real judgment and drops
    the ones that don't hold up.
    """
    results = []
    for o in offers:
        if not ROLE_GATE.search(o.title or ""):
            continue
        if not location_ok(o):
            continue
        s = score_offer(o)
        has_desc = bool((o.description or "").strip())
        # description-less boards (justjoin/rocketjobs cards) score lower on
        # the same fit because there is less text to match keywords in —
        # relax the cutoff there and let stage 2 (LLM) do the precision work.
        threshold = 50 if has_desc else 40
        # dream companies: entry bar relaxed to 20 — the % stays honest,
        # stage 2 still judges the real fit. A dream offer with real title
        # identity (+30) and only tag-level gaps lands ~20-30 and deserves
        # the LLM's attention.
        if s < threshold and is_dream_company(o.company):
            threshold = 20
        if s < threshold:
            continue
        results.append({"offer": o, "match": s})
    results.sort(key=lambda r: r["match"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stage 2: LLM re-scoring (batched, cheap, optional)
# ---------------------------------------------------------------------------

PROFILE = """\
Wojciech Cieślak — IT Systems Engineer / Systems Administrator, 3+ years
experience, Warsaw, Poland. English C1.
Current: Systems Administrator for Central, South & Eastern Europe at Antalis —
runs production infrastructure (servers, hybrid identity, endpoint management,
patching, monitoring) for 1,000+ users across 11 countries, overseeing
operations of 9 local IT departments. Primary escalation point.

COMPETENCY LEVELS (calibrated, from his competency baseline):
- STRONGEST: Microsoft 365 / hybrid identity (AD, Entra ID, Intune, Exchange
  Online, SharePoint, Teams, Azure AD Connect, cross-forest migrations) —
  intermediate-STRONG; Monitoring (Zabbix, Grafana, multi-source dashboards) —
  intermediate-STRONG, his main differentiator; ITSM/process (EasyVista, MTTR,
  SOPs) — strong for tenure.
- INTERMEDIATE: Windows Server & Linux (Ubuntu/Debian) administration;
  Scripting (PowerShell/Bash/Python, task-driven); Backup/storage (Veeam,
  Synology/QNAP); Security hardening (MS Defender, LAPS); Docs-as-Code.
- FOUNDATIONAL-TO-INTERMEDIATE (do not overvalue): Networking (Fortinet,
  VLANs, L2/L3 — ops-experienced, NOT senior design); Virtualization (VMware,
  Proxmox, Hyper-V).
- FOUNDATIONAL / NOT CLAIMABLE (penalize as hard requirements): Containers
  beyond Docker/Portainer (NO Kubernetes, NO CI/CD); SQL query-level work
  (server-level MSSQL maintenance only); Cloud IaaS beyond M365 (Azure VMs,
  VNets, AWS); certifications (he has none).
- DEFENSIBILITY LIMITS (do not score these as his experience): no vSphere/VMware
  administration (only migration-scoped vCenter work); no RHEL in production;
  no Conditional Access policy ownership (HQ owns it, he does per-user MFA);
  Apache = basic/vendor-spec only; Veeam experience is from a previous role
  (Outpro), not current; homelab (Proxmox, Portainer, WireGuard, TrueNAS) is
  practice, not professional experience.

Level: mid (3 years). Junior, helpdesk, and service-desk roles are a STEP DOWN
from his current position — he will not consider them. Senior roles are a stretch.
Location: he lives in Warsaw — Warsaw onsite/hybrid and fully remote are ALL
fine; NEVER claim relocation is required for Warsaw or remote offers.
NOT a developer: no software engineering, backend/frontend, Java, SAP ABAP.
Judge offers by the actual requirements in the description, not the title alone."""

LLM_PROMPT = """You are a strict technical recruiter matching job offers to a candidate.

CANDIDATE PROFILE:
{profile}

JOB OFFERS (JSON array; each has id, title, company, location, work_mode, salary, skills, description):
{{offers}}

Scoring rules:
- 80-100: strong overlap with the core stack AND mid-level systems/infrastructure role
- 50-79: partial overlap or stretch in one dimension
- 30-49: weak overlap OR wrong seniority direction
- 0-29: helpdesk/service-desk/junior support roles (a step DOWN for him), pure
  developer roles, or stacks with no overlap
- NEVER penalize Warsaw or remote offers as "relocation" — he is based in Warsaw
- Read the description requirements, not just the title: an "Administrator" of
  COBOL/SAP/Veeva/Jira is an application admin, NOT a systems administrator

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
                "salary": (m["offer"].salary or "")[:40],
                "skills": (m["offer"].skills or [])[:10],
                "description": (m["offer"].description or "")[:800],
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
    # Offers the LLM judged poor (blended < 50) are dropped from the digest
    enriched = [m for m in enriched if m["match"] >= 50]
    return enriched
