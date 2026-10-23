"""Match offers against Wojciech Cieślak's competency profile.

Profile derived from the approved CVs in the 'Kompetenecje i CV' library
(IT Systems Engineer: Windows/Linux servers, M365/AD/Entra/Intune,
virtualization, networking, monitoring, automation).

Scoring is transparent keyword weighting -> 0-100%.
"""

from __future__ import annotations

import re

# Weighted skill groups: (keyword regex, weight)
SKILL_WEIGHTS: list[tuple[str, int]] = [
    # Core identity — systems/infrastructure administration
    (r"\b(system(?:s)? administrator|administrator system[oó]w|sysadmin|it administrator|administrator it)\b", 30),
    (r"\b(windows server|linux|ubuntu|debian)\b", 14),
    (r"\b(active directory|\bad\b|entra\s?i?d?|azure ad|azure ad connect|intune|microsoft 365|m365|exchange online|office 365|defender|laps)\b", 14),
    (r"\b(powershell|bash|python)\b", 10),
    (r"\b(vmware|proxmox|hyper-?v|virtualization|wirtualizacja)\b", 10),
    (r"\b(zabbix|grafana|monitoring|monitoring[ui]|easyvista|itsm|service desk)\b", 10),
    (r"\b(veeam|backup|nas|synology|qnap|storage|san)\b", 8),
    (r"\b(fortinet|fortigate|ubiquiti|mikrotik|cisco|ruckus|vlan|firewall|wifi|wlan|lan|switching|nac|802\.1x|rf planning)\b", 10),
    (r"\b(gpo|group policy|wsus|wds|dns|dhcp|deployment|endpoint|helpdesk|help desk|eskbar)\b", 8),
    (r"\b(automation|automatyzacja|scripting|skrypty)\b", 6),
    (r"\b(itil|itsm|sla|mttr|ticket|inciden[ct]|root[- ]cause)\b", 6),
    (r"\b(cloud|chmura|hybrid|hibryd|on[- ]?prem|infrastructure|infrastruktura)\b", 6),
    # Junior/mid level fit (Wojciech has ~3 years)
    (r"\b(junior|mid|regular|specjalista)\b", 8),
    (r"\b(senior|lead|architect|gł[oó]wny)\b", -10),
    # Hard negatives: developer-centric roles
    (r"\b(developer|programista|software engineer|backend|frontend|fullstack|full[- ]stack)\b", -35),
    (r"\b(java(?!script)|sap abap|flutter|swift|kotlin)\b", -25),
]

# Role-title gating: offers whose title matches one of these are considered at all
ROLE_GATE = re.compile(
    r"(admin|system|infrastruktur|it |it\b|network|sieci|helpdesk|help desk| "
    r"wsparcie|support|technolog|technic|devops|monitoring|cloud|chmura|windows|linux|m365|microsoft)",
    re.I,
)

# Hard location rule: offer must be EITHER Warsaw (local/hybrid) OR fully remote.
WARSAW = re.compile(r"warszawa|warsaw|wwa|wola|mokot[óo]w|praga|ursyn[óo]w|bielany|bemowo|wilan[óo]w|żoliborz|blocha|ochota|śródmieście", re.I)
REMOTE = re.compile(r"zdaln|remote|work from home|praca zdalna|ca[łl]a polska|whole poland|wielka brytania", re.I)
HYBRID = re.compile(r"hybryd|hybrid", re.I)


def location_ok(offer) -> bool:
    """Warsaw local/hybrid OR fully remote. Everything else is rejected."""
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
    """Return scored offers above threshold, best first.

    Hard location filter: Warsaw local/hybrid OR fully remote — nothing else.
    """
    results = []
    for o in offers:
        if not ROLE_GATE.search(o.title or ""):
            continue
        if not location_ok(o):
            continue
        s = score_offer(o)
        if s < 35:
            continue
        results.append({"offer": o, "match": s})
    results.sort(key=lambda r: r["match"], reverse=True)
    return results
