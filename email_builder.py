"""Build the daily digest email (HTML + plain text) with matched offers."""

from __future__ import annotations

import html as html_mod
import re


def _g(o, key, default=""):
    """Get a field from a dict or an object offer."""
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _offer_row(m: dict, idx: int) -> str:
    o = m["offer"]
    location = _g(o, "location")
    work_mode = _g(o, "work_mode")
    loc = html_mod.escape(location or "—")
    title = html_mod.escape(_g(o, "title"))
    company = html_mod.escape(_g(o, "company") or "")
    skills = ", ".join((_g(o, "skills") or [])[:8])
    skills_h = html_mod.escape(skills) if skills else "—"
    url = html_mod.escape(_g(o, "url"))
    source = html_mod.escape(_g(o, "source") or "")
    reason = html_mod.escape(m.get("llm_reason") or "")
    loc_txt = loc
    if re.search(r"zdaln|remote", f"{location} {work_mode}", re.I):
        loc_txt = "Zdalnie/Remote"
    elif re.search(r"warszawa|warsaw", loc, re.I):
        loc_txt = f"Warszawa · {html_mod.escape(work_mode or '')}"
    salary = html_mod.escape(_g(o, "salary") or "")
    salary_html = f" · <b>{salary}</b>" if salary else ""
    reason_html = f'<div style="color:#0a7d33;font-size:11px">🤖 {reason}</div>' if reason else ""
    return f"""<tr>
<td style="padding:6px 8px;font-weight:600">{idx}</td>
<td style="padding:6px 8px">
  <div style="font-weight:700;font-size:14px"><a href="{url}">{title}</a></div>
  <div style="color:#555;font-size:12px">{company} · {loc_txt}{salary_html} · <span style="color:#999">{source}</span></div>
  <div style="color:#777;font-size:12px">Skills: {skills_h}</div>
  {reason_html}
</td>
<td style="padding:6px 8px;text-align:center;font-weight:700">{m['match']}%</td>
</tr>"""


def build_email(matches: list[dict], board_status: dict[str, str], date_str: str) -> tuple[str, str]:
    """Return (subject, html_body)."""
    top = matches[:20]
    rows = "".join(_offer_row(m, i + 1) for i, m in enumerate(top))
    status_rows = "".join(
        f"<li>{html_mod.escape(b)}: {html_mod.escape(s)}</li>" for b, s in board_status.items()
    )

    n = len(matches)
    subject = f"[Job Alerts] {date_str}: {n} dopasowanych ofert IT (≥50%)"

    html_body = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:760px">
<h2 style="color:#1a1a2e">Daily Job Alerts — {date_str}</h2>
<p>{n} ofert dopasowanych do Twojego profilu (match ≥ 50%, lokalizacja: Warszawa lub zdalnie).</p>
<table border="1" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="background:#f0f0f5">
<th style="padding:6px 8px">#</th><th style="padding:6px 8px;text-align:left">Oferta</th>
<th style="padding:6px 8px">Match</th>
</tr></thead>
<tbody>{rows}</tbody></table>
<h3 style="font-size:13px">Status scrapowania</h3>
<ul style="font-size:12px;color:#555">{status_rows}</ul>
<p style="font-size:11px;color:#999">Wygenerowano automatycznie przez workflow job-alerts (Mistral).</p>
</div>"""

    plain = []
    for i, m in enumerate(top, 1):
        o = m["offer"]
        plain.append(
            f"{i}. [{m['match']}%] {_g(o, 'title')} — {_g(o, 'company')} ({_g(o, 'location')})\n   {_g(o, 'url')}"
        )
    plain_text = f"Daily Job Alerts — {date_str}\n{n} matched offers (>=50%).\n\n" + "\n".join(plain)
    return subject, html_body
