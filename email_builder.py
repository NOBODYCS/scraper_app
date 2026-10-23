"""Build the daily digest email (HTML + plain text) with matched offers.

Design: email-client-safe inline CSS only (Gmail strips <style> blocks),
single-column cards, color-coded match badges, salary always shown when
available, dark-accent header, per-board scrape status footer.
"""

from __future__ import annotations

import html as html_mod
import re

# ---- palette -------------------------------------------------------------
INK = "#16213e"
MUTED = "#5f6772"
FAINT = "#9aa1ab"
LINE = "#e6e9ee"
BG = "#f4f6f9"
ACCENT = "#2563eb"
GREEN = "#0e7a3d"
AMBER = "#b45309"
RED = "#b91c1c"


def _g(o, key, default=""):
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _badge(match: int) -> str:
    if match >= 80:
        color = GREEN
    elif match >= 60:
        color = ACCENT
    elif match >= 50:
        color = AMBER
    else:
        color = RED
    return (
        f'<span style="display:inline-block;min-width:52px;padding:3px 8px;'
        f'border-radius:14px;background:{color};color:#ffffff;font-size:13px;'
        f'font-weight:700;text-align:center">{match}%</span>'
    )


def _location_label(location: str, work_mode: str) -> str:
    if re.search(r"zdaln|remote", f"{location} {work_mode}", re.I):
        return "🏠 Zdalnie / Remote"
    if re.search(r"warszawa|warsaw", location or "", re.I):
        mode = ""
        if re.search(r"hybryd|hybrid", work_mode or "", re.I):
            mode = " · hybrydowo"
        elif re.search(r"stacjonarn|on-?site", work_mode or "", re.I):
            mode = " · stacjonarnie"
        return f"📍 Warszawa{mode}"
    return f"📍 {html_mod.escape(location or '—')}"


def _salary_html(salary: str) -> str:
    s = (salary or "").strip()
    if not s:
        return f'<span style="color:{FAINT};font-size:12px">brak widełek</span>'
    esc = html_mod.escape(s)
    return f'<span style="font-weight:700;color:{INK};font-size:13px">💰 {esc}</span>'


def _title_html(url: str, title: str) -> str:
    if url:
        return (
            f'<div style="margin-top:2px"><a href="{url}" '
            f'style="color:{INK};font-weight:700;font-size:15px;text-decoration:none">{title}</a></div>'
        )
    return f'<div style="margin-top:2px;color:{INK};font-weight:700;font-size:15px">{title}</div>'


def _offer_card(m: dict, idx: int) -> str:
    o = m["offer"]
    title = html_mod.escape(_g(o, "title"))
    company = html_mod.escape(_g(o, "company") or "")
    url = html_mod.escape(_g(o, "url"))
    source = html_mod.escape(_g(o, "source") or "")
    skills = _g(o, "skills") or []
    skills_html = (
        "".join(
            f'<span style="display:inline-block;background:#eef2ff;color:#3730a3;'
            f'border-radius:4px;padding:1px 7px;font-size:11px;margin:1px 2px 1px 0">'
            f"{html_mod.escape(s)}</span>"
            for s in skills[:8]
        )
        or f'<span style="color:{FAINT};font-size:11px">brak tagów</span>'
    )
    loc_html = _location_label(_g(o, "location"), _g(o, "work_mode"))
    salary_html = _salary_html(_g(o, "salary"))
    reason = html_mod.escape(m.get("llm_reason") or "")
    reason_html = (
        f'<div style="margin-top:6px;color:{GREEN};font-size:11.5px;font-style:italic">'
        f"🤖 {reason}</div>" if reason else ""
    )
    return f"""<tr><td style="padding:0 0 10px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;background:#ffffff;border:1px solid {LINE};border-radius:8px">
<tr>
  <td style="padding:14px 16px 4px 16px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:top">
        <div style="font-size:11px;color:{FAINT};font-weight:700;letter-spacing:1px">#{idx:02d} · {source.upper()}</div>
        {_title_html(url, title)}
        <div style="color:{MUTED};font-size:12.5px;margin-top:2px">{company}</div>
      </td>
      <td align="right" style="vertical-align:top;padding-left:8px">{_badge(m['match'])}</td>
    </tr></table>
  </td>
</tr>
<tr><td style="padding:0 16px 12px 16px">
    <div style="margin-top:4px">{loc_html} &nbsp;·&nbsp; {salary_html}</div>
    <div style="margin-top:7px">{skills_html}</div>
    {reason_html}
</td></tr>
</table>
</td></tr>"""


