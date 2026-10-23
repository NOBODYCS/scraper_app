"""Build the daily digest email (HTML + plain text) with matches and CV attachment."""

from __future__ import annotations

import base64
import html as html_mod
import re
from pathlib import Path

CV_DIR = Path(__file__).parent / "data" / "cv"

# Approved CV variants (from the 'Kompetenecje i CV' library) mapped to
# the kind of offer they best support. Selection only — never content changes.
CV_VARIANTS = {
    "MICROSOFT": "cieslak_cv_en_MICROSOFT.pdf",
    "NETWORK": "cieslak_cv_en_NETWORK-WIFI.pdf",
    "SYSTEMS": "cieslak_cv_en_SYSTEMS.pdf",
}


def pick_cv(matched_text: str) -> tuple[str, Path]:
    t = matched_text.lower()
    if any(k in t for k in ["m365", "microsoft 365", "intune", "entra", "exchange", "office 365", "azure ad"]):
        return "MICROSOFT", CV_DIR / CV_VARIANTS["MICROSOFT"]
    if any(k in t for k in ["network", "sieci", "wifi", "wlan", "firewall", "fortinet", "vlan"]):
        return "NETWORK", CV_DIR / CV_VARIANTS["NETWORK"]
    return "SYSTEMS", CV_DIR / CV_VARIANTS["SYSTEMS"]


def _offer_row(m: dict, idx: int) -> str:
    o = m["offer"]
    loc = html_mod.escape(o.location or "—")
    title = html_mod.escape(o.title)
    company = html_mod.escape(o.company or "")
    sal = html_mod.escape(o.salary or "—")
    skills = ", ".join((o.skills or [])[:8])
    skills_h = html_mod.escape(skills) if skills else "—"
    url = html_mod.escape(o.url)
    loc_txt = f"{loc}"
    if re.search(r"zdaln|remote", f"{o.location} {o.work_mode}", re.I):
        loc_txt = "Zdalnie/Remote"
    elif re.search(r"warszawa|warsaw", loc, re.I):
        loc_txt = f"Warszawa · {html_mod.escape(o.work_mode or '')}"
    return f"""<tr>
<td style="padding:6px 8px;font-weight:600">{idx}</td>
<td style="padding:6px 8px">
  <div style="font-weight:700;font-size:14px"><a href="{url}">{title}</a></div>
  <div style="color:#555;font-size:12px">{company} · {loc_txt}</div>
  <div style="color:#777;font-size:12px">Skills: {skills_h}</div>
</td>
<td style="padding:6px 8px;text-align:center;font-weight:700">{m['match']}%</td>
</tr>"""


def build_email(matches: list[dict], board_status: dict[str, str], date_str: str) -> tuple[str, str, list[dict]]:
    """Return (subject, html_body, attachments)."""
    top = matches[:15]
    rows = "".join(_offer_row(m, i + 1) for i, m in enumerate(top))
    status_rows = "".join(
        f"<li>{html_mod.escape(b)}: {html_mod.escape(s)}</li>" for b, s in board_status.items()
    )
    cv_name, cv_path = ("", None)
    attachments = []
    if top:
        best_text = " ".join([
            top[0]["offer"].title, " ".join(top[0]["offer"].skills or []),
            top[0]["offer"].description,
        ])
        cv_name, cv_path = pick_cv(best_text)
        if cv_path.exists():
            attachments.append({
                "filename": cv_path.name,
                "mimeType": "application/pdf",
                "content": base64.b64encode(cv_path.read_bytes()).decode(),
            })

    n = len(matches)
    subject = f"[Job Alerts] {date_str}: {n} dopasowanych ofert IT"

    html_body = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:760px">
<h2 style="color:#1a1a2e">Daily Job Alerts — {date_str}</h2>
<p>{n} ofert dopasowanych do Twojego profilu (IT Systems Engineer). Poniżej TOP {len(top)}.</p>
<table border="1" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f0f0f5">
<th style="padding:6px 8px">#</th><th style="padding:6px 8px;text-align:left">Oferta</th>
<th style="padding:6px 8px">Match</th>
</tr></thead>
<tbody>{rows}</tbody></table>
<p style="font-size:12px;color:#666">Filtr lokalizacji: tylko Warszawa (stacjonarnie/hybrydowo) lub w pełni zdalnie.</p>
<p>Dobrany wariant CV dla najlepszej oferty: <b>{html_mod.escape(cv_name or "—")}</b>
{" — załączony" if attachments else " (plik niedostępny)"}</p>
<h3 style="font-size:13px">Status scrapowania</h3>
<ul style="font-size:12px;color:#555">{status_rows}</ul>
<p style="font-size:11px;color:#999">Wygenerowano automatycznie przez workflow job-alerts (Mistral).</p>
</div>"""

    plain = []
    for i, m in enumerate(top, 1):
        o = m["offer"]
        plain.append(f"{i}. [{m['match']}%] {o.title} — {o.company} ({o.location})\n   {o.url}")
    plain_text = f"Daily Job Alerts — {date_str}\n{n} matched offers.\n\n" + "\n".join(plain)
    return subject, html_body, attachments