def build_email(matches: list[dict], board_status: dict[str, str], date_str: str) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_text)."""
    top = matches[:20]
    cards = "".join(_offer_card(m, i + 1) for i, m in enumerate(top))

    n = len(matches)
    remote_n = sum(
        1 for m in matches
        if re.search(r"zdaln|remote", f"{_g(m['offer'], 'location')} {_g(m['offer'], 'work_mode')}", re.I)
    )
    waw_n = n - remote_n
    strong_n = sum(1 for m in matches if m["match"] >= 80)

    status_rows = "".join(
        f'<tr><td style="padding:2px 8px;color:{MUTED};font-size:11.5px">{html_mod.escape(b)}</td>'
        f'<td style="padding:2px 8px;color:{MUTED};font-size:11.5px;text-align:right">{html_mod.escape(s)}</td></tr>'
        for b, s in board_status.items()
    )

    subject = f"💼 {n} ofert IT dla Ciebie — {date_str}" + (f" (best: {matches[0]['match']}%)" if matches else "")

    empty = (
        f'<tr><td style="padding:30px;text-align:center;color:{MUTED};font-size:14px">'
        f"Brak ofert ≥ 50% dopasowania dzisiaj. Do jutra!</td></tr>"
    ) if not top else cards

    html_body = f"""<div style="background:{BG};padding:24px 12px;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;margin:0 auto;border-collapse:separate;background:#ffffff;border-radius:12px;overflow:hidden">
<tr><td style="background:{INK};padding:24px 28px">
  <div style="color:#ffffff;font-size:20px;font-weight:800;letter-spacing:0.5px">Daily Job Alerts</div>
  <div style="color:#c7d2fe;font-size:13px;margin-top:4px">{date_str} · Warszawa lub zdalnie · match ≥ 50%</div>
</td></tr>
<tr><td style="padding:18px 28px 4px 28px">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>
    <td style="padding:6px 14px 6px 0">
      <div style="font-size:24px;font-weight:800;color:{INK}">{n}</div>
      <div style="font-size:11px;color:{MUTED}">ofert dopasowanych</div>
    </td>
    <td style="padding:6px 14px 6px 0;border-left:1px solid {LINE}">
      <div style="font-size:24px;font-weight:800;color:{ACCENT}">{strong_n}</div>
      <div style="font-size:11px;color:{MUTED}">wysoki match (≥80%)</div>
    </td>
    <td style="padding:6px 14px 6px 0;border-left:1px solid {LINE}">
      <div style="font-size:24px;font-weight:800;color:{GREEN}">{waw_n}</div>
      <div style="font-size:11px;color:{MUTED}">w Warszawie</div>
    </td>
    <td style="padding:6px 0 6px 14px;border-left:1px solid {LINE}">
      <div style="font-size:24px;font-weight:800;color:{AMBER}">{remote_n}</div>
      <div style="font-size:11px;color:{MUTED}">zdalnie</div>
    </td>
  </tr></table>
</td></tr>
<tr><td style="padding:10px 16px 6px 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate">{empty}</table>
</td></tr>
<tr><td style="padding:8px 28px 22px 28px">
  <div style="border-top:1px solid {LINE};padding-top:12px">
    <div style="font-size:11px;font-weight:700;color:{FAINT};letter-spacing:1px;margin-bottom:6px">STATUS SCRAPOWANIA</div>
    <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{status_rows}</table>
  </div>
  <div style="margin-top:14px;font-size:10.5px;color:{FAINT}">
    Wygenerowano automatycznie przez scraper_app (Mistral workflow · match: regex + LLM vs Twój profil kompetencji)
  </div>
</td></tr>
</table>
</div>"""

    plain = []
    for i, m in enumerate(top, 1):
        o = m["offer"]
        sal = f" | {_g(o, 'salary')}" if _g(o, "salary") else ""
        plain.append(
            f"{i}. [{m['match']}%] {_g(o, 'title')} — {_g(o, 'company')} ({_g(o, 'location')}{sal})\n   {_g(o, 'url')}"
        )
    plain_text = f"Daily Job Alerts — {date_str}\n{n} matched offers (>=50%, Warszawa/remote).\n\n" + "\n".join(plain)
    return subject, html_body, plain_text
